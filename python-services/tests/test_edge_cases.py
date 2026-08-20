"""Degenerate and hostile inputs must degrade, never crash."""

import pytest

from models.models import DocumentType, IssueType, ProcessRequest


class TestHtmlEdgeCases:
    async def test_empty_document(self):
        from services.html_analyzer import HTMLAnalyzer

        analysis = await HTMLAnalyzer(html="", render_js=False, fetch_images=False).analyze()
        assert analysis.text_elements == []
        assert analysis.images == []

    async def test_malformed_markup_is_still_parsed(self):
        from services.html_analyzer import HTMLAnalyzer

        html = "<html><body><p>unclosed<div>nested<p>text</body>"
        analysis = await HTMLAnalyzer(html=html, render_js=False, fetch_images=False).analyze()
        assert any("unclosed" in t.text for t in analysis.text_elements)

    async def test_broken_image_src_is_reported_not_raised(self):
        from services.html_analyzer import HTMLAnalyzer

        html = '<html><body><img src="/does/not/exist.png" alt="x"></body></html>'
        analyzer = HTMLAnalyzer(html=html, base_url="file:///tmp/", render_js=False)
        analysis = await analyzer.analyze()
        assert len(analysis.images) == 1
        assert analysis.images[0].error

    async def test_data_uri_images_are_decoded(self):
        from services.html_analyzer import HTMLAnalyzer

        pixel = ("data:image/gif;base64,"
                 "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        analysis = await HTMLAnalyzer(
            html=f'<html><body><img src="{pixel}"></body></html>', render_js=False,
        ).analyze()
        assert analysis.images[0].is_decorative      # 1x1 tracking pixel, not content

    async def test_srcset_and_lazy_attributes_resolve(self):
        from services.html_analyzer import HTMLAnalyzer

        html = ('<html><body><img data-src="/a.png" srcset="/small.png 1x, /big.png 2x">'
                "</body></html>")
        analyzer = HTMLAnalyzer(html=html, base_url="https://example.test/",
                                render_js=False, fetch_images=False)
        analysis = await analyzer.analyze()
        assert analysis.images[0].src == "https://example.test/a.png"

    async def test_script_content_is_never_treated_as_text(self):
        from services.html_analyzer import HTMLAnalyzer

        html = "<html><body><script>var x = 'hidden text';</script><p>real</p></body></html>"
        analysis = await HTMLAnalyzer(html=html, render_js=False, fetch_images=False).analyze()
        assert [t.text for t in analysis.text_elements] == ["real"]


class TestComparisonEdgeCases:
    async def test_empty_html_reports_everything_missing(self, pdf_analysis):
        from services.comparison_engine import ComparisonEngine
        from services.html_analyzer import HTMLAnalyzer

        pdf_analyzer, pdf = pdf_analysis
        html = await HTMLAnalyzer(html="<html><body></body></html>", render_js=False,
                                  fetch_images=False).analyze()
        result = ComparisonEngine(pdf, html, pdf_analyzer.pixel_cache).generate_issues()

        assert result.text_similarity == 0.0
        assert result.image_coverage == 0.0
        assert result.question_coverage == 0.0
        types = {i.type for i in result.issues}
        assert IssueType.MISSING_IMAGE in types
        assert IssueType.MISSING_SECTION in types

    async def test_empty_pdf_side_produces_no_false_positives(self):
        from models.models import DocumentAnalysis
        from services.comparison_engine import ComparisonEngine
        from services.html_analyzer import HTMLAnalyzer

        empty = DocumentAnalysis(doc_type=DocumentType.PDF)
        html = await HTMLAnalyzer(html="<html><body><p>Some page text here</p></body></html>",
                                  render_js=False, fetch_images=False).analyze()
        result = ComparisonEngine(empty, html).generate_issues()

        # extra content is worth noting, but nothing may be reported as missing
        assert all(i.type in (IssueType.EXTRA_TEXT, IssueType.EXTRA_IMAGE,
                              IssueType.STRUCTURE_MISMATCH) for i in result.issues)

    async def test_identical_input_on_both_sides_is_clean(self, pdf_analysis):
        from services.comparison_engine import ComparisonEngine

        _, pdf = pdf_analysis
        result = ComparisonEngine(pdf, pdf).generate_issues()
        assert result.text_similarity == 1.0
        assert result.image_coverage == 1.0
        assert result.order_similarity == 1.0


class TestCorrectionEdgeCases:
    async def test_unresolvable_target_is_skipped_not_fatal(self, pdf_analysis):
        from models.models import Correction, CorrectionAction, Issue, IssueType, Severity
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        issue = Issue(type=IssueType.MISSING_ALT_TEXT, severity=Severity.LOW, confidence=0.99,
                      auto_fixable=True)
        issue.correction = Correction(
            issue_id=issue.id, action=CorrectionAction.SET_ALT_TEXT,
            target_dom_path="html > body:nth-of-type(1) > img:nth-of-type(99)",
            payload={"alt": "x", "target_text": "nothing like this exists"},
        )
        engine = CorrectionEngine("<html><body><p>hi</p></body></html>", pdf)
        patch = await engine.patch_html([issue])
        assert patch["applied"] == []
        assert len(patch["skipped"]) == 1

    async def test_issue_without_a_correction_is_marked_unfixable(self, pdf_analysis):
        from models.models import Issue, IssueStatus, IssueType, Severity
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        issue = Issue(type=IssueType.EXTRA_TEXT, severity=Severity.LOW, confidence=0.99)
        engine = CorrectionEngine("<html><body></body></html>", pdf)
        await engine.patch_html([issue])
        assert issue.status == IssueStatus.UNFIXABLE

    async def test_a_document_with_no_head_still_serializes(self, pdf_analysis):
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        engine = CorrectionEngine("<p>fragment</p>", pdf)
        assert "fragment" in engine.generate_corrected_html()


class TestPipelineEdgeCases:
    async def test_unreachable_url_fails_cleanly(self):
        from services.db import MemoryStore
        from services.pipeline import ProcessingPipeline

        store = await MemoryStore().connect()
        request = ProcessRequest(pdfUrl="https://127.0.0.1:9/nope.pdf",
                                 htmlUrl="https://127.0.0.1:9/nope.html")
        result = await ProcessingPipeline("bad-job", request, store).run()
        assert result["status"] == "FAILED"
        assert "failed to download" in result["error"].lower()

    async def test_job_without_urls_or_documents_fails_with_a_clear_message(self):
        from services.db import MemoryStore
        from services.pipeline import ProcessingPipeline

        store = await MemoryStore().connect()
        result = await ProcessingPipeline("orphan", ProcessRequest(), store).run()
        assert result["status"] == "FAILED"
        assert "could not resolve" in result["error"]


class TestCloudinaryDelivery:
    """Cloudinary blocks PDF delivery by default, so uploads that worked can 401."""

    def test_parses_delivery_urls(self):
        from services.cloudinary_client import parse_delivery_url

        image = parse_delivery_url(
            "https://res.cloudinary.com/demo/image/upload/v1787164833/projects/a/b.pdf"
        )
        assert image == {"public_id": "projects/a/b", "resource_type": "image", "type": "upload"}

        # raw public ids keep their extension
        raw = parse_delivery_url(
            "https://res.cloudinary.com/demo/raw/upload/v1/projects/a/doc.html"
        )
        assert raw["public_id"] == "projects/a/doc.html"
        assert raw["resource_type"] == "raw"

    def test_strips_transformations(self):
        from services.cloudinary_client import parse_delivery_url

        parsed = parse_delivery_url(
            "https://res.cloudinary.com/demo/image/upload/c_fill,w_200/v12/folder/pic.jpg"
        )
        assert parsed["public_id"] == "folder/pic"

    def test_ignores_urls_from_other_hosts(self):
        from services.cloudinary_client import parse_delivery_url

        assert parse_delivery_url("https://example.com/a/b.pdf") is None
        assert parse_delivery_url("not a url at all") is None

    async def test_client_declines_when_not_configured(self):
        from services.cloudinary_client import CloudinaryClient

        client = CloudinaryClient()
        client.enabled = False
        assert await client.fetch_restricted(
            "https://res.cloudinary.com/demo/image/upload/v1/a/b.pdf"
        ) is None

    async def test_auth_failures_are_not_retried(self):
        """A 401 will not fix itself; retrying it three times only wastes time."""
        import httpx

        from utils import file_utils

        calls = {"count": 0}

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, headers=None):
                calls["count"] += 1
                request = httpx.Request("GET", url)
                return httpx.Response(401, request=request)

        original = httpx.AsyncClient
        httpx.AsyncClient = lambda **kwargs: FakeClient()
        try:
            with pytest.raises(file_utils.DownloadError) as excinfo:
                await file_utils.download_bytes("https://res.cloudinary.com/demo/image/upload/v1/a.pdf")
        finally:
            httpx.AsyncClient = original

        assert excinfo.value.status_code == 401
        assert calls["count"] == 1
