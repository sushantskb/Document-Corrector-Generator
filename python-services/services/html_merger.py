"""Merge the PDF's missing content into the uploaded HTML's own template.

The uploaded study guide is typically "80% right": its banner, tabs, cards and
enrichment are worth keeping — only the PDF content it dropped is the problem.
Transcribing the whole PDF throws that design away, and patching fragments at
text-match anchors scatters them into the wrong tabs (greedy matching loves the
overview bullets). This module does the third thing:

* keep the template exactly as rendered — banner, tab panels, cards, styling;
* segment the PDF into its own sections (1.1, 1.2, …, Summary);
* place each section's *missing* blocks into the template panel where the
  majority of that section's matched content already lives — one clearly
  labelled card per PDF section, formatted in the template's visual language;
* content whose home cannot be determined goes to a "From the Textbook" tab so
  nothing is dropped;
* the template's own scripts are removed (they rebuild the page and would
  discard the merge) and replaced by a dozen lines of ours that only toggle
  which panel is visible.
"""

from __future__ import annotations

import html as html_escape
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bs4 import BeautifulSoup, Tag

from models.models import DocumentAnalysis, ImageElement, TextElement
from services.cloudinary_client import CloudinaryClient
from services.html_generator import HTMLGenerator, _BULLET_RE, strip_blank_placeholders
from utils.text_matcher import fuzzy_match, is_math_stack

logger = logging.getLogger(__name__)

_ADDED_CSS = """
.mstack { margin: .9em 0 .9em 1.2em; }
.mstack div { margin: .18em 0; font-size: 1.05em; }
.duo { display: flex; gap: 34px; align-items: center; flex-wrap: wrap;
       justify-content: flex-start; margin: 1em 0; }
.duo > * { flex: 0 1 auto; min-width: 0; margin: 0; }
.duo .mstack { flex: 0 0 auto; }
.duo img { width: auto; max-width: 100%; }
.duo figure, .duo img { text-align: center; }
.dcp-added { background:#fff; border:2px dashed #8b5cf6; border-radius:14px;
             padding:26px 30px; margin:26px 0; }
.dcp-added-head { font:700 15px/1.3 'Segoe UI',system-ui,sans-serif; color:#7c3aed;
                  margin:0 0 14px; display:flex; align-items:center; gap:8px; }
.dcp-added h4 { font:700 1.05rem/1.3 'Segoe UI',system-ui,sans-serif;
                color:#334155; margin:1.2em 0 .4em; }
.dcp-added p { margin:.6em 0; line-height:1.65; }
.dcp-added ul { margin:.6em 0; padding-left:1.4em; }
.dcp-added figure { margin:1.2em 0; text-align:center; }
.dcp-added figure img { max-width:100%; height:auto; border-radius:10px;
                        box-shadow:0 2px 10px rgba(30,60,120,.12); }
.dcp-added figcaption { margin-top:.5em; font:italic 13px/1.4 Georgia,serif; color:#64748b; }
.dcp-added .mstack { margin:.9em 0 .9em 1.2em; }
.dcp-added .mstack div { margin:.15em 0; font-size:1.05em; }
.dcp-added .duo { display:flex; gap:24px; align-items:center; flex-wrap:wrap; margin:1em 0; }
.dcp-added .duo > * { flex:1 1 260px; min-width:220px; margin:0; }
"""

_TAB_SCRIPT_TEMPLATE = """
(function () {
  var panels = %(panels)s.map(function (id) { return document.getElementById(id); });
  var buttons = Array.prototype.slice.call(
    document.querySelectorAll('nav button, nav a')).filter(function (b) {
      return b.textContent.trim(); });
  if (buttons.length !== panels.length || panels.some(function (p) { return !p; })) {
    panels.forEach(function (p) { if (p) p.style.display = 'block'; });
    return;
  }
  function activate(index) {
    panels.forEach(function (p, i) { p.style.display = i === index ? 'block' : 'none'; });
    buttons.forEach(function (b, i) { b.classList.toggle('active', i === index); });
    window.scrollTo(0, 0);
  }
  buttons.forEach(function (b, i) {
    b.addEventListener('click', function (e) { e.preventDefault(); activate(i); });
  });
  activate(0);
})();
"""


class HTMLMerger:
    """Weave a PDF's missing content into the rendered template."""

    def __init__(self, source_html: str, pdf_analysis: DocumentAnalysis,
                 comparison_engine, uploader: Optional[CloudinaryClient] = None,
                 job_id: Optional[str] = None,
                 panel_paths: Optional[Sequence[str]] = None,
                 issues: Optional[Sequence] = None,
                 region_renderer=None):
        self.soup = BeautifulSoup(source_html, "lxml")
        self.pdf = pdf_analysis
        self.engine = comparison_engine        # already ran generate_issues()
        self.uploader = uploader or CloudinaryClient()
        self.job_id = job_id
        self.panel_paths = list(panel_paths or ())
        self.issues = list(issues or ())
        self.region_renderer = region_renderer     # (page, BBox) -> PIL image
        self._placed_images: Dict[str, Tag] = {}     # pdf image id -> its <img> in the DOM
        self._restored_stacks: List[Tuple[Any, Tag]] = []   # (pdf block, .mstack tag)
        self.warnings: List[str] = []
        # borrows the generator's figure hosting and figure filtering
        self._generator = HTMLGenerator(pdf_analysis, uploader=self.uploader,
                                        job_id=job_id)
        self._host = self._generator._host

    # ------------------------------------------------------------------ public
    async def merge(self) -> str:
        panels = self._panels()
        sections = self._pdf_sections()
        matched_pdf_ids = set(pair[0].id for pair in self.engine.text_pairs)
        matched_image_ids = {pair[0].id for pair in self.engine.image_pairs}
        panel_votes = self._panel_votes(panels, sections)

        replaced = await self._replace_broken_placeholders(matched_image_ids)
        replaced += await self._rescue_remaining_placeholders()
        self._rewrite_flattened_stacks(matched_pdf_ids)
        self._pair_stacks_with_figures()

        placed = 0
        for section in sections:
            missing = self._missing_in(section, matched_pdf_ids, matched_image_ids)
            if not missing:
                continue
            card = await self._card(section["title"], missing)
            if card is None:
                continue
            panel = panel_votes.get(section["key"])
            if panel is None:
                panel = self._fallback_panel(panels)
            panel.append(card)
            placed += 1

        self._freeze_and_tab(panels)
        self._inject_css()
        cleaned = strip_blank_placeholders(self.soup)
        if cleaned:
            logger.info("[merge] %s '[blank]' placeholder token(s) emptied", cleaned)
        return str(self.soup)

    _INLINE_ONLY = frozenset({"b", "i", "strong", "em", "span", "sup", "sub", "br", "u"})

    @staticmethod
    def _squash(text: str):
        """Space-insensitive canonical form, plus a char-position map back.

        The template flattens `1 = 1` and `1 + 3 = 4` into `1 = 11 + 3 = 4` —
        the glued digits defeat any token-level comparison, but with whitespace
        removed both sides read `1=11+3=4` and containment is exact.
        """
        out: List[str] = []
        mapping: List[int] = []
        for index, char in enumerate(text):
            for piece in unicodedata.normalize("NFKC", char).casefold():
                piece = {"−": "-", "–": "-", "—": "-", "×": "x", "*": "x"}.get(piece, piece)
                if piece.isspace():
                    continue
                out.append(piece)
                mapping.append(index)
        return "".join(out), mapping

    def _rewrite_flattened_stacks(self, matched_pdf_ids: set) -> int:
        """Restore the PDF's line breaks where the template flattened a stack.

        A displayed equation stack the template pasted inline reads as one
        unbroken sentence — the very complaint that prompted this. When an
        unmatched PDF stack is found verbatim (space-insensitively) inside a
        template paragraph, the paragraph is cut at that point and the stack is
        re-emitted line by line after it.
        """
        candidates = [
            block for block in self.pdf.text_elements
            if block.id not in matched_pdf_ids and is_math_stack(block.lines)
        ]
        if not candidates:
            return 0
        rewritten = 0
        for block in candidates:
            stack_squashed, _ = self._squash(" ".join(block.lines))
            if len(stack_squashed) < 8:
                continue
            for html_block in self.engine.html.text_elements:
                if not html_block.dom_path or not html_block.text:
                    continue
                if stack_squashed not in self._squash(html_block.text)[0]:
                    continue
                try:
                    tag = self.soup.select_one(html_block.dom_path)
                except Exception:
                    tag = None
                if tag is None:
                    continue
                # only plain paragraphs are safe to rebuild — a MathJax tree or
                # nested blocks would be destroyed by clear()
                if any(child.name not in self._INLINE_ONLY
                       for child in tag.find_all(True)):
                    continue
                original = tag.get_text()
                squashed, mapping = self._squash(original)
                index = squashed.find(stack_squashed)
                if index < 0:
                    continue
                start = mapping[index]
                end = mapping[index + len(stack_squashed) - 1] + 1
                leading = original[:start].rstrip()
                trailing = original[end:].strip()

                tag.clear()
                if leading:
                    tag.string = leading
                stack = self._stack_tag(block)
                tag.insert_after(stack)
                self._restored_stacks.append((block, stack))
                if trailing:
                    rest = self.soup.new_tag(tag.name if tag.name != "li" else "p")
                    rest.string = trailing
                    stack.insert_after(rest)
                if not leading and not trailing:
                    tag.decompose()
                matched_pdf_ids.add(block.id)     # its card copy is now redundant
                rewritten += 1
                break
        if rewritten:
            logger.info("[merge] %s flattened equation stack(s) restored to the "
                        "PDF's line layout", rewritten)
        return rewritten

    async def _rescue_remaining_placeholders(self) -> int:
        """No document ships with a broken image: render the region instead.

        When no extracted figure could be paired, the figure still *exists* on
        the PDF page — in the vertical gap after the text the placeholder
        follows. That text is located by direct similarity, the gap to the next
        text block is measured, and a watermark-free render of that region
        becomes the image. A tool cannot rely on someone hand-fixing the one
        diagram whose geometry defeats extraction.
        """
        if self.region_renderer is None:
            return 0
        from utils.image_matcher import save_image_bytes
        from utils.text_matcher import fuzzy_match

        block_by_path = {b.dom_path: b for b in self.engine.html.text_elements
                         if b.dom_path}
        rescued = 0
        for img_tag in self.soup.find_all("img"):
            if (img_tag.get("src") or "https://") not in ("https://", ""):
                continue
            path = self._dom_prefix(img_tag)
            html_image = next(
                (i for i in self.engine.html.images if i.dom_path == path), None)
            before = block_by_path.get(
                (html_image.preceding_text_path if html_image else None) or "")
            if before is None or len(before.text.strip()) < 20:
                continue
            anchor, score = None, 0.0
            for block in self.pdf.text_elements:
                if block.kind == "furniture" or block.bbox is None \
                        or len(block.text) < 20:
                    continue
                value = fuzzy_match(before.text, block.text)
                if value > score:
                    anchor, score = block, value
            if anchor is None or score < 0.85:
                continue
            page_blocks = [b for b in self.pdf.text_elements
                           if b.page == anchor.page and b.bbox is not None
                           and b.kind != "furniture"]
            # only prose bounds the gap — the figure's own numeric annotations
            # and labels live inside the region we want to render
            from services.comparison_engine import _is_symbol_heavy
            prose = [b for b in page_blocks
                     if not _is_symbol_heavy(b.text)
                     and not (len(b.text.split()) <= 3 and len(b.text) <= 30)]
            below = [b for b in prose if b.bbox.top > anchor.bbox.bottom + 4]
            gap_bottom = (min(b.bbox.top for b in below) - 4 if below
                          else anchor.bbox.bottom + 280)
            if gap_bottom - anchor.bbox.bottom < 50:
                continue        # no room for a figure here; leave it for review
            from models.models import BBox
            x0 = min(b.bbox.x0 for b in page_blocks) - 6
            x1 = max(b.bbox.x1 for b in page_blocks) + 6
            region = BBox(x0=max(0.0, x0), x1=x1,
                          top=anchor.bbox.bottom + 4, bottom=gap_bottom)
            try:
                image = self.region_renderer(anchor.page, region)
            except Exception:
                logger.exception("region render failed for a placeholder")
                continue
            data, _path = save_image_bytes(image, "PNG")
            from utils.hash_utils import calculate_sha256
            result = await self.uploader.upload_bytes(
                data, subfolder="figures", public_id=calculate_sha256(data)[:32])
            if result and result.get("url"):
                src = result["url"]
            else:
                import base64
                src = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
            img_tag["src"] = src
            img_tag["data-dcp-fig"] = "1"
            rescued += 1
        if rescued:
            logger.info("[merge] %s placeholder(s) rescued with a region render "
                        "of the PDF page", rescued)
        return rescued

    def _pair_stacks_with_figures(self) -> int:
        """Lay a restored stack beside its figure, the way the PDF does.

        The PDF prints `1 = 1 … = 36` with the dot diagram to its right. When a
        stack we restored shares its vertical band with a figure we placed, the
        two are wrapped in a flex row, ordered left-to-right as in the PDF —
        general behaviour, so the next stack-plus-figure pairs itself too.
        """
        paired = 0
        for block, stack_tag in self._restored_stacks:
            beside = next(
                (image for image in self.pdf.images
                 if image.id in self._placed_images
                 and HTMLGenerator._beside(block, image)),
                None,
            )
            if beside is None:
                continue
            img_tag = self._placed_images[beside.id]
            if img_tag.find_parent(class_="duo") is not None:
                continue
            height_em = HTMLGenerator.paired_image_height_em(block, beside)
            img_tag["style"] = (f"height:{height_em}em;width:auto;max-width:100%")
            container = self._figure_container(img_tag, stack_tag)
            duo = self.soup.new_tag("div", attrs={"class": "duo"})
            stack_tag.insert_before(duo)
            stack_first = (block.bbox is None or beside.bbox is None
                           or block.bbox.x0 <= beside.bbox.x0)
            first, second = ((stack_tag, container) if stack_first
                             else (container, stack_tag))
            duo.append(first.extract())
            duo.append(second.extract())
            paired += 1
        if paired:
            logger.info("[merge] %s stack(s) laid out beside their figure, as in "
                        "the PDF", paired)
        return paired

    @staticmethod
    def _figure_container(img_tag: Tag, stack_tag: Tag) -> Tag:
        """The figure's own wrapper (image + caption), and nothing else."""
        node = img_tag
        for _ in range(4):
            parent = node.parent
            if parent is None or parent.name in ("body", "main", "html"):
                break
            if stack_tag in parent.descendants:
                break
            # a wrapper qualifies while everything inside belongs to the figure
            extra_text = "".join(
                child.get_text(" ", strip=True)
                for child in parent.find_all(True, recursive=False)
                if child is not node and child.name not in ("figcaption", "small", "figure")
                and node not in child.descendants
            )
            if extra_text.strip():
                break
            node = parent
        return node

    async def _replace_broken_placeholders(self, matched_image_ids: set) -> int:
        """Point dead placeholder images at their context-paired PDF figures.

        The comparison already worked out which PDF figure belongs at each
        broken ``<img>`` (their surrounding text matches); the template keeps
        its own figcaption, and the figure never re-appears in an added card.
        """
        replaced = 0
        by_id = {image.id: image for image in self.pdf.images}
        for issue in self.issues:
            correction = getattr(issue, "correction", None)
            if correction is None or not correction.payload.get("broken_replacement"):
                continue
            pdf_image = by_id.get(correction.payload.get("pdf_image_id"))
            if pdf_image is None or not correction.target_dom_path:
                continue
            try:
                target = self.soup.select_one(correction.target_dom_path)
            except Exception:
                target = None
            if target is None:
                continue
            if target.name != "img":
                target = target.find("img") or target
            src = await self._host(pdf_image)
            if not src:
                continue
            target["src"] = src
            if target.name == "img":
                target["data-dcp-fig"] = "1"
            for attribute in ("srcset", "data-src", "data-srcset"):
                if target.has_attr(attribute):
                    del target[attribute]
            matched_image_ids.add(pdf_image.id)     # it found its home
            self._placed_images[pdf_image.id] = target
            replaced += 1
        if replaced:
            logger.info("[merge] %s broken placeholder(s) pointed at their PDF figures",
                        replaced)
        return replaced

    # ---------------------------------------------------------------- sections
    def _pdf_sections(self) -> List[Dict[str, Any]]:
        """Split the PDF's content stream at its level-1/2 headings."""
        sections: List[Dict[str, Any]] = []
        levels: Dict[str, int] = {}
        headings = [e for e in self.pdf.text_elements if e.kind == "heading"]
        for element, node in zip(headings, self.pdf.structure):
            levels[element.id] = node.level

        current = {"key": 0, "title": "Introduction", "blocks": []}
        for element in self.pdf.text_elements:
            if element.kind in ("furniture", "unreadable") or not element.text.strip():
                continue
            if element.kind == "heading" and levels.get(element.id, 3) <= 2:
                if current["blocks"]:
                    sections.append(current)
                current = {"key": len(sections) + 1, "title": element.text.strip(),
                           "blocks": []}
                continue
            current["blocks"].append(element)
        if current["blocks"]:
            sections.append(current)

        # the generator's filtering already drops stamps, page covers and
        # fragments contained in a larger figure (the badge inside the banner)
        images = self._generator._usable_images([])
        page_area = {p.page: p.width * p.height for p in self.pdf.pages}
        claimed: set = set()
        for section in sections:
            pages = {b.page for b in section["blocks"] if b.page}
            spans = [(b.page, b.bbox.top if b.bbox else 0.0,
                      b.bbox.bottom if b.bbox else 0.0) for b in section["blocks"] if b.page]
            picked = []
            for image in images:
                if image.id in claimed or image.page not in pages:
                    continue
                if not self._within_span(image, spans):
                    continue
                # badges, QR codes and other slivers are template chrome the
                # uploaded page already provides its own version of
                area = image.bbox.area if image.bbox else 0.0
                if area < page_area.get(image.page, 1.0) * 0.02:
                    continue
                claimed.add(image.id)
                picked.append(image)
            section["images"] = picked
        return sections

    @staticmethod
    def _within_span(image: ImageElement, spans) -> bool:
        if image.bbox is None:
            return False
        for page, top, bottom in spans:
            if image.page == page and image.bbox.top >= top - 200 \
                    and image.bbox.top <= bottom + 200:
                return True
        return False

    def _missing_in(self, section, matched_text_ids, matched_image_ids):
        entries: List[Tuple[Tuple, str, Any]] = []
        for block in section["blocks"]:
            if block.id in matched_text_ids:
                continue
            text = block.text.strip()
            if len(text) <= 3 and text.replace(".", "").isdigit():
                continue
            if not any(ch.isalpha() for ch in text) and len(text.split()) <= 3:
                continue    # stray table/label fragments like "4: 9:"
            top = block.bbox.top if block.bbox else 0.0
            entries.append(((block.page or 0, top), block.kind, block))
        for image in section["images"]:
            if image.id in matched_image_ids:
                continue
            entries.append(((image.page or 0,
                             image.bbox.top if image.bbox else 0.0), "image", image))
        entries.sort(key=lambda item: item[0])
        return [(kind, element) for _, kind, element in entries]

    # ------------------------------------------------------------- placement
    def _panels(self) -> List[Tag]:
        """The template's tab panels, in document order."""
        if not self.panel_paths:
            return []
        try:
            first = self.soup.select_one(self.panel_paths[0])
        except Exception:
            first = None
        if first is None or first.parent is None:
            return []
        return [child for child in first.parent.find_all(True, recursive=False)
                if child.get_text(strip=True) or child.find("img")]

    def _panel_votes(self, panels: Sequence[Tag],
                     sections: Sequence[Dict[str, Any]]) -> Dict[int, Tag]:
        """PDF section -> the template panel its missing content belongs in.

        Three signals, strongest first:

        1. **Names.** Template panels usually carry meaning — an id like
           ``summary`` or a tab labelled "Summary" pins the PDF's SUMMARY
           section without any statistics.
        2. **Weighted votes.** Every matched block of a PDF section points at
           the panel its HTML twin lives in, weighted by match quality and
           length (a verbatim paragraph outvotes a loosely similar bullet) and
           capped so one long block cannot decide alone.
        3. **The content home.** Numbered chapter sections with weak votes go
           where the chapter's prose demonstrably lives — the panel with the
           highest total weight across all unnamed sections.
        """
        if not panels:
            return {}

        labels: Dict[int, str] = {}
        nav = self.soup.find("nav")
        buttons = [b.get_text(strip=True) for b in nav.find_all(["button", "a"])
                   if b.get_text(strip=True)] if nav else []
        for index, panel in enumerate(panels):
            name = (panel.get("id") or "").replace("_", " ").replace("-", " ")
            label = buttons[index] if index < len(buttons) else ""
            labels[index] = f"{name} {label}".strip()

        prefix_of = {index: self._dom_prefix(panel)
                     for index, panel in enumerate(panels)}
        block_section: Dict[str, int] = {}
        for section in sections:
            for block in section["blocks"]:
                block_section[block.id] = section["key"]

        votes: Dict[int, Counter] = defaultdict(Counter)
        for pdf_block, html_block, score in self.engine.text_pairs:
            key = block_section.get(pdf_block.id)
            if key is None or not html_block.dom_path:
                continue
            weight = min(25.0, score * len(pdf_block.text.split()))
            for index, prefix in prefix_of.items():
                if prefix and html_block.dom_path.startswith(prefix):
                    votes[key][index] += weight
                    break

        result: Dict[int, Tag] = {}
        title = self._document_title()
        untitled: List[int] = []
        for section in sections:
            key, section_title = section["key"], section["title"]
            named = next(
                (index for index, label in labels.items()
                 if label and fuzzy_match(section_title, label) >= 0.75),
                None,
            )
            if named is not None:
                result[key] = panels[named]
            elif fuzzy_match(section_title, title) >= 0.9 or section_title == "Introduction":
                untitled.append(key)      # chapter preamble: goes to the content home
            elif votes[key] and votes[key].most_common(1)[0][1] >= 30:
                result[key] = panels[votes[key].most_common(1)[0][0]]
            else:
                untitled.append(key)

        home = Counter()
        for key in untitled:
            home.update(votes.get(key, {}))
        for key, panel in result.items():
            pass
        if untitled:
            content_panel = (panels[home.most_common(1)[0][0]]
                             if home else None)
            for key in untitled:
                if content_panel is not None:
                    result[key] = content_panel
        return result

    def _document_title(self) -> str:
        if self.pdf.metadata.title:
            return self.pdf.metadata.title
        for node in self.pdf.structure:
            if node.level == 1:
                return node.title
        return ""

    @staticmethod
    def _dom_prefix(tag: Tag) -> str:
        parts = []
        node = tag
        while isinstance(node, Tag) and node.name:
            if node.name == "html":
                parts.insert(0, "html")
                break
            index = 1
            sibling = node.previous_sibling
            while sibling is not None:
                if isinstance(sibling, Tag) and sibling.name == node.name:
                    index += 1
                sibling = sibling.previous_sibling
            parts.insert(0, f"{node.name}:nth-of-type({index})")
            node = node.parent
        return " > ".join(parts)

    def _fallback_panel(self, panels: List[Tag]) -> Tag:
        """A "From the Textbook" tab for content with no confident home."""
        if getattr(self, "_extra_panel", None) is not None:
            return self._extra_panel
        if panels:
            template = panels[-1]
            panel = self.soup.new_tag("div", attrs={
                "class": template.get("class", []), "id": "dcp-extra-panel"})
            template.insert_after(panel)
            nav = self.soup.find("nav")
            if nav is not None:
                buttons = [b for b in nav.find_all(["button", "a"])
                           if b.get_text(strip=True)]
                if buttons:
                    extra = self.soup.new_tag("button", attrs={
                        "class": buttons[-1].get("class", [])})
                    extra.string = "📘 From the Textbook"
                    buttons[-1].insert_after(extra)
            panels.append(panel)
            self._extra_panel = panel
            return panel
        host = self.soup.find("main") or self.soup.body or self.soup
        panel = self.soup.new_tag("div", attrs={"id": "dcp-extra-panel"})
        host.append(panel)
        self._extra_panel = panel
        return panel

    # -------------------------------------------------------------- rendering
    async def _card(self, title: str, missing) -> Optional[Tag]:
        card = self.soup.new_tag("div", attrs={"class": "dcp-added"})
        head = self.soup.new_tag("div", attrs={"class": "dcp-added-head"})
        head.string = f"📘 From the textbook — {title}"
        card.append(head)

        open_list: Optional[Tag] = None
        rendered = 0
        skip_next = False
        for position, (kind, element) in enumerate(missing):
            if skip_next:
                skip_next = False
                continue
            if kind not in ("image",) and is_math_stack(getattr(element, "lines", None)) \
                    and position + 1 < len(missing):
                next_kind, next_element = missing[position + 1]
                if next_kind == "image" and HTMLGenerator._beside(element, next_element):
                    open_list = None
                    duo = self.soup.new_tag("div", attrs={"class": "duo"})
                    duo.append(self._stack_tag(element))
                    figure = await self._figure_tag(
                        next_element,
                        height_em=HTMLGenerator.paired_image_height_em(
                            element, next_element))
                    if figure is not None:
                        duo.append(figure)
                    card.append(duo)
                    rendered += 2
                    skip_next = True
                    continue
            if kind != "image" and is_math_stack(getattr(element, "lines", None)):
                open_list = None
                card.append(self._stack_tag(element))
                rendered += 1
                continue
            if kind == "image":
                open_list = None
                figure = await self._figure_tag(element)
                if figure is None:
                    continue
                card.append(figure)
                rendered += 1
                continue

            text = element.text.strip()
            if kind == "heading":
                open_list = None
                heading = self.soup.new_tag("h4")
                heading.string = text
                card.append(heading)
            elif kind == "list_item":
                if open_list is None:
                    open_list = self.soup.new_tag("ul")
                    card.append(open_list)
                item = self.soup.new_tag("li")
                item.string = _BULLET_RE.sub("", text)
                open_list.append(item)
            else:
                open_list = None
                paragraph = self.soup.new_tag("p")
                paragraph.string = text
                card.append(paragraph)
            rendered += 1
        has_text = card.find(["p", "h4", "li"]) is not None
        has_captioned_figure = any(f.find("figcaption") for f in card.find_all("figure"))
        if not has_text and not has_captioned_figure:
            # a card holding only anonymous artwork adds noise, not content —
            # the template ships its own banner and decorations
            return None
        return card if rendered else None

    def _stack_tag(self, element) -> Tag:
        stack = self.soup.new_tag("div", attrs={"class": "mstack"})
        for line in element.lines:
            row = self.soup.new_tag("div")
            row.string = line
            stack.append(row)
        return stack

    async def _figure_tag(self, element, height_em: Optional[float] = None) -> Optional[Tag]:
        src = await self._host(element)
        if not src:
            return None
        figure = self.soup.new_tag("figure")
        img = self.soup.new_tag("img", src=src, loading="lazy")
        img["data-dcp-fig"] = "1"
        if height_em:
            figure["class"] = "duo-fig"
            img["style"] = f"height:{height_em}em;width:auto;max-width:100%"
        img["alt"] = element.alt or element.caption or "Figure from the textbook"
        figure.append(img)
        if element.caption:
            figcaption = self.soup.new_tag("figcaption")
            figcaption.string = element.caption
            figure.append(figcaption)
        return figure

    # ------------------------------------------------------------- freezing
    def _freeze_and_tab(self, panels: Sequence[Tag]) -> None:
        """Drop the template's scripts, then re-implement its tab switching."""
        removed = 0
        for tag in self.soup.find_all("script"):
            if (tag.get("type") or "").lower() in ("application/json", "application/ld+json"):
                continue
            tag.decompose()
            removed += 1
        for tag in self.soup.find_all(True):
            for attribute in [a for a in tag.attrs if a.lower().startswith("on")]:
                del tag[attribute]

        if len(panels) >= 2:
            ids = []
            for index, panel in enumerate(panels):
                panel_id = panel.get("id") or f"dcp-panel-{index}"
                panel["id"] = panel_id
                ids.append(panel_id)
            script = self.soup.new_tag("script")
            script.string = _TAB_SCRIPT_TEMPLATE % {
                "panels": "[" + ",".join(f'"{i}"' for i in ids) + "]"}
            (self.soup.body or self.soup).append(script)
        elif removed:
            # no tab structure: make sure nothing stays hidden
            style = self.soup.new_tag("style")
            style.string = "[hidden]{display:block !important}"
            (self.soup.head or self.soup.body or self.soup).append(style)

    def _inject_css(self) -> None:
        style = self.soup.new_tag("style")
        style.string = _ADDED_CSS
        (self.soup.head or self.soup.body or self.soup).append(style)
