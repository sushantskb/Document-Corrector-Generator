"""Coalescing scheduler for corrected-document rebuilds.

A reviewer works through the issue list one decision at a time, and every
decision invalidates the corrected document. Rebuilding synchronously would make
each click wait on a download, a re-patch and an upload; rebuilding once per
click would run the same job a dozen times over.

So rebuilds are asynchronous and coalesced: a request while one is already
running does not queue a second run, it marks the in-flight one as stale, and a
single extra rebuild happens after it finishes. However many decisions a
reviewer makes, the document converges on their latest set with at most one
rebuild outstanding.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


class RebuildCoordinator:
    """One in-flight rebuild per job, plus at most one queued follow-up."""

    def __init__(self, store_provider: Callable[[], Any]):
        self._store_provider = store_provider
        self._tasks: Dict[str, asyncio.Task] = {}
        self._stale: Set[str] = set()
        self._results: Dict[str, Dict[str, Any]] = {}

    def is_running(self, job_id: str) -> bool:
        task = self._tasks.get(str(job_id))
        return task is not None and not task.done()

    async def request(self, job_id: str) -> str:
        """Ask for a rebuild. Returns 'started' or 'coalesced'."""
        job_id = str(job_id)
        if self.is_running(job_id):
            self._stale.add(job_id)
            logger.info("rebuild for job %s coalesced into the running one", job_id)
            return "coalesced"
        self._tasks[job_id] = asyncio.create_task(
            self._run(job_id), name=f"rebuild-{job_id}"
        )
        return "started"

    async def _run(self, job_id: str) -> None:
        from services.pipeline import ReviewService

        try:
            while True:
                self._stale.discard(job_id)
                store = await self._store_provider()
                try:
                    result = await ReviewService(job_id, store).rebuild()
                    self._results[job_id] = result
                    await store.upsert_job_state(job_id, {
                        "correctedHtmlUrl": result.get("correctedHtmlUrl"),
                        "logMessage": (f"Corrected document rebuilt: "
                                       f"{result.get('applied', 0)} correction(s) applied"),
                    })
                    logger.info("job %s rebuilt: %s applied", job_id, result.get("applied"))
                except Exception as exc:
                    logger.exception("rebuild failed for job %s", job_id)
                    self._results[job_id] = {"error": str(exc)}
                    try:
                        await store.upsert_job_state(job_id, {
                            "logMessage": f"Rebuild failed: {exc}",
                            "logLevel": "ERROR",
                        })
                    except Exception:
                        logger.debug("could not log the rebuild failure for %s", job_id)
                    break
                if job_id not in self._stale:
                    break
                # decisions arrived while we were rebuilding — converge once more
                logger.info("job %s changed during the rebuild; running once more", job_id)
        finally:
            self._tasks.pop(job_id, None)
            self._stale.discard(job_id)

    async def wait(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Await the current rebuild (used by `?wait=true` and by tests)."""
        task = self._tasks.get(str(job_id))
        if task is not None:
            await asyncio.shield(task)
        return self._results.get(str(job_id))

    async def stop(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
