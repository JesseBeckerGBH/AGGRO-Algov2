# src/api/routers/missions.py
"""Mission routes.

POST /api/missions        — build a real research_os Mission, enqueue a job,
                            kick the pipeline in a BackgroundTask.
GET  /api/missions        — list all missions from research_os.memory.
GET  /api/missions/{id}   — single mission + its latest job status.
GET  /api/missions/{id}/results — top evidence records from research_os.memory.

The job table (api/db.py) owns lifecycle state only (queued/running/done/failed).
All content — mission metadata, brief bodies, evidence — is read from
research-memory.sqlite via research_os.memory.Memory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from research_os.memory import Memory
from research_os.models import Mission
from research_os.pipeline import run_mission

from ..db import (
    create_job,
    get_job,
    get_job_db,
    list_jobs,
    set_job_done,
    set_job_failed,
    set_job_running,
)

router = APIRouter()

_MEMORY_PATH = "research-memory.sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pending_reviews(mem: Memory, mission_id: str) -> list["PendingReview"]:
    """Proposals sitting pending_operator — see adaptation.py's
    requires_operator_review levers. Surfaced explicitly here so a pending
    proposal is never silently invisible in the API response."""
    return [
        PendingReview(version=r["version"], lever=r["lever"], note=r["note"])
        for r in mem.list_policy(mission_id) if r["status"] == "pending_operator"
    ]


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class MissionCreate(BaseModel):
    id: str | None = Field(
        default=None,
        description="Stable mission ID. Auto-generated if omitted.",
    )
    objective: str = Field(..., description="What you want to know about the competitor.")
    success_condition: str = Field(
        ...,
        description="Specific, verifiable statement of what constitutes a complete answer.",
    )
    vocabulary_seed: list[str] = Field(default_factory=list)
    excluded_domains: list[str] = Field(default_factory=list)
    novelty_requirement: str = Field(default="medium")
    connector: str = Field(default="fixture", description="brave | perplexity | fixture")


class JobStatus(BaseModel):
    job_id: int
    mission_id: str
    status: str
    queued_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None


class PendingReview(BaseModel):
    version: int
    lever: str
    note: str | None = None


class MissionRow(BaseModel):
    mission_id: str
    objective: str
    success_condition: str
    novelty_requirement: str
    first_run_at: str | None
    last_run_at: str | None
    latest_job: JobStatus | None = None
    pending_review_count: int = 0
    pending_review: list[PendingReview] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Background worker — runs in FastAPI's thread pool
# ---------------------------------------------------------------------------

def _execute_mission(job_id: int, mission: Mission, connector: str) -> None:
    """Called by BackgroundTasks. Opens its own DB connections (thread-safe)."""
    with get_job_db() as db:
        set_job_running(db, job_id, _now())
    try:
        run_mission(mission, connector=connector)          # writes to research-memory.sqlite
        with get_job_db() as db:
            set_job_done(db, job_id, _now())
    except Exception as exc:  # noqa: BLE001
        with get_job_db() as db:
            set_job_failed(db, job_id, _now(), str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/", response_model=JobStatus, status_code=202)
async def create_mission_run(
    payload: MissionCreate,
    background_tasks: BackgroundTasks,
) -> JobStatus:
    """Enqueue a new CI mission run.

    Builds the correct research_os.models.Mission object, persists a job-queue
    row, and hands off execution to a background task.  Returns immediately
    with status='queued'.
    """
    mission_id = payload.id or f"m-{uuid.uuid4().hex[:12]}"

    mission = Mission(
        id=mission_id,
        objective=payload.objective,
        success_condition=payload.success_condition,
        vocabulary_seed=payload.vocabulary_seed,
        excluded_domains=payload.excluded_domains,
        novelty_requirement=payload.novelty_requirement,
    )

    with get_job_db() as db:
        job = create_job(
            db,
            mission_id=mission_id,
            connector=payload.connector,
            queued_at=_now(),
        )

    background_tasks.add_task(_execute_mission, job["job_id"], mission, payload.connector)

    return JobStatus(
        job_id=job["job_id"],
        mission_id=job["mission_id"],
        status=job["status"],
        queued_at=job["queued_at"],
        started_at=job["started_at"],
        finished_at=job["finished_at"],
        error=job["error"],
    )


@router.get("/", response_model=list[MissionRow])
async def list_missions() -> list[MissionRow]:
    """List all missions research_os.memory has ever run."""
    with Memory(_MEMORY_PATH) as mem:
        rows = mem.list_missions()

    with get_job_db() as db:
        result: list[MissionRow] = []
        for r in rows:
            jobs = list_jobs(db, mission_id=r["mission_id"], limit=1)
            latest = None
            if jobs:
                j = jobs[0]
                latest = JobStatus(
                    job_id=j["job_id"],
                    mission_id=j["mission_id"],
                    status=j["status"],
                    queued_at=j["queued_at"],
                    started_at=j["started_at"],
                    finished_at=j["finished_at"],
                    error=j["error"],
                )
            with Memory(_MEMORY_PATH) as mem2:  # fresh connection; `mem` above is closed
                pending = _pending_reviews(mem2, r["mission_id"])
            result.append(MissionRow(
                mission_id=r["mission_id"],
                objective=r["objective"],
                success_condition=r["success_condition"],
                novelty_requirement=r["novelty_requirement"],
                first_run_at=r["first_run_at"],
                last_run_at=r["last_run_at"],
                latest_job=latest,
                pending_review_count=len(pending),
                pending_review=pending,
            ))
    return result


@router.get("/{mission_id}", response_model=MissionRow)
async def get_mission(mission_id: str) -> MissionRow:
    """Single mission with its latest job status."""
    with Memory(_MEMORY_PATH) as mem:
        row = mem.get_mission_row(mission_id)
        if row:
            pending = _pending_reviews(mem, mission_id)

    if not row:
        raise HTTPException(status_code=404, detail="Mission not found in research memory")

    with get_job_db() as db:
        jobs = list_jobs(db, mission_id=mission_id, limit=1)
        latest = None
        if jobs:
            j = jobs[0]
            latest = JobStatus(
                job_id=j["job_id"],
                mission_id=j["mission_id"],
                status=j["status"],
                queued_at=j["queued_at"],
                started_at=j["started_at"],
                finished_at=j["finished_at"],
                error=j["error"],
            )

    return MissionRow(
        mission_id=row["mission_id"],
        objective=row["objective"],
        success_condition=row["success_condition"],
        novelty_requirement=row["novelty_requirement"],
        first_run_at=row["first_run_at"],
        last_run_at=row["last_run_at"],
        latest_job=latest,
        pending_review_count=len(pending),
        pending_review=pending,
    )


@router.get("/{mission_id}/results")
async def get_mission_results(mission_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Top-ranked evidence records for a mission, from research_os.memory."""
    with Memory(_MEMORY_PATH) as mem:
        row = mem.get_mission_row(mission_id)
        if not row:
            raise HTTPException(status_code=404, detail="Mission not found in research memory")
        results = mem.list_results(mission_id, limit=limit)

    return [dict(r) for r in results]


@router.get("/{mission_id}/jobs", response_model=list[JobStatus])
async def get_mission_jobs(mission_id: str) -> list[JobStatus]:
    """Full job history for a mission (newest first)."""
    with get_job_db() as db:
        jobs = list_jobs(db, mission_id=mission_id)
    return [
        JobStatus(
            job_id=j["job_id"],
            mission_id=j["mission_id"],
            status=j["status"],
            queued_at=j["queued_at"],
            started_at=j["started_at"],
            finished_at=j["finished_at"],
            error=j["error"],
        )
        for j in jobs
    ]
