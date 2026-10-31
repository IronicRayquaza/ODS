"""Start and supervise the native ODS Desktop Portal on Windows.

The sign-in task ODSPortal-<SID> runs this with the pinned pythonw.exe:

    pythonw.exe portal_supervisor.py <portal root>

It joins a kill-on-close Job Object, then starts, in order and each proven
before the next: the preview service (a per-user named pipe for publishing,
127.0.0.1 for serving), the OpenClaw gateway (127.0.0.1), the ingress (a
per-user named pipe) and the Pixel edge (127.0.0.1, bearer key). The outcome goes to
portal-ready.json for setup to wait on. When any of them exits, this
process exits, the job ends the others, and the task restarts the set.
Nothing listens on anything but loopback or a named pipe.
"""
import http.client
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import win_job  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
STARTUP_SECONDS = 120


def read_env_file(path):
    values = {}
    with open(path, encoding='utf-8') as handle:
        for line in handle.read().splitlines():
            if line and not line.startswith('#'):
                key, _, value = line.partition('=')
                values[key] = value
    return values


def write_json(path, value):
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(value, handle)
    os.replace(temporary, path)


def layout(root):
    runtime = os.path.join(root, 'runtime')
    return {
        'node': os.path.join(root, 'node', 'node.exe'),
        'openclaw': os.path.join(root, 'openclaw', 'node_modules', 'openclaw', 'openclaw.mjs'),
        'ingress': os.path.join(runtime, 'pixel-agent', 'host', 'windows_ingress.mjs'),
        'edge': os.path.join(runtime, 'pixel-edge'),
        'preview': os.path.join(runtime, 'pixel-agent', 'windows', 'preview_service.py'),
        'previews': os.path.join(root, 'previews'),
        'workspace': os.path.join(root, 'workspace'),
        'state': os.path.join(root, 'state'),
        'config': os.path.join(root, 'state', 'openclaw.json'),
        'home': os.path.join(root, 'home'),
        'chat_state': os.path.join(root, 'chat-state'),
        'logs': os.path.join(root, 'logs'),
        'env': os.path.join(root, 'portal.env'),
        'intent': os.path.join(root, 'portal-intent.json'),
        'ready': os.path.join(root, 'portal-ready.json'),
    }


def base_environment(paths):
    system_root = os.environ['SystemRoot']
    # The gateway resolves ~ (and the Pixel execution-control folder under it)
    # through USERPROFILE, so it stays inside the Portal home.
    return {
        'SystemRoot': system_root, 'windir': system_root,
        'PATH': os.pathsep.join([os.path.dirname(paths['node']), os.path.join(system_root, 'System32')]),
        'PATHEXT': '.COM;.EXE;.BAT;.CMD', 'TEMP': os.environ['TEMP'], 'TMP': os.environ['TEMP'],
        'LOCALAPPDATA': os.environ['LOCALAPPDATA'], 'APPDATA': os.environ['APPDATA'],
        'USERPROFILE': paths['home'], 'HOME': paths['home'], 'NO_COLOR': '1', 'FORCE_COLOR': '0',
    }


def start(name, argv, environment, cwd, paths):
    log = os.path.join(paths['logs'], name + '.log')
    if os.path.exists(log):
        os.replace(log, log + '.1')
    with open(log, 'wb') as output:
        return subprocess.Popen(argv, env=environment, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)


def http_status(port, path, timeout=2.0):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request('GET', path)
        return connection.getresponse().status
    finally:
        connection.close()


def wait_until(name, process, ready, deadline):
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'The Portal {name} stopped during startup (exit code {process.returncode}); see logs\\{name}.log.')
        try:
            if ready():
                return
        except OSError:
            pass  # Not listening yet.
        time.sleep(0.5)
    raise RuntimeError(f'The Portal {name} did not start within {STARTUP_SECONDS} seconds; see logs\\{name}.log.')


def start_portal(paths, settings):
    gateway_port, edge_port = int(settings['GATEWAY_PORT']), int(settings['EDGE_PORT'])
    pipe, preview_pipe, preview_port = settings['INGRESS_PIPE'], settings['PREVIEW_PIPE'], settings['PREVIEW_PORT']
    deadline = time.monotonic() + STARTUP_SECONDS
    preview = start('preview', [sys.executable, '-I', paths['preview'], preview_pipe, paths['workspace'], paths['previews'],
                                preview_port], base_environment(paths), paths['previews'], paths)
    wait_until('preview', preview, lambda: win_job.pipe_ready(preview_pipe, 500), deadline)
    gateway_env = {**base_environment(paths), 'OPENCLAW_STATE_DIR': paths['state'],
                   'OPENCLAW_CONFIG_PATH': paths['config'], 'OPENCLAW_HOME': paths['home'],
                   'ODS_PIXEL_PREVIEW_PIPE': preview_pipe}
    gateway = start('gateway', [paths['node'], paths['openclaw'], 'gateway', 'run'], gateway_env, paths['home'], paths)
    wait_until('gateway', gateway, lambda: http_status(gateway_port, '/health') == 200, deadline)
    ingress_env = {**base_environment(paths), 'PIXEL_INGRESS_PIPE': pipe, 'PIXEL_GATEWAY_PORT': str(gateway_port),
                   'PIXEL_GATEWAY_CONFIG': paths['config'], 'PIXEL_CHAT_STATE_DIR': paths['chat_state']}
    ingress = start('ingress', [paths['node'], paths['ingress']], ingress_env, os.path.dirname(paths['ingress']), paths)
    wait_until('ingress', ingress, lambda: win_job.pipe_ready(pipe, 500), deadline)
    edge_env = {**base_environment(paths), 'PIXEL_OPENWEBUI_KEY': settings['PIXEL_OPENWEBUI_KEY'],
                'PIXEL_PREVIEW_PROXY_KEY': settings['PIXEL_PREVIEW_PROXY_KEY'], 'PIXEL_INGRESS_PIPE': pipe,
                'PIXEL_EDGE_HOST': '127.0.0.1', 'PIXEL_EDGE_PORT_INTERNAL': str(edge_port)}
    # The edge is the upstream pixel_edge.py, run in place by the same pinned Python.
    code = ('import runpy, sys; sys.path.insert(0, sys.argv[1]); '
            "runpy.run_module('pixel_edge', run_name='__main__')")
    edge = start('edge', [sys.executable, '-c', code, paths['edge']], edge_env, paths['edge'], paths)
    # Any answer proves the listener; /v1/models without the key is a 401.
    wait_until('edge', edge, lambda: http_status(edge_port, '/v1/models') == 401, deadline)
    return [preview, gateway, ingress, edge]


def main(root):
    paths = layout(root)
    os.makedirs(paths['logs'], exist_ok=True)
    os.makedirs(paths['previews'], exist_ok=True)
    with open(paths['intent'], encoding='utf-8') as handle:
        if json.load(handle).get('State') != 'running':
            return 0
    job = win_job.new_job()
    win_job.assign_current(job)
    if os.path.exists(paths['ready']):
        os.remove(paths['ready'])
    try:
        children = start_portal(paths, read_env_file(paths['env']))
    except (RuntimeError, OSError) as error:
        write_json(paths['ready'], {'Error': str(error)})
        return 1
    write_json(paths['ready'], {'ProcessId': os.getpid(), 'Children': [child.pid for child in children],
                                'StartedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    while all(child.poll() is None for child in children):
        time.sleep(1)
    # Exiting closes the job handle; kill-on-close ends the others.
    return next(child.returncode for child in children if child.returncode is not None) or 1


if __name__ == '__main__':
    try:
        code = main(sys.argv[1])
    except BaseException as error:
        # Setup waits on this record; the error still propagates to the task.
        write_json(layout(sys.argv[1])['ready'], {'Error': f'The Portal supervisor failed: {error!r}'})
        raise
    sys.exit(code)
