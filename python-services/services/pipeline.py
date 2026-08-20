"""End-to-end job orchestration.

download → analyze PDF → analyze HTML → compare → correct → verify → publish →
persist. Progress is written to MongoDB after every stage so the frontend's
polling shows real movement, and any failure marks the job FAILED with the
reason instead of leaving it stuck in PROCESSING.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from models.models import (
    ComparisonResult, DocumentAnalysis, Issue, IssueStatus, JobStatus, ProcessRequest,
    ProcessingReport,
)
from services.cloudinary_client import CloudinaryClient
from services.comparison_engine import ComparisonEngine
from services.correction_engine import AUTO_FIX_THRESHOLD, CorrectionEngine
from services.html_analyzer import HTMLAnalyzer
from services.pdf_analyzer import PDFAnalyzer
from services.verification_engine import VerificationEngine
from utils.file_utils import (
    DownloadError, cleanup_temp_files, decode_text, download_bytes, download_text,
    save_temp_file,
)

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised when the reviewer cancels a job while it is running."""


MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
_job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProcessingPipeline:
    """Run one job from URLs to a corrected, verified, published document."""

    def __init__(self, job_id: str, request: ProcessRequest, store: Any):
        self.job_id = str(job_id)
        self.request = request
        self.store = store
        self.uploader = CloudinaryClient()
        self.temp_paths: List[str] = []
        self.started = time.monotonic()

    # ---------------------------------------------------------------- progress
    async def _progress(self, stage: str, percent: int, **extra: Any) -> None:
        await self._check_cancelled()
        payload = {"stage": stage, "progress": percent, "status": JobStatus.PROCESSING.value}
        payload.update(extra)
        try:
            await self.store.upsert_job_state(self.job_id, payload)
        except Exception as exc:                 # never fail a job over a status write
            logger.warning("job %s: could not persist progress (%s)", self.job_id, exc)
        logger.info("job %s: %s (%s%%)", self.job_id, stage, percent)

    async def _check_cancelled(self) -> None:
        """Stop between stages if the job was cancelled from the UI."""
        checker = getattr(self.store, "job_status", None)
        if checker is None:
            return
        try:
            if await checker(self.job_id) == "CANCELLED":
                raise JobCancelled("cancelled by the reviewer")
        except JobCancelled:
            raise
        except Exception as exc:
            logger.debug("job %s: cancellation check failed (%s)", self.job_id, exc)

    # --------------------------------------------------------------------- run
    async def run(self) -> Dict[str, Any]:
        async with _job_semaphore:
            try:
                return await self._run()
            except JobCancelled:
                logger.info("job %s cancelled", self.job_id)
                await self.store.upsert_job_state(self.job_id, {
                    "status": "CANCELLED",
                    "stage": "cancelled",
                    "completedAt": _now(),
                })
                return {"jobId": self.job_id, "status": "CANCELLED"}
            except Exception as exc:
                logger.exception("job %s failed", self.job_id)
                await self.store.upsert_job_state(self.job_id, {
                    "status": JobStatus.FAILED.value,
                    "stage": "failed",
                    "error": str(exc),
                    "completedAt": _now(),
                })
                return {"jobId": self.job_id, "status": JobStatus.FAILED.value, "error": str(exc)}
            finally:
                cleanup_temp_files(self.temp_paths)

    async def _run(self) -> Dict[str, Any]:
        # never start work the reviewer has already called off
        await self._check_cancelled()

        # 1. resolve the two source documents ------------------------------- 5%
        await self.store.upsert_job_state(self.job_id, {
            "status": JobStatus.PROCESSING.value, "stage": "resolving", "progress": 2,
            "startedAt": _now(), "error": None,
            "projectId": self.request.projectId,
        })
        pdf_url, html_url = await self.resolve_sources()
        await self._progress("downloading", 8, pdfUrl=pdf_url, htmlUrl=html_url)

        # 2. download ------------------------------------------------------ 15%
        pdf_bytes, html_bytes = await asyncio.gather(
            self.fetch_source(pdf_url, "PDF"), self.fetch_source(html_url, "HTML"),
        )
        pdf_path = save_temp_file(pdf_bytes, suffix=".pdf")
        html_text = decode_text(html_bytes)
        self.temp_paths.append(pdf_path)

        # 3. analyze the PDF (blocking work, kept off the event loop) ------- 35%
        await self._progress("analyzing-pdf", 18)
        pdf_analyzer = PDFAnalyzer(path=pdf_path, source=pdf_url)
        pdf_analysis: DocumentAnalysis = await asyncio.to_thread(pdf_analyzer.analyze)
        await self._progress("analyzing-html", 38, pdfPages=pdf_analysis.metadata.page_count)

        # 4. analyze the HTML ---------------------------------------------- 55%
        # Render from the markup we already fetched rather than navigating to the
        # source URL: Cloudinary serves `raw` assets as an attachment, which
        # Chromium treats as a download instead of a page. `base_url` keeps
        # relative image paths resolving against the original location.
        html_analyzer = HTMLAnalyzer(
            html=html_text, base_url=html_url, render_js=self.request.renderJs,
        )
        html_analysis = await html_analyzer.analyze()
        pixels = {**pdf_analyzer.pixel_cache, **html_analyzer.pixel_cache}

        # 5. compare and generate issues ----------------------------------- 65%
        await self._progress("comparing", 58)
        comparison_engine = ComparisonEngine(pdf_analysis, html_analysis, pixels)
        comparison: ComparisonResult = comparison_engine.generate_issues()
        issues: List[Issue] = comparison.issues
        await self._progress("correcting", 68, issuesFound=len(issues))

        # 6. apply high-confidence corrections ----------------------------- 78%
        # Store the exact markup the DOM paths were computed against, so a fix
        # approved later patches the same document rather than a reparsed one.
        source_html = html_analyzer.rendered_html or html_analyzer.raw_html
        source_url = await publish_html(source_html, f"source-{self.job_id}", self.uploader)
        freeze = (html_analyzer.js_generated
                  and os.getenv("FREEZE_RENDERED_HTML", "1") != "0")
        await self.store.upsert_job_state(self.job_id, {
            "renderedHtmlUrl": source_url,
            "autoFixThreshold": self.request.autoFixThreshold,
            "stripBaseTag": html_analyzer.injected_base,
            # a rebuild must treat the document exactly as the pipeline did —
            # skipping the freeze would republish a copy whose own scripts
            # discard every correction the reviewer just approved
            "freezeScripts": freeze,
            "revealPaths": html_analyzer.hidden_content,
            "unstickPaths": html_analyzer.pinned_elements,
        })
        corrector = CorrectionEngine(
            source_html, pdf_analysis, html_analysis, uploader=self.uploader,
            auto_fix_threshold=self.request.autoFixThreshold,
            job_id=self.job_id, strip_base_tag=html_analyzer.injected_base,
            freeze_scripts=freeze,
            reveal_paths=html_analyzer.hidden_content,
            unstick_paths=html_analyzer.pinned_elements,
        )
        await corrector.prepare_figures(issues)      # host figures for later approvals
        if self.request.autoFix:
            patch = await corrector.patch_html(issues)
        else:
            patch = {"html": corrector.generate_corrected_html(), "applied": [],
                     "skipped": [(issue, "auto-fix disabled") for issue in issues],
                     "warnings": corrector.warnings}
        corrected_html = patch["html"]
        auto_fixed = len(patch["applied"])

        # 7. verify -------------------------------------------------------- 88%
        await self._progress("verifying", 80, issuesAutoFixed=auto_fixed)
        verifier = VerificationEngine(
            pdf_analysis, corrected_html, comparison, issues,
            base_url=html_url, render_js=self.request.renderJs,
            pdf_pixels=pdf_analyzer.pixel_cache,
        )
        verification = await verifier.verify_corrections()

        # 8. publish the corrected HTML, plus a fresh rendition built straight
        #    from the PDF — for enriched HTML that never mirrored the PDF,
        #    patching has a ceiling and this is the usable deliverable ------- 92%
        await self._progress("publishing", 90)
        corrected_url = await self.publish(corrected_html)
        generated_url = await self.generate_from_pdf(
            pdf_analysis,
            source_html=source_html,
            comparison_engine=comparison_engine,
            panel_paths=html_analyzer.hidden_content,
            issues=issues,
            region_renderer=pdf_analyzer.render_region,
        )

        # 9. report and persistence --------------------------------------- 100%
        elapsed_ms = int((time.monotonic() - self.started) * 1000)
        report: ProcessingReport = verifier.generate_report(
            job_id=self.job_id,
            project_id=self.request.projectId,
            verification=verification,
            corrected_html_url=corrected_url,
            metrics={
                "processing_ms": elapsed_ms,
                "auto_fix_threshold": self.request.autoFixThreshold,
                "js_rendered": html_analysis.stats.get("rendered", False),
                "cloudinary_enabled": self.uploader.enabled,
                "warnings": (pdf_analysis.warnings + html_analysis.warnings
                             + patch.get("warnings", []))[:20],
            },
        )
        await self.persist(issues, report, patch["skipped"])

        pdf_analyzer.close()
        completion = {
            "status": JobStatus.COMPLETED.value,
            "stage": "completed",
            "progress": 100,
            "completedAt": _now(),
            "correctedHtmlUrl": corrected_url,
            "generatedHtmlUrl": generated_url,
            "issuesFound": len(issues),
            "issuesAutoFixed": auto_fixed,
            "issuesNeedingReview": report.needs_review,
            "qualityScore": report.quality_score,
            "verificationPassed": verification.passed,
            "durationMs": elapsed_ms,
            "error": None,
        }
        await self.store.upsert_job_state(self.job_id, completion)
        logger.info("job %s completed in %sms: %s issues, %s auto-fixed, quality %.2f",
                    self.job_id, elapsed_ms, len(issues), auto_fixed, report.quality_score)
        return {"jobId": self.job_id, **completion, "reportSummary": report.summary}

    # ----------------------------------------------------------------- helpers
    async def resolve_sources(self) -> Tuple[str, str]:
        """URLs from the request, or from the job's linked documents in MongoDB."""
        pdf_url, html_url = self.request.pdfUrl, self.request.htmlUrl
        if pdf_url and html_url:
            return pdf_url, html_url

        job = await self.store.get_job(self.job_id)
        if not job:
            raise ValueError(f"job {self.job_id} not found and no document URLs were supplied")
        if not self.request.projectId:
            self.request.projectId = str(job.get("projectId") or "") or None

        if not pdf_url and job.get("pdfDocumentId"):
            document = await self.store.get_document(str(job["pdfDocumentId"]))
            pdf_url = (document or {}).get("cloudinaryUrl")
        if not html_url and job.get("htmlDocumentId"):
            document = await self.store.get_document(str(job["htmlDocumentId"]))
            html_url = (document or {}).get("cloudinaryUrl")
        if not (pdf_url and html_url):
            raise ValueError("could not resolve both the PDF and HTML source URLs for this job")
        return pdf_url, html_url

    async def fetch_source(self, url: str, label: str) -> bytes:
        """Download a source document, falling back to authenticated access.

        Cloudinary blocks PDF delivery on new accounts, so the upload succeeds
        and the delivery URL then answers 401. Rather than failing the job, the
        Admin API is used to fetch the same asset.
        """
        try:
            content, _ = await download_bytes(url)
            return content
        except DownloadError as exc:
            if exc.status_code not in (401, 403):
                raise
            logger.warning("job %s: %s delivery blocked (HTTP %s); trying the Admin API",
                           self.job_id, label, exc.status_code)
            fetched = await self.uploader.fetch_restricted(url)
            if fetched is None:
                raise DownloadError(
                    f"{label} could not be downloaded: Cloudinary returned "
                    f"{exc.status_code}. Enable 'PDF and ZIP files delivery' under "
                    f"Settings → Security in the Cloudinary console, or check the "
                    f"API credentials in .env.",
                    exc.status_code,
                ) from exc
            data, _name = fetched
            return data

    async def publish(self, html: str) -> Optional[str]:
        return await publish_html(html, self.job_id, self.uploader)

    async def generate_from_pdf(self, pdf_analysis, source_html=None,
                                comparison_engine=None,
                                panel_paths=None, issues=None,
                                region_renderer=None) -> Optional[str]:
        """Build and publish the complete rendition.

        Preferred: merge the PDF's missing content into the uploaded HTML's own
        template — the design is usually right, only content is missing. When
        there is no template to merge into (or merging fails), fall back to a
        standalone rendition generated purely from the PDF.
        """
        html: Optional[str] = None
        if source_html and comparison_engine is not None:
            try:
                from services.html_merger import HTMLMerger

                merger = HTMLMerger(source_html, pdf_analysis, comparison_engine,
                                    uploader=self.uploader, job_id=self.job_id,
                                    panel_paths=panel_paths, issues=issues,
                                    region_renderer=region_renderer)
                html = await merger.merge()
            except Exception:
                logger.exception("job %s: template merge failed; falling back to "
                                 "pure generation", self.job_id)
                html = None
        if html is None:
            from services.html_generator import HTMLGenerator

            try:
                generator = HTMLGenerator(pdf_analysis, uploader=self.uploader,
                                          job_id=self.job_id)
                html = await generator.generate()
            except Exception:      # an add-on must never fail the job
                logger.exception("job %s: PDF-to-HTML generation failed", self.job_id)
                return None
        result = await self.uploader.upload_html(
            html, public_id=f"generated-{self.job_id}", subfolder="generated",
        )
        if result and result.get("url"):
            return result["url"]
        from utils.file_utils import write_text_file

        path = write_text_file(html, suffix=f"-generated-{self.job_id}.html")
        return f"file://{path}"

    async def persist(self, issues: Iterable[Issue], report: ProcessingReport,
                      skipped: Iterable[Tuple[Issue, str]]) -> None:
        """Write issues, corrections and the report.

        Issues and corrections go out in the frontend's schema (the UI reads
        them directly); the engine's full report stays in its own collection.
        """
        from services.job_sync import correction_document, issue_document

        issues = list(issues)
        reasons = {issue.id: reason for issue, reason in skipped}
        documents = []
        for issue in issues:
            document = issue_document(issue, self.job_id, self.request.projectId)
            if issue.id in reasons:
                document["engine"]["skipReason"] = reasons[issue.id]
            documents.append(document)
        await self.store.save_issues(self.job_id, documents)

        applied = [i for i in issues
                   if i.correction and i.correction.applied]
        if applied:
            issue_ids = await self.store.issue_object_ids(self.job_id)
            await self.store.save_corrections(self.job_id, [
                correction_document(issue, self.job_id, self.request.projectId,
                                    issue_ids.get(issue.id))
                for issue in applied
            ])
        else:
            await self.store.save_corrections(self.job_id, [])

        await self.store.save_report(self.job_id, report.model_dump(mode="json"))


class ReviewService:
    """Apply a reviewer's approve/reject decisions and republish the HTML."""

    def __init__(self, job_id: str, store: Any):
        self.job_id = str(job_id)
        self.store = store
        self.uploader = CloudinaryClient()

    async def decide(self, issue_id: str, approved: bool, note: Optional[str] = None,
                     reapply: bool = True) -> Dict[str, Any]:
        stored = await self.store.get_issues(self.job_id)
        target = next((i for i in stored if i.get("id") == issue_id), None)
        if target is None:
            raise KeyError(f"issue {issue_id} not found for job {self.job_id}")

        status = IssueStatus.APPROVED.value if approved else IssueStatus.REJECTED.value
        await self.store.update_issue(self.job_id, issue_id, {
            "status": status,
            "reviewNote": note,
            "reviewedAt": _now().isoformat(),
        })

        result: Dict[str, Any] = {"jobId": self.job_id, "issueId": issue_id, "status": status}
        if reapply:
            result.update(await self.rebuild())
        return result

    async def rebuild(self) -> Dict[str, Any]:
        """Rebuild the corrected HTML from the current approve/reject decisions.

        Rebuilding from the original document (rather than patching the previous
        output) means a rejection genuinely undoes its fix.
        """
        job = await self.store.get_job(self.job_id) or {}
        html_url = job.get("renderedHtmlUrl") or job.get("htmlUrl")
        if not html_url:
            raise ValueError("original HTML URL is unknown for this job; cannot rebuild")

        stored = await self.store.get_issues(self.job_id)
        issues = [Issue.model_validate(_strip_mongo(item)) for item in stored]
        approved = [i.id for i in issues if i.status == IssueStatus.APPROVED]
        rejected = [i.id for i in issues if i.status == IssueStatus.REJECTED]

        html = await download_text(html_url)
        corrector = CorrectionEngine(
            html, None, None, uploader=self.uploader,
            auto_fix_threshold=float(job.get("autoFixThreshold") or AUTO_FIX_THRESHOLD),
            job_id=self.job_id, strip_base_tag=bool(job.get("stripBaseTag")),
            freeze_scripts=bool(job.get("freezeScripts")),
            reveal_paths=job.get("revealPaths") or [],
            unstick_paths=job.get("unstickPaths") or [],
        )
        patch = await corrector.patch_html(issues, approved=approved, rejected=rejected)
        corrected_url = await publish_html(patch["html"], self.job_id, self.uploader)

        from services.job_sync import correction_document, issue_document

        project_id = job.get("projectId")
        # The reviewer owns `status`: a decision made while this rebuild was in
        # flight would otherwise be overwritten by the stale copy we started
        # from. Everything else about the issue is ours to update.
        documents = []
        for issue in issues:
            document = issue_document(issue, self.job_id, project_id)
            document.pop("status", None)
            documents.append(document)
        await self.store.save_issues(self.job_id, documents)
        applied = [i for i in issues if i.correction and i.correction.applied]
        issue_ids = await self.store.issue_object_ids(self.job_id)
        await self.store.save_corrections(self.job_id, [
            correction_document(issue, self.job_id, project_id, issue_ids.get(issue.id))
            for issue in applied
        ])
        await self.store.upsert_job_state(self.job_id, {
            "correctedHtmlUrl": corrected_url,
            "issuesAutoFixed": len(patch["applied"]),
            "rebuiltAt": _now(),
        })
        return {
            "correctedHtmlUrl": corrected_url,
            "applied": len(patch["applied"]),
            "skipped": len(patch["skipped"]),
        }


async def publish_html(html: str, job_id: str,
                       uploader: Optional[CloudinaryClient] = None) -> Optional[str]:
    """Upload corrected HTML to Cloudinary; fall back to a local file when offline."""
    uploader = uploader or CloudinaryClient()
    result = await uploader.upload_html(html, public_id=f"corrected-{job_id}")
    if result and result.get("url"):
        return result["url"]
    from utils.file_utils import write_text_file

    path = write_text_file(html, suffix=f"-corrected-{job_id}.html")
    logger.warning("job %s: corrected HTML kept locally at %s", job_id, path)
    return f"file://{path}"


_PERSISTENCE_ONLY = (
    "_id", "jobId", "projectId", "skipReason", "reviewNote", "reviewedAt",
    "uiStatus", "autoFixable", "action", "createdAt", "updatedAt",
)


def _strip_mongo(document: Dict[str, Any]) -> Dict[str, Any]:
    """Drop persistence-only keys before validating back into a model."""
    return {k: v for k, v in document.items() if k not in _PERSISTENCE_ONLY}
