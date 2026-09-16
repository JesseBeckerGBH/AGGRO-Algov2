"""Stage 6 — structured failure logging and drift detection.

"Annealing without measurement is guessing." This module does the measuring:
it classifies each run's failures into exactly one of the six classes in
adaptation-rules.yaml, and runs the weekly-cadence drift check against the four
thresholds there. It only *detects and records* — forcing query regeneration
and requiring operator acknowledgement (the `on_breach` action) is Stage 7.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .config import adaptation_rules
from .models import Briefing, Mission, Result


def _severity(failure_class: str) -> str:
    fc = adaptation_rules().get("failure_classes", {}).get(failure_class, {})
    return str(fc.get("severity", "medium"))


def detect_failures(
    mission: Mission,
    surfaced: list[Result],
    briefing: Briefing,
    *,
    connector_errors: dict[str, tuple[str, str]],
    retrieved: int,
) -> list[dict]:
    """Return zero or more failure events. Each is classified into exactly one
    class (adaptation-rules.yaml: "Every logged failure must be classified into
    exactly one")."""
    out: list[dict] = []

    for cname, (signal, detail) in connector_errors.items():
        out.append({
            "failure_class": "connector_failure", "signal": signal,
            "severity": _severity("connector_failure"), "detail": f"{cname}: {detail}",
        })

    if retrieved == 0:
        out.append({
            "failure_class": "connector_failure", "signal": "empty_result_set",
            "severity": _severity("connector_failure"),
            "detail": "no results from any connector",
        })
        return out  # nothing downstream to judge

    if surfaced and not any(r.source_class == "primary" for r in surfaced):
        out.append({
            "failure_class": "coverage_failure", "signal": "no_primary_source_found",
            "severity": _severity("coverage_failure"),
            "detail": "no primary-class result in the surfaced set",
        })

    if len(surfaced) >= 4:
        dom, n = Counter(r.domain for r in surfaced).most_common(1)[0]
        if n / len(surfaced) > 0.5:
            out.append({
                "failure_class": "novelty_failure", "signal": "repeat_domain_dominance",
                "severity": _severity("novelty_failure"),
                "detail": f"{dom} is {n}/{len(surfaced)} of the surfaced set",
            })

    weak = [r for r in surfaced[:3] if r.source_class in ("low_trust", "blocked")]
    if weak:
        out.append({
            "failure_class": "ranking_failure",
            "signal": "low_value_item_ranked_top_three",
            "severity": _severity("ranking_failure"),
            "detail": "; ".join(f"{r.domain} ({r.source_class})" for r in weak),
        })

    ceiling = (
        adaptation_rules().get("levers", {})
        .get("briefing_length_target", {}).get("ceiling_words", 600)
    )
    words = len(briefing.body.split())
    if words > ceiling * 1.15:
        out.append({
            "failure_class": "briefing_failure", "signal": "too_long",
            "severity": _severity("briefing_failure"),
            "detail": f"{words} words (ceiling {ceiling})",
        })

    return out


@dataclass
class DriftReport:
    mission_id: str
    metrics: dict[str, float]
    breaches: list[tuple[str, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.breaches


def check_drift(mission_id: str, mem) -> DriftReport:
    dd = adaptation_rules().get("drift_detection", {})
    ny_floor = float(dd.get("novelty_yield_floor", 0.20))
    conc_ceiling = float(dd.get("domain_concentration_ceiling", 0.35))
    ps_floor = float(dd.get("primary_share_floor", 0.30))
    ds_floor = float(dd.get("disconfirming_share_floor", 0.10))

    ny = mem.recent_novelty_yield(mission_id, 3)
    _, conc = mem.domain_shares(mission_id)
    ps = mem.recent_primary_share(mission_id, 3)
    ds = mem.recent_disconfirming_share(mission_id, 3)
    metrics = {
        "novelty_yield": ny, "domain_concentration": conc,
        "primary_share": ps, "disconfirming_share": ds,
    }

    breaches: list[tuple[str, str]] = []
    if ny < ny_floor:
        breaches.append(("novelty_yield", f"{ny:.0%} < floor {ny_floor:.0%}"))
    if conc > conc_ceiling:
        breaches.append(("domain_concentration", f"{conc:.0%} > ceiling {conc_ceiling:.0%}"))
    if ps < ps_floor:
        breaches.append(("primary_share", f"{ps:.0%} < floor {ps_floor:.0%}"))
    if ds < ds_floor:
        breaches.append(("disconfirming_share", f"{ds:.0%} < floor {ds_floor:.0%}"))

    return DriftReport(mission_id, metrics, breaches)


# adaptation-rules.yaml drift_detection.on_breach: "force query family
# regeneration on affected missions" — so a drift breach must feed the same
# evidence counters propose() reads, or the loop never reacts to a mission
# that has simply gone stale (same connector, same queries, warm memory).
_DRIFT_FAILURE_MAP = {
    "novelty_yield": ("novelty_failure", "novelty_yield_below_floor"),
    "domain_concentration": ("novelty_failure", "domain_concentration_high"),
    "primary_share": ("coverage_failure", "primary_share_below_floor"),
    "disconfirming_share": ("coverage_failure", "disconfirming_share_below_floor"),
}


def drift_to_failures(drift: DriftReport) -> list[dict]:
    out = []
    for name, msg in drift.breaches:
        fclass, signal = _DRIFT_FAILURE_MAP[name]
        out.append({
            "failure_class": fclass, "signal": signal,
            "severity": _severity(fclass), "detail": f"drift: {msg}",
        })
    return out
