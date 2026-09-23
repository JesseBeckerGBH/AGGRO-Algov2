# src/api/main.py
"""AGGRO FastAPI application entry point.

Starts the thin API wrapper around the research_os engine.

Run locally::

    uvicorn research_os.api.main:app --reload
    # or from the repo root with the venv active:
    .venv/Scripts/uvicorn src.api.main:app --reload --app-dir .
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import briefs, missions

app = FastAPI(
    title="AGGRO Competitive Intelligence API",
    version="0.1.0",
    description=(
        "Thin REST wrapper around the research_os engine. "
        "Mission content and briefs are stored in research-memory.sqlite "
        "managed by research_os.memory.Memory. "
        "This API owns only async job-queue lifecycle state."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000",
                   "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(missions.router, prefix="/api/missions", tags=["Missions"])
app.include_router(briefs.router,   prefix="/api/briefs",   tags=["Briefs"])


@app.get("/api/health", tags=["Health"])
async def health() -> dict:
    return {"status": "ok", "version": app.version}
