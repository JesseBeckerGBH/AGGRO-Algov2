"""Render a reranked result set into a decision-support briefing.

Follows the structure in prompts/briefing-agent.md: bottom line, what is new,
what contradicts it (always its own section), confidence with the specific
gap, next question, one-line footer. Every claim carries its source class.

This is a DETERMINISTIC render — it arranges and labels, it does not
synthesise prose. Real synthesis (the 250-400 word narrative the success
test wants) is an LLM call and is the next step after the slice; the seam
is `synthesize()` below.
"""

from __future__ import annotations

import re

from . import llm
from .models import Briefing, Mission, Result

_STRONG_CLASSES = {"primary", "high_trust_secondary", "named_expert"}
_CONTRA_CUES = (
    "criticism", "overstated", "does not", "doesn't", "fails", "myth",
    "debunk", "no evidence", "overfit", "spurious",
)


def _is_contradicting(r: Result) -> bool:
    if r.query_angle == "disconfirming":
        return True
    text = f"{r.title} {r.snippet}".lower()
    return any(cue in text for cue in _CONTRA_CUES)


def _confidence(surfaced: list[Result]) -> tuple[str, str]:
    if not surfaced:
        return "low", "nothing survived reranking"
    strong = sum(1 for r in surfaced if r.source_class in _STRONG_CLASSES)
    share = strong / len(surfaced)
    if share >= 0.5 and len(surfaced) >= 3:
        return "medium", "no primary-source contradiction retrieved yet"
    if share >= 0.25:
        return "low", "thin primary coverage; leans on secondary and community"
    return "low", "top results are discovery-tier, not terminal evidence"


def render(mission: Mission, reranked: list[Result], *, top: int = 6,
          retrieved: int = 0, generated_at: str = "",
          word_target: int = 400, adaptation_note: str = "none") -> Briefing:
    surfaced = reranked[:top]
    novel_domains = sorted({r.domain for r in surfaced if r.novel_domain})
    contra = [r for r in surfaced if _is_contradicting(r)]
    # pull disconfirming-angle hits from the FULL ranked set, even below the cut —
    # an empty "what contradicts it" should mean the angle found nothing, not that
    # its results were merely outranked
    for r in reranked:
        if r.query_angle == "disconfirming" and r not in contra and r not in surfaced:
            contra.append(r)
    contra = contra[:3]
    fresh = [
        r for r in surfaced
        if not r.seen_before
        and r not in contra
        and "near_duplicate_content" not in r.penalties  # never present a near-dupe as new
    ]

    lines: list[str] = []

    # Bottom line
    if not surfaced:
        lines.append("## Bottom line\n")
        lines.append(
            "Nothing material surfaced for this mission. Either the query family "
            "is too narrow or the source classes asked for do not cover this "
            "question yet.\n"
        )
        body = "\n".join(lines)
        return Briefing(mission.id, generated_at, body, retrieved, 0, 0)

    lead = surfaced[0]
    lines.append("## Bottom line\n")
    lines.append(
        f"{mission.objective.rstrip('.')}. Strongest signal so far: "
        f"\"{lead.title}\" ({lead.domain}, {lead.source_class}).\n"
    )

    # What is new
    lines.append("## What is new\n")
    if fresh:
        for r in fresh:
            why = r.snippet.strip() or "no snippet"
            lines.append(
                f"- **{r.title}** — {r.domain} ({r.source_class}). {why}"
            )
    else:
        lines.append("- Nothing here is new relative to research memory.")
    lines.append("")

    # What contradicts it
    lines.append("## What contradicts it\n")
    if contra:
        for r in contra:
            lines.append(
                f"- **{r.title}** — {r.domain} ({r.source_class}). "
                f"{r.snippet.strip() or 'no snippet'}"
            )
    else:
        lines.append("No disconfirming evidence surfaced.")
    lines.append("")

    # Confidence
    level, gap = _confidence(surfaced)
    lines.append("## Confidence\n")
    lines.append(f"{level} — {gap}.\n")

    # Next question
    lines.append("## Next question\n")
    lines.append(
        f"Test the success condition directly: {mission.success_condition.rstrip('.')}. "
        f"Pursue via the adjacent-field angle if the next run is still thin.\n"
    )

    # Footer
    lines.append(
        f"_retrieved {retrieved} · surfaced {len(surfaced)} · "
        f"novel domains {len(novel_domains)} · adaptation applied: {adaptation_note}_"
    )

    body = "\n".join(lines)
    return Briefing(
        mission_id=mission.id,
        generated_at=generated_at,
        body=body,
        retrieved=retrieved,
        surfaced=len(surfaced),
        novel_domains=len(novel_domains),
    )


_SYSTEM = """\
You write a research briefing that supports a decision. You are given a
mission and a reranked, source-classified result set. Obey every rule:

- Output GitHub-flavoured Markdown. No preamble, no restating the mission,
  no closing offer of help.
- Sections, in this order and with these headings:
  ## Bottom line   -- one or two sentences: the answer, or the state of it.
  ## What is new   -- only material not already in memory (items marked
                      seen_before=yes are NOT new). Each: the finding, the
                      source with its class in parentheses, why it matters
                      for the stated decision.
  ## What contradicts it  -- the disconfirming evidence. If none, write
                      exactly "No disconfirming evidence surfaced." Never omit
                      this section.
  ## Confidence    -- high | medium | low, and the specific gap.
  ## Next question -- the single strongest follow-up and which angle pursues it.
- Every claim carries an inline source with its class label, e.g.
  "(arxiv.org, primary)". Copy the class EXACTLY as given in the RESULTS
  block for that domain -- never upgrade or downgrade it because the name
  looks official or unofficial to you. If you believe a label is wrong,
  say so in Confidence as a named gap; do not silently relabel it. The
  class shown to the reader must always match the system's own record.
- Lead with what changed or is newly known, never with background.
- An aggregator or community item may only support a claim alongside a
  stronger-class source for the same point.
- Prefer one mechanism the reader can act on over five facts they cannot.
- If two strong sources disagree, present the disagreement; do not resolve it.
- About {word_target} words of body (hard ceiling {ceiling}). If nothing
  material was found, say so in two sentences and stop -- do not manufacture
  significance.
- Flat and declarative. No enthusiasm, no "it is important to note".
Do not write the footer line; it is appended mechanically."""


def _result_block(results: list[Result]) -> str:
    lines = []
    for i, r in enumerate(results, start=1):
        flags = []
        if r.seen_before:
            flags.append("seen_before=yes")
        if r.novel_domain:
            flags.append("novel_domain=yes")
        if _is_contradicting(r):
            flags.append("reads_as_disconfirming=yes")
        lines.append(
            f"{i}. {r.title}\n"
            f"   url: {r.canonical_url or r.url}\n"
            f"   domain/class: {r.domain} / {r.source_class} "
            f"(signal: {r.class_signal}, score: {r.final_score})\n"
            f"   {'; '.join(flags) if flags else 'no flags'}\n"
            f"   snippet: {r.snippet.strip() or '(none)'}"
        )
    return "\n".join(lines)


_CLASS_TAG = re.compile(r"\(([a-z0-9][\w.-]*\.[a-z]{2,})\s*,\s*([a-zA-Z_ ]+)\)")


def _enforce_class_labels(body: str, known: dict[str, str]) -> str:
    """Correct any '(domain, class)' tag the model wrote to the class this
    system actually recorded for that domain. A prompt rule is a request; this
    is the guarantee -- the reader-facing label must match the record even if
    the model "corrected" it on its own (e.g. relabeling a vendor site as
    primary when the classifier had filed it as aggregator)."""

    def fix(m: re.Match) -> str:
        domain, claimed = m.group(1), m.group(2).strip()
        actual = known.get(domain)
        if actual is None or claimed.replace(" ", "_") == actual:
            return m.group(0)
        return f"({domain}, {actual})"

    return _CLASS_TAG.sub(fix, body)


def synthesize(
    mission: Mission,
    reranked: list[Result],
    *,
    top: int = 6,
    retrieved: int = 0,
    generated_at: str = "",
    provider: str | None = None,
    model: str | None = None,
    word_target: int = 400,
    adaptation_note: str = "none",
    _transport=None,
) -> Briefing:
    """LLM narrative over the reranked set, obeying prompts/briefing-agent.md.

    Falls through to the provider layer in llm.py; raises llm.LLMKeyMissing if
    no key is configured. The footer is appended deterministically so its
    counts are always accurate regardless of what the model wrote.
    """
    surfaced = reranked[:top]
    novel_domains = sorted({r.domain for r in surfaced if r.novel_domain})
    disconfirming = [
        r for r in reranked
        if r.query_angle == "disconfirming" and r not in surfaced
    ][:3]

    user = (
        f"MISSION\n"
        f"objective: {mission.objective}\n"
        f"success condition: {mission.success_condition}\n"
        f"target source classes: {', '.join(mission.target_classes) or 'any'}\n"
        f"novelty requirement: {mission.novelty_requirement}\n\n"
        f"RESULTS (already reranked, best first)\n{_result_block(surfaced)}\n"
    )
    if disconfirming:
        user += (
            f"\nDISCONFIRMING-ANGLE RESULTS (from the 'case against' query; use "
            f"for the 'What contradicts it' section)\n{_result_block(disconfirming)}\n"
        )

    ceiling = int(word_target * 1.5)
    system = (_SYSTEM.replace("{word_target}", str(word_target))
                     .replace("{ceiling}", str(ceiling)))
    body = llm.synthesize(
        system, user, provider=provider, model=model, _transport=_transport
    ).rstrip()
    # Enforce, don't just request: the class label shown to the reader must
    # match the system's own record, even if the model relabeled it.
    body = _enforce_class_labels(body, {r.domain: r.source_class for r in reranked})

    footer = (
        f"\n\n_retrieved {retrieved} · surfaced {len(surfaced)} · "
        f"novel domains {len(novel_domains)} · adaptation applied: {adaptation_note}_"
    )
    return Briefing(
        mission_id=mission.id,
        generated_at=generated_at,
        body=body + footer,
        retrieved=retrieved,
        surfaced=len(surfaced),
        novel_domains=len(novel_domains),
    )
