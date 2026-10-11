"""Synthetic paired replay, temporary databases and no provider/network access."""
import argparse
import json
from pathlib import Path
import tempfile

from ..models import Session
from ..paper.engine import PaperEngine
from ..paper.storage import PaperStore
from .paired import PairedEngine
from .storage import RotationStore
from .comparison import comparisons


def replay(path, *, rotation_enabled=True):
    with tempfile.TemporaryDirectory(prefix="rotation_replay_") as directory:
        baseline = PaperStore(Path(directory)/"baseline.db")
        review = RotationStore(Path(directory)/"review.db")
        try:
            engine = PairedEngine(PaperEngine(baseline, "synthetic-replay", enabled=True), review,
                                  rotation_enabled=rotation_enabled)
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                kind = item["event"]
                if kind == "frame":
                    engine.on_frame(Session(**item["session"]), item["rows"], item["at"],
                        ws_connected=item.get("ws_connected", True), ws_gap=item.get("ws_gap", False))
                elif kind == "tick":
                    engine.on_tick(item["ticker"], item["price"], item["at"], valid=item.get("valid", True))
                elif kind == "gap":
                    engine.gap(item["at"], item["reason"])
                elif kind == "heartbeat":
                    engine.heartbeat(item["at"])
                elif kind == "finish":
                    engine.finish(item["at"], "synthetic_replay_end")
                else:
                    raise ValueError("Unknown synthetic event")
            if engine.review_error:
                raise AssertionError("Sidecar replay failed")
            day = engine.session.date
            baseline_result = dict(trades=baseline.trades(), summary=baseline.summary(day), actual_order_calls=0)
            review.export_review(day, Path(directory)/"export", baseline.summary(day))
            return dict(baseline=baseline_result, rotation_trades=review.trades(),
                rotation_summary=review.summary(day), post_exit=review.documents("paper_post_exit"),
                switches=review.documents("rotation_switches"),
                comparisons=comparisons(review.documents("review_trades"), review.documents("paper_post_exit"), review.documents("rotation_switches")),
                actual_order_calls=0)
        finally:
            baseline.close()
            review.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    result = replay(args.fixture)
    if result != replay(args.fixture):
        raise AssertionError("Nondeterministic rotation replay")
    print(json.dumps(dict(determinism="PASS", actual_order_calls=0,
        baseline=result["baseline"]["summary"], rotation=result["rotation_summary"], switches=result["switches"]), indent=2))


if __name__ == "__main__":
    main()
