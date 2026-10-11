"""Material evidence dominance, never an estimate of rising probability."""
from ..models import instant
from ..paper.policy import ExperimentalBaselinePolicyV1, finite, top
from . import STRATEGY_VERSION


class RotationPolicy(ExperimentalBaselinePolicyV1):
    def evidence(self, row, watch):
        evidence = super().evidence(row, watch)
        evidence.update(policy=STRATEGY_VERSION, entry_confirmation_policy="paper_v1_baseline")
        return evidence

    def dominance(self, candidate, current, watch, at, max_age):
        if not current or not self.fresh(candidate, at, max_age) or not self.fresh(current, at, max_age):
            return None
        # Unknown rank is not proof of TOP10 departure. Missing current rows fail closed.
        ranks = ("current_leader_rank", "emerging_rank")
        if not all(top(candidate.get(k)) and isinstance(current.get(k), int)
                   and not isinstance(current[k], bool) and current[k] > candidate[k] for k in ranks):
            return None
        if not all(finite(current.get(k)) and finite(candidate.get(k)) and candidate[k] > current[k]
                   for k in ("return_1m", "return_5m")):
            return None
        # Both momentum observations must come from the same completed cutoff.
        if not candidate.get("completed_bar_at") or not current.get("completed_bar_at"):
            return None
        if instant(candidate["completed_bar_at"]) != instant(current["completed_bar_at"]):
            return None
        if not self.confirmed(candidate, watch):
            return None
        weakness = [k + "_outside_top10" for k in ranks if current[k] > 10]
        weakness += [k + "_nonpositive" for k in ("return_1m", "return_5m") if current[k] <= 0]
        if not weakness:
            return None
        return dict(rule="material_evidence_dominance", current=dict(current), candidate=dict(candidate),
                    current_weakness=weakness, entry_confirmation=self.evidence(candidate, watch))
