"""Deterministic synthetic event replay. Temporary DB; no provider or network.

Usage: python -m market_radar.paper.replay tests/fixtures/paper_baseline_v1.jsonl
This fixture format is not a claim that historical Radar snapshots contain every
live tick. A 30-second snapshot replay cannot validate first-tick executions.
"""
import argparse
import json
from pathlib import Path
import tempfile

from ..models import Session
from .engine import PaperEngine
from .storage import PaperStore


def replay(path):
    with tempfile.TemporaryDirectory(prefix="paper_replay_") as directory:
        store = PaperStore(Path(directory)/"paper.db")
        try:
            engine = PaperEngine(store, "synthetic-replay", enabled=True)
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                kind = item.pop("event")
                if kind == "frame":
                    engine.on_frame(Session(**item["session"]), item["rows"], item["at"],
                                    ws_connected=item.get("ws_connected", True), ws_gap=item.get("ws_gap", False))
                elif kind == "tick":
                    engine.on_tick(item["ticker"], item["price"], item["at"], valid=item.get("valid", True))
                elif kind == "gap":
                    engine.gap(item["at"], item["reason"])
                elif kind == "finish":
                    engine.finish(item["at"], "synthetic_replay_end")
                else:
                    raise ValueError("Unknown synthetic event")
            return dict(trades=store.trades(), summary=store.summary(engine.session.date), actual_order_calls=0)
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    first, second = replay(args.fixture), replay(args.fixture)
    if first != second:
        raise AssertionError("Replay is not deterministic")
    fields = ("ticker", "entry_signal_at", "entry_price_at", "entry_price", "exit_price", "exit_reason", "entry_reason")
    print(json.dumps(dict(determinism="PASS", actual_order_calls=0, summary=first["summary"],
                         trades=[{k:r[k] for k in fields} for r in first["trades"]]), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
