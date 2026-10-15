from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Any


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _configure_kernel32(kernel32: Any) -> None:
    """Configure Win32 signatures when backed by a real ctypes DLL."""

    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, wintypes.INT, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
    }
    for name, (argtypes, restype) in signatures.items():
        function = getattr(kernel32, name)
        try:
            function.argtypes = argtypes
            function.restype = restype
        except (AttributeError, TypeError):
            # Test doubles use ordinary Python callables and do not expose
            # ctypes function metadata.
            continue


def _win32_error(api_name: str) -> OSError:
    code = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
    return OSError(code, f"{api_name} failed with Windows error {code}")


class WindowsJobObject:
    """Own one Windows process tree with KILL_ON_JOB_CLOSE semantics.

    The handle is intentionally process-local and non-persisted. If the owning
    Render host exits or crashes, Windows closes the handle and terminates every
    process that remains in the job. SQLite lease state remains the durable
    scheduling authority; the Job Object is only local process-lifetime truth.
    """

    def __init__(self, *, kernel32: Any | None = None) -> None:
        if kernel32 is None:
            if os.name != "nt":
                raise OSError("Windows Job Objects are only available on Windows.")
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _configure_kernel32(kernel32)
        self._kernel32 = kernel32
        self._handle: Any | None = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise _win32_error("CreateJobObjectW")

        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            self._handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            handle = self._handle
            self._handle = None
            self._kernel32.CloseHandle(handle)
            raise _win32_error("SetInformationJobObject")

    @property
    def handle(self) -> Any | None:
        return self._handle

    def assign_current_process(self) -> None:
        if self._handle is None:
            raise OSError("Windows Job Object is already closed.")
        process_handle = self._kernel32.GetCurrentProcess()
        if not process_handle:
            raise _win32_error("GetCurrentProcess")
        self.assign_process_handle(int(process_handle))

    def assign_process(self, process: Any) -> None:
        raw_handle = getattr(process, "_handle", None)
        if raw_handle is None:
            raise OSError("Windows subprocess handle is unavailable.")
        self.assign_process_handle(int(raw_handle))

    def assign_process_handle(self, process_handle: int) -> None:
        if self._handle is None:
            raise OSError("Windows Job Object is already closed.")
        if not self._kernel32.AssignProcessToJobObject(
            self._handle,
            wintypes.HANDLE(process_handle),
        ):
            raise _win32_error("AssignProcessToJobObject")

    def terminate(self, *, exit_code: int = 1) -> None:
        if self._handle is None:
            return
        if not self._kernel32.TerminateJobObject(self._handle, int(exit_code)):
            raise _win32_error("TerminateJobObject")

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            # KILL_ON_JOB_CLOSE is the crash-safety authority. CloseHandle is
            # intentionally best-effort here because process/result errors must
            # not be masked by cleanup diagnostics.
            self._kernel32.CloseHandle(handle)
