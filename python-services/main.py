"""Document Correction Platform — Phase 2 API.

Analyzes a PDF and its HTML rendition, reports every difference, applies the
corrections it is confident about, verifies the result and publishes the
corrected document.

Run with:  uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("main")

from models.models import (  # noqa: E402  (import after logging/env setup)
    IssueDecision, JobStatus, ProcessRequest, ProcessResponse,
)
from services.cloudinary_client import is_configured as cloudinary_configured  # noqa: E402
from services.db import close_store, get_store  # noqa: E402
from services.pipeline import ProcessingPipeline, ReviewService  # noqa: E402
from services.queue_worker import QueueWorker  # noqa: E402
from services.rebuild_queue import RebuildCoordinator  # noqa: E402

VERSION = "0.2.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = await get_store()
    logger.info("storage backend: %s | cloudinary: %s",
                store.kind, "configured" if cloudinary_configured() else "disabled")
    worker = QueueWorker(store)
    worker.start()
    app.state.worker = worker
    app.state.rebuilds = RebuildCoordinator(get_store)
    yield
    await worker.stop()
    await app.state.rebuilds.stop()
    await close_store()


app = FastAPI(
    title="Document Correction API",
    description="Phase 2: PDF/HTML analysis, comparison, correction and verification",
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# jobs currently running in this process, so a double-submit cannot start twice
_running: Dict[str, asyncio.Task] = {}


class HealthResponse(BaseModel):
    status: str
    version: str
    storage: str
    cloudinary: bool
    activeJobs: int
    worker: bool
    jobsProcessed: int


class IssueListResponse(BaseModel):
    jobId: str
    total: int
    counts: Dict[str, int]
    issues: List[Dict[str, Any]]


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    store = await get_store()
    worker = getattr(app.state, "worker", None)
    return HealthResponse(
        status="ok" if await store.ping() else "degraded",
        version=VERSION,
        storage=store.kind,
        cloudinary=cloudinary_configured(),
        activeJobs=len(_running),
        worker=bool(worker and worker._task and not worker._task.done()),
        jobsProcessed=worker.processed if worker else 0,
    )


# --------------------------------------------------------------------------- #
# Processing
# --------------------------------------------------------------------------- #
@app.post("/process/{jobId}", response_model=ProcessResponse, status_code=202)
async def process_documents(jobId: str, request: Optional[ProcessRequest] = None,
                            wait: bool = Query(False, description="run inline instead of in the background"),
                            background_tasks: BackgroundTasks = None) -> ProcessResponse:
    """Start the full pipeline for a job.

    URLs may be supplied in the body; anything omitted is resolved from the
    job's linked documents in MongoDB. Returns immediately with 202 unless
    ``?wait=true`` is passed (useful for scripts and tests).
    """
    request = request or ProcessRequest()
    request.jobId = jobId
    store = await get_store()

    if jobId in _running and not _running[jobId].done():
        raise HTTPException(status_code=409, detail=f"job {jobId} is already processing")

    pipeline = ProcessingPipeline(jobId, request, store)

    if wait:
        result = await pipeline.run()
        if result.get("status") == JobStatus.FAILED.value:
            raise HTTPException(status_code=500, detail=result.get("error", "processing failed"))
        return ProcessResponse(
            jobId=jobId, status=JobStatus(result["status"]),
            progress=result.get("progress", 100),
            message=f"{result.get('issuesFound', 0)} issue(s) found, "
                    f"{result.get('issuesAutoFixed', 0)} auto-fixed",
        )

    await store.upsert_job_state(jobId, {
        "status": JobStatus.QUEUED.value, "stage": "queued", "progress": 0, "error": None,
    })

    async def _run() -> None:
        try:
            await pipeline.run()
        finally:
            _running.pop(jobId, None)

    _running[jobId] = asyncio.create_task(_run())
    return ProcessResponse(
        jobId=jobId, status=JobStatus.QUEUED, progress=0,
        message="processing started",
    )


@app.post("/process", response_model=ProcessResponse, status_code=202)
async def process_documents_legacy(request: ProcessRequest,
                                   wait: bool = Query(False)) -> ProcessResponse:
    """Phase 1 compatible entry point that takes the job id in the body."""
    if not request.jobId:
        raise HTTPException(status_code=400, detail="jobId is required")
    return await process_documents(request.jobId, request, wait=wait)


# --------------------------------------------------------------------------- #
# Job status, issues and reports
# --------------------------------------------------------------------------- #
@app.get("/jobs/{jobId}")
async def get_job_status(jobId: str) -> Dict[str, Any]:
    store = await get_store()
    job = await store.get_job(jobId)
    if not job:
        raise HTTPException(status_code=404, detail=f"job {jobId} not found")
    job["jobId"] = jobId
    job.setdefault("status", JobStatus.QUEUED.value)
    job.setdefault("progress", 0)
    job["isRunning"] = jobId in _running and not _running[jobId].done()
    return job


@app.get("/jobs/{jobId}/issues", response_model=IssueListResponse)
async def get_job_issues(jobId: str,
                         severity: Optional[str] = Query(None, description="HIGH | MEDIUM | LOW"),
                         status: Optional[str] = Query(None, description="OPEN | AUTO_FIXED | ..."),
                         type: Optional[str] = Query(None, description="issue type filter"),
                         limit: int = Query(500, ge=1, le=2000)) -> IssueListResponse:
    store = await get_store()
    issues = await store.get_issues(jobId)
    if severity:
        issues = [i for i in issues if i.get("severity") == severity.upper()]
    if status:
        issues = [i for i in issues if i.get("status") == status.upper()]
    if type:
        issues = [i for i in issues if i.get("type") == type.upper()]

    counts: Dict[str, int] = {}
    for issue in issues:
        counts[issue.get("severity", "UNKNOWN")] = counts.get(issue.get("severity", "UNKNOWN"), 0) + 1
        key = f"status:{issue.get('status', 'OPEN')}"
        counts[key] = counts.get(key, 0) + 1
    return IssueListResponse(
        jobId=jobId, total=len(issues), counts=counts, issues=issues[:limit],
    )


@app.get("/jobs/{jobId}/report")
async def get_job_report(jobId: str) -> Dict[str, Any]:
    store = await get_store()
    report = await store.get_report(jobId)
    if not report:
        raise HTTPException(
            status_code=404,
            detail=f"no report for job {jobId} yet — has processing finished?",
        )
    return report


@app.post("/jobs/{jobId}/approve-issue")
async def approve_issue(jobId: str, decision: IssueDecision) -> Dict[str, Any]:
    """Approve a correction: apply it and republish the corrected HTML."""
    return await _decide(jobId, decision, approved=True)


@app.post("/jobs/{jobId}/reject-issue")
async def reject_issue(jobId: str, decision: IssueDecision) -> Dict[str, Any]:
    """Reject a correction: undo it if it was applied and republish."""
    return await _decide(jobId, decision, approved=False)


async def _decide(jobId: str, decision: IssueDecision, approved: bool) -> Dict[str, Any]:
    store = await get_store()
    service = ReviewService(jobId, store)
    try:
        return await service.decide(
            decision.issueId, approved=approved, note=decision.note,
            reapply=decision.reapply,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/jobs/{jobId}/rebuild")
async def rebuild_corrected_html(
    jobId: str,
    wait: bool = Query(False, description="block until the rebuild finishes"),
) -> Dict[str, Any]:
    """Re-apply every current decision and republish the corrected document.

    Called by the frontend after a reviewer approves or rejects an issue. It
    returns immediately: rebuilds are coalesced, so a reviewer working through a
    list does not queue one run per click.
    """
    store = await get_store()
    if await store.get_job(jobId) is None:
        raise HTTPException(status_code=404, detail=f"job {jobId} not found")

    coordinator: RebuildCoordinator = app.state.rebuilds
    state = await coordinator.request(jobId)
    if not wait:
        return {"jobId": jobId, "rebuild": state}

    result = await coordinator.wait(jobId) or {}
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {"jobId": jobId, "rebuild": "completed", **result}


@app.delete("/jobs/{jobId}")
async def cancel_job(jobId: str) -> Dict[str, Any]:
    """Cancel a job that is still running in this process."""
    task = _running.get(jobId)
    if task is None or task.done():
        raise HTTPException(status_code=404, detail=f"job {jobId} is not running")
    task.cancel()
    store = await get_store()
    await store.upsert_job_state(jobId, {
        "status": JobStatus.FAILED.value, "stage": "cancelled", "error": "cancelled by request",
    })
    return {"jobId": jobId, "status": "CANCELLED"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=bool(os.getenv("RELOAD")),
    )
