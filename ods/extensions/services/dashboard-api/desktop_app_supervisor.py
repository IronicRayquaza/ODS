"""Run one native ODS Desktop app (windows-desktop profile) for its sign-in task.

    pythonw.exe desktop_app_supervisor.py <app folder>

<app folder>\\launch.json is the recipe setup resolved (desktop_app_recipes.py)
plus the absolute executable path. This process joins a kill-on-close Job
Object, starts the app without a console, records ready.json once the app's
health path answers on 127.0.0.1, and exits when the app exits; the job ends
anything the app started, and the task's restart policy brings it back.
"""
import ctypes as C
import http.client
import json
import os
import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000
KILL_ON_JOB_CLOSE = 0x2000
EXTENDED_LIMIT_INFORMATION = 9
STARTUP_SECONDS = 120


class _Limits(C.Structure):
    _fields_ = [('process_time', C.c_longlong), ('job_time', C.c_longlong), ('flags', C.c_uint32),
                ('min_ws', C.c_size_t), ('max_ws', C.c_size_t), ('processes', C.c_uint32), ('affinity', C.c_size_t),
                ('priority', C.c_uint32), ('scheduling', C.c_uint32), ('io', C.c_ulonglong * 6),
                ('process_memory', C.c_size_t), ('job_memory', C.c_size_t), ('peak_process', C.c_size_t),
                ('peak_job', C.c_size_t)]


def join_kill_on_close_job():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = C.c_void_p
    kernel.GetCurrentProcess.restype = C.c_void_p
    kernel.SetInformationJobObject.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32]
    kernel.AssignProcessToJobObject.argtypes = [C.c_void_p, C.c_void_p]
    job = kernel.CreateJobObjectW(None, None)
    limits = _Limits(flags=KILL_ON_JOB_CLOSE)
    if not job or not kernel.SetInformationJobObject(job, EXTENDED_LIMIT_INFORMATION, C.byref(limits), C.sizeof(limits)) \
            or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise C.WinError(C.get_last_error())
    return job  # Held for this process's lifetime; closing it ends the app.


def write_json(path, value):
    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump(value, handle)
    os.replace(path + '.tmp', path)


def healthy(port, path):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET', path)
        return connection.getresponse().status < 500
    finally:
        connection.close()


def main(folder):
    with open(os.path.join(folder, 'launch.json'), encoding='utf-8') as handle:
        launch = json.load(handle)
    ready = os.path.join(folder, 'ready.json')
    if os.path.exists(ready):
        os.remove(ready)
    _job = join_kill_on_close_job()
    logs = os.path.join(folder, 'logs')
    os.makedirs(logs, exist_ok=True)
    os.makedirs(launch['data'], exist_ok=True)
    environment = {key: os.environ[key] for key in ('SystemRoot', 'windir', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'PATH')
                   if key in os.environ}
    environment.update(launch['env'])
    with open(os.path.join(logs, 'app.log'), 'wb') as output:
        app = subprocess.Popen([launch['executablePath'], *launch['args']], env=environment, cwd=os.path.dirname(launch['executablePath']),
                               stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
    deadline = time.monotonic() + STARTUP_SECONDS
    while True:
        if app.poll() is not None:
            write_json(ready, {'Error': f"{launch['name']} stopped during startup (exit code {app.returncode}); see logs\\app.log."})
            return 1
        try:
            if healthy(launch['port'], launch['health']):
                break
        except OSError:
            pass  # Not listening yet.
        if time.monotonic() > deadline:
            write_json(ready, {'Error': f"{launch['name']} did not answer within {STARTUP_SECONDS} seconds; see logs\\app.log."})
            return 1
        time.sleep(0.5)
    write_json(ready, {'ProcessId': app.pid, 'Port': launch['port']})
    return app.wait()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
