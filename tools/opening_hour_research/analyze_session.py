"""Post-session read-only Opening Hour Research analyzer. No credentials/network."""
import argparse
import json
from pathlib import Path
import sys
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from market_radar.opening_hour.service import analyze_session
from market_radar.opening_hour.storage import DEFAULT_DB


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--date", dest="day")
    parser.add_argument("--cache-path", type=Path)
    parser.add_argument("--paper-db", type=Path, action="append", default=[])
    parser.add_argument("--output-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--export-root", type=Path)
    args = parser.parse_args()
    try:
        result = analyze_session(args.session_dir, day=args.day, cache_path=args.cache_path,
            paper_paths=args.paper_db, output_db=args.output_db, export_root=args.export_root)
    except (ValueError, OSError, sqlite3.DatabaseError) as exc:
        # Controlled analyzer diagnostics only; never print source response contents.
        parser.exit(2, f"Opening Hour analysis failed ({type(exc).__name__}). Check finalized inputs and output paths.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
