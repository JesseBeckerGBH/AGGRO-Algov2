"""Novelty scoring.

Boolean seen/unseen is not enough (success-metrics.md, "Novelty Yield"):
novelty is defined by domain, prior exposure, and how concentrated the
mission's memory already is on this result's domain. A result from a domain
that already dominates the mission is barely novel even if this exact URL is
new.

    novelty_score in [0, 1]:
      +0.45  domain never seen for this mission
      +0.30  content/URL never seen before
      +0.25  * (1 - concentration of this domain in mission memory)

`yield_of` is the share of a surfaced set that clears a novelty bar — the
number drift detection watches against novelty_yield_floor (0.20).
"""

from __future__ import annotations

from .models import Result

_NOVEL_BAR = 0.40


def score_one(result: Result, domain_share: float) -> float:
    s = 0.0
    if result.novel_domain:
        s += 0.45
    if not result.seen_before:
        s += 0.30
    s += 0.25 * max(0.0, 1.0 - domain_share)
    return round(min(s, 1.0), 4)


def score(results: list[Result], domain_shares: dict[str, float]) -> list[Result]:
    for r in results:
        r.novelty_score = score_one(r, domain_shares.get(r.domain, 0.0))
    return results


def yield_of(results: list[Result]) -> float:
    if not results:
        return 0.0
    novel = sum(1 for r in results if r.novelty_score >= _NOVEL_BAR)
    return round(novel / len(results), 4)
