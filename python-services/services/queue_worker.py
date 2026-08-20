"""Background worker that picks up QUEUED jobs.

The frontend creates a job and polls it; nothing tells this service to start.
So the service watches the `jobs` collection itself, claims each QUEUED job
atomically (`findOneAndUpdate` to PROCESSING, so two workers can never take the
same one) and runs the pipeline. The UI's "retry" action simply sets a job back
to QUEUED, which this loop picks up on its next tick.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from models.models import ProcessRequest

logger = logging.getLogger(__name__)

POLL_INTERVAL = float(os.getenv("JOB_POLL_INTERVAL", "3"))
WORKER_ENABLED = os.getenv("JOB_WORKER", "1").lower() not in ("0", "false", "no")
IDLE_BACKOFF = float(os.getenv("JOB_POLL_IDLE_BACKOFF", "10"))


class QueueWorker:
    """Polls for queued work until it is asked to stop."""

    def __init__(self, store: Any, interval: float = POLL_INTERVAL):
        self.store = store
        self.interval = interval
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        self.processed = 0

    def start(self) -> None:
        if not WORKER_ENABLED:
            logger.info("job worker disabled (JOB_WORKER=0)")
            return
        if getattr(self.store, "claim_queued_job", None) is None:
            logger.warning("storage backend cannot claim queued jobs; worker not started")
            return
        self._task = asyncio.create_task(self._loop(), name="queue-worker")
        logger.info("job worker polling every %ss", self.interval)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        idle_ticks = 0
        while not self._stopping.is_set():
            try:
                claimed = await self.store.claim_queued_job()
            except Exception as exc:
                logger.error("worker could not poll for jobs: %s", exc)
                claimed = None

            if claimed is None:
                # nothing waiting: ease off so an idle service is not hammering
                # the database every few seconds
                idle_ticks += 1
                delay = self.interval if idle_ticks < 20 else IDLE_BACKOFF
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                continue

            idle_ticks = 0
            await self._process(claimed)

    async def _process(self, job: dict) -> None:
        from services.pipeline import ProcessingPipeline

        job_id = str(job.get("_id") or job.get("jobId"))
        logger.info("worker claimed job %s", job_id)
        request = ProcessRequest(
            jobId=job_id,
            projectId=str(job["projectId"]) if job.get("projectId") else None,
        )
        try:
            result = await ProcessingPipeline(job_id, request, self.store).run()
            self.processed += 1
            logger.info("worker finished job %s: %s", job_id, result.get("status"))
        except Exception:
            logger.exception("worker crashed on job %s", job_id)
            try:
                await self.store.upsert_job_state(job_id, {
                    "status": "FAILED", "stage": "failed",
                    "error": "the processing worker crashed; see the service logs",
                })
            except Exception:
                logger.exception("could not mark job %s failed", job_id)
