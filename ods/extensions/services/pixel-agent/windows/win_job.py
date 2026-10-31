"""Windows Job Objects for the ODS Desktop Portal (windows-desktop profile).

A kill-on-close job ends every process in it when its last handle closes, so a
supervisor or sandbox runner that dies for any reason takes its whole process
tree with it. Nothing here needs administrator rights.
"""
import ctypes as C

U32, PTR, SIZE, BOOL = C.c_uint32, C.c_void_p, C.c_size_t, C.c_int
LIMIT_ACTIVE_PROCESS = 0x8
LIMIT_JOB_MEMORY = 0x200
LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x400
LIMIT_KILL_ON_JOB_CLOSE = 0x2000
EXTENDED_LIMIT_INFORMATION = 9
WAIT_OBJECT_0, WAIT_TIMEOUT, INFINITE = 0, 0x102, 0xFFFFFFFF


class _IoCounters(C.Structure):
    _fields_ = [(name, C.c_ulonglong) for name in ('reads', 'writes', 'others', 'read_bytes', 'write_bytes', 'other_bytes')]


class _BasicLimits(C.Structure):
    _fields_ = [('process_time', C.c_longlong), ('job_time', C.c_longlong), ('flags', U32),
                ('min_working_set', SIZE), ('max_working_set', SIZE), ('active_processes', U32),
                ('affinity', SIZE), ('priority', U32), ('scheduling', U32)]


class _ExtendedLimits(C.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', _IoCounters), ('process_memory', SIZE),
                ('job_memory', SIZE), ('peak_process_memory', SIZE), ('peak_job_memory', SIZE)]


class _Api:
    def __init__(self):
        self.k = C.WinDLL('kernel32', use_last_error=True)

        def bind(name, result, *args):
            call = getattr(self.k, name)
            call.argtypes, call.restype = args, result
            setattr(self, name, call)
        bind('CreateJobObjectW', PTR, PTR, C.c_wchar_p)
        bind('SetInformationJobObject', BOOL, PTR, C.c_int, PTR, U32)
        bind('AssignProcessToJobObject', BOOL, PTR, PTR)
        bind('TerminateJobObject', BOOL, PTR, U32)
        bind('GetCurrentProcess', PTR)
        bind('CloseHandle', BOOL, PTR)
        bind('WaitForSingleObject', U32, PTR, U32)
        bind('WaitNamedPipeW', BOOL, C.c_wchar_p, U32)


API = _Api() if hasattr(C, 'WinDLL') else None


def check(ok, what):
    if not ok:
        raise C.WinError(C.get_last_error(), what)
    return ok


def new_job(*, memory_bytes=0, max_processes=0):
    """A kill-on-close job; optional job-wide memory and process-count limits."""
    job = check(API.CreateJobObjectW(None, None), 'CreateJobObjectW')
    limits = _ExtendedLimits()
    limits.basic.flags = LIMIT_KILL_ON_JOB_CLOSE | LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    if memory_bytes:
        limits.basic.flags |= LIMIT_JOB_MEMORY
        limits.job_memory = memory_bytes
    if max_processes:
        limits.basic.flags |= LIMIT_ACTIVE_PROCESS
        limits.basic.active_processes = max_processes
    check(API.SetInformationJobObject(job, EXTENDED_LIMIT_INFORMATION, C.byref(limits), C.sizeof(limits)),
          'SetInformationJobObject')
    return job


def assign(job, process_handle):
    check(API.AssignProcessToJobObject(job, process_handle), 'AssignProcessToJobObject')


def assign_current(job):
    """Put this process in the job; children it starts afterwards join it too."""
    assign(job, API.GetCurrentProcess())


def terminate(job, code=1):
    check(API.TerminateJobObject(job, code), 'TerminateJobObject')


def pipe_ready(name, timeout_ms):
    """True once a named pipe server is listening; does not take an instance."""
    return bool(API.WaitNamedPipeW(name, timeout_ms))
