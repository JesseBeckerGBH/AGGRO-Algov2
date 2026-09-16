"""The decisive layer: replace the origin engine's ranking with our own.

Governing rule (README, master-package.md, reranking-logic.md, source-classes.yaml):
results are ordered BY SOURCE CLASS FIRST, then by everything else. That is
implemented as a strict bucket sort — every `primary` outranks every
`high_trust_secondary`, which outranks every `named_expert`, and so on — with
results *within* a class ordered by

    score = relevance  x  class_weight  x  penalty_mult  x  bonus_mult

Penalties are multiplicative with a 0.05 floor; the combined bonus multiplier
is capped at 1.40. Both tables come from source-classes.yaml. Keeping the
class buckets hard is what stops a bullseye-relevant forum post from ever
displacing a merely-relevant primary source.
"""

from __future__ import annotations

from .config import class_weight, source_classes
from .models import Mission, Result

_PENALTY_FLOOR = 0.05
_BONUS_CAP = 1.40
_NAMED_AUTHOR_CLASSES = {"named_expert", "high_trust_secondary"}
_STOP = {"one", "plus", "the", "and", "for", "via", "new"}


def _topic_words(mission: Mission) -> set[str]:
    words: set[str] = set()
    for term in mission.topical_terms():
        for w in term.split():
            if len(w) > 2 and w not in _STOP:
                words.add(w)
    return words


def _off_topic(result: Result, topic_words: set[str]) -> bool:
    """True when the result shares no subject vocabulary with the mission at
    all. 'Class before relevance' means class wins *among relevant results* —
    an irrelevant primary source is noise, not a top hit."""
    if not topic_words:
        return False
    text = f"{result.title} {result.snippet}".lower()
    return not any(w in text for w in topic_words)

# Strict precedence. Lower rank sorts first. Anything unknown sorts last.
_CLASS_ORDER = [
    "primary",
    "high_trust_secondary",
    "named_expert",
    "aggregator",
    "community",
    "low_trust",
    "blocked",
]


def _class_rank(source_class: str) -> int:
    try:
        return _CLASS_ORDER.index(source_class)
    except ValueError:
        return len(_CLASS_ORDER)


def _stem(word: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(word) > len(suf) + 2 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def _title_words(title: str) -> set[str]:
    cleaned = title.lower().replace("...", " ").replace("…", " ")
    for junk in ("- github", "| github", "· github", "github -"):
        cleaned = cleaned.replace(junk, " ")
    return {_stem(w.strip("-:|·,.")) for w in cleaned.split() if len(w) > 2}


def _relevance(result: Result, mission: Mission, pool_size: int) -> float:
    """Origin position nudged by vocabulary overlap. Intentionally the smallest
    term in the chain: it only orders results *inside* a class bucket."""
    position = max(0.0, (pool_size - result.raw_rank) / pool_size) if pool_size else 0.0
    text = f"{result.title} {result.snippet}".lower()
    vocab = [t.lower() for t in mission.vocabulary_seed] + list(_topic_words(mission))
    hits = sum(1 for t in set(vocab) if t and t in text)
    overlap = min(hits / 4.0, 1.0)
    # lean on topical overlap, not origin position: position mostly reflects the
    # engine's own ranking, which is what we are here to replace.
    return round(0.3 * position + 0.7 * overlap, 4)


def _penalties(result: Result, domain_seen_count: int, near_dup: bool) -> dict[str, float]:
    cfg = source_classes().get("penalties", {})
    out: dict[str, float] = {}
    if domain_seen_count >= 2:  # 3rd and later from the same domain this mission
        out["domain_repetition"] = float(cfg.get("domain_repetition", {}).get("factor", 0.60))
    if near_dup:
        out["near_duplicate_content"] = float(
            cfg.get("near_duplicate_content", {}).get("factor", 0.25)
        )
    if result.seen_before:
        out["already_seen"] = float(cfg.get("already_seen", {}).get("factor", 0.30))
    if result.source_class in _NAMED_AUTHOR_CLASSES and result.class_signal in (
        "default", "domain_pattern", "domain_map"
    ):
        out["no_named_author"] = float(cfg.get("no_named_author", {}).get("factor", 0.75))
    return out


def _bonuses(result: Result) -> dict[str, float]:
    cfg = source_classes().get("bonuses", {})
    out: dict[str, float] = {}
    if result.novel_domain:
        out["novel_domain"] = float(cfg.get("novel_domain", {}).get("factor", 1.15))
    return out


def rerank(results: list[Result], mission: Mission, overlay: dict | None = None) -> list[Result]:
    overlay = overlay or {}
    dom_delta = overlay.get("domain_weight_delta", {})
    cls_delta = overlay.get("class_weight_delta", {})
    topic_words = _topic_words(mission)
    kept = [r for r in results if not _off_topic(r, topic_words)]
    dropped = len(results) - len(kept)
    if dropped:
        # leave a breadcrumb on the first survivor for the footer / logs
        for r in kept[:1]:
            r.rerank_notes.append(f"dropped {dropped} off-topic result(s) before ranking")
    results = kept

    pool = len(results)
    domain_counts: dict[str, int] = {}
    seen_titles: list[tuple[str, set[str]]] = []  # (domain, title words) of earlier rows
    seen_snippets: set[str] = set()

    for r in sorted(results, key=lambda x: x.raw_rank):
        seen = domain_counts.get(r.domain, 0)
        domain_counts[r.domain] = seen + 1

        words = _title_words(r.title)
        near_dup = (r.snippet_hash != "" and r.snippet_hash in seen_snippets) or any(
            dom == r.domain and words and tw
            and (len(words & tw) / len(words | tw) >= 0.55 or len(words & tw) >= 5)
            for dom, tw in seen_titles
        )
        seen_titles.append((r.domain, words))
        if r.snippet_hash:
            seen_snippets.add(r.snippet_hash)

        r.base_score = _relevance(r, mission, pool)
        r.class_weight = round(
            max(0.0, class_weight(r.source_class) + cls_delta.get(r.source_class, 0.0)), 4
        )
        r.penalties = _penalties(r, seen, near_dup)
        r.bonuses = _bonuses(r)

        penalty_mult = 1.0
        for f in r.penalties.values():
            penalty_mult *= f
        penalty_mult = max(penalty_mult, _PENALTY_FLOOR)

        bonus_mult = 1.0
        for f in r.bonuses.values():
            bonus_mult *= f
        bonus_mult = min(bonus_mult, _BONUS_CAP)

        # novelty orders results *within* a class bucket (reranking-logic.md:
        # class, then relevance, then novelty). 0.0 -> 0.85x, 1.0 -> 1.15x.
        novelty_factor = 0.85 + 0.30 * r.novelty_score
        # Stage 7: self-annealing per-domain weight delta (bounded, reversible).
        domain_factor = max(0.1, 1.0 + dom_delta.get(r.domain, 0.0))
        r.final_score = round(
            r.base_score * r.class_weight * penalty_mult * bonus_mult
            * novelty_factor * domain_factor, 6
        )
        notes = list(r.rerank_notes) + [f"class={r.source_class}({r.class_weight:.2f})"]
        if r.penalties:
            notes.append("penalties=" + ",".join(r.penalties))
        if r.bonuses:
            notes.append("bonuses=" + ",".join(r.bonuses))
        r.rerank_notes = notes

    # class bucket first, then score within the bucket
    return sorted(results, key=lambda x: (_class_rank(x.source_class), -x.final_score))
