"""OS boundaries. No process termination; shutdown is only called by the final gate."""
import ctypes
import logging
import os
from pathlib import Path
import subprocess


class RunBlocked(RuntimeError):
    """Only controlled reason codes, never provider exception text."""


class InstanceGuard:
    """Machine-wide runner mutex plus conservative detection of legacy launchers.

    Legacy builds do not participate in the mutex. Their public CollectorLock is
    checked separately; known legacy processes are rejected even when idle.
    """
    def __init__(self, processes=None):
        self.handle = None
        self.processes = processes

    def check_legacy(self):
        import psutil
        for process in (self.processes() if self.processes else psutil.process_iter()):
            if process.pid == os.getpid():
                continue
            try:
                name = process.name().lower()
                # Do not read unrelated process arguments (they can contain secrets).
                if name in {"stockhelper.exe", "stock_helper.exe"}:
                    raise RunBlocked("existing_stock_helper_process")
                if name.startswith(("python", "pyw")):
                    args = [str(a).lower().replace("\\", "/") for a in process.cmdline()]
                    known = any(a.endswith(("/live_ui.py", "/main.py", "/launch_stock_helper.py"))
                                or a in {"live_ui.py", "main.py", "market_radar", "market_radar.__main__"}
                                or a.startswith("tools.kiwoom_provider.") for a in args)
                    if known:
                        raise RunBlocked("existing_collector_process")
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                # If even its name is unavailable, do not assume it is safe.
                raise RunBlocked("process_inventory_unavailable") from None

    def acquire(self):
        if os.name != "nt":
            raise RunBlocked("windows_required")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateMutexW(None, False, "Global\\StockHelperOneHourRunner_v1")
        error = ctypes.get_last_error()
        if not handle:
            raise RunBlocked("runner_mutex_unavailable")
        if error == 183:
            kernel.CloseHandle(handle)
            raise RunBlocked("another_one_hour_runner")
        self.handle = handle
        try:
            self.check_legacy()
            from market_radar.providers.kiwoom_lock import CollectorLock
            lock = CollectorLock()
            try:
                lock.acquire()
            except Exception:
                raise RunBlocked("existing_collector_lock") from None
            finally:
                lock.close()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(self.handle)
            self.handle = None


class WindowsShutdown:
    def __init__(self, execute=None):
        self.execute = execute or subprocess.run
        self.requested = False
        self.attempted = False
        self.may_be_pending = False

    def _call(self, arguments):
        if os.name != "nt":
            raise RunBlocked("windows_required")
        # Absolute system path; never execute a shutdown.exe beside the download.
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows"))/"System32"/"shutdown.exe"
        result = self.execute([str(executable), *arguments], check=False, capture_output=True,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0:
            raise RunBlocked("windows_shutdown_request_failed")

    def request(self):
        if self.attempted:
            raise RunBlocked("shutdown_already_attempted")
        self.attempted = True
        self.may_be_pending = True
        try:
            self._call(["/s", "/f", "/t", "60"])
        except RunBlocked:
            # A completed nonzero response did not schedule our request. An
            # unexpected transport/adapter exception remains uncertain instead.
            self.may_be_pending = False
            raise
        self.requested = True

    def cancel(self):
        if not (self.requested or self.may_be_pending):
            return
        self._call(["/a"])
        self.requested = False
        self.may_be_pending = False


def install_safe_logging():
    """Existing worker loggers use this public logging hook, including Logger()."""
    from market_radar.research.review_export import SENSITIVE
    previous = logging.getLogRecordFactory()
    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        # Exception strings/tracebacks may contain untrusted transport response text.
        record.exc_info = record.exc_text = record.stack_info = None
        message = record.getMessage()
        if SENSITIVE.search(message):
            record.msg, record.args = "sensitive_log_message_suppressed", ()
        return record
    logging.setLogRecordFactory(factory)
    return previous
