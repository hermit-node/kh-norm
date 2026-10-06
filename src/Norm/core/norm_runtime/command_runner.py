"""Bounded command execution: no interactive stdin or descendant-owned pipes."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import tempfile
import time
from ctypes import wintypes


class _WindowsJob:
    def __init__(self):
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.k = k
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                                       'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, process):
        if not self.k.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        # Popen closes the primary thread handle; resume the suspended process only
        # after assignment, so no child can escape before the job owns it.
        resume = ctypes.WinDLL('ntdll').NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = ctypes.c_long
        status = resume(wintypes.HANDLE(int(process._handle)))
        if status < 0:
            raise OSError(f'NtResumeProcess failed: 0x{status & 0xffffffff:08x}')

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


def run_bounded(argv: list[str], *, cwd: str, timeout: float,
                max_output_chars: int = 20000, stdin_text: str | None = None) -> dict:
    """Run one command tree. Descendants cannot outlive this tool invocation."""
    if stdin_text is not None and not isinstance(stdin_text, str):
        raise ValueError('stdin_text must be a string')
    if stdin_text is not None and len(stdin_text.encode('utf-8')) > 1048576:
        raise ValueError('stdin_text exceeds 1 MiB')
    started = time.monotonic()
    process = None
    job = None
    timed_out = False
    cleanup_error = None
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as input_file:
        if stdin_text is not None:
            input_file.write(stdin_text.encode('utf-8'))
            input_file.seek(0)
        try:
            if os.name == 'nt':
                job = _WindowsJob()
            process = subprocess.Popen(
                argv, cwd=cwd, stdin=input_file if stdin_text is not None else subprocess.DEVNULL,
                stdout=output, stderr=errors, close_fds=True,
                creationflags=(0x00000004 | subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt',
            )
            if job:
                job.attach_and_resume(process)
            try:
                process.wait(timeout=max(0.01, timeout))
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            # Never call communicate(): a descendant might retain its pipe handles.
            # Closing the job kills the entire Windows tree, even if the shell exited.
            if job:
                job.close()
            elif process is not None and os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process is not None:
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    cleanup_error = 'process did not exit within bounded cleanup deadline'
        def tail(handle):
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_output_chars * 4))
            return handle.read().decode('utf-8', errors='replace')[-max_output_chars:]
        result = {'exit_code': process.returncode, 'timed_out': timed_out,
                  'timeout_seconds': timeout, 'duration_seconds': round(time.monotonic()-started, 3),
                  'stdout': tail(output), 'stderr': tail(errors), 'pid': process.pid,
                  'stdin_mode': 'provided' if stdin_text is not None else 'closed',
                  'process_tree_contained': True}
        if cleanup_error:
            result['cleanup_error'] = cleanup_error
        return result
