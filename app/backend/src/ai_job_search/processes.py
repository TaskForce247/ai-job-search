from __future__ import annotations

import ctypes
import os
import signal
import subprocess
from pathlib import Path

from .errors import AppError
from .runtime import minimal_environment


class ProcessJob:
    """Own one process tree; never select unrelated processes by name."""

    def __init__(self):
        self.handle = None
        self.pid: int | None = None
        if os.name == "nt":
            from ctypes import wintypes

            class BasicLimits(ctypes.Structure):
                _fields_ = [
                    ("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                    ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IOCounters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in (
                    "ReadOperations", "WriteOperations", "OtherOperations", "ReadBytes", "WriteBytes", "OtherBytes",
                )]

            class ExtendedLimits(ctypes.Structure):
                _fields_ = [("Basic", BasicLimits), ("IO", IOCounters),
                            ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t),
                            ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel = kernel
            self.handle = kernel.CreateJobObjectW(None, None)
            if not self.handle:
                raise OSError(ctypes.get_last_error(), "Cannot create a managed process tree")
            limits = ExtendedLimits()
            limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                error = ctypes.get_last_error()
                self.close()
                raise OSError(error, "Cannot configure a managed process tree")

    def assign(self, pid: int) -> None:
        self.pid = pid
        if os.name == "nt":
            process = self.kernel.OpenProcess(0x0100 | 0x0001, False, pid)
            if not process:
                raise OSError(ctypes.get_last_error(), "Cannot access the started process")
            try:
                if not self.kernel.AssignProcessToJobObject(self.handle, process):
                    raise OSError(ctypes.get_last_error(), "Cannot manage the started process tree")
            finally:
                self.kernel.CloseHandle(process)

    def terminate(self) -> None:
        if os.name == "nt":
            if self.handle and not self.kernel.TerminateJobObject(self.handle, 1):
                raise OSError(ctypes.get_last_error(), "Cannot stop the managed process tree")
        elif self.pid:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        if os.name == "nt" and self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def command(arguments: list[str], directory: Path, *, timeout: int = 120,
            environment: dict[str, str] | None = None) -> str:
    env = minimal_environment()
    env.update(environment or {})
    job = ProcessJob()
    try:
        process = subprocess.Popen(
            arguments, cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    except BaseException:
        job.close()
        raise
    try:
        try:
            job.assign(process.pid)
        except BaseException:
            process.terminate()
            process.wait(timeout=10)
            raise
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            job.terminate()
            partial, _ = process.communicate()
            tool = Path(arguments[0]).name
            raise AppError(
                "process_timeout",
                f"{tool} did not finish within {timeout} seconds and was stopped. Saved work is available; repair the dependency and retry.",
                504, detail={"tool": tool, "timeout_seconds": timeout,
                             "output": (partial or b"")[-8000:].decode("utf-8", errors="replace")},
            ) from exc
        text = output[-2_000_000:].decode("utf-8", errors="replace")
        if process.returncode:
            raise AppError("tool_failed", f"{Path(arguments[0]).name} failed. Review the saved diagnostic and repair the tool.", 422,
                           detail={"output": text[-8000:], "exit_code": process.returncode})
        return text
    finally:
        if process.poll() is None:
            job.terminate()
            process.wait(timeout=10)
        job.close()
