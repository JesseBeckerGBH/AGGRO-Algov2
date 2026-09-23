# src/api/routers/briefs.py
"""Brief routes.

GET  /api/briefs               — list all briefings (newest first) from research_os.memory.
GET  /api/briefs/{id}          — single briefing body + evidence records.
GET  /api/briefs/{id}/export   — plaintext/markdown export of the brief body.

No SQLAlchemy.  No shadow tables.  All content is read directly from
research-memory.sqlite via research_os.memory.Memory.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from research_os.memory import Memory

router = APIRouter()

_MEMORY_PATH = "research-memory.sqlite"


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class BriefSummary(BaseModel):
    briefing_id: int
    mission_id: str
    generated_at: str
    retrieved: int
    surfaced: int
    novel_domains: int
    novelty_yield: float
    primary_share: float
    disconfirming_share: float


class EvidenceRecord(BaseModel):
    result_id: int
    title: str
    url: str
    canonical_url: str
    domain: str
    source_class: str
    class_signal: str
    final_score: float
    retrieved_at: str


class BriefDetail(BriefSummary):
    body: str
    evidence: list[EvidenceRecord]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[BriefSummary])
async def list_briefs(mission_id: str | None = None,
                      limit: int = 50) -> list[BriefSummary]:
    """All briefings, newest first. Optionally filter by mission_id."""
    with Memory(_MEMORY_PATH) as mem:
        rows = mem.list_briefings(mission_id=mission_id, limit=limit)

    return [
        BriefSummary(
            briefing_id=r["briefing_id"],
            mission_id=r["mission_id"],
            generated_at=r["generated_at"],
            retrieved=r["retrieved"] or 0,
            surfaced=r["surfaced"] or 0,
            novel_domains=r["novel_domains"] or 0,
            novelty_yield=r["novelty_yield"] or 0.0,
            primary_share=r["primary_share"] or 0.0,
            disconfirming_share=r["disconfirming_share"] or 0.0,
        )
        for r in rows
    ]


@router.get("/{briefing_id}", response_model=BriefDetail)
async def get_brief(briefing_id: int) -> BriefDetail:
    """Full briefing with body text and top evidence records."""
    with Memory(_MEMORY_PATH) as mem:
        brief = mem.get_briefing(briefing_id)
        if not brief:
            raise HTTPException(status_code=404, detail="Briefing not found")
        # pull top evidence for this mission so the front-end can render source cards
        evidence_rows = mem.list_results(brief["mission_id"], limit=20)

    evidence = [
        EvidenceRecord(
            result_id=r["result_id"],
            title=r["title"] or "",
            url=r["url"] or "",
            canonical_url=r["canonical_url"] or "",
            domain=r["domain"] or "",
            source_class=r["source_class"] or "",
            class_signal=r["class_signal"] or "",
            final_score=r["final_score"] or 0.0,
            retrieved_at=r["retrieved_at"] or "",
        )
        for r in evidence_rows
    ]

    return BriefDetail(
        briefing_id=brief["briefing_id"],
        mission_id=brief["mission_id"],
        generated_at=brief["generated_at"],
        body=brief["body"] or "",
        retrieved=brief["retrieved"] or 0,
        surfaced=brief["surfaced"] or 0,
        novel_domains=brief["novel_domains"] or 0,
        novelty_yield=brief["novelty_yield"] or 0.0,
        primary_share=brief["primary_share"] or 0.0,
        disconfirming_share=brief["disconfirming_share"] or 0.0,
        evidence=evidence,
    )


@router.get("/{briefing_id}/export", response_class=PlainTextResponse)
async def export_brief(briefing_id: int, fmt: str = "md") -> str:
    """Export brief body as plain Markdown or add a thin header for other formats."""
    with Memory(_MEMORY_PATH) as mem:
        brief = mem.get_briefing(briefing_id)
    if not brief:
        raise HTTPException(status_code=404, detail="Briefing not found")

    body: str = brief["body"] or ""

    if fmt == "md":
        header = (
            f"# Intelligence Brief — {brief['mission_id']}\n"
            f"_Generated: {brief['generated_at']}_\n\n"
            f"---\n\n"
        )
        return header + body

    # default: return raw body for any other format value
    return body
