"""Download public Kiwoom documentation only. Never loads credentials or executes it."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import requests

OUTPUT = Path(__file__).resolve().parent / "output" / "official"
REPO = "Kiwoom-Securities/Kiwoom-REST-API"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--examples", nargs="*", default=[])
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    tree_file = OUTPUT / "tree.json"
    if tree_file.exists():
        tree = json.loads(tree_file.read_text(encoding="utf-8"))
    else:
        response = session.get(f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1", timeout=30)
        response.raise_for_status()
        tree = response.json()
        tree_file.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    paths = {item["path"] for item in tree["tree"] if item["type"] == "blob"}
    print("Official revision:", tree["sha"])
    if not args.paths and not args.examples:
        for path in sorted(paths):
            if path.startswith("kiwoom/") or ("overseas" in path and path.endswith(".py")):
                print(path)
        return
    manifest_path = OUTPUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    requested = list(args.paths)
    for name in args.examples:
        matches = [p for p in paths if p.startswith("examples/") and Path(p).name == name]
        if len(matches) != 1:
            raise ValueError("Example basename must identify exactly one official file")
        requested.extend(matches)
    for path in requested:
        if path not in paths:
            raise ValueError("Not a published file in the pinned official repository")
        url = f"https://raw.githubusercontent.com/{REPO}/{tree['sha']}/{path}"
        response = session.get(url, timeout=30)
        response.raise_for_status()
        target = OUTPUT / (path + ".txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        manifest[path] = {"url": url, "sha256": hashlib.sha256(response.content).hexdigest()}
        print("Downloaded:", path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
