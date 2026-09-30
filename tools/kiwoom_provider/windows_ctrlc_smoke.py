"""Send a real Windows console CTRL_C_EVENT to an isolated production smoke.

The collector uses its normal KeyboardInterrupt handler. This driver never calls
worker.stop(), kills a thread or terminates a process. The console stays hidden.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=600, choices=range(600, 1201))
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data/kiwoom_smoke_v2_1')
    parser.add_argument('--max-ws-symbols', type=int, default=20)
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('Windows console diagnostic only')
    if args.data_dir.exists():
        raise SystemExit('Use a fresh data directory')
    args.data_dir.mkdir(parents=True)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    with (args.data_dir/'console.log').open('wb') as output:
        child = subprocess.Popen([sys.executable, '-u', str(ROOT/'tools/kiwoom_provider/smoke_v2_1.py'),
            '--seconds', '1800', '--data-dir', str(args.data_dir), '--max-ws-symbols', str(args.max_ws_symbols)],
            cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
            stdout=output, stderr=subprocess.STDOUT)
        started = time.monotonic()
        while child.poll() is None and time.monotonic()-started < args.seconds:
            time.sleep(.2)
        delivered = False
        if child.poll() is None:
            # The sender joins only the child's isolated console. Its own Ctrl+C
            # handler is ignored; the collector's handler remains enabled.
            code = ('import ctypes,sys,time; k=ctypes.windll.kernel32; k.FreeConsole(); '
                    'ok=k.AttachConsole(int(sys.argv[1])); '
                    'k.SetConsoleCtrlHandler(None,True); '
                    'sent=bool(ok and k.GenerateConsoleCtrlEvent(0,0)); '
                    'time.sleep(.1); k.FreeConsole(); sys.exit(0 if sent else 1)')
            delivered = subprocess.run([sys.executable, '-c', code, str(child.pid)], timeout=10).returncode == 0
        signal_at = time.monotonic()
        while child.poll() is None and time.monotonic()-signal_at < 60:
            time.sleep(.1)
        result = dict(ctrl_c_event_delivered=delivered, child_exit_code=child.poll(),
                      signal_to_process_exit_seconds=time.monotonic()-signal_at,
                      child_pid=child.pid, forced_termination=False)
        (args.data_dir/'ctrlc_driver.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
        if child.poll() is None:
            print('Collector still running; inspect phase logs. No process was killed.', flush=True)
        return int(not delivered or child.poll() != 0)


if __name__ == '__main__':
    raise SystemExit(main())
