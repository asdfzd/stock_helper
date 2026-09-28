"""Run all project tests in isolated subprocesses; never starts a live provider."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data" / "kiwoom_provider_validation"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    paths = sorted((ROOT / "tests").glob("*_test.py")) + [ROOT / "tools/kiwoom_probe/probe_test.py"]
    results = []
    for path in paths:
        result = subprocess.run([sys.executable, str(path)], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        text = result.stdout.decode("utf-8", errors="replace")
        (OUTPUT / (path.stem + ".log")).write_text(text, encoding="utf-8")
        matched = re.search(r"Ran (\d+) tests?", text)
        item = {"file": str(path.relative_to(ROOT)), "exit_code": result.returncode,
                "units": int(matched[1]) if matched else 1, "count_basis": "methods" if matched else "script"}
        results.append(item)
        print(json.dumps(item), flush=True)
    code = "from pathlib import Path; import live_ui; live_ui.APP_ROOT=Path('data/kiwoom_provider_validation').resolve(); raise SystemExit(live_ui.run_ocr_self_test())"
    ocr = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    (OUTPUT / "ocr_initialization_process.log").write_bytes(ocr.stdout)
    item = {"file": "live_ui.run_ocr_self_test", "exit_code": ocr.returncode, "units": 1, "count_basis": "initialization"}
    results.append(item)
    print(json.dumps(item), flush=True)
    summary = {"results": results, "passed_units": sum(r["units"] for r in results if r["exit_code"] == 0),
               "failed_suites": sum(r["exit_code"] != 0 for r in results)}
    (OUTPUT / "tests_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return bool(summary["failed_suites"])


if __name__ == "__main__":
    raise SystemExit(main())
