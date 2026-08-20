"""Generate a fresh HTML document directly from a PDF analysis.

Patching an existing HTML works when that HTML tries to mirror the PDF. When it
is an *enriched* rendition — a study guide with its own structure — inserting
every "missing" PDF block can only wedge fragments between cards that were never
meant to hold them. This module is the alternative: walk the PDF's own content
in reading order and emit a clean, self-contained page.

Everything hard was already done by the analyzer: paragraphs merged from lines,
headings ranked into a hierarchy, running headers and watermarks filtered out,
and figures rendered without the publisher's stamp.
"""

from __future__ import annotations

import base64
import html as html_escape
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from models.models import BBox, DocumentAnalysis, ImageElement, TableElement, TextElement
from utils import bbox_utils
from services.cloudinary_client import CloudinaryClient
from utils.hash_utils import calculate_sha256
from utils.text_matcher import fuzzy_match, is_math_stack

logger = logging.getLogger(__name__)

_BULLET_RE = re.compile(r"^\s*[•●▪◦‣*]\s*")

_TABLE_STYLE = """
table.gen { border-collapse:collapse; margin:1.4em auto; max-width:100%;
            font:15px/1.5 'Segoe UI',system-ui,-apple-system,sans-serif;
            box-shadow:0 2px 10px rgba(30,60,120,.08); border-radius:10px; overflow:hidden; }
table.gen td, table.gen th { border:1px solid #dbe4f3; padding:9px 16px; text-align:center; }
table.gen tr:first-child td { background:linear-gradient(135deg,#3b82f6,#6366f1);
                              color:#fff; font-weight:600; border-color:#5b7fd6; }
table.gen tr:nth-child(even) td { background:#f2f6ff; }
div.scroll-x { overflow-x:auto; }
"""

_STYLE = _TABLE_STYLE + """
/* Deliberately a single bright look — this document does not follow the
   viewer's dark scheme; it is meant to read like a colourful study page. */
:root { --ink:#1e293b; --muted:#64748b; --border:#e2e8f0;
        --c1:#3b82f6; --c2:#8b5cf6; --c3:#ec4899; --c4:#f59e0b;
        --c5:#10b981; --c6:#06b6d4; }
* { box-sizing:border-box; }
body { margin:0; background:#eef3fb; color:var(--ink);
       font:17px/1.7 Georgia,'Times New Roman',serif; }
header.doc { background:linear-gradient(120deg,#4f8ef7 0%,#7c6cf0 55%,#b16cf0 100%);
             color:#fff; padding:46px 24px 40px; text-align:center; }
header.doc h1 { margin:0; font:800 2.2rem/1.2 'Segoe UI',system-ui,sans-serif;
                letter-spacing:.5px; text-shadow:0 2px 8px rgba(20,40,90,.25); }
header.doc p { margin:10px 0 0; opacity:.9;
               font:14px/1.4 'Segoe UI',system-ui,sans-serif; }
nav.toc { position:sticky; top:0; z-index:10; background:#ffffffee;
          backdrop-filter:blur(6px); border-bottom:1px solid var(--border);
          padding:10px 14px; display:flex; flex-wrap:wrap; gap:8px;
          justify-content:center; font:600 14px/1.2 'Segoe UI',system-ui,sans-serif; }
nav.toc a { color:var(--ink); text-decoration:none; padding:8px 16px;
            border-radius:999px; background:#f1f5f9; border:1.5px solid var(--border);
            transition:all .15s; }
nav.toc a:hover { border-color:var(--c1); color:var(--c1); }
nav.toc a.active { color:#fff; border-color:transparent;
                   background:linear-gradient(135deg,var(--c1),var(--c2)); }
main { max-width:900px; margin:0 auto; padding:28px 20px 80px; }
section.panel { background:#fff; border-radius:16px; padding:34px 38px;
                margin:26px 0; border-top:5px solid var(--c1);
                box-shadow:0 4px 18px rgba(30,60,120,.07); }
section.panel:nth-of-type(6n+2) { border-top-color:var(--c2); }
section.panel:nth-of-type(6n+3) { border-top-color:var(--c3); }
section.panel:nth-of-type(6n+4) { border-top-color:var(--c4); }
section.panel:nth-of-type(6n+5) { border-top-color:var(--c5); }
section.panel:nth-of-type(6n)   { border-top-color:var(--c6); }
body.tabbed section.panel { display:none; }
body.tabbed section.panel.active { display:block; }
h2 { font:700 1.5rem/1.3 'Segoe UI',system-ui,sans-serif; color:var(--c1);
     margin:0 0 .7em; }
section.panel:nth-of-type(6n+2) h2 { color:var(--c2); }
section.panel:nth-of-type(6n+3) h2 { color:var(--c3); }
section.panel:nth-of-type(6n+4) h2 { color:#d97706; }
section.panel:nth-of-type(6n+5) h2 { color:#059669; }
section.panel:nth-of-type(6n)   h2 { color:#0891b2; }
h3 { font:700 1.15rem/1.3 'Segoe UI',system-ui,sans-serif; color:#334155;
     margin:1.6em 0 .5em; }
h4,h5,h6 { margin:1.4em 0 .4em; font-family:'Segoe UI',system-ui,sans-serif; }
p { margin:.75em 0; }
ul { margin:.75em 0; padding-left:1.5em; }
li { margin:.4em 0; }
li::marker { color:var(--c2); }
figure { margin:1.8em 0; text-align:center; }
figure img { max-width:100%; height:auto; border-radius:12px; background:#fff;
             box-shadow:0 3px 14px rgba(30,60,120,.12); }
figcaption { margin-top:.6em; font:italic 14px/1.4 Georgia,serif; color:var(--muted); }
.mstack { margin:1em 0 1em 1.5em; }
.mstack div { margin:.15em 0; font-size:1.05em; }
.duo { display:flex; gap:34px; align-items:center; flex-wrap:wrap;
       justify-content:flex-start; margin:1.2em 0; }
.duo > * { flex:0 1 auto; min-width:0; margin:0; }
.duo .mstack { flex:0 0 auto; }
.duo .duo-fig img { width:auto; max-width:100%; }
footer.doc { text-align:center; color:var(--muted); padding:26px;
             font:13px/1.4 'Segoe UI',system-ui,sans-serif; }
@media print { body.tabbed section.panel { display:block !important; }
               nav.toc { display:none; } }
"""

_TAB_SCRIPT = """
(function () {
  document.body.classList.add('tabbed');
  var links = Array.prototype.slice.call(document.querySelectorAll('nav.toc a'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('section.panel'));
  function activate(id, push) {
    panels.forEach(function (p) { p.classList.toggle('active', p.id === id); });
    links.forEach(function (a) {
      a.classList.toggle('active', a.getAttribute('href') === '#' + id);
    });
    if (push) history.replaceState(null, '', '#' + id);
    window.scrollTo(0, 0);
  }
  links.forEach(function (a) {
    a.addEventListener('click', function (event) {
      event.preventDefault();
      activate(a.getAttribute('href').slice(1), true);
    });
  });
  var initial = location.hash.slice(1);
  if (!panels.some(function (p) { return p.id === initial; })) {
    initial = panels.length ? panels[0].id : '';
  }
  if (initial) activate(initial, false);
})();
"""

class HTMLGenerator:
    """Build one clean HTML document from a :class:`DocumentAnalysis` of a PDF."""

    def __init__(self, analysis: DocumentAnalysis,
                 uploader: Optional[CloudinaryClient] = None,
                 job_id: Optional[str] = None,
                 title: Optional[str] = None):
        self.analysis = analysis
        self.uploader = uploader or CloudinaryClient()
        self.job_id = job_id
        self.title = title
        self.warnings: List[str] = []
        self._heading_levels = self._map_heading_levels()

    # ------------------------------------------------------------------ public
    async def generate(self) -> str:
        """The complete document as a self-contained HTML string.

        Content is grouped into one <section> per top-level PDF section, shown
        one at a time behind a tab bar (all sections render when JavaScript is
        off or the page is printed). The tab script is our own dozen lines of
        class toggling — it never builds content, so nothing depends on it.
        """
        sections = await self._render_sections()
        title = html_escape.escape(self._document_title())
        subtitle = self._subtitle()

        toc_html = ""
        if len(sections) >= 2:
            links = "".join(
                f'<a href="#{anchor}">{html_escape.escape(label)}</a>'
                for anchor, label, _ in sections
            )
            toc_html = f"<nav class=\"toc\">{links}</nav>"

        body = "\n".join(
            f'<section class="panel" id="{anchor}">' + "\n".join(parts) + "</section>"
            for anchor, _, parts in sections
        )
        script = f"<script>{_TAB_SCRIPT}</script>" if len(sections) >= 2 else ""

        return (
            "<!doctype html>\n"
            f"<html lang=\"{self.analysis.metadata.language or 'en'}\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{title}</title>\n"
            "<meta name=\"generator\" content=\"document-correction-platform\">\n"
            f"<style>{_STYLE}</style>\n"
            "</head>\n<body>\n"
            f"<header class=\"doc\"><h1>{title}</h1>{subtitle}</header>\n"
            f"{toc_html}\n<main>\n{body}\n</main>\n"
            "<footer class=\"doc\">Generated from the source PDF by the "
            "Document Correction Platform.</footer>\n"
            f"{script}\n</body>\n</html>\n"
        )

    # ----------------------------------------------------------------- content
    async def _render_sections(self) -> List[Tuple[str, str, List[str]]]:
        """(anchor, label, rendered parts) per top-level section.

        A new section starts at every level-1/2 heading; whatever precedes the
        first one becomes an introduction panel. The document title is not
        repeated inside the body — it is already the page header.
        """
        title = self._document_title()
        sections: List[Tuple[str, str, List[str]]] = []
        parts: List[str] = []
        label = "Introduction"
        open_list = False

        def close_list():
            nonlocal open_list
            if open_list:
                parts.append("</ul>")
                open_list = False

        def flush():
            nonlocal parts
            if any(not part.startswith("<h2") for part in parts):
                anchor = f"section-{len(sections) + 1}"
                sections.append((anchor, _clip(label, 28), parts))
            parts = []

        stream = self._reading_stream()
        skip_next = False
        for position, (kind, element) in enumerate(stream):
            if skip_next:
                skip_next = False
                continue
            # a displayed equation stack with its figure beside it — the PDF's
            # own layout — renders side by side instead of stacked pages apart
            if kind not in ("image", "table") and is_math_stack(element.lines) \
                    and position + 1 < len(stream):
                next_kind, next_element = stream[position + 1]
                if next_kind == "image" and self._beside(element, next_element):
                    close_list()
                    height_em = self.paired_image_height_em(element, next_element)
                    figure_html = await self._figure(next_element, height_em=height_em)
                    parts.append(f'<div class="duo">{self._stack_html(element)}'
                                 f"{figure_html}</div>")
                    skip_next = True
                    continue
            if kind == "image":
                close_list()
                parts.append(await self._figure(element))
                continue
            if kind == "table":
                close_list()
                parts.append(self._table(element))
                continue

            text = element.text.strip()
            if not text:
                continue
            if kind != "heading" and is_math_stack(element.lines):
                close_list()
                parts.append(self._stack_html(element))
                continue
            if kind == "heading":
                level = self._heading_levels.get(element.id, 3)
                if fuzzy_match(text, title) > 0.9:
                    continue        # the page header already says this
                close_list()
                if level <= 2:
                    flush()
                    label = text
                    parts.append(f"<h2>{html_escape.escape(text)}</h2>")
                else:
                    parts.append(f"<h{min(6, level)}>{html_escape.escape(text)}"
                                 f"</h{min(6, level)}>")
            elif kind == "list_item":
                if not open_list:
                    parts.append("<ul>")
                    open_list = True
                parts.append(f"<li>{html_escape.escape(_BULLET_RE.sub('', text))}</li>")
            else:
                if len(text) <= 3 and text.replace(".", "").isdigit():
                    continue    # a stray page/chapter number, not a sentence
                close_list()
                parts.append(f"<p>{html_escape.escape(text)}</p>")
        close_list()
        flush()
        return sections

    def _reading_stream(self) -> List[Tuple[str, Any]]:
        """Text blocks and figures interleaved in reading order.

        Furniture (running headers, page numbers), decorative stamps and
        standalone caption lines are dropped — captions travel with their
        figure, and the rest belongs to the print layout, not the content.
        """
        entries: List[Tuple[Tuple, str, Any]] = []
        figure_captions = [
            image.caption for image in self.analysis.images
            if image.caption and not image.is_decorative
        ]
        tables = self._usable_tables()
        images = self._usable_images(tables)

        # Regions whose words are already shown by a table or a drawn diagram:
        # repeating them as loose paragraphs is the "same table twice" defect.
        # Raster figures are excluded from this — text a PDF paints *inside* an
        # embedded picture is not extractable, so any text overlapping a raster
        # figure is real content sitting on top of it and must be kept.
        covered: Dict[int, List[BBox]] = {}
        heading_safe: Dict[int, List[BBox]] = {}
        for table in tables:
            covered.setdefault(table.page or 0, []).append(table.bbox)
        for image in images:
            if image.bbox is not None and image.kind == "vector":
                heading_safe.setdefault(image.page or 0, []).append(image.bbox)

        for element in self.analysis.text_elements:
            if element.kind in ("furniture", "unreadable") or not element.text.strip():
                continue
            if element.kind == "caption" and any(
                fuzzy_match(element.text, caption) > 0.9 for caption in figure_captions
            ):
                continue        # rendered as the figure's <figcaption>
            if element.bbox is not None:
                page_no = element.page or 0
                if self._inside_any(element.bbox, covered.get(page_no, ())):
                    continue
                # a diagram's internal labels are part of the picture — but a
                # heading is document structure and is never swallowed
                if element.kind != "heading" and self._inside_any(
                    element.bbox, heading_safe.get(page_no, ())
                ):
                    continue
            top = element.bbox.top if element.bbox else 0.0
            left = element.bbox.x0 if element.bbox else 0.0
            entries.append(((element.page or 0, top, left, 0), element.kind, element))

        for image in images:
            top = image.bbox.top if image.bbox else 0.0
            left = image.bbox.x0 if image.bbox else 0.0
            entries.append(((image.page or 0, top, left, 1), "image", image))

        for table in tables:
            entries.append(((table.page or 0, table.bbox.top, table.bbox.x0, 1),
                            "table", table))

        entries.sort(key=lambda item: item[0])
        return [(kind, element) for _, kind, element in entries]

    def _usable_tables(self) -> List[TableElement]:
        """Tables worth rebuilding as real <table> markup.

        pdfplumber reports every ruled region as a table; only grids with at
        least two rows and columns and mostly filled cells are trustworthy
        enough to re-emit as structure.
        """
        usable: List[TableElement] = []
        for table in self.analysis.tables:
            if table.bbox is None or not table.cells:
                continue
            if table.rows < 2 or table.cols < 2:
                continue
            cells = [cell for row in table.cells for cell in row]
            filled = sum(1 for cell in cells if cell.strip())
            if cells and filled / len(cells) >= 0.5:
                usable.append(table)
        return usable

    def _usable_images(self, tables: Sequence[TableElement]) -> List[ImageElement]:
        """Figures, deduplicated and minus regions a rebuilt table covers.

        Two rules learned from real chapters. A ruled table is detected both
        ways — as a vector "figure" and as a table — and the real <table> wins.
        And a banner is often accompanied by its own sub-images (the chapter
        number badge, a QR code) exported as separate objects: a figure that
        sits inside a larger kept figure would repeat on the page, so only the
        outermost one stays.
        """
        candidates: List[ImageElement] = []
        for image in self.analysis.images:
            if image.is_decorative or image.error:
                continue
            if image.bbox is not None and any(
                bbox_utils.iou(image.bbox, table.bbox) > 0.5
                for table in tables if (table.page or 0) == (image.page or 0)
            ):
                continue
            candidates.append(image)

        # largest first, so containers are kept and their fragments dropped
        candidates.sort(key=lambda i: -(i.bbox.area if i.bbox else 0))
        kept: List[ImageElement] = []
        for image in candidates:
            contained = image.bbox is not None and any(
                other.bbox is not None and (other.page or 0) == (image.page or 0)
                and bbox_utils.intersection_area(image.bbox, other.bbox)
                    >= image.bbox.area * 0.7
                for other in kept
            )
            if not contained:
                kept.append(image)
        return kept

    @staticmethod
    def _inside_any(box: BBox, regions) -> bool:
        centre_x, centre_y = box.center
        return any(region.x0 <= centre_x <= region.x1
                   and region.top <= centre_y <= region.bottom
                   for region in regions)

    @staticmethod
    def _stack_html(element: TextElement) -> str:
        rows = "".join(f"<div>{html_escape.escape(line)}</div>" for line in element.lines)
        return f'<div class="mstack">{rows}</div>'

    # each rendered stack line is roughly this tall, in em of body text
    _STACK_LINE_EM = 1.8

    @classmethod
    def paired_image_height_em(cls, stack_block: TextElement,
                               image: "ImageElement") -> float:
        """How tall a figure paired with a stack should render.

        The PDF already decided the proportions: both elements have measured
        heights on the page, so the figure is sized by the ratio of its height
        to the stack's — globally, for every pair, instead of letting the
        image's native resolution dominate the row.
        """
        stack_lines = max(2, len(stack_block.lines or []))
        ratio = 1.0
        if stack_block.bbox is not None and image.bbox is not None \
                and stack_block.bbox.height > 0:
            ratio = image.bbox.height / stack_block.bbox.height
        height = stack_lines * cls._STACK_LINE_EM * ratio
        return round(min(26.0, max(7.0, height)), 1)

    @staticmethod
    def _beside(text_element: TextElement, image: "ImageElement") -> bool:
        """Do these two share their vertical band on the same page?"""
        if (text_element.page != image.page or text_element.bbox is None
                or image.bbox is None):
            return False
        overlap = (min(text_element.bbox.bottom, image.bbox.bottom)
                   - max(text_element.bbox.top, image.bbox.top))
        smaller = min(text_element.bbox.height, image.bbox.height)
        return smaller > 0 and overlap >= smaller * 0.5

    def _table(self, table: TableElement) -> str:
        rows = []
        for row in table.cells:
            cells = "".join(
                f"<td>{html_escape.escape((cell or '').strip())}</td>" for cell in row
            )
            rows.append(f"<tr>{cells}</tr>")
        return ('<div class="scroll-x"><table class="gen">'
                + "".join(rows) + "</table></div>")

    async def _figure(self, image: ImageElement,
                      height_em: Optional[float] = None) -> str:
        src = await self._host(image)
        if not src:
            return ""
        caption = ""
        if image.caption:
            caption = f"<figcaption>{html_escape.escape(image.caption)}</figcaption>"
        alt = html_escape.escape(image.alt or image.caption or "Figure from the source PDF")
        sizing = (f' style="height:{height_em}em;width:auto;max-width:100%"'
                  if height_em else "")
        klass = ' class="duo-fig"' if height_em else ""
        return (f'<figure{klass}><img src="{src}" alt="{alt}" loading="lazy"{sizing}>'
                f"{caption}</figure>")

    async def _host(self, image: ImageElement) -> Optional[str]:
        if image.cloudinary_url:
            return image.cloudinary_url
        if not image.local_path or not os.path.exists(image.local_path):
            return None
        with open(image.local_path, "rb") as fh:
            data = fh.read()
        digest = image.sha256 or calculate_sha256(data)
        result = await self.uploader.upload_bytes(
            data, subfolder="figures", public_id=digest[:32],
        )
        if result and result.get("url"):
            image.cloudinary_url = result["url"]
            return result["url"]
        self._warn("Cloudinary unavailable; figure embedded as a data URI")
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    # ----------------------------------------------------------------- helpers
    def _map_heading_levels(self) -> Dict[str, int]:
        """heading text-element id -> its level from the structure pass.

        extract_structure() builds its nodes from the heading elements in
        document order, so zipping the two sequences recovers the mapping.
        """
        headings = [e for e in self.analysis.text_elements if e.kind == "heading"]
        levels: Dict[str, int] = {}
        for element, node in zip(headings, self.analysis.structure):
            levels[element.id] = node.level
        return levels

    def _document_title(self) -> str:
        if self.title:
            return self.title
        if self.analysis.metadata.title:
            return self.analysis.metadata.title
        for node in self.analysis.structure:
            if node.level == 1:
                return node.title
        return "Generated document"

    def _subtitle(self) -> str:
        pages = self.analysis.metadata.page_count
        author = self.analysis.metadata.author
        bits = [f"{pages} pages" if pages else "", author or ""]
        line = " · ".join(b for b in bits if b)
        return f"<p>{html_escape.escape(line)}</p>" if line else ""

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            logger.warning("[generate] %s", message)
            self.warnings.append(message)


_BLANK_TOKEN_RE = re.compile(r"\[\s*blank\s*\]", re.IGNORECASE)


def strip_blank_placeholders(soup) -> int:
    """Remove literal "[blank]" placeholder tokens from a document.

    The source PDF leaves fill-in cells empty for the student; some templates
    write the word "[blank]" instead. An empty cell should *be* empty — the
    token is deleted everywhere, and a table cell left with nothing gets a
    non-breaking space so the grid keeps its shape.
    """
    from bs4 import NavigableString

    changed = 0
    for node in soup.find_all(string=_BLANK_TOKEN_RE):
        if node.find_parent(("script", "style")) is not None:
            continue
        replacement = _BLANK_TOKEN_RE.sub("", str(node))
        replacement = re.sub(r"[ \t]{2,}", " ", replacement)
        if not replacement.strip() and node.find_parent(("td", "th")) is not None:
            replacement = "\u00a0"
        node.replace_with(NavigableString(replacement))
        changed += 1
    return changed


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
