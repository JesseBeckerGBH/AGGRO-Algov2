"""Turn a mission into a query family.

Deterministic stand-in for the LLM Mission Planner (prompts/mission-planner.md).
It keeps the angle taxonomy and per-novelty angle sets from
configs/query-families.yaml, but builds each query so it is *grounded in the
mission subject* — the yaml's bare templates ("{topic} filing OR paper")
produced un-anchored queries that pulled SEC filings for "serve plus one".

Minimum three angles; disconfirming + adjacent_field forced on medium/high
novelty so the system cannot build a well-sourced echo chamber.
"""

from __future__ import annotations

import datetime as _dt

from .config import angle_set, query_families
from .models import Mission, Query

_FORCED_ON_NOVELTY = {"medium", "high"}
_FORCED_ANGLES = ("disconfirming", "adjacent_field")


def _query_for(angle: str, subject: str, seed: str, year: str) -> str:
    # Domain-agnostic on purpose: missions run over academic/predictive-modeling
    # topics AND business/competitive-intelligence topics, and a template tuned
    # for one reads as nonsense on the other (an earlier version's disconfirming
    # query used "overfitting" / "fails to generalize", which pulled unrelated
    # ML papers into a pricing-strategy mission's "what contradicts it").
    grounded = f"{subject} {seed}".strip()
    table = {
        "canonical": grounded,
        "technical": grounded,
        "primary_source_hunt": f"{grounded} official OR announcement OR documentation",
        "mechanism": f"how {grounded} works" if grounded else f"{subject} explained",
        "disconfirming": f"{grounded} criticism OR backlash OR complaints OR "
                         f"controversy OR problems",
        "adjacent_field": f"{seed or subject} comparable situations in other markets",
        "practitioner_experience": f"{grounded} real world experience review",
        "historical": f"{subject} history background timeline",
        "quantitative": f"{grounded} data OR numbers OR statistics OR benchmark",
        "frontier": f"{grounded} {year}".strip(),
    }
    return " ".join(table.get(angle, grounded).split())


def plan(mission: Mission) -> list[Query]:
    qf = query_families()
    known = set(qf.get("angles", {}))
    chosen = angle_set(mission.novelty_requirement)

    if mission.novelty_requirement in _FORCED_ON_NOVELTY:
        for a in _FORCED_ANGLES:
            if a not in chosen and a in known:
                chosen.append(a)

    subject = mission.subject()
    seeds = [s.strip() for s in mission.vocabulary_seed if s.strip()] or [""]
    year = str(_dt.date.today().year)

    queries: list[Query] = []
    for i, name in enumerate(chosen):
        seed = seeds[i % len(seeds)]
        text = _query_for(name, subject, seed, year)
        if text:
            queries.append(Query(angle=name, text=text))

    if len(queries) < 3:  # min_angles_per_mission
        raise ValueError(
            f"mission {mission.id!r} produced only {len(queries)} queries; "
            f"need >= 3 structurally different angles"
        )
    return queries
