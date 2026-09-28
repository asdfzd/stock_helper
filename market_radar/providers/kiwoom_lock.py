"""One local collector process for the single-session Kiwoom credentials."""
import os
from runtime_paths import APP_ROOT
from ..provider import ProviderError


class CollectorLock:
    def __init__(self):
        self.stream = None

    def acquire(self):
        path = APP_ROOT / "data" / "kiwoom_collector.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
        try:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise ProviderError("Another local Kiwoom collector is running") from None
        self.stream = stream

    def close(self):
        if self.stream:
            self.stream.close()  # OS releases the lock, including after crashes
            self.stream = None
