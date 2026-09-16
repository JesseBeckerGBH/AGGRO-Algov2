"""The vertical slice, wired end to end.

mission -> query family -> connector -> canonicalize + dedupe -> classify
-> novelty (memory) -> rerank (class before relevance) -> top K -> briefing
-> persist. One ugly path that produces one output you can actually judge
against the success test in docs/operations/implementation-order.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import adaptation
from . import briefing as briefing_mod
from . import canonical, classify, llm, novelty, planning, rerank, telemetry
from .connectors import get_connectors
from .models import Briefing, Mission, Result
from .memory import Memory
from .telemetry import DriftReport


@dataclass
class RunOutput:
    mission: Mission
    briefing: Briefing
    reranked: list[Result]
    retrieved: int
    per_connector: dict[str, int] = None  # type: ignore[assignment]
    novelty_yield: float = 0.0
    domain_top_share: float = 0.0
    new_vocab: list[str] = None  # type: ignore[assignment]
    failures: list[dict] = None  # type: ignore[assignment]
    drift: DriftReport = None  # type: ignore[assignment]
    adaptation: dict = None  # type: ignore[assignment]


def load_mission(path: str | Path) -> Mission:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if "mission" in data:  # allow either a bare mapping or {mission: {...}}
        data = data["mission"]
    return Mission.from_dict(data)


def run_mission(
    mission: Mission,
    *,
    connector: str = "fixture",
    limit: int = 10,
    top: int = 6,
    db_path: str | Path = "research-memory.sqlite",
    brief_mode: str = "render",  # render | llm | auto
    llm_provider: str | None = None,
    llm_model: str | None = None,
    adapt: bool = True,
) -> RunOutput:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conns = get_connectors(connector)

    # Stage 4 + 7: load promoted vocabulary and the active policy overlay
    with Memory(db_path) as _m:
        for term in _m.promoted_terms(mission.id):
            if term.lower() not in {s.lower() for s in mission.vocabulary_seed}:
                mission.vocabulary_seed.append(term)
        overlay = _m.active_overlay(mission.id)

    # Stage 7: apply connector routing from the overlay (demote / skip)
    demotions = overlay.get("connector_demotions", {})
    if demotions:
        rules = adaptation.adaptation_rules().get("levers", {}).get("connector_routing", {})
        cap = int(rules.get("max_consecutive_demotions", 3))
        active = [c for c in conns if demotions.get(c.name, 0) < cap]
        conns = sorted(active or conns, key=lambda c: demotions.get(c.name, 0))

    word_target = overlay.get("briefing_word_target") or 400
    adapt_note = _overlay_note(overlay)

    # Stage 7: an approved query_family_regeneration forces the two uncomfortable
    # angles and rotates the vocabulary seed for the next run.
    if overlay.get("query_regen"):
        mission.novelty_requirement = "high"
        if len(mission.vocabulary_seed) > 1:
            mission.vocabulary_seed = mission.vocabulary_seed[1:] + mission.vocabulary_seed[:1]

    queries = planning.plan(mission)

    collected: list[Result] = []
    per_connector: dict[str, int] = {c.name: 0 for c in conns}
    with Memory(db_path) as mem:
        mem.upsert_mission(mission, now)

        connector_errors: dict[str, tuple[str, str]] = {}
        for q in queries:
            for conn in conns:
                try:
                    hits = conn.search(q.text, limit=limit)
                except Exception as exc:  # a dead connector must not kill the run
                    connector_errors[conn.name] = (
                        type(exc).__name__, str(exc)[:200]
                    )
                    mem.record_query(mission.id, q, conn.name, now, 0)
                    continue
                for h in hits:
                    h.query_angle = q.angle
                qid = mem.record_query(mission.id, q, conn.name, now, len(hits))
                for h in hits:
                    h._query_id = qid  # type: ignore[attr-defined]
                per_connector[conn.name] += len(hits)
                collected.extend(hits)

        retrieved = len(collected)

        canonical.enrich(collected)
        deduped = canonical.dedupe(collected)
        deduped = [
            r for r in deduped
            if not any(r.domain == d or r.domain.endswith("." + d)
                       for d in mission.excluded_domains)
        ]
        classify.classify(deduped, mission)
        mem.mark_novelty(mission.id, deduped)
        prior_shares, prior_top = mem.domain_shares(mission.id)  # state BEFORE this run
        novelty.score(deduped, prior_shares)
        ordered = rerank.rerank(deduped, mission, overlay=overlay)

        mode = brief_mode
        if mode == "auto":
            mode = "llm" if llm.available_provider() else "render"
        if mode == "llm":
            brief = briefing_mod.synthesize(
                mission, ordered, top=top, retrieved=retrieved, generated_at=now,
                provider=llm_provider, model=llm_model,
                word_target=word_target, adaptation_note=adapt_note,
            )
        else:
            brief = briefing_mod.render(
                mission, ordered, top=top, retrieved=retrieved, generated_at=now,
                word_target=word_target, adaptation_note=adapt_note,
            )

        surfaced = ordered[:top]
        n_yield = novelty.yield_of(surfaced)
        p_share = (
            sum(1 for r in surfaced if r.source_class == "primary") / len(surfaced)
            if surfaced else 0.0
        )
        d_share = (
            sum(1 for r in surfaced
                if r.query_angle == "disconfirming" or briefing_mod._is_contradicting(r))
            / len(surfaced) if surfaced else 0.0
        )

        # persist the deduped+scored set and the briefing
        by_qid: dict[int, list[Result]] = {}
        for r in ordered:
            by_qid.setdefault(getattr(r, "_query_id", 0), []).append(r)
        for qid, rows in by_qid.items():
            mem.record_results(mission.id, qid or None, rows)
        mem.record_briefing(brief, novelty_yield=n_yield,
                            primary_share=p_share, disconfirming_share=d_share)

        # Stage 4: update concentration + harvest vocabulary AFTER scoring this run
        mem.record_domain_hits(mission.id, ordered, now)
        new_vocab = mem.harvest_vocab(
            mission.id, ordered, mission.vocabulary_seed, now
        )
        _, top_share = mem.domain_shares(mission.id)

        # Stage 6: classify + log this run's failures, then run the drift check
        failures = telemetry.detect_failures(
            mission, surfaced, brief,
            connector_errors=connector_errors, retrieved=retrieved,
        )
        drift = telemetry.check_drift(mission.id, mem)
        # adaptation-rules.yaml on_breach: a drift breach must feed the same
        # evidence propose() reads, or a mission that's simply gone stale
        # (same connector, same queries, warm memory) never gets a remediation.
        failures = failures + telemetry.drift_to_failures(drift)
        mem.record_failures(mission.id, failures, now)
        mem.record_drift_check(
            mission.id, now, drift.metrics, [name for name, _ in drift.breaches]
        )

        # Stage 7: run the closed loop (propose -> gate -> apply -> observe -> decide)
        adapt_result = adaptation.run_cycle(mission, mem, now) if adapt else None

    return RunOutput(
        mission=mission, briefing=brief, reranked=ordered,
        retrieved=retrieved, per_connector=per_connector,
        novelty_yield=n_yield, domain_top_share=top_share, new_vocab=new_vocab,
        failures=failures, drift=drift, adaptation=adapt_result,
    )


def _overlay_note(overlay: dict) -> str:
    bits = []
    for name, n in overlay.get("connector_demotions", {}).items():
        bits.append(f"{name} demoted x{n}")
    if overlay.get("briefing_word_target"):
        bits.append(f"brief target {overlay['briefing_word_target']}w")
    for d, delta in overlay.get("domain_weight_delta", {}).items():
        bits.append(f"{d} {delta:+.2f}")
    for c, delta in overlay.get("class_weight_delta", {}).items():
        bits.append(f"{c} class {delta:+.2f}")
    return "; ".join(bits) if bits else "none"
