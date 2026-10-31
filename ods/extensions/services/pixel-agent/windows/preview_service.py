"""Publish and serve Portal previews natively on Windows (windows-desktop profile).

The Linux host service (host/workspace_preview.py) depends on fcntl, pwd and
Unix sockets. This is the Windows equivalent for the publish action the ODS
plugin's pixel_ods_workspace_preview tool sends, with the same receipt:

- Control: a per-user named pipe. One newline-framed JSON request
  {schemaVersion: 1, action: "publish", relativeDirectory} in, one receipt out.
- Snapshot: create-only and content-addressed under <root>\\snapshots\\<site-id>.
  Only regular files are copied; a link or junction anywhere in the source is
  refused, so a sandboxed command cannot point a preview outside the workspace.
- Serving: 127.0.0.1:<port>, path /<site-id>/..., Host <site-id>.localhost,
  each site its own browser origin, with a sandboxing Content-Security-Policy
  that blocks every network request except to the site itself.
The receipt says readbackVerified only after index.html is fetched back over
HTTP and matches the snapshot.
"""
import asyncio
import hashlib
import http.client
import http.server
import json
import mimetypes
import os
import re
import shutil
import stat
import sys
import threading
import urllib.parse

BOUNDARY = ("Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; "
            "no arbitrary host path, network destination, server process, overwrite, or execution authority.")
PATH_COMPONENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
ASSET_COMPONENT = re.compile(r'^(?!__ods_)(?!__pycache__$)[A-Za-z0-9_\[][A-Za-z0-9._\[\]-]{0,127}$')
SITE_ID = re.compile(r'^site-[a-f0-9]{24}$')
SENSITIVE = re.compile(r'(?i)^(\.env.*|.*\.(pem|key|p12|pfx)|id_rsa.*|id_ed25519.*|\.git|\.ssh|\.npmrc|\.netrc)$')
MAX_FILES, MAX_BYTES, MAX_REQUEST = 128, 16 * 1024 * 1024, 8192
REPARSE_POINT = 0x400
CSP = ("sandbox allow-scripts allow-forms allow-modals allow-popups allow-downloads; default-src 'self'; "
       "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors http://localhost:* http://127.0.0.1:*; "
       "base-uri 'none'; form-action 'self'")


class PublishError(ValueError):
    """A publish request the service refuses; the code goes back to the plugin."""


def valid_relative_directory(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512 or value.startswith('/') or '\\' in value:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    parts = value.split('/')
    return len(parts) <= 12 and all(part not in ('', '.', '..') and PATH_COMPONENT.match(part) for part in parts)


def is_reparse_point(path):
    return bool(getattr(os.lstat(path), 'st_file_attributes', 0) & REPARSE_POINT) or os.path.islink(path)


def collect_site(workspace, relative_directory):
    """Every regular file to publish, as sorted (posix path, absolute path) pairs."""
    current = workspace
    for part in relative_directory.split('/'):
        current = os.path.join(current, part)
        if not os.path.isdir(current) or is_reparse_point(current):
            raise PublishError('source_directory_unavailable')
    files, total = [], 0
    for directory, folders, names in os.walk(current):
        for folder in list(folders):
            full = os.path.join(directory, folder)
            if is_reparse_point(full):
                raise PublishError('source_contains_link')
            if SENSITIVE.match(folder) or not ASSET_COMPONENT.match(folder):
                folders.remove(folder)
        for name in names:
            full = os.path.join(directory, name)
            if is_reparse_point(full):
                raise PublishError('source_contains_link')
            if SENSITIVE.match(name) or not ASSET_COMPONENT.match(name) or not stat.S_ISREG(os.lstat(full).st_mode):
                continue
            relative = os.path.relpath(full, current).replace(os.sep, '/')
            total += os.path.getsize(full)
            files.append((relative, full))
            if len(files) > MAX_FILES or total > MAX_BYTES:
                raise PublishError('site_too_large')
    files.sort()
    if not any(relative == 'index.html' for relative, _ in files):
        raise PublishError('entry_file_missing')
    return files, total


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def site_digest(entries):
    """sha256 over the sorted (path, file digest) manifest."""
    manifest = '\n'.join(f'{relative}\0{digest}' for relative, digest in entries)
    return hashlib.sha256(manifest.encode('utf-8')).hexdigest()


def snapshot(files, snapshots):
    entries = [(relative, file_sha256(full)) for relative, full in files]
    sha = site_digest(entries)
    site_id = 'site-' + sha[:24]
    destination = os.path.join(snapshots, site_id)
    if not os.path.isdir(destination):
        staging = destination + '.staging-' + os.urandom(8).hex()
        for relative, full in files:
            target = os.path.join(staging, *relative.split('/'))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(full, target)
        # Copied bytes are checked again: the source may change while it is read.
        if [(relative, file_sha256(os.path.join(staging, *relative.split('/')))) for relative, _ in files] != entries:
            shutil.rmtree(staging)
            raise PublishError('source_changed_during_capture')
        try:
            os.rename(staging, destination)
        except FileExistsError:
            shutil.rmtree(staging)  # The same content was published concurrently.
    return site_id, sha, entries


def readback(port, site_id, expected_sha):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        connection.request('GET', f'/{site_id}/', headers={'Host': f'{site_id}.localhost:{port}'})
        response = connection.getresponse()
        body = response.read()
        return response.status, hashlib.sha256(body).hexdigest() == expected_sha
    finally:
        connection.close()


def publish(request, workspace, snapshots, port):
    if (not isinstance(request, dict) or set(request) != {'schemaVersion', 'action', 'relativeDirectory'}
            or request['schemaVersion'] != 1 or request['action'] != 'publish'
            or not valid_relative_directory(request['relativeDirectory'])):
        raise PublishError('invalid_request')
    files, total = collect_site(workspace, request['relativeDirectory'])
    site_id, sha, entries = snapshot(files, snapshots)
    entry_sha = dict(entries)['index.html']
    status, matches = readback(port, site_id, entry_sha)
    if status != 200 or not matches:
        raise PublishError('readback_failed')
    paths = [relative for relative, _ in entries]
    shown = paths[:32]
    while sum(len(path) for path in shown) > 2048:
        shown.pop()
    empty = [relative for relative, full in files if os.path.getsize(full) == 0 and relative != 'index.html']
    return {
        'schemaVersion': 1, 'kind': 'ods-pixel-workspace-preview', 'status': 'succeeded',
        'relativeDirectory': request['relativeDirectory'], 'siteId': site_id, 'sha256': sha,
        'url': f'http://{site_id}.localhost:{port}/{site_id}/', 'port': port, 'files': len(entries), 'bytes': total,
        'entryFile': 'index.html', 'entrySha256': entry_sha, 'httpStatus': 200, 'readbackVerified': True,
        'executable': False, 'overwritten': False, 'boundary': BOUNDARY,
        'publishedPaths': shown, 'publishedPathsOmitted': len(paths) - len(shown),
        'publishedEmptyPaths': [path for path in empty if path in shown][:32],
        'publishedEmptyPathsOmitted': len([path for path in empty if path not in shown]),
    }


class PreviewHandler(http.server.BaseHTTPRequestHandler):
    server_version = 'ODS-Preview'
    sys_version = ''

    def log_message(self, format, *args):  # noqa: A002 - keep the stdlib signature
        pass

    def _refuse(self, status):
        self.send_response(status)
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def do_GET(self):  # noqa: N802 - stdlib name
        parsed = urllib.parse.urlsplit(self.path)
        parts = parsed.path.split('/')
        site_id = parts[1] if len(parts) > 1 else ''
        host = (self.headers.get('Host') or '').lower()
        # One origin per site: a page cannot read another site's files.
        if not SITE_ID.match(site_id) or host != f'{site_id}.localhost:{self.server.server_port}':
            self._refuse(404)
            return
        relative = [urllib.parse.unquote(part) for part in parts[2:] if part]
        if not relative or parsed.path.endswith('/'):
            relative.append('index.html')
        if not all(ASSET_COMPONENT.match(part) for part in relative):
            self._refuse(404)
            return
        path = os.path.join(self.server.snapshots, site_id, *relative)
        if not os.path.isfile(path):
            self._refuse(404)
            return
        with open(path, 'rb') as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(relative[-1])[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)


class PreviewServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, snapshots):
        super().__init__(('127.0.0.1', port), PreviewHandler)
        self.snapshots = snapshots


class ControlProtocol(asyncio.Protocol):
    def __init__(self, service):
        self.service = service
        self.buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > MAX_REQUEST:
            self._reply({'schemaVersion': 1, 'status': 'failed', 'code': 'request_too_large'})
        elif b'\n' in self.buffer:
            line = bytes(self.buffer.split(b'\n', 1)[0])
            asyncio.get_running_loop().run_in_executor(None, self._handle, line)

    def eof_received(self):
        # The plugin half-closes after its one line.
        if b'\n' not in self.buffer and self.buffer:
            asyncio.get_running_loop().run_in_executor(None, self._handle, bytes(self.buffer))
        return True

    def _handle(self, line):
        try:
            receipt = publish(json.loads(line.decode('utf-8')), *self.service)
        except PublishError as error:
            receipt = {'schemaVersion': 1, 'status': 'failed', 'code': str(error)}
        except (ValueError, UnicodeDecodeError):
            receipt = {'schemaVersion': 1, 'status': 'failed', 'code': 'invalid_request'}
        asyncio.run_coroutine_threadsafe(self._send(receipt), self.loop)

    async def _send(self, receipt):
        self._reply(receipt)

    def _reply(self, receipt):
        if not self.transport.is_closing():
            self.transport.write((json.dumps(receipt) + '\n').encode('utf-8'))
            self.transport.close()


async def serve(pipe, workspace, snapshots, port):
    loop = asyncio.get_running_loop()
    service = (workspace, snapshots, port)

    def factory():
        protocol = ControlProtocol(service)
        protocol.loop = loop
        return protocol
    # start_serving_pipe is the Proactor loop's named-pipe server.
    servers = await loop.start_serving_pipe(factory, pipe)
    print('preview ready', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        for server in servers:
            server.close()


def main(argv):
    pipe, workspace, root, port = argv[0], argv[1], argv[2], int(argv[3])
    snapshots = os.path.join(root, 'snapshots')
    os.makedirs(snapshots, exist_ok=True)
    httpd = PreviewServer(port, snapshots)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    asyncio.run(serve(pipe, workspace, snapshots, port))


if __name__ == '__main__':
    main(sys.argv[1:])
