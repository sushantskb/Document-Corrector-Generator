"""PDF extraction built on pdfplumber.

Produces the same :class:`DocumentAnalysis` shape as the HTML analyzer so the
comparison engine can treat both sides symmetrically:

* text as reading-ordered paragraph blocks with fonts, sizes, colours and boxes
* images as raster XObjects *and* clustered vector figures (textbook diagrams
  are usually vector art, not embedded bitmaps), each rendered to pixels
* a heading hierarchy inferred from typography
* tables, per-page layout, questions and watermark candidates
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pdfplumber

from models.models import (
    BBox, DocumentAnalysis, DocumentMetadata, DocumentType, ImageElement,
    PageLayout, QuestionElement, StructureElement, TableElement, TextElement,
)
from utils import bbox_utils
from utils.file_utils import download_file, is_url, save_temp_file
from utils.image_matcher import describe_image, is_blank, save_image_bytes
from utils.text_matcher import (
    detect_watermark_text, is_unreadable, looks_like_question, normalize_text,
    parse_question, strip_cid_tokens,
)

logger = logging.getLogger(__name__)

RENDER_DPI = int(os.getenv("PDF_RENDER_DPI", "150"))
MAX_PAGES = int(os.getenv("PDF_MAX_PAGES", "400"))
MIN_FIGURE_AREA_RATIO = 0.004      # ignore rules, underlines and bullet glyphs
MAX_FIGURE_AREA_RATIO = 0.80       # anything this large is a page background, not a figure
OVERLAY_FORM_RATIO = 0.90          # a form covering the whole page is a watermark/background layer
_NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(\.\d+)*)\s+\S")
# Print-production leftovers: "Chapter 1.indd 4  10-07-2025 14:06:40"
_PRINT_ARTIFACT_RE = re.compile(
    r"\.(indd|qxd|pmd|ai|psd)\b|\b\d{2}-\d{2}-\d{4}\s+\d{1,2}:\d{2}(:\d{2})?",
    re.IGNORECASE,
)
_CAPTION_RE = re.compile(r"^\s*(fig(ure)?|table|chart|diagram|photo|image)[\s.:]*\d", re.IGNORECASE)


class PDFAnalyzer:
    """Analyze one PDF from a local path, raw bytes or a Cloudinary URL."""

    def __init__(self, path: Optional[str] = None, data: Optional[bytes] = None,
                 source: Optional[str] = None, render_dpi: int = RENDER_DPI,
                 extract_pixels: bool = True):
        if not path and not data:
            raise ValueError("PDFAnalyzer needs either a file path or PDF bytes")
        if data and not path:
            path = save_temp_file(data, suffix=".pdf")
        self.path = path
        self.source = source or path
        self.render_dpi = render_dpi
        self.extract_pixels = extract_pixels
        self._pdf: Optional[pdfplumber.PDF] = None
        self._clean_doc = None                     # pypdfium2 doc with overlays removed
        self._clean_renders: Dict[int, Any] = {}   # page number -> PIL render
        self._overlays_removed = 0
        self.warnings: List[str] = []
        self.pixel_cache: Dict[str, Any] = {}     # ImageElement.id -> PIL image
        self._body_size: Optional[float] = None

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    async def from_url(cls, url: str, **kwargs) -> "PDFAnalyzer":
        path = await download_file(url, suffix=".pdf") if is_url(url) else url
        return cls(path=path, source=url, **kwargs)

    def open(self) -> pdfplumber.PDF:
        if self._pdf is None:
            self._pdf = pdfplumber.open(self.path)
        return self._pdf

    def close(self) -> None:
        if self._pdf is not None:
            try:
                self._pdf.close()
            finally:
                self._pdf = None
        if self._clean_doc is not None:
            try:
                self._clean_doc.close()
            finally:
                self._clean_doc = None
        self._clean_renders.clear()

    def __enter__(self) -> "PDFAnalyzer":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def pages(self) -> Sequence:
        pdf = self.open()
        if len(pdf.pages) > MAX_PAGES:
            self._warn(f"PDF has {len(pdf.pages)} pages; analyzing the first {MAX_PAGES}")
            return pdf.pages[:MAX_PAGES]
        return pdf.pages

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            logger.warning("[pdf] %s", message)
            self.warnings.append(message)

    # ------------------------------------------------------------------- public
    def analyze(self) -> DocumentAnalysis:
        """Run every extractor and return the full analysis."""
        text_result = self.extract_text()
        text_elements: List[TextElement] = text_result["elements"]
        self.mark_page_furniture(text_elements)
        images = self.extract_images(text_elements)
        tables = self.extract_tables()
        structure = self.extract_structure(text_elements)
        questions = self.extract_questions(text_elements)
        layout = self.get_canvas_structure(text_elements, images, tables)
        self._attach_captions(images, text_elements)

        analysis = DocumentAnalysis(
            doc_type=DocumentType.PDF,
            source=self.source,
            metadata=self.extract_metadata(),
            text_elements=text_elements,
            images=images,
            structure=structure,
            questions=questions,
            tables=tables,
            pages=layout,
            warnings=list(self.warnings),
            stats={
                "text_blocks": len(text_elements),
                "words": sum(len(el.text.split()) for el in text_elements),
                "images": len(images),
                "vector_figures": sum(1 for i in images if i.kind == "vector"),
                "tables": len(tables),
                "headings": len(structure),
                "questions": len(questions),
                "pages": len(layout),
                "body_font_size": self._body_size,
                "unreadable_blocks": sum(1 for e in text_elements
                                         if e.kind == "unreadable"),
                "watermarks": self.detect_watermarks(text_elements),
            },
        )
        return analysis

    # --------------------------------------------------------------------- text
    def extract_text(self) -> Dict[str, Any]:
        """Text as paragraph blocks with location and typography metadata."""
        elements: List[TextElement] = []
        by_page: Dict[int, List[TextElement]] = defaultdict(list)
        order = 0

        for page in self.pages:
            page_no = page.page_number
            try:
                lines = page.extract_text_lines(
                    layout=False, strip=True, return_chars=True
                )
            except Exception as exc:
                self._warn(f"page {page_no}: line extraction failed ({exc})")
                lines = []
            if not lines:
                # scanned page or unusual encoding — fall back to plain text
                raw = page.extract_text() or ""
                if raw.strip():
                    for chunk in raw.split("\n"):
                        if chunk.strip():
                            elements.append(TextElement(
                                text=chunk.strip(), page=page_no, order_index=order,
                            ))
                            by_page[page_no].append(elements[-1])
                            order += 1
                else:
                    self._warn(f"page {page_no}: no extractable text (scanned image?)")
                continue

            for block in self._group_lines(lines):
                element = self._line_block_to_element(block, page_no, order)
                if not element.text:
                    continue
                elements.append(element)
                by_page[page_no].append(element)
                order += 1

        unreadable = 0
        for element in elements:
            if is_unreadable(element.text):
                element.kind = "unreadable"
                unreadable += 1
            else:
                element.text = strip_cid_tokens(element.text)
                element.lines = [strip_cid_tokens(line) for line in element.lines]
        if unreadable:
            self._warn(
                f"{unreadable} of {len(elements)} text blocks could not be decoded "
                "(font without a Unicode mapping) — their text is excluded")
        self._body_size = self._dominant_size(
            [e for e in elements if e.kind != "unreadable"])
        for element in elements:
            if element.kind != "unreadable":
                element.kind = self._classify_kind(element)
        return {
            "elements": elements,
            "by_page": dict(by_page),
            "text": "\n".join(el.text for el in elements),
        }

    def _group_lines(self, lines: List[dict]) -> List[List[dict]]:
        """Merge consecutive lines into paragraph blocks.

        A new block starts on a large vertical gap, a font-size change, or an
        indentation change — the same cues a reader uses.
        """
        blocks: List[List[dict]] = []
        current: List[dict] = []
        prev: Optional[dict] = None
        for line in sorted(lines, key=lambda l: (round(l["top"], 1), l["x0"])):
            if prev is not None:
                gap = line["top"] - prev["bottom"]
                height = max(1.0, prev["bottom"] - prev["top"])
                size_changed = abs(self._line_size(line) - self._line_size(prev)) > 1.0
                indent_changed = abs(line["x0"] - prev["x0"]) > max(18.0, height * 1.5)
                if gap > height * 0.85 or size_changed or indent_changed:
                    blocks.append(current)
                    current = []
            current.append(line)
            prev = line
        if current:
            blocks.append(current)
        return [b for b in blocks if b]

    @classmethod
    def _line_text(cls, line: dict) -> str:
        """Line text, with double-struck glyphs collapsed.

        Some PDFs fake bold by drawing every glyph twice at the same spot, which
        extracts as "CChhaapptteerr 11". The duplicates are identified by their
        position — two identical glyphs at the same coordinates — so ordinary
        double letters are never touched.
        """
        text = (line.get("text") or "").strip()
        chars = line.get("chars") or []
        if len(chars) < 6 or not text:
            return text

        overstruck = 0
        previous = None
        for char in chars:
            if (previous is not None
                    and char.get("text") == previous.get("text")
                    and abs(float(char.get("x0", 0)) - float(previous.get("x0", 0))) < 0.6
                    and abs(float(char.get("top", 0)) - float(previous.get("top", 0))) < 0.6):
                overstruck += 1
            previous = char
        if overstruck < len(chars) * 0.4:
            return text
        return cls._collapse_pairs(text)

    @staticmethod
    def _collapse_pairs(text: str) -> str:
        """"CChhaapptteerr 11..iinndddd" -> "Chapter 1.indd"."""
        result: List[str] = []
        index = 0
        while index < len(text):
            result.append(text[index])
            if index + 1 < len(text) and text[index + 1] == text[index]:
                index += 2      # drop the struck-through duplicate
            else:
                index += 1
        return "".join(result)

    @staticmethod
    def _line_size(line: dict) -> float:
        chars = line.get("chars") or []
        sizes = [c.get("size") for c in chars if c.get("size")]
        return round(sum(sizes) / len(sizes), 2) if sizes else 0.0

    def _line_block_to_element(self, block: List[dict], page_no: int, order: int) -> TextElement:
        line_texts = [re.sub(r"\s+", " ", self._line_text(line)).strip()
                      for line in block]
        line_texts = [line for line in line_texts if line]
        text = " ".join(line_texts)
        chars = [c for line in block for c in (line.get("chars") or [])]
        fonts = Counter(c.get("fontname", "") for c in chars if c.get("fontname"))
        sizes = [c.get("size") for c in chars if c.get("size")]
        font = fonts.most_common(1)[0][0] if fonts else None
        size = round(sum(sizes) / len(sizes), 2) if sizes else None
        bbox = BBox.from_tuple((
            min(l["x0"] for l in block), min(l["top"] for l in block),
            max(l["x1"] for l in block), max(l["bottom"] for l in block),
        ))
        return TextElement(
            text=text, page=page_no, bbox=bbox, font=font, size=size,
            lines=line_texts if len(line_texts) > 1 else [],
            color=self._char_color(chars), order_index=order,
            bold=bool(font and re.search(r"bold|black|heavy|semibold", font, re.I)),
            italic=bool(font and re.search(r"italic|oblique", font, re.I)),
        )

    @staticmethod
    def _char_color(chars: List[dict]) -> Optional[str]:
        for char in chars:
            color = char.get("non_stroking_color")
            if color is None:
                continue
            if isinstance(color, (int, float)):
                value = int(max(0.0, min(1.0, float(color))) * 255)
                return "#%02x%02x%02x" % (value, value, value)
            if isinstance(color, (list, tuple)):
                if len(color) >= 3:
                    r, g, b = (int(max(0.0, min(1.0, float(c))) * 255) for c in color[:3])
                    return "#%02x%02x%02x" % (r, g, b)
                if len(color) == 1:
                    value = int(max(0.0, min(1.0, float(color[0]))) * 255)
                    return "#%02x%02x%02x" % (value, value, value)
        return None

    @staticmethod
    def _dominant_size(elements: List[TextElement]) -> Optional[float]:
        """Body font size = the size covering the most characters."""
        weights: Counter = Counter()
        for element in elements:
            if element.size:
                weights[round(element.size, 1)] += len(element.text)
        return weights.most_common(1)[0][0] if weights else None

    def _classify_kind(self, element: TextElement) -> str:
        text = element.text.strip()
        if _CAPTION_RE.match(text):
            return "caption"
        if re.match(r"^\s*([-•●▪*]|\(?[a-z0-9]{1,3}[.)])\s+\S", text, re.IGNORECASE):
            return "list_item"
        if self._is_heading(element):
            return "heading"
        return "paragraph"

    def _is_heading(self, element: TextElement) -> bool:
        text = element.text.strip()
        if not text or len(text) > 140 or len(text.split()) > 20:
            return False
        # page numbers are often set larger than body text; a heading has words
        if not any(character.isalpha() for character in text):
            return False
        body = self._body_size or 0
        larger = bool(element.size and body and element.size >= body + 0.75)
        emphatic = element.bold and bool(element.size and body and element.size >= body - 0.2)
        numbered = bool(_NUMBERED_HEADING_RE.match(text))
        all_caps = text.isupper() and len(text) > 3
        if not (larger or emphatic or all_caps or (numbered and larger)):
            return False
        return not text.rstrip().endswith((".", ",", ";", ":")) or numbered or all_caps

    # ------------------------------------------------------------------- images
    def extract_images(self, text_elements: Optional[List[TextElement]] = None
                       ) -> List[ImageElement]:
        """Raster XObjects plus clustered vector figures, both rendered to pixels."""
        images: List[ImageElement] = []
        by_page: Dict[int, List[TextElement]] = defaultdict(list)
        for element in (text_elements or []):
            if element.bbox and element.page:
                by_page[element.page].append(element)
        order = 0
        for page in self.pages:
            page_no = page.page_number
            regions: List[Tuple[BBox, str]] = []
            page_area = float(page.width) * float(page.height)
            stamp_sizes = self._repeated_source_sizes()
            for raw in (page.images or []):
                if raw.get("srcsize") and tuple(raw["srcsize"]) in stamp_sizes:
                    continue    # the watermark/background stamp, not a figure
                bbox = self._clamp_bbox(page, (raw["x0"], raw["top"], raw["x1"], raw["bottom"]))
                if bbox is None:
                    continue
                if bbox.area < page_area * (MIN_FIGURE_AREA_RATIO / 4):
                    continue
                if bbox.area >= page_area * MAX_FIGURE_AREA_RATIO:
                    # a picture the size of the page is the paper, not a figure
                    continue
                regions.append((bbox, "raster"))
            for bbox in self.detect_vector_figures(page):
                if any(bbox_utils.iou(bbox, existing) > 0.6 for existing, _ in regions):
                    continue
                if self._is_text_panel(bbox, by_page.get(page_no, [])):
                    continue    # a bordered box full of prose is not a figure
                regions.append((bbox, "vector"))

            for bbox, kind in regions:
                element = ImageElement(
                    page=page_no, bbox=bbox, source="PDF", kind=kind, order_index=order,
                    width=round(bbox.width, 2), height=round(bbox.height, 2),
                )
                if self.extract_pixels:
                    self._render_region(page, element)
                if element.error and kind == "vector":
                    continue    # a figure we cannot render is not worth reporting
                images.append(element)
                order += 1
        self.mark_repeated_stamps(images)
        return images

    def mark_repeated_stamps(self, images: List[ImageElement],
                             min_pages: int = 3) -> int:
        """Flag pictures that recur on page after page.

        A copyright stamp, a logo or a decorative border is drawn on most pages
        of a textbook. Each occurrence looks like a perfectly good figure, so
        without this the HTML gets told it is missing the same watermark
        eighteen times over — and one of them may even be pasted in.
        """
        page_count = len({image.page for image in images if image.page}) or 1
        by_hash: Dict[str, List[ImageElement]] = defaultdict(list)
        for image in images:
            if image.phash:
                by_hash[image.phash].append(image)

        marked = 0
        for occurrences in by_hash.values():
            pages = {image.page for image in occurrences}
            if len(pages) < min_pages or len(pages) < page_count * 0.4:
                continue
            for image in occurrences:
                image.is_decorative = True
                marked += 1
        if marked:
            logger.info("[pdf] ignored %s repeated stamp/watermark image(s)", marked)
        return marked

    @staticmethod
    def _is_text_panel(region: BBox, page_texts: List[TextElement],
                       max_words: int = 20) -> bool:
        """Is this "figure" really a panel of prose?

        Textbooks box their summaries, definitions and worked examples in
        coloured frames. The frame is a cluster of vector shapes, so it looks
        exactly like a diagram — and capturing it as a picture would paste a
        screenshot of text (watermark included) into the HTML, where the same
        words already exist as text.
        """
        words = 0
        for element in page_texts:
            if element.bbox is None:
                continue
            centre_x, centre_y = element.bbox.center
            inside = (region.x0 <= centre_x <= region.x1
                      and region.top <= centre_y <= region.bottom)
            if inside:
                # numbers are diagram content ("1 4 9 16 25 36", "Level 2"),
                # so only alphabetic words argue that this is really prose
                words += sum(1 for token in element.text.split()
                             if sum(ch.isalpha() for ch in token) >= 2)
                if words > max_words:
                    return True
        return False

    def _clamp_bbox(self, page, values) -> Optional[BBox]:
        bbox = BBox.from_tuple(values)
        clamped = BBox(
            x0=max(0.0, min(bbox.x0, float(page.width))),
            top=max(0.0, min(bbox.top, float(page.height))),
            x1=max(0.0, min(bbox.x1, float(page.width))),
            bottom=max(0.0, min(bbox.bottom, float(page.height))),
        )
        if clamped.width < 2 or clamped.height < 2:
            return None
        return clamped

    def _repeated_source_sizes(self) -> set:
        """Pixel sizes of embedded images that recur on page after page.

        The watermark stamp and the paper background are the *same source
        image* stamped on every page (1894×1894 and 2480×3508 in this book),
        while genuine figures each have their own dimensions. The source size
        is therefore a reliable signature — unlike a hash of the rendered
        region, which differs per page because different text sits underneath.
        """
        if getattr(self, "_repeated_sizes", None) is not None:
            return self._repeated_sizes
        pages_by_size: Dict[Tuple[int, int], set] = defaultdict(set)
        for page in self.pages:
            for raw in (page.images or []):
                size = raw.get("srcsize")
                if size:
                    pages_by_size[tuple(size)].add(page.page_number)
        page_count = len(self.pages) or 1
        self._repeated_sizes = {
            size for size, pages in pages_by_size.items()
            if len(pages) >= 3 and len(pages) >= page_count * 0.4
        }
        if self._repeated_sizes:
            logger.info("[pdf] %s repeated source image size(s) treated as stamps: %s",
                        len(self._repeated_sizes), sorted(self._repeated_sizes))
        return self._repeated_sizes

    # ------------------------------------------------- watermark-free rendering
    def _get_clean_doc(self):
        """A pypdfium2 copy of the document with watermark stamps removed.

        The "© NCERT — not to be republished" stamp is the same image drawn
        over the middle of every page, so a figure cropped from a render
        carries it baked into its pixels. Only the stamp *image objects* are
        deleted — never their containing forms, because those can also hold
        the page's mask/blend layers, and removing one of those turns the
        artwork into a black box.

        A repeated image only counts as a stamp to remove when its placement
        covers the middle of the page (15–85% of the page area). Repeated
        full-page layers are compositing masks and small repeated banners are
        headers; both stay.
        """
        if self._clean_doc is not None:
            return self._clean_doc
        import pypdfium2 as pdfium
        import pypdfium2.raw as pr

        stamp_sizes = self._repeated_source_sizes()
        doc = pdfium.PdfDocument(self.path)

        def strip(parent_raw, is_page: bool, page_area: float, depth: int = 0) -> int:
            removed = 0
            count = (pr.FPDFPage_CountObjects(parent_raw) if is_page
                     else pr.FPDFFormObj_CountObjects(parent_raw))
            for index in reversed(range(count)):
                child = (pr.FPDFPage_GetObject(parent_raw, index) if is_page
                         else pr.FPDFFormObj_GetObject(parent_raw, index))
                obj_type = pr.FPDFPageObj_GetType(child)
                if obj_type == pr.FPDF_PAGEOBJ_IMAGE:
                    meta = pr.FPDF_IMAGEOBJ_METADATA()
                    if not pr.FPDFImageObj_GetImageMetadata(child, None, meta):
                        continue
                    if (meta.width, meta.height) not in stamp_sizes:
                        continue
                    left = pr.c_float(); bottom = pr.c_float()
                    right = pr.c_float(); top = pr.c_float()
                    if not pr.FPDFPageObj_GetBounds(child, left, bottom, right, top):
                        continue
                    share = ((right.value - left.value) * (top.value - bottom.value)) / page_area
                    if not (0.15 <= share <= 0.85):
                        continue        # a mask layer or a banner, not the stamp
                    ok = (pr.FPDFPage_RemoveObject(parent_raw, child) if is_page
                          else pr.FPDFFormObj_RemoveObject(parent_raw, child))
                    removed += bool(ok)
                elif obj_type == pr.FPDF_PAGEOBJ_FORM and depth < 4:
                    removed += strip(child, False, page_area, depth + 1)
            return removed

        removed_total = 0
        for page in doc:
            page_area = page.get_width() * page.get_height()
            if page_area <= 0:
                continue
            removed = strip(page.raw, True, page_area)
            if removed:
                pr.FPDFPage_GenerateContent(page.raw)
                removed_total += removed
        self._overlays_removed = removed_total
        if removed_total:
            logger.info("[pdf] removed %s watermark stamp(s) before rendering figures",
                        removed_total)
        self._clean_doc = doc
        return doc

    def _clean_page_render(self, page_no: int):
        """Full-page render (overlays removed), cached — one render per page."""
        if page_no in self._clean_renders:
            return self._clean_renders[page_no]
        doc = self._get_clean_doc()
        image = doc[page_no - 1].render(scale=self.render_dpi / 72.0).to_pil()
        self._clean_renders[page_no] = image
        return image

    def render_region(self, page_number: int, bbox: BBox):
        """A watermark-free render of an arbitrary page region (PIL image)."""
        full = self._clean_page_render(page_number)
        scale = self.render_dpi / 72.0
        return full.crop((
            max(0, int(bbox.x0 * scale)), max(0, int(bbox.top * scale)),
            min(full.width, int(bbox.x1 * scale)),
            min(full.height, int(bbox.bottom * scale)),
        ))

    def _render_region(self, page, element: ImageElement) -> None:
        """Rasterize a page region so it can be hashed and compared to HTML images."""
        try:
            image = None
            try:
                full = self._clean_page_render(page.page_number)
                scale = self.render_dpi / 72.0
                expected_width = int(round(float(page.width) * scale))
                # a rotated or unusual page may not line up; fall back below
                if abs(full.width - expected_width) <= 2:
                    box = element.bbox
                    image = full.crop((
                        max(0, int(box.x0 * scale)), max(0, int(box.top * scale)),
                        min(full.width, int(box.x1 * scale)),
                        min(full.height, int(box.bottom * scale)),
                    ))
            except Exception as exc:
                logger.debug("clean render unavailable for page %s: %s",
                             page.page_number, exc)
            if image is None:
                crop = page.crop(element.bbox.as_tuple(), strict=False)
                image = crop.to_image(resolution=self.render_dpi).original
            if image is None:
                element.error = "empty render"
                return
            if is_blank(image):
                element.error = "blank region"
                return
            data, path = save_image_bytes(image, "PNG")
            info = describe_image(image, data)
            element.phash = info.get("phash")
            element.dhash = info.get("dhash")
            element.sha256 = info.get("sha256")
            element.local_path = path
            self.pixel_cache[element.id] = image
        except Exception as exc:
            element.error = f"render failed: {exc}"
            self._warn(f"page {element.page}: could not render figure ({exc})")

    def detect_vector_figures(self, page, gap: float = 12.0) -> List[BBox]:
        """Cluster curves/rects/lines into diagram regions.

        Textbook figures are frequently vector drawings with no image XObject at
        all; without this they would look 'missing' on the PDF side.
        """
        shapes: List[BBox] = []
        for kind in ("curves", "rects", "lines"):
            for shape in (getattr(page, kind, None) or []):
                bbox = BBox.from_tuple((shape["x0"], shape["top"],
                                        shape["x1"], shape["bottom"]))
                # hairlines (a 0pt-thick axis or tick) are real drawing — pad
                # them to a measurable box instead of discarding them
                if bbox.width < 2:
                    bbox = BBox(x0=bbox.x0 - 1, x1=bbox.x1 + 1,
                                top=bbox.top, bottom=bbox.bottom)
                if bbox.height < 2:
                    bbox = BBox(x0=bbox.x0, x1=bbox.x1,
                                top=bbox.top - 1, bottom=bbox.bottom + 1)
                bbox = BBox(
                    x0=max(0.0, min(bbox.x0, float(page.width))),
                    x1=max(0.0, min(bbox.x1, float(page.width))),
                    top=max(0.0, min(bbox.top, float(page.height))),
                    bottom=max(0.0, min(bbox.bottom, float(page.height))),
                )
                # skip full-width rules and page borders
                if bbox.height < 4 and bbox.width > page.width * 0.5:
                    continue
                if bbox.width >= page.width * 0.97 and bbox.height >= page.height * 0.97:
                    continue
                shapes.append(bbox)
        if len(shapes) < 3:
            return []

        # An axis with sparse ticks — a number line, a "power line" — never
        # clusters by proximity: its marks sit ~40pt apart. Small shapes whose
        # centres share a row and together span a good part of the page width
        # are one diagram strip.
        sparse_strips: List[List[BBox]] = []
        row_buckets: Dict[int, List[BBox]] = defaultdict(list)
        column_buckets: Dict[int, List[BBox]] = defaultdict(list)
        for shape in shapes:
            if shape.width <= 60 and shape.height <= 30:
                row_buckets[int(round((shape.top + shape.bottom) / 2.0 / 16.0))].append(shape)
                column_buckets[int(round((shape.x0 + shape.x1) / 2.0 / 16.0))].append(shape)
        for row in row_buckets.values():
            if len(row) >= 5:
                span = max(b.x1 for b in row) - min(b.x0 for b in row)
                if span >= float(page.width) * 0.3:
                    sparse_strips.append(row)
        # …and its vertical twin: a power line or timeline drawn as a tall
        # axis with ticks marching down one column. Columns living entirely in
        # the page margin are decoration (question-mark icons), not diagrams.
        for column in column_buckets.values():
            if len(column) < 5:
                continue
            span = max(b.bottom for b in column) - min(b.top for b in column)
            right_edge = max(b.x1 for b in column)
            left_edge = min(b.x0 for b in column)
            in_margin = (right_edge < float(page.width) * 0.18
                         or left_edge > float(page.width) * 0.82)
            if span >= float(page.height) * 0.28 and not in_margin:
                sparse_strips.append(column)

        clusters: List[List[BBox]] = list(sparse_strips)
        for bbox in sorted(shapes, key=lambda b: (b.top, b.x0)):
            for cluster in clusters:
                merged = bbox_utils.merge_bboxes(cluster)
                grown = BBox(x0=merged.x0 - gap, top=merged.top - gap,
                             x1=merged.x1 + gap, bottom=merged.bottom + gap)
                if bbox_utils.check_overlap(grown, bbox):
                    cluster.append(bbox)
                    break
            else:
                clusters.append([bbox])

        sparse_ids = {id(strip) for strip in sparse_strips}
        merged_boxes: List[Tuple[BBox, int, bool]] = []
        for cluster in clusters:
            if len(cluster) < 3:
                continue
            merged = bbox_utils.merge_bboxes(cluster)
            if merged is not None:
                merged_boxes.append((merged, len(cluster), id(cluster) in sparse_ids))

        # An arrows-between-rows diagram clusters into thin strips (the arcs)
        # with its number rows in the gaps. Strips that overlap horizontally
        # and sit close vertically are one diagram, grown a little so the rows
        # between the arcs are inside the crop.
        merged_boxes.sort(key=lambda item: item[0].top)
        combined: List[Tuple[BBox, int, int, bool]] = []
        for box, size, sparse in merged_boxes:
            if combined:
                last, strips, total, was_sparse = combined[-1]
                horizontal = (min(last.x1, box.x1) - max(last.x0, box.x0))
                narrower = min(last.width, box.width)
                if (box.top - last.bottom <= 30 and narrower > 0
                        and horizontal >= narrower * 0.5):
                    combined[-1] = (bbox_utils.merge_bboxes([last, box]),
                                    strips + 1, total + size, was_sparse or sparse)
                    continue
            combined.append((box, 1, size, sparse))

        # …and horizontally: a diagram often clusters as fragments along one
        # band (the power-4 line arrived as two slivers 80pt apart). Fragments
        # that share their vertical band and sit near each other are one figure.
        combined.sort(key=lambda item: item[0].x0)
        joined: List[Tuple[BBox, int, int, bool]] = []
        for box, strips, size, sparse in combined:
            merged_flag = False
            for index, (other, other_strips, other_size, other_sparse) in enumerate(joined):
                overlap = (min(other.bottom, box.bottom) - max(other.top, box.top))
                smaller = min(other.height, box.height)
                gap = box.x0 - other.x1
                if smaller > 0 and overlap >= smaller * 0.5 and -10 <= gap <= 60:
                    joined[index] = (bbox_utils.merge_bboxes([other, box]),
                                     other_strips + strips, other_size + size,
                                     other_sparse or sparse)
                    merged_flag = True
                    break
            if not merged_flag:
                joined.append((box, strips, size, sparse))
        combined = joined

        figures: List[BBox] = []
        page_area = float(page.width) * float(page.height)
        label_lines = self._label_lines(page)
        page_words = page.extract_words() or []
        for box, strips, size, sparse in combined:
            arcs_row = (size >= 5 and box.height > 0
                        and box.width >= box.height * 6)
            if strips > 1 or arcs_row or sparse:
                # An arrows diagram owns more than its arcs: the number rows,
                # the row labels ("Level 1"), the title above and the trailing
                # ellipses. Growing by blind pixels clips all of those, so the
                # crop extends to every label-like text line the diagram's band
                # touches — and a lone thin arcs row (the Perfect Cubes strip)
                # only clears the size floor once its numbers are included.
                box = self._extend_to_labels(box, label_lines, page)
            box = self._complete_cut_words(box, page_words, page)
            if box is not None and box.area >= page_area * MIN_FIGURE_AREA_RATIO:
                figures.append(box)
        return figures

    @staticmethod
    def _complete_cut_words(box: Optional[BBox], words, page,
                            max_growth: float = 110.0) -> Optional[BBox]:
        """Extend a crop so it never slices through a word.

        A figure box whose edge crosses the middle of a word produces the
        tell-tale half-rendered text ("What do you thi…"). Any word the box
        partially covers is pulled fully inside — bounded, and if honouring
        that would balloon the crop past the bound, the figure is rejected
        rather than shipped mangled.
        """
        if box is None:
            return None
        original = box
        for _ in range(4):
            grew = False
            for word in words:
                wx0, wx1 = float(word["x0"]), float(word["x1"])
                wtop, wbottom = float(word["top"]), float(word["bottom"])
                ix = min(box.x1, wx1) - max(box.x0, wx0)
                iy = min(box.bottom, wbottom) - max(box.top, wtop)
                if ix <= 0 or iy <= 0:
                    continue
                covered = (ix * iy) / max(1e-6, (wx1 - wx0) * (wbottom - wtop))
                if covered >= 0.98 or covered < 0.15:
                    continue        # fully inside, or barely grazed
                box = BBox(x0=min(box.x0, wx0), x1=max(box.x1, wx1),
                           top=min(box.top, wtop), bottom=max(box.bottom, wbottom))
                grew = True
            if not grew:
                break
        if (original.x0 - box.x0 > max_growth or box.x1 - original.x1 > max_growth
                or original.top - box.top > max_growth
                or box.bottom - original.bottom > max_growth):
            return None
        return BBox(x0=max(0.0, box.x0), top=max(0.0, box.top),
                    x1=min(float(page.width), box.x1),
                    bottom=min(float(page.height), box.bottom))

    def _label_lines(self, page) -> List[Tuple[BBox, int]]:
        """Text lines grouped with their alphabetic word count."""
        rows: Dict[int, List[dict]] = defaultdict(list)
        for word in (page.extract_words() or []):
            rows[int(round(word["top"] / 4.0))].append(word)
        lines: List[Tuple[BBox, int]] = []
        for words in rows.values():
            bbox = BBox.from_tuple((
                min(w["x0"] for w in words), min(w["top"] for w in words),
                max(w["x1"] for w in words), max(w["bottom"] for w in words),
            ))
            alpha = sum(1 for w in words
                        if sum(ch.isalpha() for ch in w["text"]) >= 2)
            lines.append((bbox, alpha))
        return lines

    @staticmethod
    def _extend_to_labels(box: BBox, label_lines, page) -> BBox:
        """Grow a diagram's box over its own numbers, labels and title.

        A line joins the diagram when it is label-like (at most three
        alphabetic words — "Perfect Squares", "Level 1 3 5 7 9 11 …") and its
        centre lies in or just above/below the diagram's band. Prose never
        qualifies, so body text cannot drag the crop across the page.
        """
        for _ in range(2):      # a joined line can bring the next one in reach
            for line_box, alpha_words in label_lines:
                if alpha_words > 3:
                    continue
                centre_y = (line_box.top + line_box.bottom) / 2.0
                if box.top - 26 <= centre_y <= box.bottom + 18:
                    box = bbox_utils.merge_bboxes([box, line_box])
        return BBox(
            x0=max(0.0, box.x0 - 6),
            x1=min(float(page.width), box.x1 + 6),
            top=max(0.0, box.top - 6),
            bottom=min(float(page.height), box.bottom + 8),
        )

    def _attach_captions(self, images: List[ImageElement], texts: List[TextElement]) -> None:
        """Give each figure the nearest caption line below it (used as alt text)."""
        captions = [t for t in texts if t.kind == "caption" and t.bbox]
        for image in images:
            if not image.bbox:
                continue
            candidates = [
                t for t in captions
                if t.page == image.page and t.bbox.top >= image.bbox.bottom - 4
                and t.bbox.top - image.bbox.bottom < 60
            ]
            if candidates:
                nearest = min(candidates, key=lambda t: t.bbox.top - image.bbox.bottom)
                image.caption = nearest.text
                image.alt = nearest.text

    # ------------------------------------------------------------------ structure
    def extract_structure(self, text_elements: Optional[List[TextElement]] = None
                          ) -> List[StructureElement]:
        """Heading hierarchy inferred from font size, weight and numbering."""
        elements = text_elements if text_elements is not None else self.extract_text()["elements"]
        headings = [el for el in elements if el.kind == "heading"]
        if not headings:
            return []

        sizes = sorted({round(h.size, 1) for h in headings if h.size}, reverse=True)
        size_to_level = {size: min(idx + 1, 6) for idx, size in enumerate(sizes)}

        structure: List[StructureElement] = []
        stack: List[StructureElement] = []
        for order, heading in enumerate(headings):
            level = size_to_level.get(round(heading.size, 1), 3) if heading.size else 3
            numbering = _NUMBERED_HEADING_RE.match(heading.text.strip())
            if numbering:      # "2.3.1 Reflection" -> depth 3 regardless of size
                level = min(6, numbering.group(1).count(".") + 1)
            node = StructureElement(
                title=heading.text.strip(), level=level, page=heading.page,
                order_index=order, tag=f"h{level}",
            )
            while stack and stack[-1].level >= level:
                stack.pop()
            if stack:
                node.parent_id = stack[-1].id
                stack[-1].children.append(node.id)
            stack.append(node)
            structure.append(node)
        return structure

    # --------------------------------------------------------------------- tables
    def extract_tables(self) -> List[TableElement]:
        tables: List[TableElement] = []
        for page in self.pages:
            try:
                found = page.find_tables()
            except Exception as exc:
                self._warn(f"page {page.page_number}: table detection failed ({exc})")
                continue
            for table in found:
                try:
                    rows = table.extract()
                except Exception:
                    rows = []
                cells = [[(c or "").strip() for c in row] for row in rows]
                tables.append(TableElement(
                    page=page.page_number,
                    bbox=self._clamp_bbox(page, table.bbox),
                    rows=len(cells),
                    cols=max((len(r) for r in cells), default=0),
                    text=" | ".join(" ".join(r) for r in cells).strip(),
                    cells=cells,
                ))
        return tables

    # ------------------------------------------------------------------ questions
    def extract_questions(self, text_elements: Optional[List[TextElement]] = None
                          ) -> List[QuestionElement]:
        elements = text_elements if text_elements is not None else self.extract_text()["elements"]
        questions: List[QuestionElement] = []
        for element in elements:
            if element.kind == "unreadable":
                continue
            if not looks_like_question(element.text):
                continue
            parsed = parse_question(element.text) or {}
            questions.append(QuestionElement(
                number=parsed.get("number"),
                text=parsed.get("text") or element.text,
                options=parsed.get("options") or [],
                page=element.page,
                order_index=len(questions),
            ))
        return questions

    # -------------------------------------------------------------------- layout
    def get_canvas_structure(self, text_elements: Optional[List[TextElement]] = None,
                             images: Optional[List[ImageElement]] = None,
                             tables: Optional[List[TableElement]] = None) -> List[PageLayout]:
        """Per-page geometry: size, rotation, element counts and column count."""
        texts = text_elements if text_elements is not None else self.extract_text()["elements"]
        images = images if images is not None else []
        tables = tables if tables is not None else []
        layouts: List[PageLayout] = []
        for page in self.pages:
            page_no = page.page_number
            page_texts = [t for t in texts if t.page == page_no and t.bbox]
            layouts.append(PageLayout(
                page=page_no,
                width=float(page.width),
                height=float(page.height),
                rotation=int(getattr(page, "rotation", 0) or 0),
                text_blocks=len(page_texts),
                images=sum(1 for i in images if i.page == page_no),
                tables=sum(1 for t in tables if t.page == page_no),
                rects=len(page.rects or []),
                lines=len(page.lines or []),
                curves=len(page.curves or []),
                columns=bbox_utils.detect_columns(
                    [t.bbox for t in page_texts], float(page.width)
                ),
            ))
        return layouts

    # ----------------------------------------------------------------- metadata
    def extract_metadata(self) -> DocumentMetadata:
        pdf = self.open()
        raw = {k: str(v) for k, v in (pdf.metadata or {}).items()}
        return DocumentMetadata(
            title=raw.get("Title"),
            author=raw.get("Author"),
            subject=raw.get("Subject"),
            creator=raw.get("Creator"),
            producer=raw.get("Producer"),
            page_count=len(pdf.pages),
            created_at=raw.get("CreationDate"),
            modified_at=raw.get("ModDate"),
            extra=raw,
        )

    def mark_page_furniture(self, elements: List[TextElement],
                            margin_ratio: float = 0.12) -> int:
        """Tag running headers, footers and page numbers as furniture.

        A chapter title reprinted at the top of every page is part of the print
        layout, not of the chapter's content — an HTML rendition has no reason
        to repeat it, and reporting each occurrence as missing text buries the
        real findings. Only blocks that sit in a margin band on *every*
        appearance are treated this way, so a heading that merely recurs in the
        body is left alone.
        """
        heights = {page.page_number: float(page.height) for page in self.pages}
        page_count = len(heights) or 1
        occurrences: Dict[str, List[TextElement]] = defaultdict(list)
        for element in elements:
            normalized = normalize_text(element.text)
            if normalized and element.bbox and element.page:
                occurrences[normalized].append(element)

        def in_margin(element: TextElement) -> bool:
            height = heights.get(element.page or 0, 0.0)
            if not height:
                return False
            band = height * margin_ratio
            return element.bbox.top <= band or element.bbox.bottom >= height - band

        marked = 0
        for normalized, found in occurrences.items():
            pages = {e.page for e in found}
            repeated = len(pages) >= 3 and len(pages) >= page_count * 0.4
            page_number = normalized.isdigit() and len(normalized) <= 4
            # A layout-file name or an export timestamp is never chapter content,
            # and it carries a different page number each time, so repetition
            # alone will not catch it.
            artifact = bool(_PRINT_ARTIFACT_RE.search(found[0].text))
            if not (repeated or page_number or artifact):
                continue
            if not all(in_margin(element) for element in found):
                continue
            for element in found:
                element.kind = "furniture"
                marked += 1
        if marked:
            logger.info("[pdf] ignored %s running header/footer block(s)", marked)
        return marked

    # ---------------------------------------------------------------- watermarks
    def detect_watermarks(self, text_elements: List[TextElement]) -> List[Dict[str, Any]]:
        """Short strings repeated across most pages, or matching known hints."""
        page_count = len({el.page for el in text_elements if el.page}) or 1
        occurrences: Dict[str, set] = defaultdict(set)
        for element in text_elements:
            normalized = normalize_text(element.text)
            if 3 <= len(normalized) <= 80:
                occurrences[normalized].add(element.page)

        watermarks: List[Dict[str, Any]] = []
        for text, pages in occurrences.items():
            hint = detect_watermark_text(text)
            repeated = page_count >= 3 and len(pages) >= max(3, int(page_count * 0.8))
            if hint or repeated:
                watermarks.append({
                    "text": text,
                    "pages": sorted(p for p in pages if p),
                    "reason": "hint" if hint else "repeated-on-every-page",
                    "confidence": 0.9 if hint else 0.6,
                })
        return sorted(watermarks, key=lambda w: -w["confidence"])[:20]
