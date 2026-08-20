"""Extraction tests for both analyzers, against the generated sample chapter."""

import pytest

from models.models import DocumentType


class TestPDFAnalyzer:
    def test_extracts_metadata(self, pdf_analysis):
        _, analysis = pdf_analysis
        assert analysis.doc_type == DocumentType.PDF
        assert analysis.metadata.page_count == 3
        assert analysis.metadata.title == "Light - Reflection and Refraction"

    def test_text_blocks_carry_location_and_typography(self, pdf_analysis):
        _, analysis = pdf_analysis
        blocks = analysis.text_elements
        assert len(blocks) > 10
        body = next(b for b in blocks if "highly polished surface" in b.text)
        assert body.page == 1
        assert body.bbox is not None and body.bbox.width > 0
        assert body.size and body.font
        assert body.kind == "paragraph"

    def test_paragraph_lines_are_merged(self, pdf_analysis):
        _, analysis = pdf_analysis
        body = next(b for b in analysis.text_elements if "highly polished surface" in b.text)
        # the PDF draws this paragraph as four separate lines
        assert body.text.count(" ") > 40

    def test_detects_headings_with_a_hierarchy(self, pdf_analysis):
        _, analysis = pdf_analysis
        titles = [s.title for s in analysis.structure]
        assert "10.1 Reflection of Light" in titles
        assert "Exercises" in titles
        chapter = analysis.structure[0]
        assert chapter.level == 1
        assert all(s.level >= 1 for s in analysis.structure)

    def test_extracts_every_figure_with_pixels(self, pdf_analysis):
        _, analysis = pdf_analysis
        assert len(analysis.images) == 3
        for image in analysis.images:
            assert image.phash and image.sha256
            assert image.bbox and image.bbox.area > 0
            assert image.local_path

    def test_figures_get_their_caption_as_alt_text(self, pdf_analysis):
        _, analysis = pdf_analysis
        captions = [i.caption for i in analysis.images]
        assert any(c and "Figure 10.1" in c for c in captions)
        assert all(i.alt == i.caption for i in analysis.images if i.caption)

    def test_extracts_exercises(self, pdf_analysis):
        _, analysis = pdf_analysis
        assert len(analysis.questions) == 5
        assert analysis.questions[0].number == "1"
        assert "principal focus" in analysis.questions[0].text

    def test_page_layout_is_reported(self, pdf_analysis):
        _, analysis = pdf_analysis
        assert [p.page for p in analysis.pages] == [1, 2, 3]
        assert all(p.width > 0 and p.height > 0 for p in analysis.pages)
        assert analysis.pages[0].columns == 1

    def test_reports_no_warnings_for_a_clean_pdf(self, pdf_analysis):
        _, analysis = pdf_analysis
        assert analysis.warnings == []


class TestHTMLAnalyzer:
    def test_renders_javascript_and_reports_geometry(self, html_analysis):
        _, analysis = html_analysis
        assert analysis.stats["rendered"] is True
        assert all(t.bbox is not None for t in analysis.text_elements)

    def test_metadata_and_language(self, html_analysis):
        _, analysis = html_analysis
        assert analysis.metadata.title == "Light - Reflection and Refraction"
        assert analysis.metadata.language == "en"
        assert "Chapter 10" in (analysis.metadata.subject or "")

    def test_text_blocks_are_not_double_counted(self, html_analysis):
        _, analysis = html_analysis
        texts = [t.text for t in analysis.text_elements]
        # the paragraph must appear once, not again inside its ancestors
        matching = [t for t in texts if "highly polished surface" in t]
        assert len(matching) == 1

    def test_dom_paths_are_css_selectors(self, html_analysis):
        _, analysis = html_analysis
        heading = next(t for t in analysis.text_elements if t.tag == "h1")
        assert heading.dom_path.startswith("html > body")
        assert "nth-of-type" in heading.dom_path

    def test_images_are_fetched_and_hashed(self, html_analysis):
        _, analysis = html_analysis
        assert len(analysis.images) == 2
        for image in analysis.images:
            assert image.src and image.phash
            assert image.error is None

    def test_figcaptions_become_captions(self, html_analysis):
        _, analysis = html_analysis
        assert any("Figure 10.1" in (i.caption or "") for i in analysis.images)

    def test_headings_keep_their_document_level(self, html_analysis):
        _, analysis = html_analysis
        levels = {s.title: s.level for s in analysis.structure}
        assert levels["10.1 Reflection of Light"] == 2
        assert levels["10.3 Refraction of Light"] == 4      # the planted defect

    def test_ordered_list_items_become_numbered_questions(self, html_analysis):
        _, analysis = html_analysis
        assert len(analysis.questions) == 4
        assert [q.number for q in analysis.questions] == ["1", "2", "3", "4"]
        assert all(not q.numbering_explicit for q in analysis.questions)

    def test_extracts_embedded_json(self, html_analysis):
        _, analysis = html_analysis
        payload = next(p for p in analysis.embedded_data if p["id"] == "chapter-data")
        assert payload["data"]["chapter"] == 10

    def test_flags_watermark_text(self, html_analysis):
        _, analysis = html_analysis
        assert any("do not copy" in w["text"].lower() for w in analysis.stats["watermarks"])

    def test_dom_tree_is_nested(self, html_analysis):
        analyzer, _ = html_analysis
        tree = analyzer.get_dom_tree()
        assert tree["tag"] == "body"
        assert any(child["tag"] == "h1" for child in tree["children"])

    async def test_static_parsing_without_a_browser(self):
        from services.html_analyzer import HTMLAnalyzer

        analyzer = HTMLAnalyzer(
            html="<html><body><h1>Title</h1><p>Body text here</p></body></html>",
            render_js=False, fetch_images=False,
        )
        analysis = await analyzer.analyze()
        assert analysis.stats["rendered"] is False
        assert [t.text for t in analysis.text_elements] == ["Title", "Body text here"]

    async def test_hidden_elements_are_marked_invisible(self):
        from services.html_analyzer import HTMLAnalyzer

        analyzer = HTMLAnalyzer(
            html='<html><body><p style="display:none">secret</p><p>shown</p></body></html>',
            render_js=False, fetch_images=False,
        )
        analysis = await analyzer.analyze()
        visibility = {t.text: t.visible for t in analysis.text_elements}
        assert visibility == {"secret": False, "shown": True}


class TestRenderingSource:
    """Rendering must not depend on the source URL being loadable in a browser.

    The real documents live on Cloudinary, which serves `raw` assets as an
    attachment — navigating to one makes Chromium start a download instead of
    rendering a page. The analyzer therefore renders the markup it already has,
    using the URL only to resolve relative links.
    """

    async def test_renders_markup_when_the_base_url_is_unreachable(self):
        from services.html_analyzer import HTMLAnalyzer

        html = """<html><head><title>SPA</title></head><body>
            <div id="content"></div>
            <script>
              document.getElementById('content').innerHTML =
                '<h1>Built by script</h1><p>This paragraph only exists after JS runs.</p>';
            </script></body></html>"""
        analyzer = HTMLAnalyzer(
            html=html,
            base_url="https://unreachable.invalid/assets/chapter.html",
            fetch_images=False,
        )
        analysis = await analyzer.analyze()

        assert analysis.stats["rendered"] is True
        texts = [t.text for t in analysis.text_elements]
        assert "Built by script" in texts
        assert any("only exists after JS runs" in t for t in texts)

    async def test_the_injected_base_tag_is_flagged_for_removal(self):
        from services.html_analyzer import HTMLAnalyzer

        analyzer = HTMLAnalyzer(
            html="<html><head></head><body><p>hi</p></body></html>",
            base_url="https://example.test/x.html", fetch_images=False,
        )
        await analyzer.analyze()
        # the base tag is a rendering aid; the corrected document must not keep it
        assert analyzer.injected_base is True


class TestPrintArtifacts:
    """Real textbook PDFs carry production leftovers that are not content."""

    @staticmethod
    def _line(text, doubled=False):
        chars = []
        for index, char in enumerate(text):
            x = (index // 2 if doubled else index) * 5.0
            chars.append({"text": char, "x0": x, "top": 0.0, "size": 10})
        return {"text": text, "chars": chars}

    def test_double_struck_text_is_repaired(self):
        from services.pdf_analyzer import PDFAnalyzer

        # faux-bold: every glyph drawn twice at the same coordinates
        original = "Chapter 1.indd 4"
        line = self._line("".join(char * 2 for char in original), doubled=True)
        assert PDFAnalyzer._line_text(line) == original

    def test_ordinary_double_letters_are_left_alone(self):
        from services.pdf_analyzer import PDFAnalyzer

        line = self._line("committee bookkeeper still")
        assert PDFAnalyzer._line_text(line) == "committee bookkeeper still"

    def test_short_lines_are_not_touched(self):
        from services.pdf_analyzer import PDFAnalyzer

        assert PDFAnalyzer._line_text(self._line("aa")) == "aa"

    def test_layout_file_footers_are_treated_as_furniture(self, pdf_analysis):
        """A footer like "Chapter 1.indd 4 10-07-2025" differs on every page,
        so repetition alone never catches it."""
        from models.models import BBox, TextElement
        from services.pdf_analyzer import PDFAnalyzer

        analyzer, _ = pdf_analysis
        page_height = analyzer.pages[0].height
        elements = [
            TextElement(text="Chapter 1.indd 4 10-07-2025 14:06:40", page=1,
                        bbox=BBox(x0=60, top=page_height - 20, x1=300, bottom=page_height - 8)),
            TextElement(text="Chapter 1.indd 5 10-07-2025 14:06:41", page=2,
                        bbox=BBox(x0=60, top=page_height - 20, x1=300, bottom=page_height - 8)),
            TextElement(text="Squares of natural numbers are called perfect squares.", page=1,
                        bbox=BBox(x0=60, top=300, x1=500, bottom=320)),
        ]
        analyzer.mark_page_furniture(elements)

        assert [e.kind for e in elements[:2]] == ["furniture", "furniture"]
        assert elements[2].kind != "furniture"      # real body text survives


class TestFigureFiltering:
    """Watermarks and page backgrounds are not chapter figures."""

    def test_page_covering_images_are_not_figures(self):
        """The scan of the paper itself covers ~100% of the page."""
        from services.pdf_analyzer import MAX_FIGURE_AREA_RATIO

        assert MAX_FIGURE_AREA_RATIO <= 0.9    # anything near page-size is excluded

    def test_an_image_repeated_across_pages_is_a_stamp(self):
        from models.models import ImageElement
        from services.pdf_analyzer import PDFAnalyzer

        analyzer = PDFAnalyzer.__new__(PDFAnalyzer)   # no file needed for this method
        stamp_hash = "ab" * 8
        images = [ImageElement(page=page, phash=stamp_hash) for page in range(1, 11)]
        images.append(ImageElement(page=3, phash="cd" * 8))   # a genuine one-off figure

        marked = PDFAnalyzer.mark_repeated_stamps(analyzer, images)

        assert marked == 10
        assert all(i.is_decorative for i in images[:10])
        assert images[10].is_decorative is False

    def test_a_figure_on_few_pages_is_kept(self):
        from models.models import ImageElement
        from services.pdf_analyzer import PDFAnalyzer

        analyzer = PDFAnalyzer.__new__(PDFAnalyzer)
        # same diagram reused twice in a 10-page chapter: legitimate content
        images = [ImageElement(page=p, phash="ef" * 8) for p in (2, 7)]
        images += [ImageElement(page=p, phash=f"{p:02x}" * 8) for p in range(1, 9)]

        assert PDFAnalyzer.mark_repeated_stamps(analyzer, images) == 0

    def test_text_panels_are_not_vector_figures(self):
        from models.models import BBox, TextElement
        from services.pdf_analyzer import PDFAnalyzer

        panel = BBox(x0=50, top=100, x1=550, bottom=400)
        prose = [TextElement(text="A number obtained by multiplying a number by itself is "
                                  "called a square number, and the squares of all natural "
                                  "numbers are called perfect squares in this chapter",
                             bbox=BBox(x0=80, top=150, x1=500, bottom=180))]
        assert PDFAnalyzer._is_text_panel(panel, prose) is True

        diagram_labels = [TextElement(text="x axis", bbox=BBox(x0=80, top=150, x1=120, bottom=160))]
        assert PDFAnalyzer._is_text_panel(panel, diagram_labels) is False


class TestHTMLGeneration:
    """The add-on that builds a fresh HTML straight from the PDF."""

    @pytest.fixture(scope="class")
    async def generated(self, pdf_analysis):
        from bs4 import BeautifulSoup

        from services.html_generator import HTMLGenerator

        _, analysis = pdf_analysis
        generator = HTMLGenerator(analysis, job_id="gen-test")
        html = await generator.generate()
        return html, BeautifulSoup(html, "lxml")

    async def test_title_and_sections_come_from_the_pdf(self, generated):
        _, soup = generated
        assert "Light" in soup.h1.get_text()
        headings = [h.get_text() for h in soup.find_all("h2")]
        assert any("10.1 Reflection" in h for h in headings)
        assert any("Exercises" in h for h in headings)

    async def test_reading_order_is_preserved(self, generated):
        _, soup = generated
        text = soup.main.get_text(" ", strip=True)
        assert text.index("10.1 Reflection") < text.index("10.2 Spherical")
        assert text.index("10.2 Spherical") < text.index("Exercises")

    async def test_every_content_figure_is_included_with_its_caption(self, generated):
        _, soup = generated
        figures = soup.find_all("figure")
        assert len(figures) == 3
        captions = [f.figcaption.get_text() for f in figures if f.figcaption]
        assert any("Figure 10.1" in c for c in captions)
        assert all(f.img and f.img.get("src") for f in figures)

    async def test_captions_are_not_duplicated_as_paragraphs(self, generated):
        _, soup = generated
        paragraphs = " ".join(p.get_text() for p in soup.find_all("p"))
        assert "Figure 10.1 Reflection of light" not in paragraphs

    async def test_the_page_is_self_contained(self, generated):
        html, soup = generated
        # the only script allowed is our own inline tab toggler — nothing external
        for script in soup.find_all("script"):
            assert not script.get("src")
            assert "classList" in (script.string or "")
        assert not soup.find("link", rel="stylesheet")
        # figures inline as data URIs when no asset store is configured
        assert all(img["src"].startswith(("data:", "http")) for img in soup.find_all("img"))

    async def test_toc_links_resolve(self, generated):
        _, soup = generated
        for link in soup.select("nav.toc a"):
            assert soup.find(id=link["href"].lstrip("#")) is not None

    async def test_sections_are_real_panels(self, generated):
        _, soup = generated
        panels = soup.select("section.panel")
        assert len(panels) >= 2
        assert all(p.get("id") for p in panels)
        # without JavaScript every panel renders — printing shows the whole document
        assert "body.tabbed section.panel" in generated[0]

    async def test_pipeline_publishes_the_generated_document(self):
        import os

        from models.models import ProcessRequest
        from services.db import MemoryStore
        from services.pipeline import ProcessingPipeline

        here = os.path.dirname(os.path.abspath(__file__))
        store = await MemoryStore().connect()
        await ProcessingPipeline("job-gen", ProcessRequest(
            pdfUrl=os.path.join(here, "fixtures", "chapter.pdf"),
            htmlUrl=os.path.join(here, "fixtures", "chapter.html"),
        ), store).run()
        state = await store.get_job("job-gen")
        assert state.get("generatedHtmlUrl")
        path = state["generatedHtmlUrl"].replace("file://", "")
        with open(path, encoding="utf-8") as fh:
            assert "Light" in fh.read()


class TestTemplateMerge:
    """Missing PDF content is merged into the uploaded HTML's own template."""

    @pytest.fixture(scope="class")
    async def merged(self, pdf_analysis):
        import os

        from bs4 import BeautifulSoup

        from services.comparison_engine import ComparisonEngine
        from services.html_analyzer import HTMLAnalyzer
        from services.html_merger import HTMLMerger

        pdf_analyzer, pdf = pdf_analysis
        here = os.path.dirname(os.path.abspath(__file__))
        analyzer = HTMLAnalyzer(path=os.path.join(here, "fixtures", "chapter.html"))
        html = await analyzer.analyze()
        engine = ComparisonEngine(pdf, html,
                                  {**pdf_analyzer.pixel_cache, **analyzer.pixel_cache})
        engine.generate_issues()
        merger = HTMLMerger(analyzer.rendered_html or analyzer.raw_html, pdf, engine,
                            job_id="merge-test", panel_paths=analyzer.hidden_content)
        result = await merger.merge()
        return result, BeautifulSoup(result, "lxml")

    async def test_the_template_survives(self, merged):
        _, soup = merged
        # the template's own content and structure are untouched
        assert soup.find("h1").get_text().startswith("Chapter 10")
        assert soup.find("ol") is not None

    async def test_missing_content_is_added_in_labelled_cards(self, merged):
        _, soup = merged
        cards = soup.select("div.dcp-added")
        assert cards, "missing PDF content must be added somewhere"
        text = " ".join(c.get_text(" ", strip=True) for c in cards)
        # the fixture HTML drops exercise 4 and figure 10.2
        assert "rear-view mirror" in text
        heads = [c.select_one(".dcp-added-head").get_text() for c in cards]
        assert all("From the textbook" in h for h in heads)

    async def test_missing_figures_are_included(self, merged):
        _, soup = merged
        cards = soup.select("div.dcp-added")
        figures = [f for c in cards for f in c.find_all("figure")]
        assert any("Figure 10.2" in (f.figcaption.get_text() if f.figcaption else "")
                   for f in figures)

    async def test_matched_content_is_not_duplicated(self, merged):
        _, soup = merged
        cards_text = " ".join(c.get_text(" ", strip=True)
                              for c in soup.select("div.dcp-added"))
        # this paragraph exists in the template, so the merge must not re-add it
        assert "highly polished surface" not in cards_text

    async def test_template_scripts_are_removed(self, merged):
        _, soup = merged
        scripts = [t for t in soup.find_all("script")
                   if (t.get("type") or "") not in ("application/json", "application/ld+json")]
        # only our tab toggler may remain, and only when the page has tabs
        for script in scripts:
            assert "activate" in (script.string or "")


class TestFlattenedStackRestore:
    """A stack the template flattened inline gets its line breaks back."""

    async def test_glued_equations_are_cut_out_and_stacked(self, pdf_analysis):
        from bs4 import BeautifulSoup

        from models.models import TextElement
        from services.html_merger import HTMLMerger

        _, pdf = pdf_analysis
        # the template glued the equations: "1 = 11 + 3 = 41 + 3 + 5 = 9."
        html = ('<html><body><main><div class="card">'
                "<p>From this we observe a pattern, as shown below. "
                "1 = 11 + 3 = 41 + 3 + 5 = 9.</p></div></main></body></html>")

        class FakeEngine:
            text_pairs = []
            image_pairs = []
            class html:  # noqa: N801 - duck-typed analysis
                text_elements = []
        engine = FakeEngine()
        merger = HTMLMerger(html, pdf, engine, job_id="t")
        stack_block = TextElement(
            text="1 = 1 1 + 3 = 4 1 + 3 + 5 = 9.",
            lines=["1 = 1", "1 + 3 = 4", "1 + 3 + 5 = 9."], page=1)
        merger.pdf.text_elements.append(stack_block)
        engine.html.text_elements = [TextElement(
            text="From this we observe a pattern, as shown below. "
                 "1 = 11 + 3 = 41 + 3 + 5 = 9.",
            dom_path="html > body:nth-of-type(1) > main:nth-of-type(1) > "
                     "div:nth-of-type(1) > p:nth-of-type(1)")]

        matched = set()
        assert merger._rewrite_flattened_stacks(matched) == 1
        soup = BeautifulSoup(str(merger.soup), "lxml")
        paragraph = soup.find("p")
        assert paragraph.get_text().strip().endswith("as shown below.")
        rows = [d.get_text() for d in soup.select(".mstack div")]
        assert rows == ["1 = 1", "1 + 3 = 4", "1 + 3 + 5 = 9."]
        assert stack_block.id in matched
        merger.pdf.text_elements.remove(stack_block)

    def test_numeric_diagrams_are_not_text_panels(self):
        from models.models import BBox, TextElement
        from services.pdf_analyzer import PDFAnalyzer

        panel = BBox(x0=100, top=100, x1=500, bottom=250)
        numbers = [TextElement(text="1 4 9 16 25 36 3 5 7 9 11 2 2 2 2 Level 1 Level 2",
                               bbox=BBox(x0=150, top=150, x1=450, bottom=200))]
        assert PDFAnalyzer._is_text_panel(panel, numbers) is False


class TestStackFigurePairing:
    """A restored stack lays out beside its figure, as the PDF prints them."""

    async def test_stack_and_placed_figure_wrap_in_a_flex_pair(self, pdf_analysis):
        from bs4 import BeautifulSoup

        from models.models import BBox, ImageElement, TextElement
        from services.html_merger import HTMLMerger

        _, pdf = pdf_analysis
        html = ('<html><body><main><div class="card">'
                "<p>as shown below. 1 = 11 + 3 = 4.</p>"
                '<div class="fig"><img src="https://x/dots.png"><figcaption>dots</figcaption></div>'
                "</div></main></body></html>")

        class FakeEngine:
            text_pairs = []
            image_pairs = []
            class html:  # noqa: N801
                text_elements = []
        engine = FakeEngine()
        merger = HTMLMerger(html, pdf, engine, job_id="t")

        stack_block = TextElement(text="1 = 1 1 + 3 = 4.", lines=["1 = 1", "1 + 3 = 4."],
                                  page=1, bbox=BBox(x0=60, top=200, x1=200, bottom=300))
        figure = ImageElement(page=1, bbox=BBox(x0=300, top=190, x1=520, bottom=310))
        merger.pdf.text_elements.append(stack_block)
        engine.html.text_elements = [TextElement(
            text="as shown below. 1 = 11 + 3 = 4.",
            dom_path="html > body:nth-of-type(1) > main:nth-of-type(1) > "
                     "div:nth-of-type(1) > p:nth-of-type(1)")]

        matched = set()
        assert merger._rewrite_flattened_stacks(matched) == 1
        # the figure was "placed" (as a broken-placeholder replacement would)
        merger._placed_images[figure.id] = merger.soup.find("img")
        merger.pdf.images.append(figure)
        assert merger._pair_stacks_with_figures() == 1

        soup = BeautifulSoup(str(merger.soup), "lxml")
        duo = soup.select_one("div.duo")
        assert duo is not None
        children = [c for c in duo.find_all(True, recursive=False)]
        # PDF order: the stack (x0=60) sits left of the figure (x0=300)
        assert "mstack" in (children[0].get("class") or [])
        img = children[1].find("img")
        assert img is not None
        assert children[1].find("figcaption").get_text() == "dots"
        # the figure is sized by the PDF's own proportions: figure height 120pt
        # vs stack height 100pt over 2 lines -> 2 * 1.8 * 1.2 = 4.32, floored to 7em
        assert "height:7.0em" in (img.get("style") or "")

        merger.pdf.text_elements.remove(stack_block)
        merger.pdf.images.remove(figure)

    async def test_no_pair_when_the_figure_is_elsewhere_on_the_page(self, pdf_analysis):
        from models.models import BBox, ImageElement, TextElement
        from services.html_merger import HTMLMerger

        _, pdf = pdf_analysis
        html = ('<html><body><main><p>as shown below. 1 = 11 + 3 = 4.</p>'
                '<img src="https://x/other.png"></main></body></html>')

        class FakeEngine:
            text_pairs = []
            image_pairs = []
            class html:  # noqa: N801
                text_elements = []
        engine = FakeEngine()
        merger = HTMLMerger(html, pdf, engine, job_id="t")
        stack_block = TextElement(text="1 = 1 1 + 3 = 4.", lines=["1 = 1", "1 + 3 = 4."],
                                  page=1, bbox=BBox(x0=60, top=200, x1=200, bottom=300))
        far_figure = ImageElement(page=1, bbox=BBox(x0=300, top=600, x1=520, bottom=700))
        merger.pdf.text_elements.append(stack_block)
        engine.html.text_elements = [TextElement(
            text="as shown below. 1 = 11 + 3 = 4.",
            dom_path="html > body:nth-of-type(1) > main:nth-of-type(1) > p:nth-of-type(1)")]

        matched = set()
        merger._rewrite_flattened_stacks(matched)
        merger._placed_images[far_figure.id] = merger.soup.find("img")
        merger.pdf.images.append(far_figure)
        assert merger._pair_stacks_with_figures() == 0    # different vertical band

        merger.pdf.text_elements.remove(stack_block)
        merger.pdf.images.remove(far_figure)


class TestBlankPlaceholders:
    """"[blank]" is a placeholder token, not content — cells should be empty."""

    def test_tokens_are_stripped_everywhere_but_scripts(self):
        from bs4 import BeautifulSoup

        from services.html_generator import strip_blank_placeholders

        soup = BeautifulSoup(
            "<table><tr><td>12</td><td>[blank]</td><td> [BLANK] </td></tr></table>"
            "<p>answer: [blank] then more</p>"
            "<script>keep('[blank]')</script>", "lxml")
        assert strip_blank_placeholders(soup) == 3

        cells = [td.get_text() for td in soup.find_all("td")]
        assert cells[0] == "12"
        assert cells[1] == "\xa0" and cells[2] == "\xa0"   # empty, grid keeps shape
        assert soup.p.get_text() == "answer: then more"
        assert "[blank]" in soup.script.string              # code is not content


class TestCropSafety:
    """A figure crop must never slice through a word."""

    class _Page:
        width, height = 600.0, 800.0

    def test_partially_covered_words_are_pulled_inside(self):
        from models.models import BBox
        from services.pdf_analyzer import PDFAnalyzer

        box = BBox(x0=100, top=100, x1=200, bottom=200)
        words = [
            {"x0": 180, "x1": 260, "top": 140, "bottom": 155, "text": "thinking"},
            {"x0": 110, "x1": 150, "top": 120, "bottom": 132, "text": "inside"},
            {"x0": 400, "x1": 450, "top": 500, "bottom": 512, "text": "elsewhere"},
        ]
        grown = PDFAnalyzer._complete_cut_words(box, words, self._Page())
        assert grown.x1 >= 260          # the sliced word is now fully inside
        assert grown.bottom <= 210      # unrelated words did not drag the box

    def test_a_crop_that_would_balloon_is_rejected(self):
        from models.models import BBox
        from services.pdf_analyzer import PDFAnalyzer

        box = BBox(x0=100, top=100, x1=200, bottom=200)
        # a chain of half-covered words marching far beyond the growth bound
        # (each word overlaps the previous extension enough to keep pulling)
        words = [{"x0": 150 + i * 90, "x1": 260 + i * 90,
                  "top": 150, "bottom": 162, "text": f"w{i}"} for i in range(5)]
        assert PDFAnalyzer._complete_cut_words(box, words, self._Page()) is None


class TestUnreadablePdfText:
    """A font without a Unicode mapping must fail loudly once, not 400 times."""

    def test_cid_garbage_is_detected(self):
        from utils.text_matcher import is_unreadable, strip_cid_tokens

        assert is_unreadable("(cid:12)(cid:156)(cid:147)(cid:161) IX")
        assert not is_unreadable("Squares of natural numbers are perfect squares.")
        assert strip_cid_tokens("good (cid:44) text") == "good text"

    async def test_comparison_emits_one_warning_not_hundreds(self, pdf_analysis):
        from models.models import DocumentAnalysis, DocumentType, IssueType, TextElement
        from services.comparison_engine import ComparisonEngine
        from services.html_analyzer import HTMLAnalyzer

        garbage = DocumentAnalysis(doc_type=DocumentType.PDF, text_elements=[
            TextElement(text="(cid:1)(cid:2)(cid:3)(cid:4)(cid:5)", page=p,
                        kind="unreadable") for p in range(1, 11)
        ])
        html = await HTMLAnalyzer(
            html="<html><body><p>Perfectly good readable chapter text here.</p></body></html>",
            render_js=False, fetch_images=False).analyze()
        result = ComparisonEngine(garbage, html).generate_issues()

        types = [i.type for i in result.issues]
        assert types.count(IssueType.EXTRACTION_WARNING) == 1
        assert IssueType.MISSING_TEXT not in types
        assert IssueType.EXTRA_TEXT not in types      # cannot judge without PDF text
        assert IssueType.STRUCTURE_MISMATCH not in types
