"""Keep SIGINT handling on the main thread, including repeated Ctrl+C during export."""
import signal
import time
import os


def collect_until_stopped(worker, seconds=None):
    previous = signal.getsignal(signal.SIGINT)
    if os.name == 'nt':
        import ctypes
        # Windows launchers can inherit an ignored CTRL_C flag into child processes.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
    handling_interrupt = False
    def interrupt(*_):
        nonlocal handling_interrupt
        if handling_interrupt:
            return  # A nested Ctrl+C must not re-enter logging/counter locks.
        handling_interrupt = True
        try:
            first = not worker.stop_event.is_set()
            worker.stop(reason='KeyboardInterrupt')
        finally:
            handling_interrupt = False
        if first:
            raise KeyboardInterrupt
    signal.signal(signal.SIGINT, interrupt)
    started = time.monotonic()
    try:
        worker.start()
        try:
            while worker.running and (seconds is None or time.monotonic()-started < seconds):
                # Avoid interrupting Thread.join() on Windows; the worker owns DB cleanup.
                time.sleep(.1)
        except KeyboardInterrupt:
            pass
        finally:
            if not worker.stop_event.is_set():
                worker.stop(reason='timer' if seconds is not None else 'collection_complete')
            while worker.running:
                time.sleep(.1)
            worker.thread.join(timeout=0)
    finally:
        signal.signal(signal.SIGINT, previous)
