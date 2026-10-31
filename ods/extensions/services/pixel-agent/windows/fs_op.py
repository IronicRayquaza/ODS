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

request = json.loads(os.environ['ODS_FS_REQUEST'])
op, path = request['op'], request['path']
if op == 'read':
    with open(path, 'rb') as handle:
        sys.stdout.buffer.write(handle.read())
elif op == 'write':
    if request.get('mkdir', True):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    data = sys.stdin.buffer.read()
    with open(path, 'wb') as handle:
        handle.write(data)
elif op == 'mkdirp':
    os.makedirs(path, exist_ok=True)
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
        print('null')
    else:
        kind = 'directory' if os.path.isdir(path) else 'file' if os.path.isfile(path) else 'other'
        print(json.dumps({'type': kind, 'size': info.st_size, 'mtimeMs': info.st_mtime * 1000}))
else:
    raise ValueError(f'unknown file operation {op!r}')
