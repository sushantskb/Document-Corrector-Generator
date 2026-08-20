"""Correction application, the auto-fix threshold, and verification accounting."""

from bs4 import BeautifulSoup

from models.models import IssueStatus, IssueType


async def _correct(pdf, html_analyzer, html_analysis, issues, **kwargs):
    from services.correction_engine import CorrectionEngine

    engine = CorrectionEngine(
        html_analyzer.rendered_html or html_analyzer.raw_html, pdf, html_analysis,
        job_id="test", strip_base_tag=html_analyzer.injected_base, **kwargs,
    )
    return engine, await engine.patch_html(issues)


class TestCorrection:
    async def test_applies_only_high_confidence_fixes(self, pdf_analysis, html_analysis, comparison):
        _, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, result = comparison
        _, patch = await _correct(pdf, analyzer, analysis, result.issues)

        applied_types = {i.type for i in result.issues if i.status == IssueStatus.AUTO_FIXED}
        assert IssueType.MISSING_IMAGE in applied_types
        assert IssueType.ORDER_MISMATCH not in applied_types      # 0.90 < 0.95
        assert any("below auto-fix threshold" in reason for _, reason in patch["skipped"])

    async def test_missing_figure_is_inserted_with_caption(self, pdf_analysis, html_analysis, comparison):
        _, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, result = comparison
        _, patch = await _correct(pdf, analyzer, analysis, result.issues)

        soup = BeautifulSoup(patch["html"], "lxml")
        captions = [c.get_text(strip=True) for c in soup.find_all("figcaption")]
        assert any("Figure 10.2" in c for c in captions)
        inserted = soup.find("img", attrs={"data-dcp-inserted": "image"})
        assert inserted is not None and inserted["src"]
        assert inserted["alt"].startswith("Figure 10.2")

    async def test_wrong_image_is_repointed(self, pdf_analysis, html_analysis, comparison):
        _, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, result = comparison
        _, patch = await _correct(pdf, analyzer, analysis, result.issues)

        soup = BeautifulSoup(patch["html"], "lxml")
        replaced = soup.find("img", attrs={"data-dcp-inserted": "replaced"})
        assert replaced is not None
        assert "figure_4" not in replaced["src"]          # the decoy is gone

    async def test_heading_level_and_watermark_are_fixed(self, pdf_analysis, html_analysis, comparison):
        _, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, result = comparison
        _, patch = await _correct(pdf, analyzer, analysis, result.issues)

        soup = BeautifulSoup(patch["html"], "lxml")
        assert soup.find("h4") is None
        assert any("10.3 Refraction" in h.get_text() for h in soup.find_all("h2"))
        assert "do not copy" not in soup.get_text().lower()

    async def test_missing_exercise_lands_in_the_right_place(self, pdf_analysis, html_analysis, comparison):
        _, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, result = comparison
        _, patch = await _correct(pdf, analyzer, analysis, result.issues)

        soup = BeautifulSoup(patch["html"], "lxml")
        items = [li.get_text(" ", strip=True) for li in soup.find("ol").find_all("li")]
        assert len(items) == 5
        assert "rear-view mirror" in items[3]             # PDF order, not appended last

    async def test_approving_a_section_move_relocates_its_content(
        self, pdf_analysis, html_analysis, comparison
    ):
        _, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, result = comparison
        order_issue = next(i for i in result.issues if i.type == IssueType.ORDER_MISMATCH)
        engine, _ = await _correct(pdf, analyzer, analysis, [])
        patch = await engine.patch_html(result.issues, approved=[order_issue.id])

        soup = BeautifulSoup(patch["html"], "lxml")
        headings = [h.get_text(strip=True) for h in soup.find_all(["h2"])]
        assert headings.index("10.2 Spherical Mirrors") < headings.index("10.3 Refraction of Light")
        # the section's paragraph travelled with its heading
        body_order = [t.get_text(" ", strip=True)[:20] for t in soup.body.find_all(["h2", "p"])]
        assert body_order.index("10.2 Spherical Mirro") < body_order.index("The reflecting surfa")

    async def test_rejected_fix_is_not_applied(self, pdf_analysis, html_analysis, comparison):
        _, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, result = comparison
        watermark = next(i for i in result.issues if i.type == IssueType.WATERMARK)
        engine, _ = await _correct(pdf, analyzer, analysis, [])
        patch = await engine.patch_html(result.issues, rejected=[watermark.id])

        assert watermark.status == IssueStatus.REJECTED
        assert "do not copy" in BeautifulSoup(patch["html"], "lxml").get_text().lower()

    async def test_stale_dom_path_does_not_hit_the_wrong_element(self, pdf_analysis):
        """A path is only trusted when the element still holds the expected text."""
        from models.models import Correction, CorrectionAction
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        html = "<html><body><h2>First</h2><h2>Second</h2><h2>Third</h2></body></html>"
        engine = CorrectionEngine(html, pdf)
        correction = Correction(
            issue_id="x", action=CorrectionAction.FIX_HEADING_LEVEL,
            target_dom_path="html > body:nth-of-type(1) > h2:nth-of-type(1)",
            payload={"level": 3, "target_text": "Third"},
        )
        assert engine.fix_structure(correction) is True
        soup = BeautifulSoup(engine.generate_corrected_html(), "lxml")
        assert soup.find("h3").get_text() == "Third"      # not "First"

    async def test_corrected_html_is_stamped(self, pdf_analysis, html_analysis, comparison):
        _, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, result = comparison
        _, patch = await _correct(pdf, analyzer, analysis, result.issues)
        assert "document-correction-platform" in patch["html"]
        assert "<base" not in patch["html"]                # rendering aid must not ship


class TestVerification:
    async def test_verification_confirms_the_applied_fixes(
        self, pdf_analysis, html_analysis, comparison
    ):
        from services.verification_engine import VerificationEngine

        pdf_analyzer, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, before = comparison
        _, patch = await _correct(pdf, analyzer, analysis, before.issues)

        verifier = VerificationEngine(
            pdf, patch["html"], before, before.issues, pdf_pixels=pdf_analyzer.pixel_cache,
        )
        result = await verifier.verify_corrections()

        assert len(result.resolved_issue_ids) == 5
        assert result.regression_issue_ids == []
        assert result.after.image_coverage == 1.0
        assert result.after.question_coverage == 1.0
        assert result.after.overall_score > before.overall_score

    async def test_checklist_flags_what_is_still_wrong(
        self, pdf_analysis, html_analysis, comparison
    ):
        from services.verification_engine import VerificationEngine

        pdf_analyzer, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, before = comparison
        _, patch = await _correct(pdf, analyzer, analysis, before.issues)
        verifier = VerificationEngine(
            pdf, patch["html"], before, before.issues, pdf_pixels=pdf_analyzer.pixel_cache,
        )
        result = await verifier.verify_corrections()

        checks = {item.name: item.passed for item in result.checklist}
        assert checks["Image coverage"] is True
        assert checks["Question coverage"] is True
        assert checks["Element order"] is False        # left for the reviewer
        assert result.passed is False

    async def test_report_summarizes_the_run(self, pdf_analysis, html_analysis, comparison):
        from services.verification_engine import VerificationEngine

        pdf_analyzer, pdf = pdf_analysis
        analyzer, analysis = html_analysis
        _, before = comparison
        _, patch = await _correct(pdf, analyzer, analysis, before.issues)
        verifier = VerificationEngine(
            pdf, patch["html"], before, before.issues, pdf_pixels=pdf_analyzer.pixel_cache,
        )
        verification = await verifier.verify_corrections()
        report = verifier.generate_report("job-1", "project-1", verification,
                                          metrics={"processing_ms": 1234})

        assert report.job_id == "job-1"
        assert report.summary["issues_found"] == 6
        assert report.summary["auto_fixed"] == 5
        assert report.summary["auto_fix_rate"] > 0.8
        assert report.metrics["processing_ms"] == 1234
        assert report.quality_score > 0.9
        assert report.recommendations


class TestJavaScriptGeneratedDocuments:
    """A page that builds its own content would otherwise discard the fixes."""

    SPA = """<html><head><title>SPA</title></head><body>
        <div id="content"></div>
        <script>document.getElementById('content').innerHTML =
            '<p>Original paragraph built on load.</p>';</script>
        </body></html>"""

    async def test_rendering_detects_script_generated_content(self):
        from services.html_analyzer import HTMLAnalyzer

        analyzer = HTMLAnalyzer(html=self.SPA + "<p>" + ("filler text " * 80) + "</p>",
                                fetch_images=False)
        await analyzer.analyze()
        # the markup already carries the filler, so this page is *not* JS-built
        assert analyzer.js_generated is False

        builder = """<html><head><title>SPA</title></head><body><div id="c"></div>
            <script>document.getElementById('c').innerHTML =
                '<p>' + 'generated sentence. '.repeat(80) + '</p>';</script>
            </body></html>"""
        analyzer = HTMLAnalyzer(html=builder, fetch_images=False)
        await analyzer.analyze()
        assert analyzer.js_generated is True

    async def test_freezing_keeps_the_corrections_and_drops_behaviour(self, pdf_analysis):
        from models.models import Correction, CorrectionAction, Issue, IssueType, Severity
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        html = ('<html><head></head><body><p id="a" onclick="boom()">anchor</p>'
                '<script>document.body.innerHTML = "";</script>'
                '<script type="application/json" id="data">{"keep": true}</script></body></html>')
        issue = Issue(type=IssueType.MISSING_TEXT, severity=Severity.HIGH, confidence=0.99,
                      auto_fixable=True)
        issue.correction = Correction(
            issue_id=issue.id, action=CorrectionAction.INSERT_TEXT,
            target_dom_path="html > body:nth-of-type(1) > p:nth-of-type(1)",
            payload={"text": "restored sentence", "tag": "p", "target_text": "anchor"},
        )
        engine = CorrectionEngine(html, pdf, freeze_scripts=True)
        patch = await engine.patch_html([issue])

        soup = BeautifulSoup(patch["html"], "lxml")
        assert "restored sentence" in soup.get_text()
        assert soup.find("script", attrs={"type": "application/json"}) is not None  # data kept
        assert soup.find("script", attrs={"type": None}) is None                    # behaviour gone
        assert soup.find("p", id="a").get("onclick") is None
        assert any("interactive behaviour" in w for w in patch["warnings"])

    async def test_a_static_page_keeps_its_scripts(self, pdf_analysis):
        from models.models import Correction, CorrectionAction, Issue, IssueType, Severity
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        html = '<html><body><p>anchor</p><script>console.log(1)</script></body></html>'
        issue = Issue(type=IssueType.MISSING_TEXT, severity=Severity.HIGH, confidence=0.99,
                      auto_fixable=True)
        issue.correction = Correction(
            issue_id=issue.id, action=CorrectionAction.INSERT_TEXT,
            target_dom_path="html > body:nth-of-type(1) > p:nth-of-type(1)",
            payload={"text": "added", "tag": "p", "target_text": "anchor"},
        )
        engine = CorrectionEngine(html, pdf, freeze_scripts=False)
        patch = await engine.patch_html([issue])
        assert "console.log(1)" in patch["html"]


class TestInsertionPlacement:
    """Chapter content must never be injected into the page's furniture.

    Anchors come from text matching, and a chapter title legitimately matches
    the site banner — which is how PDF paragraphs and figure crops ended up
    inside <header> and <nav>, floating over the layout.
    """

    HTML = """<html><body>
        <header><div class="header-content"><h1>A Square and A Cube</h1></div></header>
        <nav><div id="tabs">Overview Study Material</div></nav>
        <main><p id="body">Squares of natural numbers are called perfect squares.</p></main>
        </body></html>"""

    @staticmethod
    def _issue(target_path, target_text):
        from models.models import Correction, CorrectionAction, Issue, IssueType, Severity

        issue = Issue(type=IssueType.MISSING_TEXT, severity=Severity.HIGH, confidence=0.99,
                      auto_fixable=True)
        issue.correction = Correction(
            issue_id=issue.id, action=CorrectionAction.INSERT_TEXT,
            target_dom_path=target_path,
            payload={"text": "A number obtained by multiplying a number by itself.",
                     "tag": "p", "target_text": target_text},
        )
        return issue

    async def test_an_anchor_in_the_header_is_refused(self, pdf_analysis):
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        issue = self._issue("html > body:nth-of-type(1) > header:nth-of-type(1) > "
                            "div:nth-of-type(1) > h1:nth-of-type(1)", "A Square and A Cube")
        engine = CorrectionEngine(self.HTML, pdf)
        patch = await engine.patch_html([issue])

        assert patch["applied"] == []
        soup = BeautifulSoup(patch["html"], "lxml")
        assert soup.header.find(attrs={"data-dcp-inserted": True}) is None
        assert "no usable anchor" in (issue.correction.error or "")

    async def test_an_anchor_in_the_content_is_used(self, pdf_analysis):
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        issue = self._issue("html > body:nth-of-type(1) > main:nth-of-type(1) > p:nth-of-type(1)",
                            "Squares of natural numbers are called perfect squares.")
        engine = CorrectionEngine(self.HTML, pdf)
        patch = await engine.patch_html([issue])

        assert len(patch["applied"]) == 1
        soup = BeautifulSoup(patch["html"], "lxml")
        inserted = soup.main.find(attrs={"data-dcp-inserted": True})
        assert inserted is not None
        # and it must not be stretched into a column by a flex/grid parent
        assert "flex-basis:100%" in inserted.get("style", "")

    async def test_the_text_fallback_also_avoids_chrome(self, pdf_analysis):
        """Even when the path is stale, the search must not land in the nav."""
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        issue = self._issue("html > body:nth-of-type(1) > section:nth-of-type(99)",
                            "Overview Study Material")
        engine = CorrectionEngine(self.HTML, pdf)
        patch = await engine.patch_html([issue])

        assert patch["applied"] == []
        soup = BeautifulSoup(patch["html"], "lxml")
        assert soup.nav.find(attrs={"data-dcp-inserted": True}) is None


class TestInsertionQuality:
    """Regressions found by mass-approving a real chapter's issues."""

    @staticmethod
    def _text_issue(target_path, target_text, body="inserted paragraph text"):
        from models.models import Correction, CorrectionAction, Issue, IssueType, Severity

        issue = Issue(type=IssueType.MISSING_TEXT, severity=Severity.HIGH, confidence=0.99,
                      auto_fixable=True)
        issue.correction = Correction(
            issue_id=issue.id, action=CorrectionAction.INSERT_TEXT,
            target_dom_path=target_path,
            payload={"text": body, "tag": "p", "target_text": target_text},
        )
        return issue

    async def test_a_paragraph_anchored_to_a_list_item_goes_after_the_list(self, pdf_analysis):
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        html = ('<html><body><main><div class="card"><ul>'
                '<li>Understand squares of natural numbers here.</li>'
                '<li>Second bullet point.</li></ul></div></main></body></html>')
        issue = self._text_issue(
            "html > body:nth-of-type(1) > main:nth-of-type(1) > div:nth-of-type(1) > "
            "ul:nth-of-type(1) > li:nth-of-type(1)",
            "Understand squares of natural numbers here.")
        engine = CorrectionEngine(html, pdf)
        patch = await engine.patch_html([issue])

        soup = BeautifulSoup(patch["html"], "lxml")
        inserted = soup.find("p", attrs={"data-dcp-inserted": True})
        assert inserted is not None
        assert inserted.parent.name != "ul"            # never a <p> between <li>s
        assert inserted.find_previous_sibling("ul") is not None

    async def test_before_insertions_also_escape_the_list(self, pdf_analysis):
        from models.models import CorrectionAction
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        html = ('<html><body><main><ul>'
                '<li>Person 1 opens every locker in the hallway.</li></ul></main></body></html>')
        issue = self._text_issue(
            "html > body:nth-of-type(1) > main:nth-of-type(1) > ul:nth-of-type(1) > "
            "li:nth-of-type(1)", "Person 1 opens every locker in the hallway.")
        issue.correction.payload["position"] = "before"
        engine = CorrectionEngine(html, pdf)
        patch = await engine.patch_html([issue])

        soup = BeautifulSoup(patch["html"], "lxml")
        inserted = soup.find("p", attrs={"data-dcp-inserted": True})
        assert inserted.parent.name != "ul"
        assert inserted.find_next_sibling("ul") is not None   # before the list

    async def test_blocks_sharing_an_anchor_keep_their_order(self, pdf_analysis):
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        html = '<html><body><main><p>anchor paragraph text</p></main></body></html>'
        issues = [self._text_issue(
            "html > body:nth-of-type(1) > main:nth-of-type(1) > p:nth-of-type(1)",
            "anchor paragraph text", body=f"block number {i}") for i in range(3)]
        engine = CorrectionEngine(html, pdf)
        patch = await engine.patch_html(issues)

        soup = BeautifulSoup(patch["html"], "lxml")
        texts = [p.get_text(strip=True) for p in soup.main.find_all("p")]
        assert texts == ["anchor paragraph text", "block number 0",
                         "block number 1", "block number 2"]

    async def test_the_fallback_never_anchors_to_a_container(self, pdf_analysis):
        """Matching a card's full text would drop the insertion outside its
        styled children — the bare full-bleed text the reviewer saw."""
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        html = ('<html><body><main><div class="section"><div class="card">'
                '<p>the one paragraph of content in this card</p>'
                '</div></div></main></body></html>')
        issue = self._text_issue("html > body > div:nth-of-type(9)",   # stale path
                                 "the one paragraph of content in this card")
        engine = CorrectionEngine(html, pdf)
        patch = await engine.patch_html([issue])

        soup = BeautifulSoup(patch["html"], "lxml")
        inserted = soup.find("p", attrs={"data-dcp-inserted": True})
        assert inserted is not None
        assert inserted.parent.get("class") == ["card"]     # beside the leaf, in the card


class TestFrozenNavigation:
    """A frozen copy's tab bar must still take the reader somewhere."""

    HTML = ('<html><head></head><body>'
            '<nav><button class="nav-btn">One</button><button class="nav-btn">Two</button>'
            '<button class="nav-btn">Three</button></nav>'
            '<main><div id="content">'
            '<div class="section active"><p>first section body</p></div>'
            '<div class="section"><p>second section body</p></div>'
            '<div class="section"><p>third section body</p></div>'
            '</div></main>'
            '<script>document.body.innerHTML = "";</script></body></html>')

    async def test_tab_buttons_become_jump_links(self, pdf_analysis):
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        engine = CorrectionEngine(
            self.HTML, pdf, freeze_scripts=True,
            reveal_paths=[
                "html > body:nth-of-type(1) > main:nth-of-type(1) > div:nth-of-type(1) > "
                f"div:nth-of-type({i})" for i in (2, 3)
            ],
        )
        issue = TestInsertionQuality._text_issue(
            "html > body:nth-of-type(1) > main:nth-of-type(1) > div:nth-of-type(1) > "
            "div:nth-of-type(1) > p:nth-of-type(1)", "first section body")
        patch = await engine.patch_html([issue])
        soup = BeautifulSoup(patch["html"], "lxml")

        links = soup.nav.find_all("a")
        assert [a.get_text(strip=True) for a in links] == ["One", "Two", "Three"]
        targets = [a["href"].lstrip("#") for a in links]
        assert all(soup.find(id=t) is not None for t in targets)
        assert soup.nav.find("button") is None
        assert links[0].get("class") == ["nav-btn"]      # styling carried over

    async def test_without_a_confident_mapping_buttons_are_left_alone(self, pdf_analysis):
        from services.correction_engine import CorrectionEngine

        _, pdf = pdf_analysis
        html = self.HTML.replace('<button class="nav-btn">Three</button>', "")  # 2 buttons, 3 sections
        engine = CorrectionEngine(
            html, pdf, freeze_scripts=True,
            reveal_paths=["html > body:nth-of-type(1) > main:nth-of-type(1) > "
                          "div:nth-of-type(1) > div:nth-of-type(2)"],
        )
        issue = TestInsertionQuality._text_issue(
            "html > body:nth-of-type(1) > main:nth-of-type(1) > div:nth-of-type(1) > "
            "div:nth-of-type(1) > p:nth-of-type(1)", "first section body")
        patch = await engine.patch_html([issue])
        soup = BeautifulSoup(patch["html"], "lxml")
        assert len(soup.nav.find_all("button")) == 2     # no guessing
