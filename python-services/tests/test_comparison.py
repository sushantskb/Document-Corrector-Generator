"""The comparison engine must find every planted defect and invent none.

The fixture HTML differs from the PDF in exactly five ways:
  1. figure 10.2 is missing entirely
  2. figure 10.3 is the wrong picture
  3. exercise 4 was dropped
  4. the 10.3 heading is an <h4> instead of an <h2>
  5. sections 10.2 and 10.3 are swapped
plus a watermark paragraph that has no PDF counterpart.
"""

from models.models import IssueType, Severity


def types_of(result):
    return [issue.type for issue in result.issues]


class TestDetection:
    async def test_finds_the_missing_figure(self, comparison):
        _, result = comparison
        issue = next(i for i in result.issues if i.type == IssueType.MISSING_IMAGE)
        assert issue.severity == Severity.HIGH
        assert "10.2" in issue.evidence["caption"]
        assert issue.auto_fixable and issue.confidence >= 0.95

    async def test_finds_the_substituted_figure(self, comparison):
        _, result = comparison
        issue = next(i for i in result.issues if i.type == IssueType.IMAGE_MISMATCH)
        assert "10.3" in issue.evidence["caption"]
        assert issue.correction.payload["pdf_image_id"]

    async def test_finds_the_dropped_exercise(self, comparison):
        _, result = comparison
        issue = next(i for i in result.issues if i.type == IssueType.MISSING_QUESTION)
        assert "convex mirror as a rear-view mirror" in issue.evidence["text"]

    async def test_finds_the_wrong_heading_level(self, comparison):
        _, result = comparison
        issue = next(i for i in result.issues if i.type == IssueType.HEADING_LEVEL_MISMATCH)
        assert issue.evidence["pdf_level"] == 2 and issue.evidence["html_level"] == 4
        assert issue.correction.payload["level"] == 2

    async def test_finds_the_swapped_section(self, comparison):
        _, result = comparison
        issue = next(i for i in result.issues if i.type == IssueType.ORDER_MISMATCH)
        assert issue.correction.payload["scope"] == "section"
        assert "10.2" in issue.evidence["title"]

    async def test_finds_the_watermark(self, comparison):
        _, result = comparison
        issue = next(i for i in result.issues if i.type == IssueType.WATERMARK)
        assert issue.evidence["present_in_pdf"] is False
        assert issue.confidence >= 0.95

    async def test_reports_each_defect_once(self, comparison):
        _, result = comparison
        assert len(result.issues) == 6, [i.type for i in result.issues]

    async def test_does_not_invent_missing_text(self, comparison):
        """Every paragraph really is present, so no MISSING_TEXT may be raised."""
        _, result = comparison
        assert IssueType.MISSING_TEXT not in types_of(result)
        assert IssueType.EXTRA_IMAGE not in types_of(result)

    async def test_scores_reflect_the_defects(self, comparison):
        _, result = comparison
        assert result.text_similarity > 0.8          # only the caption is absent
        assert result.image_coverage < 0.5           # one of three figures matched
        assert result.question_coverage == 0.8       # four of five exercises
        assert result.structure_similarity == 1.0    # all headings present
        assert 0.7 < result.overall_score < 0.8

    async def test_issues_are_sorted_by_severity(self, comparison):
        _, result = comparison
        severities = [i.severity for i in result.issues]
        assert severities == sorted(severities, key=lambda s: ["HIGH", "MEDIUM", "LOW"].index(s.value))


class TestPerLevelApis:
    async def test_compare_text_reports_both_directions(self, comparison):
        engine, _ = comparison
        result = engine.compare_text()
        assert result["matched"] >= 7
        assert result["pdf_blocks"] > 0 and result["html_blocks"] > 0

    async def test_compare_images_counts_coverage(self, comparison):
        engine, _ = comparison
        result = engine.compare_images()
        assert result["pdf_images"] == 3 and result["html_images"] == 2
        assert result["matched"] == 1

    async def test_compare_questions_counts_coverage(self, comparison):
        engine, _ = comparison
        result = engine.compare_questions()
        assert result["pdf_questions"] == 5 and result["html_questions"] == 4
        assert result["coverage"] == 0.8

    async def test_compare_visual_layout_runs_on_matched_images(self, comparison):
        engine, _ = comparison
        result = engine.compare_visual_layout()
        assert result["compared"] >= 1
        assert 0.0 <= result["similarity"] <= 1.0


class TestIdenticalDocuments:
    """An HTML that faithfully mirrors the PDF must produce no issues."""

    async def test_clean_pair_has_no_issues(self, pdf_analysis):
        from services.comparison_engine import ComparisonEngine
        from services.html_analyzer import HTMLAnalyzer

        pdf_analyzer, pdf = pdf_analysis
        html = "<html><head><title>t</title></head><body>"
        for element in pdf.text_elements:
            tag = "h2" if element.kind == "heading" else "p"
            html += f"<{tag}>{element.text}</{tag}>"
        html += "</body></html>"

        analyzer = HTMLAnalyzer(html=html, render_js=False, fetch_images=False)
        analysis = await analyzer.analyze()
        result = ComparisonEngine(pdf, analysis, pdf_analyzer.pixel_cache).generate_issues()

        assert result.text_similarity == 1.0
        assert result.question_coverage == 1.0
        assert IssueType.MISSING_TEXT not in types_of(result)
        assert IssueType.MISSING_QUESTION not in types_of(result)
        assert IssueType.ORDER_MISMATCH not in types_of(result)


class TestUnreliableText:
    """Maths does not survive PDF text extraction, so it must not be auto-applied."""

    def test_equations_are_recognised_as_symbol_heavy(self):
        from services.comparison_engine import _is_symbol_heavy

        assert _is_symbol_heavy("(i) 382 (ii) 342 (iii) 462 (iv) 562")
        assert _is_symbol_heavy("12 + 22 + 22 = 32   22 + 32 + 62 = 72")
        # half prose, half maths still counts — the maths is still mangled
        assert _is_symbol_heavy("Find the squares: (i) 382 (ii) 342 (iii) 462 (iv) 562")

    def test_prose_is_not(self):
        from services.comparison_engine import _is_symbol_heavy

        assert not _is_symbol_heavy("Squares of natural numbers are called perfect squares.")
        assert not _is_symbol_heavy("All perfect squares end with 0, 1, 4, 5, 6 or 9.")
        # numbers alone do not make a sentence unreliable
        assert not _is_symbol_heavy("In 2020, 2021 and 2022 the syllabus changed for class 8.")
        assert not _is_symbol_heavy("short")

    async def test_such_blocks_stay_below_the_auto_fix_threshold(self, pdf_analysis):
        """They still get reported — they just wait for a human."""
        from models.models import DocumentType, IssueType
        from services.comparison_engine import ComparisonEngine
        from services.html_analyzer import HTMLAnalyzer
        from models.models import DocumentAnalysis, TextElement

        pdf_analyzer, _ = pdf_analysis
        pdf = DocumentAnalysis(doc_type=DocumentType.PDF, text_elements=[
            TextElement(text="Find the squares: (i) 382 (ii) 342 (iii) 462 (iv) 562", page=1),
            TextElement(text="Squares of natural numbers are called perfect squares.", page=1),
        ])
        html = await HTMLAnalyzer(html="<html><body><p>Unrelated introduction text here.</p></body></html>",
                                  render_js=False, fetch_images=False).analyze()
        result = ComparisonEngine(pdf, html).generate_issues()

        missing = {i.evidence.get("text"): i for i in result.issues
                   if i.type == IssueType.MISSING_TEXT}
        maths = next(i for text, i in missing.items() if "382" in text)
        prose = next(i for text, i in missing.items() if "perfect squares" in text)

        assert maths.confidence <= 0.80          # below the 0.95 auto-fix bar
        assert "mathematics" in maths.suggestion
        assert prose.confidence > maths.confidence
