"""The service must speak the frontend's schema, and pick up its own work.

These tests pin the contract the Next.js app reads: `issues` documents in its
vocabulary, `jobs.stage/stats/logs` in its enums, a `corrections` log, and a
worker that claims QUEUED jobs.
"""

import pytest

from models.models import (
    Correction, CorrectionAction, Issue, IssueStatus, IssueType, ProcessRequest, Severity,
)
from services import job_sync

FRONTEND_TYPES = {"MISSING_TEXT", "EXTRA_TEXT", "TEXT_MISMATCH", "FORMATTING",
                  "IMAGE_MISSING", "IMAGE_MISMATCH", "TABLE_STRUCTURE", "ORDER_MISMATCH"}
FRONTEND_SEVERITIES = {"CRITICAL", "MAJOR", "MINOR", "INFO"}
FRONTEND_STATUSES = {"AUTO_FIXED", "PENDING_REVIEW", "APPROVED", "REJECTED"}
FRONTEND_STAGES = {"ANALYZING_PDF", "ANALYZING_HTML", "COMPARING", "CORRECTING", "VERIFYING"}


class TestVocabularyMapping:
    def test_every_engine_type_maps_to_a_type_the_ui_can_label(self):
        for issue_type in IssueType:
            assert job_sync.TYPE_MAP[issue_type] in FRONTEND_TYPES

    def test_severity_mapping_covers_confidence(self):
        high = Issue(type=IssueType.MISSING_IMAGE, severity=Severity.HIGH, confidence=0.9)
        medium = Issue(type=IssueType.ORDER_MISMATCH, severity=Severity.MEDIUM, confidence=0.9)
        low_sure = Issue(type=IssueType.ALIGNMENT, severity=Severity.LOW, confidence=0.9)
        low_unsure = Issue(type=IssueType.EXTRA_TEXT, severity=Severity.LOW, confidence=0.55)

        assert job_sync.ui_severity(high) == "CRITICAL"
        assert job_sync.ui_severity(medium) == "MAJOR"
        assert job_sync.ui_severity(low_sure) == "MINOR"
        assert job_sync.ui_severity(low_unsure) == "INFO"
        assert {job_sync.ui_severity(i) for i in (high, medium, low_sure, low_unsure)} <= FRONTEND_SEVERITIES

    def test_status_mapping_round_trips(self):
        for engine_status, ui_status in job_sync.STATUS_TO_UI.items():
            assert ui_status in FRONTEND_STATUSES
            # a UI status maps back to something the engine can act on
            assert job_sync.STATUS_FROM_UI[ui_status] in IssueStatus

    def test_pipeline_stages_map_into_the_ui_stepper(self):
        for stage, mapped in job_sync.STAGE_MAP.items():
            assert mapped is None or mapped in FRONTEND_STAGES

    def test_issue_document_has_every_field_the_ui_reads(self):
        issue = Issue(
            type=IssueType.MISSING_IMAGE, severity=Severity.HIGH, confidence=0.96,
            status=IssueStatus.AUTO_FIXED, page=2, dom_path="html > body > p",
            description="Figure is missing", suggestion="Insert it",
            evidence={"caption": "Figure 10.2", "src": "old.png"},
        )
        document = job_sync.issue_document(issue, "job1", "proj1")

        assert document["type"] == "IMAGE_MISSING"
        assert document["severity"] == "CRITICAL"
        assert document["status"] == "AUTO_FIXED"
        assert document["message"] == "Figure is missing"
        assert document["suggestion"] == "Insert it"
        assert document["selector"] == "html > body > p"
        assert document["page"] == 2
        assert document["pdfText"] == "Figure 10.2"
        # the engine's own record rides along for later replay
        assert document["engine"]["id"] == issue.id

    def test_correction_document_records_before_and_after(self):
        issue = Issue(type=IssueType.MISSING_ALT_TEXT, severity=Severity.LOW, confidence=0.97,
                      status=IssueStatus.AUTO_FIXED, dom_path="img",
                      evidence={"src": "figure.png"})
        issue.correction = Correction(issue_id=issue.id, action=CorrectionAction.SET_ALT_TEXT,
                                      target_dom_path="img", payload={"alt": "Figure 10.1"})
        document = job_sync.correction_document(issue, "job1", "proj1", "issue-oid")

        assert document["appliedBy"] == "AUTO"
        assert document["status"] == "APPLIED"
        assert document["after"] == "Figure 10.1"
        assert document["selector"] == "img"

    def test_job_document_maps_stage_and_stats(self):
        update = job_sync.job_document({
            "stage": "analyzing-html", "progress": 38, "status": "PROCESSING",
            "issuesFound": 12, "issuesAutoFixed": 9, "issuesNeedingReview": 3,
            "qualityScore": 0.9231,
        })
        assert update["stage"] == "ANALYZING_HTML"
        assert update["stats.issuesFound"] == 12
        assert update["stats.autoFixed"] == 9
        assert update["stats.pendingReview"] == 3
        assert update["stats.qualityScore"] == 92        # the UI shows 0-100

    def test_terminal_states_clear_the_stage(self):
        update = job_sync.job_document({"stage": "completed", "status": "COMPLETED"})
        assert update["stage"] is None


class TestStoredDocuments:
    @pytest.fixture
    async def processed(self):
        import os

        from services.db import MemoryStore
        from services.pipeline import ProcessingPipeline

        here = os.path.dirname(os.path.abspath(__file__))
        store = await MemoryStore().connect()
        await ProcessingPipeline("job-fe", ProcessRequest(
            pdfUrl=os.path.join(here, "fixtures", "chapter.pdf"),
            htmlUrl=os.path.join(here, "fixtures", "chapter.html"),
            projectId="proj-fe",
        ), store).run()
        return store

    async def test_issues_are_stored_in_the_frontend_shape(self, processed):
        stored = processed.issues["job-fe"]
        assert len(stored) == 6
        for document in stored:
            assert document["type"] in FRONTEND_TYPES
            assert document["severity"] in FRONTEND_SEVERITIES
            assert document["status"] in FRONTEND_STATUSES
            assert document["message"]
            assert 0.0 <= document["confidence"] <= 1.0

    async def test_corrections_are_logged_for_applied_fixes(self, processed):
        corrections = processed.corrections["job-fe"]
        assert len(corrections) == 5
        assert all(c["status"] == "APPLIED" for c in corrections)
        assert all(c["appliedBy"] in ("AUTO", "MANUAL") for c in corrections)

    async def test_engine_can_read_its_own_issues_back(self, processed):
        issues = await processed.get_issues("job-fe")
        assert len(issues) == 6
        restored = [Issue.model_validate({k: v for k, v in i.items()
                                          if k not in ("_id", "uiStatus", "autoFixable", "action")})
                    for i in issues]
        assert {i.type for i in restored} & {IssueType.MISSING_IMAGE, IssueType.WATERMARK}

    async def test_a_ui_approval_is_what_the_rebuild_acts_on(self, processed):
        """The UI writes status straight into the issue; the engine must honour it."""
        from services.pipeline import ReviewService

        order_issue = next(i for i in processed.issues["job-fe"]
                           if i["type"] == "ORDER_MISMATCH")
        order_issue["status"] = "APPROVED"          # exactly what PATCH /api/issues/[id] does

        result = await ReviewService("job-fe", processed).rebuild()
        assert result["applied"] == 6               # the five auto-fixes plus the approval


class TestQueueWorker:
    async def test_claims_and_processes_a_queued_job(self):
        import os

        from services.db import MemoryStore
        from services.queue_worker import QueueWorker

        here = os.path.dirname(os.path.abspath(__file__))
        store = await MemoryStore().connect()
        store.documents["d1"] = {"cloudinaryUrl": os.path.join(here, "fixtures", "chapter.pdf")}
        store.documents["d2"] = {"cloudinaryUrl": os.path.join(here, "fixtures", "chapter.html")}
        store.jobs["queued-1"] = {
            "_id": "queued-1", "projectId": "p1", "status": "QUEUED",
            "pdfDocumentId": "d1", "htmlDocumentId": "d2",
        }

        worker = QueueWorker(store)
        claimed = await store.claim_queued_job()
        assert claimed["status"] == "PROCESSING"     # claimed atomically, not left QUEUED
        await worker._process(claimed)

        assert worker.processed == 1
        job = await store.get_job("queued-1")
        assert job["status"] == "COMPLETED"
        assert job["progress"] == 100

    async def test_nothing_queued_returns_nothing(self):
        from services.db import MemoryStore

        store = await MemoryStore().connect()
        assert await store.claim_queued_job() is None

    async def test_a_cancelled_job_stops_mid_run(self):
        import os

        from services.db import MemoryStore
        from services.pipeline import ProcessingPipeline

        here = os.path.dirname(os.path.abspath(__file__))
        store = await MemoryStore().connect()
        store.jobs["cancel-me"] = {"jobId": "cancel-me", "status": "CANCELLED"}

        result = await ProcessingPipeline("cancel-me", ProcessRequest(
            pdfUrl=os.path.join(here, "fixtures", "chapter.pdf"),
            htmlUrl=os.path.join(here, "fixtures", "chapter.html"),
        ), store).run()
        assert result["status"] == "CANCELLED"


class TestIdempotentWrites:
    """A rebuild that changes nothing must not rewrite the issue list.

    Every rebuild re-applies the same corrections, so without this the whole
    list is restamped on each review click — losing when each issue was really
    reviewed, and writing hundreds of documents for nothing.
    """

    @staticmethod
    def _document(status="AUTO_FIXED", applied_at="2026-01-01T00:00:00Z"):
        return {
            "jobId": "j1", "status": status, "message": "m",
            "engine": {"id": "iss_1", "correction": {"action": "SET_ALT_TEXT",
                                                     "applied": True,
                                                     "applied_at": applied_at}},
        }

    def test_reapplying_the_same_fix_is_not_a_change(self):
        from services.db import _content_hash

        first = _content_hash(self._document(applied_at="2026-01-01T00:00:00Z"))
        again = _content_hash(self._document(applied_at="2026-06-30T12:00:00Z"))
        assert first == again

    def test_a_status_change_is_a_change(self):
        from services.db import _content_hash

        assert _content_hash(self._document()) != _content_hash(self._document(status="APPROVED"))

    def test_bookkeeping_fields_do_not_affect_the_digest(self):
        from services.db import _content_hash

        base = self._document()
        stamped = {**base, "updatedAt": "now", "createdAt": "then", "engineHash": "x"}
        assert _content_hash(base) == _content_hash(stamped)

    def test_hashing_does_not_mutate_the_document_being_written(self):
        from services.db import _content_hash

        document = self._document()
        _content_hash(document)
        assert document["engine"]["correction"]["applied_at"] == "2026-01-01T00:00:00Z"


class TestRebuildCoordination:
    """Approving a run of issues must converge, not queue a rebuild per click."""

    @pytest.fixture
    async def processed_store(self):
        import os

        from services.db import MemoryStore
        from services.pipeline import ProcessingPipeline

        here = os.path.dirname(os.path.abspath(__file__))
        store = await MemoryStore().connect()
        await ProcessingPipeline("job-rb", ProcessRequest(
            pdfUrl=os.path.join(here, "fixtures", "chapter.pdf"),
            htmlUrl=os.path.join(here, "fixtures", "chapter.html"),
            projectId="proj-rb",
        ), store).run()
        return store

    async def test_a_single_request_runs_once(self, processed_store):
        from services.rebuild_queue import RebuildCoordinator

        coordinator = RebuildCoordinator(lambda: _ready(processed_store))
        assert await coordinator.request("job-rb") == "started"
        result = await coordinator.wait("job-rb")
        assert result["correctedHtmlUrl"]
        assert not coordinator.is_running("job-rb")

    async def test_requests_during_a_rebuild_are_coalesced(self, processed_store):
        from services.rebuild_queue import RebuildCoordinator

        coordinator = RebuildCoordinator(lambda: _ready(processed_store))
        first = await coordinator.request("job-rb")
        second = await coordinator.request("job-rb")
        third = await coordinator.request("job-rb")

        assert first == "started"
        assert (second, third) == ("coalesced", "coalesced")
        result = await coordinator.wait("job-rb")
        assert result["correctedHtmlUrl"]

    async def test_the_last_decision_wins(self, processed_store):
        """A decision made mid-rebuild is picked up by the follow-up run."""
        from services.rebuild_queue import RebuildCoordinator

        coordinator = RebuildCoordinator(lambda: _ready(processed_store))
        await coordinator.request("job-rb")
        # the reviewer approves something while the first rebuild is in flight
        order_issue = next(i for i in processed_store.issues["job-rb"]
                           if i["type"] == "ORDER_MISMATCH")
        order_issue["status"] = "APPROVED"
        await coordinator.request("job-rb")

        result = await coordinator.wait("job-rb")
        assert result["applied"] == 6      # five auto-fixes plus the late approval

    async def test_a_decision_made_during_a_rebuild_is_not_lost(self, processed_store):
        """The reviewer owns `status`; a rebuild must never write it back.

        A rebuild reads the issue list, takes seconds to run, then saves. A
        decision that lands in that window would be overwritten by the stale
        copy the rebuild started from — the reviewer's click silently vanishes.
        """
        from services.pipeline import ReviewService

        target = next(i for i in processed_store.issues["job-rb"]
                      if i["status"] == "PENDING_REVIEW")

        class ClicksMidRebuild:
            """Wraps the store so the approval lands *after* the rebuild reads."""

            def __init__(self, inner):
                self._inner = inner

            async def get_issues(self, job_id):
                rows = await self._inner.get_issues(job_id)
                target["status"] = "APPROVED"      # the reviewer clicks, right now
                return rows

            def __getattr__(self, name):
                return getattr(self._inner, name)

        await ReviewService("job-rb", ClicksMidRebuild(processed_store)).rebuild()

        stored = {i["_id"]: i for i in processed_store.issues["job-rb"]}
        assert stored[target["_id"]]["status"] == "APPROVED"

    async def test_a_failed_rebuild_is_reported_not_raised(self, processed_store):
        from services.rebuild_queue import RebuildCoordinator

        await processed_store.upsert_job_state("job-rb", {"renderedHtmlUrl": None,
                                                          "htmlUrl": None})
        coordinator = RebuildCoordinator(lambda: _ready(processed_store))
        await coordinator.request("job-rb")
        result = await coordinator.wait("job-rb")
        assert "error" in result
        assert not coordinator.is_running("job-rb")


async def _ready(store):
    return store


class TestRebuildTreatsTheDocumentLikeThePipeline:
    """A rebuild must freeze/reveal exactly as the original run did.

    This was found the painful way: the pipeline published a frozen document
    with working jump-links, and the reviewer's first approval replaced it with
    an unfrozen rebuild whose own scripts discarded every correction.
    """

    async def test_freeze_settings_survive_into_the_rebuild(self):
        import os

        from services.db import MemoryStore
        from services.pipeline import ProcessingPipeline, ReviewService

        here = os.path.dirname(os.path.abspath(__file__))
        store = await MemoryStore().connect()
        await ProcessingPipeline("job-fz", ProcessRequest(
            pdfUrl=os.path.join(here, "fixtures", "chapter.pdf"),
            htmlUrl=os.path.join(here, "fixtures", "chapter.html"),
        ), store).run()

        state = await store.get_job("job-fz")
        # the fixture page is static, so freezing is rightly off — but the
        # decision itself must be recorded for the rebuild to repeat
        assert "freezeScripts" in state
        assert "revealPaths" in state

        # and a rebuild runs without error using those settings
        result = await ReviewService("job-fz", store).rebuild()
        assert result["correctedHtmlUrl"]
