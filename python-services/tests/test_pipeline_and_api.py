"""End-to-end pipeline and HTTP API tests, run against the in-memory store."""

import os

import pytest

from models.models import JobStatus, ProcessRequest

PDF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "chapter.pdf")
HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "chapter.html")


@pytest.fixture
async def store():
    from services.db import MemoryStore

    return await MemoryStore().connect()


class TestPipeline:
    async def test_complete_run(self, store):
        from services.pipeline import ProcessingPipeline

        request = ProcessRequest(pdfUrl=PDF, htmlUrl=HTML, projectId="p1")
        result = await ProcessingPipeline("job-1", request, store).run()

        assert result["status"] == JobStatus.COMPLETED.value
        assert result["progress"] == 100
        assert result["issuesFound"] == 6
        assert result["issuesAutoFixed"] == 5
        assert result["qualityScore"] > 0.9
        assert result["correctedHtmlUrl"]

    async def test_persists_issues_and_report(self, store):
        from services.pipeline import ProcessingPipeline

        request = ProcessRequest(pdfUrl=PDF, htmlUrl=HTML)
        await ProcessingPipeline("job-2", request, store).run()

        issues = await store.get_issues("job-2")
        report = await store.get_report("job-2")
        assert len(issues) == 6
        assert {i["status"] for i in issues} == {"AUTO_FIXED", "OPEN"}
        assert report["summary"]["verification_passed"] is False
        assert report["metrics"]["processing_ms"] > 0
        assert len(report["checklist"]) == 9

    async def test_auto_fix_can_be_disabled(self, store):
        from services.pipeline import ProcessingPipeline

        request = ProcessRequest(pdfUrl=PDF, htmlUrl=HTML, autoFix=False)
        result = await ProcessingPipeline("job-3", request, store).run()
        assert result["issuesFound"] == 6
        assert result["issuesAutoFixed"] == 0

    async def test_a_failure_marks_the_job_failed(self, store):
        from services.pipeline import ProcessingPipeline

        request = ProcessRequest(pdfUrl="/nonexistent/file.pdf", htmlUrl=HTML)
        result = await ProcessingPipeline("job-4", request, store).run()

        assert result["status"] == JobStatus.FAILED.value
        job = await store.get_job("job-4")
        assert job["status"] == JobStatus.FAILED.value
        assert job["error"]

    async def test_missing_urls_are_resolved_from_the_job_documents(self, store):
        from services.pipeline import ProcessingPipeline

        store.documents["doc-pdf"] = {"cloudinaryUrl": PDF}
        store.documents["doc-html"] = {"cloudinaryUrl": HTML}
        store.jobs["job-5"] = {
            "jobId": "job-5", "projectId": "p9",
            "pdfDocumentId": "doc-pdf", "htmlDocumentId": "doc-html",
        }
        result = await ProcessingPipeline("job-5", ProcessRequest(), store).run()
        assert result["status"] == JobStatus.COMPLETED.value

    async def test_review_decisions_rebuild_the_document(self, store):
        from bs4 import BeautifulSoup

        from services.pipeline import ProcessingPipeline, ReviewService

        await ProcessingPipeline(
            "job-6", ProcessRequest(pdfUrl=PDF, htmlUrl=HTML), store
        ).run()
        issues = await store.get_issues("job-6")
        order_issue = next(i for i in issues if i["type"] == "ORDER_MISMATCH")
        watermark = next(i for i in issues if i["type"] == "WATERMARK")

        service = ReviewService("job-6", store)
        await service.decide(order_issue["id"], approved=True)
        result = await service.decide(watermark["id"], approved=False)

        path = result["correctedHtmlUrl"].replace("file://", "")
        with open(path, encoding="utf-8") as fh:
            soup = BeautifulSoup(fh.read(), "lxml")
        headings = [h.get_text(strip=True) for h in soup.find_all("h2")]
        assert headings.index("10.2 Spherical Mirrors") < headings.index("10.3 Refraction of Light")
        assert "do not copy" in soup.get_text().lower()      # rejection undid the removal

        statuses = {i["type"]: i["status"] for i in await store.get_issues("job-6")}
        assert statuses["ORDER_MISMATCH"] == "APPROVED"
        assert statuses["WATERMARK"] == "REJECTED"


@pytest.fixture
async def client(store, monkeypatch):
    """TestClient wired to the in-memory store so no external service is touched."""
    import services.db as db

    db._store = store
    import main

    from fastapi.testclient import TestClient

    with TestClient(main.app) as test_client:
        yield test_client
    db._store = None


class TestApi:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["version"] == "0.2.0"
        assert body["storage"] == "memory"

    def test_process_and_read_back(self, client):
        response = client.post("/process/api-1?wait=true",
                               json={"pdfUrl": PDF, "htmlUrl": HTML, "projectId": "p1"})
        assert response.status_code == 202
        assert response.json()["status"] == "COMPLETED"

        job = client.get("/jobs/api-1").json()
        assert job["progress"] == 100
        assert job["issuesFound"] == 6

        issues = client.get("/jobs/api-1/issues").json()
        assert issues["total"] == 6
        assert issues["counts"]["HIGH"] == 3

        report = client.get("/jobs/api-1/report").json()
        assert report["auto_fixed"] == 5
        assert report["quality_score"] > 0.9

    def test_issue_filters(self, client):
        client.post("/process/api-2?wait=true", json={"pdfUrl": PDF, "htmlUrl": HTML})
        assert client.get("/jobs/api-2/issues?severity=HIGH").json()["total"] == 3
        assert client.get("/jobs/api-2/issues?status=OPEN").json()["total"] == 1
        assert client.get("/jobs/api-2/issues?type=WATERMARK").json()["total"] == 1

    def test_approve_and_reject(self, client):
        client.post("/process/api-3?wait=true", json={"pdfUrl": PDF, "htmlUrl": HTML})
        issues = client.get("/jobs/api-3/issues?status=OPEN").json()["issues"]
        issue_id = issues[0]["id"]

        approved = client.post("/jobs/api-3/approve-issue", json={"issueId": issue_id})
        assert approved.status_code == 200
        assert approved.json()["status"] == "APPROVED"
        assert approved.json()["correctedHtmlUrl"]

        rejected = client.post("/jobs/api-3/reject-issue", json={"issueId": issue_id})
        assert rejected.json()["status"] == "REJECTED"

    def test_unknown_resources_return_404(self, client):
        assert client.get("/jobs/nope").status_code == 404
        assert client.get("/jobs/nope/report").status_code == 404
        client.post("/process/api-4?wait=true", json={"pdfUrl": PDF, "htmlUrl": HTML})
        missing = client.post("/jobs/api-4/approve-issue", json={"issueId": "iss_missing"})
        assert missing.status_code == 404

    def test_bad_request_is_reported(self, client):
        assert client.post("/process", json={}).status_code == 400
        broken = client.post("/process/api-5?wait=true",
                             json={"pdfUrl": "/no/such.pdf", "htmlUrl": HTML})
        assert broken.status_code == 500
