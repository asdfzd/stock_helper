"""Thread-scoped Windows idle sleep inhibition; never edits a power plan."""
import ctypes
import os
import threading

from tools.one_hour_runner.system import RunBlocked


class WindowsSleepInhibitor:
    CONTINUOUS = 0x80000000
    SYSTEM_REQUIRED = 0x00000001

    def __init__(self, execute=None):
        self.execute = execute
        self.owner = None

    def _call(self, flags):
        if self.execute is None:
            if os.name != 'nt':
                raise RunBlocked('sleep_inhibition_windows_required')
            fn = ctypes.WinDLL('kernel32', use_last_error=True).SetThreadExecutionState
            fn.argtypes, fn.restype = [ctypes.c_uint32], ctypes.c_uint32
        else:
            fn = self.execute
        if not fn(flags):
            raise RunBlocked('sleep_inhibition_failed')

    def acquire(self):
        if self.owner is not None:
            raise RunBlocked('sleep_inhibition_already_acquired')
        self._call(self.CONTINUOUS | self.SYSTEM_REQUIRED)
        self.owner = threading.get_ident()

    def close(self):
        if self.owner is None:
            return
        if self.owner != threading.get_ident():
            raise RunBlocked('sleep_inhibition_wrong_thread')
        self._call(self.CONTINUOUS)
        self.owner = None
