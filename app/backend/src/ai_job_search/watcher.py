from __future__ import annotations

import ctypes
import logging
import os
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .errors import AppError

logger = logging.getLogger(__name__)


def _close(handle, event, overlap, buffer, native, errors, codes, cancel, owner) -> None:
    # The finalizer retains the buffer until pending native I/O is cancelled.
    try:
        if not cancel(int(handle), None):
            error = ctypes.get_last_error()
            if error != codes.ERROR_NOT_FOUND:
                logger.error("Workspace monitoring cancellation failed (Windows error %s).", error)
        try:
            native.GetOverlappedResult(handle, overlap, True)
        except errors.error as exc:
            if exc.winerror != codes.ERROR_OPERATION_ABORTED:
                logger.error("Workspace monitoring cleanup failed (Windows error %s).", exc.winerror)
    finally:
        handle.Close()
        event.Close()
        owner.shutdown(wait=True)


class ApplicationWatcher:
    """Drain native change notifications before reusing an application read model."""

    def __init__(self, root: Path):
        self.native = os.name == "nt"
        self._finalizer = None
        self._changed_paths: set[str] | None = set()
        if not self.native:
            return
        from ctypes import wintypes

        import ntsecuritycon
        import pywintypes
        import win32con
        import win32event
        import win32file
        import winerror

        self._file, self._errors, self._codes, self._event_api = win32file, pywintypes, winerror, win32event
        cancel = ctypes.WinDLL("kernel32", use_last_error=True).CancelIoEx
        cancel.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        cancel.restype = wintypes.BOOL
        self._filter = (win32con.FILE_NOTIFY_CHANGE_FILE_NAME | win32con.FILE_NOTIFY_CHANGE_DIR_NAME
                        | win32con.FILE_NOTIFY_CHANGE_SIZE | win32con.FILE_NOTIFY_CHANGE_LAST_WRITE)
        self._buffer = win32file.AllocateReadBuffer(64 * 1024)
        self._overlap = pywintypes.OVERLAPPED()
        self._event = win32event.CreateEvent(None, True, False, None)
        self._overlap.hEvent = self._event
        try:
            self._handle = win32file.CreateFile(
                str(root), ntsecuritycon.FILE_LIST_DIRECTORY,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                None, win32con.OPEN_EXISTING,
                win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED, None,
            )
        except pywintypes.error as exc:
            self._event.Close()
            raise AppError("workspace_monitor_unavailable", "The local workspace cannot be monitored. Choose a writable local folder and retry.", 409) from exc
        # Windows cancels pending I/O when its issuing thread exits.
        self._owner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workspace-monitor")
        try:
            self._arm()
        except BaseException:
            self._handle.Close()
            self._event.Close()
            self._owner.shutdown(wait=True)
            raise
        self._finalizer = weakref.finalize(
            self, _close, self._handle, self._event, self._overlap, self._buffer, win32file, pywintypes, winerror,
            cancel, self._owner,
        )

    def _arm(self) -> None:
        self._event_api.ResetEvent(self._event)
        try:
            self._owner.submit(
                self._file.ReadDirectoryChangesW, self._handle, self._buffer, True, self._filter, self._overlap,
            ).result()
        except self._errors.error as exc:
            raise AppError("workspace_monitor_unavailable", "Workspace change monitoring stopped. Reopen the application before continuing.", 409) from exc

    @staticmethod
    def relevant(name: str, action: int) -> bool:
        name = name.replace("/", "\\").lower()
        if name in ("documents", ".app"):
            return action != 3
        return name in ("job_search_tracker.csv", "documents\\applications", ".app\\preparations") or any(
            name.startswith(prefix) for prefix in ("documents\\applications\\", ".app\\preparations\\")
        )

    def changed(self) -> bool:
        if not self.native:
            return True
        self._changed_paths = set()
        dirty = False
        for _ in range(32):
            try:
                length = self._file.GetOverlappedResult(self._handle, self._overlap, False)
            except self._errors.error as exc:
                if exc.winerror == self._codes.ERROR_IO_INCOMPLETE:
                    return dirty
                if exc.winerror != self._codes.ERROR_NOTIFY_ENUM_DIR:
                    raise AppError("workspace_monitor_unavailable", "Workspace change monitoring failed. Reopen the application before continuing.", 409) from exc
                length = 0
            notifications = self._file.FILE_NOTIFY_INFORMATION(self._buffer, length) if length else []
            self._arm()
            relevant = {name.replace("/", "\\").lower() for action, name in notifications if self.relevant(name, action)}
            if not length:
                self._changed_paths = None
            elif self._changed_paths is not None:
                self._changed_paths.update(relevant)
            dirty |= not length or bool(relevant)
        self._changed_paths = None
        return True

    def affects(self, relative: str) -> bool:
        path = relative.replace("/", "\\").lower()
        return self._changed_paths is None or any(
            path == changed or path.startswith(changed + "\\") for changed in self._changed_paths
        )

    def close(self) -> None:
        if self._finalizer is not None:
            self._finalizer()
