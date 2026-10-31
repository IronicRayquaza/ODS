"""One Portal file operation, run INSIDE the AppContainer by sandbox_run.py fsop.

The request is JSON in ODS_FS_REQUEST: {"op": read|write|mkdirp|remove|rename|stat,
"path": ..., "to"?, "recursive"?, "force"?, "mkdir"?}. Write data arrives on
stdin; read data and stat JSON leave on stdout. Paths are whatever the agent
asked for: the container's ACL, not this script, decides what is reachable.
"""
import json
import os
import shutil
import sys


def make_dirs(target):
    """Create target and any missing parents, walking up only to a folder that exists.

    os.makedirs checks every parent before creating anything, and the container
    cannot see the folders above the workspace, so for the workspace itself it
    tried to create C:\\ and failed (live 2026-11-01). Outside the workspace this
    still ends at a folder the container may not create, and the error stands.
    """
    missing = []
    folder = os.path.abspath(target)
    while not os.path.isdir(folder):
        missing.append(folder)
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    for folder in reversed(missing):
        os.mkdir(folder)


def main(request, stdin, stdout):
    op, path = request['op'], request['path']
    if op == 'read':
        with open(path, 'rb') as handle:
            stdout.write(handle.read())
    elif op == 'write':
        if request.get('mkdir', True):
            make_dirs(os.path.dirname(os.path.abspath(path)))
        data = stdin.read()
        with open(path, 'wb') as handle:
            handle.write(data)
    elif op == 'mkdirp':
        make_dirs(path)
    elif op == 'remove':
        if os.path.isdir(path) and not os.path.islink(path):
            if request.get('recursive'):
                shutil.rmtree(path)
            else:
                os.rmdir(path)
        elif os.path.lexists(path):
            os.remove(path)
        elif not request.get('force'):
            raise FileNotFoundError(path)
    elif op == 'rename':
        os.replace(path, request['to'])
    elif op == 'stat':
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            stdout.write(b'null\n')
        else:
            kind = 'directory' if os.path.isdir(path) else 'file' if os.path.isfile(path) else 'other'
            stdout.write(json.dumps({'type': kind, 'size': info.st_size, 'mtimeMs': info.st_mtime * 1000}).encode() + b'\n')
    else:
        raise ValueError(f'unknown file operation {op!r}')


if __name__ == '__main__':
    main(json.loads(os.environ['ODS_FS_REQUEST']), sys.stdin.buffer, sys.stdout.buffer)
