"""ods-sandbox-run: run one Portal command in a Windows AppContainer (windows-desktop profile).

    sandbox_run.py setup --profile NAME --workspace DIR [--read DIR ...]
    sandbox_run.py teardown --profile NAME --workspace DIR
    sandbox_run.py run --profile NAME --workspace DIR --path PATH [--cwd DIR] [--cancel-marker FILE] -- COMMAND
    sandbox_run.py fsop --profile NAME --workspace DIR --python EXE (request in ODS_FS_REQUEST, data on stdin)

setup (once, at install) creates the AppContainer profile and grants its SID
modify on the workspace and read/execute on each --read folder (toolchain and
helpers). run then starts cmd.exe /d /s /c COMMAND inside that AppContainer,
with no capabilities, so it has:
  - no network at all, loopback included (llama-server, the gateway and the
    dashboard are unreachable);
  - file access only where an ACL names the container SID or ALL APPLICATION
    PACKAGES (Windows system files, Program Files): not the user profile;
  - a kill-on-close Job Object with memory and process limits. Killing this
    runner, or creating the cancel marker, ends the whole command tree.
The regular AppContainer is used, not LPAC: LPAC loses the system-file access
cmd.exe and node.exe need, and restoring it requires administrator rights.
Known limits (measured on Windows 11): MSYS2/Git Bash cannot start in an
AppContainer, and Node cannot spawn children with piped stdio there (libuv
pipe names are refused), so `node --test` needs --experimental-test-isolation=none.
"""
import argparse
import ctypes as C
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import win_job  # noqa: E402

U32, PTR, SIZE, BOOL, LONG = C.c_uint32, C.c_void_p, C.c_size_t, C.c_int, C.c_long
WCHAR = C.c_wchar_p
EXTENDED_STARTUPINFO_PRESENT, CREATE_SUSPENDED = 0x80000, 0x4
CREATE_UNICODE_ENVIRONMENT, CREATE_NO_WINDOW = 0x400, 0x08000000
ATTRIBUTE_HANDLE_LIST, ATTRIBUTE_SECURITY_CAPABILITIES = 0x20002, 0x20009
STARTF_USESTDHANDLES, DUPLICATE_SAME_ACCESS = 0x100, 0x2
ALREADY_EXISTS = -2147024713  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)
JOB_MEMORY_BYTES = 4 * 1024 ** 3
JOB_MAX_PROCESSES = 64
CANCELLED_EXIT = 130


class _Capabilities(C.Structure):
    _fields_ = [('sid', PTR), ('capabilities', PTR), ('count', U32), ('reserved', U32)]


class _StartupInfo(C.Structure):
    _fields_ = [('cb', U32), ('reserved', WCHAR), ('desktop', WCHAR), ('title', WCHAR), ('x', U32), ('y', U32),
                ('width', U32), ('height', U32), ('columns', U32), ('rows', U32), ('fill', U32), ('flags', U32),
                ('show', C.c_uint16), ('reserved2_size', C.c_uint16), ('reserved2', PTR),
                ('stdin', PTR), ('stdout', PTR), ('stderr', PTR)]


class _StartupInfoEx(C.Structure):
    _fields_ = [('info', _StartupInfo), ('attributes', PTR)]


class _ProcessInfo(C.Structure):
    _fields_ = [('process', PTR), ('thread', PTR), ('process_id', U32), ('thread_id', U32)]


class _Api:
    def __init__(self):
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.u = C.WinDLL('userenv', use_last_error=True)
        self.a = C.WinDLL('advapi32', use_last_error=True)

        def bind(dll, name, result, *args):
            call = getattr(dll, name)
            call.argtypes, call.restype = args, result
            setattr(self, name, call)
        bind(self.u, 'CreateAppContainerProfile', LONG, WCHAR, WCHAR, WCHAR, PTR, U32, C.POINTER(PTR))
        bind(self.u, 'DeriveAppContainerSidFromAppContainerName', LONG, WCHAR, C.POINTER(PTR))
        bind(self.u, 'DeleteAppContainerProfile', LONG, WCHAR)
        bind(self.a, 'ConvertSidToStringSidW', BOOL, PTR, C.POINTER(WCHAR))
        bind(self.k, 'InitializeProcThreadAttributeList', BOOL, PTR, U32, U32, C.POINTER(SIZE))
        bind(self.k, 'UpdateProcThreadAttribute', BOOL, PTR, U32, SIZE, PTR, SIZE, PTR, PTR)
        bind(self.k, 'CreateProcessW', BOOL, WCHAR, PTR, PTR, PTR, BOOL, U32, PTR, WCHAR,
             C.POINTER(_StartupInfoEx), C.POINTER(_ProcessInfo))
        bind(self.k, 'ResumeThread', U32, PTR)
        bind(self.k, 'GetExitCodeProcess', BOOL, PTR, C.POINTER(U32))
        bind(self.k, 'GetStdHandle', PTR, U32)
        bind(self.k, 'DuplicateHandle', BOOL, PTR, PTR, PTR, C.POINTER(PTR), U32, BOOL, U32)
        bind(self.k, 'CloseHandle', BOOL, PTR)


API = None


def profile_name(user_sid):
    """One AppContainer per Windows account; the name is not a secret."""
    return 'ODS.Portal.Sandbox.' + hashlib.sha256(user_sid.encode('ascii')).hexdigest()[:24]


def build_command_line(command):
    # cmd.exe parses its own line: /s removes exactly the outer quotes, so the
    # command passes verbatim. subprocess.list2cmdline would add backslash
    # escapes that cmd.exe does not understand.
    if '\0' in command or '\r' in command or '\n' in command:
        raise ValueError('a sandboxed command is one line without NUL')
    return f'cmd.exe /d /s /c "{command}"'


def sandbox_environment(workspace, path):
    system_root = os.environ['SystemRoot']
    return {
        # Windows requires LOCALAPPDATA to create an AppContainer process and
        # moves LOCALAPPDATA, TEMP and TMP into the container's own folder.
        'SystemRoot': system_root, 'windir': system_root, 'LOCALAPPDATA': os.environ['LOCALAPPDATA'],
        'PATH': path, 'PATHEXT': '.COM;.EXE;.BAT;.CMD', 'ComSpec': os.path.join(system_root, 'System32', 'cmd.exe'),
        'USERPROFILE': workspace, 'HOME': workspace, 'NO_COLOR': '1',
        # Node resolves realpath from the drive root, which the container may not list.
        'NODE_OPTIONS': '--preserve-symlinks --preserve-symlinks-main',
    }


def environment_block(environment):
    return ''.join(f'{key}={value}\0' for key, value in sorted(environment.items(), key=lambda item: item[0].upper())) + '\0'


def container_sid(name):
    sid = PTR()
    result = API.CreateAppContainerProfile(name, name, 'ODS Portal sandbox', None, 0, C.byref(sid))
    if result == ALREADY_EXISTS:
        result = API.DeriveAppContainerSidFromAppContainerName(name, C.byref(sid))
    if result != 0:
        raise OSError(f'AppContainer profile {name} failed: 0x{result & 0xFFFFFFFF:08x}')
    text = WCHAR()
    win_job.check(API.ConvertSidToStringSidW(sid, C.byref(text)), 'ConvertSidToStringSidW')
    return sid, text.value


def grant(folder, sid_text, rights):
    # icacls ships with Windows; (OI)(CI) makes new files and folders inherit.
    subprocess.run([os.path.join(os.environ['SystemRoot'], 'System32', 'icacls.exe'), folder, '/grant',
                    f'*{sid_text}:(OI)(CI){rights}', '/Q'], check=True, stdout=subprocess.DEVNULL,
                   creationflags=CREATE_NO_WINDOW)


def setup(options):
    _, sid_text = container_sid(options.profile)
    os.makedirs(options.workspace, exist_ok=True)
    grant(options.workspace, sid_text, 'M')
    for folder in options.read:
        grant(folder, sid_text, 'RX')
    print(json.dumps({'profile': options.profile, 'sid': sid_text}))
    return 0


def teardown(options):
    # Uninstall: the profile and its private folder (the container's TEMP) go.
    result = API.DeleteAppContainerProfile(options.profile)
    if result != 0:
        raise OSError(f'Deleting AppContainer profile {options.profile} failed: 0x{result & 0xFFFFFFFF:08x}')
    return 0


def inheritable_std_handles():
    current = win_job.API.GetCurrentProcess()
    handles = []
    for which in (-10 & 0xFFFFFFFF, -11 & 0xFFFFFFFF, -12 & 0xFFFFFFFF):
        source = API.GetStdHandle(which)
        if not source or source == PTR(-1).value:
            source = C.c_void_p(__import__('msvcrt').get_osfhandle(os.open(os.devnull, os.O_RDWR))).value
        copy = PTR()
        win_job.check(API.DuplicateHandle(current, source, current, C.byref(copy), 0, True, DUPLICATE_SAME_ACCESS),
                      'DuplicateHandle')
        handles.append(copy.value)
    return handles


def start_contained(program, command_line, sid, environment, cwd, job):
    size = SIZE()
    API.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
    attributes = C.create_string_buffer(size.value)
    win_job.check(API.InitializeProcThreadAttributeList(attributes, 2, 0, C.byref(size)), 'InitializeProcThreadAttributeList')
    capabilities = _Capabilities(sid, None, 0, 0)
    win_job.check(API.UpdateProcThreadAttribute(attributes, 0, ATTRIBUTE_SECURITY_CAPABILITIES, C.byref(capabilities),
                                                C.sizeof(capabilities), None, None), 'security capabilities')
    std = inheritable_std_handles()
    handle_list = (PTR * 3)(*std)
    win_job.check(API.UpdateProcThreadAttribute(attributes, 0, ATTRIBUTE_HANDLE_LIST, handle_list,
                                                C.sizeof(handle_list), None, None), 'handle list')
    startup = _StartupInfoEx()
    startup.info.cb = C.sizeof(startup)
    startup.info.flags = STARTF_USESTDHANDLES
    startup.info.stdin, startup.info.stdout, startup.info.stderr = std
    startup.attributes = C.cast(attributes, PTR)
    process = _ProcessInfo()
    line = C.create_unicode_buffer(command_line)
    block = C.create_unicode_buffer(environment_block(environment))
    win_job.check(API.CreateProcessW(program, line, None, None, True,
                                     EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
                                     | CREATE_NO_WINDOW, block, cwd, C.byref(startup), C.byref(process)), 'CreateProcessW')
    for handle in std:
        API.CloseHandle(handle)
    # Suspended until it is in the job, so not one instruction runs outside it.
    win_job.assign(job, process.process)
    API.ResumeThread(process.thread)
    API.CloseHandle(process.thread)
    return process.process


def wait(process, job, cancel_marker):
    while win_job.API.WaitForSingleObject(process, 250) == win_job.WAIT_TIMEOUT:
        if cancel_marker and os.path.exists(cancel_marker):
            win_job.terminate(job, CANCELLED_EXIT)
            win_job.API.WaitForSingleObject(process, win_job.INFINITE)
            return CANCELLED_EXIT
    code = U32()
    win_job.check(API.GetExitCodeProcess(process, C.byref(code)), 'GetExitCodeProcess')
    return code.value


def run(options):
    environment = sandbox_environment(options.workspace, options.path)
    program = os.path.join(os.environ['SystemRoot'], 'System32', 'cmd.exe')
    command = ' '.join(options.command)
    cwd = options.cwd or options.workspace
    sid, _ = container_sid(options.profile)
    job = win_job.new_job(memory_bytes=JOB_MEMORY_BYTES, max_processes=JOB_MAX_PROCESSES)
    process = start_contained(program, build_command_line(command), sid, environment, cwd, job)
    return wait(process, job, options.cancel_marker)


def fsop(options):
    # The agent's file tools (read, write, edit) run inside the same container,
    # so its ACL bounds them too: a link planted in the workspace leads nowhere.
    request = os.environ.get('ODS_FS_REQUEST', '')
    if not request or len(request) > 32768:
        raise ValueError('ODS_FS_REQUEST is missing or too long')
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fs_op.py')
    environment = {**sandbox_environment(options.workspace, os.path.dirname(options.python)), 'ODS_FS_REQUEST': request}
    sid, _ = container_sid(options.profile)
    job = win_job.new_job(memory_bytes=JOB_MEMORY_BYTES, max_processes=1)
    process = start_contained(options.python, subprocess.list2cmdline([options.python, '-I', script]), sid,
                              environment, options.workspace, job)
    return wait(process, job, '')


def parse(argv):
    parser = argparse.ArgumentParser(prog='ods-sandbox-run')
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('setup', 'teardown', 'run', 'fsop'):
        sub = commands.add_parser(name)
        sub.add_argument('--profile', required=True)
        sub.add_argument('--workspace', required=True)
        if name == 'setup':
            sub.add_argument('--read', action='append', default=[])
        elif name == 'fsop':
            sub.add_argument('--python', required=True)
        else:
            sub.add_argument('--path', required=True)
            sub.add_argument('--cwd', default='')
            sub.add_argument('--cancel-marker', default='')
            sub.add_argument('command', nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    if options.action == 'run':
        if options.command[:1] == ['--']:
            options.command = options.command[1:]
        if not options.command:
            parser.error('run needs a command after --')
    for folder in [options.workspace] + ([options.cwd] if getattr(options, 'cwd', '') else []):
        if not os.path.isabs(folder):
            parser.error(f'{folder} must be an absolute path')
    return options


def main(argv):
    global API
    API = _Api()
    options = parse(argv)
    if options.action == 'setup':
        return setup(options)
    if options.action == 'fsop':
        return fsop(options)
    if options.action == 'teardown':
        return teardown(options)
    if shutil.which('cmd.exe', path=os.path.join(os.environ['SystemRoot'], 'System32')) is None:
        raise FileNotFoundError('cmd.exe was not found in System32')
    return run(options)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
