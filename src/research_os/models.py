"""Core data structures passed between pipeline stages.

Deliberately plain dataclasses. Every stage takes Results and returns Results
so stages can be reordered or removed without ceremony — except the one
ordering the governing rule forbids reordering: class weight is applied
before relevance (see rerank.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Mission:
    """A research objective with a completion test. No success_condition -> not
    a mission, just browsing (query-families.yaml, mission_schema)."""

    id: str
    objective: str
    success_condition: str
    target_classes: list[str] = field(default_factory=list)
    date_sensitivity: str = "any"  # any | year | quarter | month | week
    novelty_requirement: str = "medium"  # low | medium | high
    excluded_domains: list[str] = field(default_factory=list)
    vocabulary_seed: list[str] = field(default_factory=list)

    _GENERIC_WORDS = {
        "identify", "predictive", "feature", "features", "underused", "public",
        "model", "models", "using", "based", "approach", "system", "new", "that",
        "are", "with", "how", "why", "the", "for", "and", "into", "from",
    }

    def topical_terms(self) -> list[str]:
        """Subject words from the objective plus the vocabulary seeds. Used to
        ground queries and to gate irrelevant results in rerank. A bare year
        (e.g. "2026") is excluded -- shared only because two unrelated things
        both mention the current year is not evidence of topical relevance."""
        words = [w.strip(",.:;()").lower() for w in self.objective.split()]
        subject = [
            w for w in words
            if len(w) > 2 and w not in self._GENERIC_WORDS and not w.isdigit()
        ]
        seeds = [s.lower().strip() for s in self.vocabulary_seed if s.strip()]
        seen: set[str] = set()
        out: list[str] = []
        for t in subject + seeds:
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def subject(self) -> str:
        words = [w.strip(",.:;()").lower() for w in self.objective.split()]
        keep = [
            w for w in words
            if len(w) > 2 and w not in self._GENERIC_WORDS and not w.isdigit()
        ]
        return " ".join(keep[:3]) or (self.vocabulary_seed[0] if self.vocabulary_seed else self.id)

    @staticmethod
    def from_dict(d: dict) -> "Mission":
        required = ("id", "objective", "success_condition")
        missing = [k for k in required if not d.get(k)]
        if missing:
            raise ValueError(
                f"mission missing required field(s): {', '.join(missing)} "
                f"— a mission without a success condition is browsing, not research"
            )
        squash = lambda s: " ".join(str(s).split())
        return Mission(
            id=str(d["id"]).strip(),
            objective=squash(d["objective"]),
            success_condition=squash(d["success_condition"]),
            target_classes=list(d.get("target_classes", [])),
            date_sensitivity=str(d.get("date_sensitivity", "any")),
            novelty_requirement=str(d.get("novelty_requirement", "medium")),
            excluded_domains=list(d.get("excluded_domains", [])),
            vocabulary_seed=list(d.get("vocabulary_seed", [])),
        )


@dataclass
class Query:
    angle: str
    text: str
    expected_source_class: str = ""


@dataclass
class Result:
    """One retrieved item, enriched as it moves through the pipeline."""

    title: str
    url: str
    snippet: str = ""
    connector: str = ""
    raw_rank: int = 0
    query_angle: str = ""
    retrieved_at: str = field(default_factory=_now)

    # filled by canonical.py
    canonical_url: str = ""
    domain: str = ""
    content_hash: str = ""
    snippet_hash: str = ""  # normalized snippet body, for near-duplicate detection

    # filled by classify.py
    source_class: str = ""
    class_signal: str = ""  # which signal decided it

    # filled by memory.py
    seen_before: bool = False
    novel_domain: bool = False
    domain_prior_hits: int = 0  # times this mission has stored this domain before

    # filled by novelty.py
    novelty_score: float = 0.0

    # filled by rerank.py
    base_score: float = 0.0
    class_weight: float = 0.0
    penalties: dict[str, float] = field(default_factory=dict)
    bonuses: dict[str, float] = field(default_factory=dict)
    final_score: float = 0.0
    rerank_notes: list[str] = field(default_factory=list)


@dataclass
class Briefing:
    mission_id: str
    generated_at: str
    body: str
    retrieved: int
    surfaced: int
    novel_domains: int
    adaptation_applied: str = "none"
