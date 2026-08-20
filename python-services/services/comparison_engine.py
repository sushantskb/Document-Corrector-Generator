"""Six-level comparison of a PDF against its HTML rendition.

Levels: text, images, structure, order, questions and visual layout. Every
disagreement becomes an :class:`Issue` carrying a severity, a calibrated
confidence and — where a safe machine fix exists — a ready-to-apply
:class:`Correction`. The correction engine only ever applies what it is handed
here, which keeps detection and mutation cleanly separated.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from models.models import (
    ComparisonResult, Correction, CorrectionAction, DocumentAnalysis, ImageElement,
    Issue, IssueType, QuestionElement, Severity, StructureElement, TextElement,
)
from utils import bbox_utils
from utils.image_matcher import match_images
from utils.text_matcher import fuzzy_match, match_blocks, normalize_text, order_similarity

logger = logging.getLogger(__name__)

TEXT_MATCH_THRESHOLD = float(os.getenv("TEXT_MATCH_THRESHOLD", "0.75"))
IMAGE_MATCH_THRESHOLD = float(os.getenv("IMAGE_MATCH_THRESHOLD", "0.75"))
STRUCTURE_MATCH_THRESHOLD = float(os.getenv("STRUCTURE_MATCH_THRESHOLD", "0.80"))
QUESTION_MATCH_THRESHOLD = float(os.getenv("QUESTION_MATCH_THRESHOLD", "0.72"))
MIN_TEXT_WORDS = 3          # shorter blocks are page furniture, not content
_NOISE_RE = re.compile(r"^[\s\d\W]*$")
# Confidence ceiling for blocks whose PDF text cannot be trusted verbatim.
UNRELIABLE_TEXT_CONFIDENCE = 0.80


_NUMERIC_TOKEN_RE = re.compile(r"(?<![\w.])\d+(?![\w.])")


def _is_symbol_heavy(text: str) -> bool:
    """Is this block carrying maths that PDF extraction cannot represent?

    PDFs store `x²` as the characters `x` and `2`, so an equation extracts as
    `x2` and can never match the HTML, where MathJax renders it properly. Such
    a block is real evidence that *something* may be missing, but its text is
    not trustworthy enough to paste into the document automatically.

    Two signals together: several bare numbers, and a low proportion of letters.
    Either alone is ordinary prose — "All perfect squares end with 0, 1, 4, 5,
    6 or 9" is full of numbers and perfectly reliable.
    """
    stripped = re.sub(r"\s", "", text or "")
    if len(stripped) < 8:
        return False
    letters = sum(1 for character in stripped if character.isalpha())
    ratio = letters / len(stripped)
    if ratio < 0.45:
        return True
    return ratio < 0.6 and len(_NUMERIC_TOKEN_RE.findall(text)) >= 3


class ComparisonEngine:
    """Compare one PDF analysis with one HTML analysis."""

    def __init__(self, pdf: DocumentAnalysis, html: DocumentAnalysis,
                 pixel_cache: Optional[Dict[str, Any]] = None,
                 text_threshold: float = TEXT_MATCH_THRESHOLD,
                 image_threshold: float = IMAGE_MATCH_THRESHOLD):
        self.pdf = pdf
        self.html = html
        self.pixel_cache = pixel_cache or {}
        self.text_threshold = text_threshold
        self.image_threshold = image_threshold

        # Content fingerprints for every addressable HTML element. DOM paths go
        # stale the moment an earlier fix inserts or retags something, so each
        # correction carries the text/src it expects to find at its target.
        self._html_text_by_path: Dict[str, str] = {}
        for element in html.text_elements:
            if element.dom_path:
                self._html_text_by_path[element.dom_path] = element.text
        for node in html.structure:
            if node.dom_path:
                self._html_text_by_path[node.dom_path] = node.title
        self._html_src_by_path: Dict[str, str] = {
            image.dom_path: image.src for image in html.images if image.dom_path and image.src
        }

        # filled in by compare_text(), consumed by the anchoring helpers
        self.text_map: Dict[str, str] = {}          # pdf text id -> html dom path
        self.text_pairs: List[Tuple[TextElement, TextElement, float]] = []
        self.image_pairs: List[Tuple[ImageElement, ImageElement, float]] = []
        self.structure_pairs: List[Tuple[StructureElement, StructureElement, float]] = []

    # ------------------------------------------------------------------ helpers
    def _content_texts(self, analysis: DocumentAnalysis) -> List[TextElement]:
        """Visible, non-trivial blocks — the ones a reader would notice missing."""
        result = []
        for element in analysis.text_elements:
            if not element.visible or element.kind in ("furniture", "unreadable"):
                continue    # print furniture, or text the extractor failed to decode
            text = element.text.strip()
            if not text or _NOISE_RE.match(text):
                continue
            if len(text.split()) < MIN_TEXT_WORDS and element.kind != "heading":
                continue
            result.append(element)
        return result

    @staticmethod
    def _issue(**kwargs) -> Issue:
        return Issue(**kwargs)

    def _attach(self, issue: Issue, action: CorrectionAction, payload: Dict[str, Any],
                target: Optional[str] = None, auto_fixable: bool = True) -> Issue:
        if target:
            expected_text = self._html_text_by_path.get(target)
            if expected_text:
                payload.setdefault("target_text", expected_text)
            expected_src = self._html_src_by_path.get(target)
            if expected_src:
                payload.setdefault("target_src", expected_src)
        issue.correction = Correction(
            issue_id=issue.id, action=action, payload=payload, target_dom_path=target,
        )
        issue.auto_fixable = auto_fixable
        return issue

    @property
    def pdf_text_reliable(self) -> bool:
        """Is there enough decoded PDF text to compare text at all?

        A font without a Unicode mapping makes every block come out as
        `(cid:…)` garbage. Comparing that would report the entire chapter as
        missing and the entire HTML as extra — so when most blocks are
        undecodable, text-level comparison is switched off and only figures
        are compared. One warning issue says so, instead of four hundred
        false ones.
        """
        total = len(self.pdf.text_elements)
        unreadable = sum(1 for e in self.pdf.text_elements
                         if e.kind == "unreadable")
        return total == 0 or unreadable < total * 0.4

    def _extraction_warning(self) -> Issue:
        unreadable = sum(1 for e in self.pdf.text_elements
                         if e.kind == "unreadable")
        return self._issue(
            type=IssueType.EXTRACTION_WARNING,
            severity=Severity.MEDIUM,
            confidence=0.99,
            description=(f"The PDF's text could not be decoded ({unreadable} blocks — "
                         "the font has no Unicode mapping, common in regional-language "
                         "textbooks). Text comparison was skipped; only figures were "
                         "compared. The HTML's own text was left untouched."),
            suggestion="If text verification is needed, provide a PDF with embedded "
                       "Unicode text or an OCR pass.",
        )

    # --------------------------------------------------------------- 1. text
    def compare_text(self) -> Dict[str, Any]:
        """Fuzzy block alignment between PDF text and HTML text."""
        if not self.pdf_text_reliable:
            return {"similarity": 0.0, "matched": 0, "pdf_blocks": 0,
                    "html_blocks": len(self._content_texts(self.html)),
                    "issues": [self._extraction_warning()], "skipped": True}
        pdf_blocks = self._content_texts(self.pdf)
        html_blocks = self._content_texts(self.html)
        result = match_blocks(
            [b.text for b in pdf_blocks], [b.text for b in html_blocks], self.text_threshold
        )

        self.text_pairs = []
        self.text_map = {}
        for p_idx, h_idx, score in result["matches"]:
            pdf_block, html_block = pdf_blocks[p_idx], html_blocks[h_idx]
            self.text_pairs.append((pdf_block, html_block, score))
            if html_block.dom_path:
                self.text_map[pdf_block.id] = html_block.dom_path

        issues: List[Issue] = []
        for p_idx in result["unmatched_source"]:
            block = pdf_blocks[p_idx]
            partial_best = max(
                (fuzzy_match(block.text, other.text) for other in html_blocks), default=0.0
            )
            confidence = round(min(0.97, 0.62 + (self.text_threshold - partial_best)), 3)
            symbol_heavy = _is_symbol_heavy(block.text)
            if symbol_heavy:
                # keep it in the review queue, but never paste it in unattended
                confidence = min(confidence, UNRELIABLE_TEXT_CONFIDENCE)
            issue = self._issue(
                type=IssueType.MISSING_TEXT,
                severity=(Severity.MEDIUM if symbol_heavy else
                          Severity.HIGH if len(block.text.split()) > 12 else Severity.MEDIUM),
                confidence=confidence,
                page=block.page,
                pdf_element_id=block.id,
                location=f"PDF page {block.page}",
                description=f"Text present in the PDF is not in the HTML: “{_clip(block.text)}”",
                suggestion=("Check this against the PDF — it looks like mathematics, which "
                            "does not survive text extraction intact."
                            if symbol_heavy else
                            "Insert the missing text after the preceding matched block."),
                evidence={"text": block.text, "kind": block.kind,
                          "best_partial_score": round(partial_best, 3),
                          "words": len(block.text.split()),
                          **({"note": "mostly symbols or digits — PDF extraction of "
                                      "mathematics is unreliable, so this is not auto-applied"}
                             if symbol_heavy else {})},
            )
            anchor, position = self._anchor_for(block)
            issues.append(self._attach(
                issue, CorrectionAction.INSERT_TEXT,
                {"text": block.text, "tag": "h2" if block.kind == "heading" else "p",
                 "position": position},
                target=anchor, auto_fixable=bool(anchor),
            ))

        for h_idx in result["unmatched_target"]:
            block = html_blocks[h_idx]
            issues.append(self._issue(
                type=IssueType.EXTRA_TEXT,
                severity=Severity.LOW,
                confidence=0.55,
                dom_path=block.dom_path,
                html_element_id=block.id,
                location=block.dom_path or "",
                description=f"HTML has text with no PDF counterpart: “{_clip(block.text)}”",
                suggestion="Confirm this is intentional (navigation, credits) or remove it.",
                evidence={"text": block.text, "tag": block.tag},
            ))

        for pdf_block, html_block, score in self.text_pairs:
            if score >= 0.95:
                continue
            issues.append(self._issue(
                type=IssueType.TEXT_MISMATCH,
                severity=Severity.MEDIUM if score < 0.85 else Severity.LOW,
                confidence=round(min(0.9, 1.0 - score + 0.35), 3),
                page=pdf_block.page,
                dom_path=html_block.dom_path,
                pdf_element_id=pdf_block.id,
                html_element_id=html_block.id,
                location=html_block.dom_path or "",
                description=f"Wording differs from the PDF (similarity {score:.2f}).",
                suggestion="Review the wording; the PDF text is authoritative.",
                evidence={"pdf_text": _clip(pdf_block.text, 200),
                          "html_text": _clip(html_block.text, 200),
                          "similarity": score},
            ))

        similarity = round(len(self.text_pairs) / max(1, len(pdf_blocks)), 4)
        return {
            "similarity": similarity,
            "matched": len(self.text_pairs),
            "pdf_blocks": len(pdf_blocks),
            "html_blocks": len(html_blocks),
            "issues": issues,
        }

    # -------------------------------------------------------------- 2. images
    def compare_images(self) -> Dict[str, Any]:
        """Perceptual matching of PDF figures against HTML images."""
        pdf_images = [i for i in self.pdf.images if not i.error and not i.is_decorative]
        html_images = [i for i in self.html.images if not i.is_decorative]
        result = match_images(pdf_images, html_images, self.image_threshold, self.pixel_cache)

        issues: List[Issue] = []
        self.image_pairs = []
        for match in result["matches"]:
            pdf_image = pdf_images[match["source_index"]]
            html_image = html_images[match["target_index"]]
            self.image_pairs.append((pdf_image, html_image, match["confidence"]))

            if pdf_image.caption and not (html_image.alt or "").strip():
                issue = self._issue(
                    type=IssueType.MISSING_ALT_TEXT,
                    severity=Severity.LOW,
                    confidence=0.97,
                    page=pdf_image.page,
                    dom_path=html_image.dom_path,
                    pdf_element_id=pdf_image.id,
                    html_element_id=html_image.id,
                    location=html_image.dom_path or "",
                    description="Matched image has no alt text although the PDF has a caption.",
                    suggestion=f"Set alt=\"{_clip(pdf_image.caption, 80)}\".",
                    evidence={"caption": pdf_image.caption},
                )
                issues.append(self._attach(
                    issue, CorrectionAction.SET_ALT_TEXT,
                    {"alt": pdf_image.caption}, target=html_image.dom_path,
                ))

        unmatched_pdf = [pdf_images[i] for i in result["unmatched_source"]]
        unmatched_html = [html_images[i] for i in result["unmatched_target"]]

        # A figure whose caption survived in the HTML but whose picture did not
        # match is a *wrong* image, not a missing one.
        caption_pairs: Dict[str, ImageElement] = {}
        claimed_html: set = set()
        for pdf_image in unmatched_pdf:
            twin = self._caption_twin(
                pdf_image, [h for h in unmatched_html if h.id not in claimed_html]
            )
            if twin is not None:
                caption_pairs[pdf_image.id] = twin
                claimed_html.add(twin.id)

        by_id = {image.id: image for image in unmatched_pdf}
        for pdf_image_id, twin in caption_pairs.items():
            pdf_image = by_id[pdf_image_id]
            issue = self._issue(
                type=IssueType.IMAGE_MISMATCH,
                severity=Severity.HIGH,
                confidence=0.95,
                page=pdf_image.page,
                dom_path=twin.dom_path,
                pdf_element_id=pdf_image.id,
                html_element_id=twin.id,
                location=twin.dom_path or "",
                description=("The HTML image under the matching caption is a different "
                             "picture from the PDF figure."),
                suggestion="Replace the image with the figure extracted from the PDF.",
                evidence={"caption": pdf_image.caption, "html_src": twin.src,
                          "similarity": _best_score(pdf_image, twin, self.pixel_cache)},
            )
            issues.append(self._attach(
                issue, CorrectionAction.REPLACE_IMAGE,
                {"pdf_image_id": pdf_image.id, "alt": pdf_image.caption},
                target=twin.dom_path,
            ))

        for pdf_image in unmatched_pdf:
            if pdf_image.id in caption_pairs:
                continue        # already reported as a wrong picture, not a missing one
            anchor, position = self._anchor_for_image(pdf_image)
            # without readable text there is no caption and no anchor — the
            # finding is real but weaker, and it must not bury the queue in
            # hundreds of criticals
            weak = not self.pdf_text_reliable
            issue = self._issue(
                type=IssueType.MISSING_IMAGE,
                severity=Severity.MEDIUM if weak else Severity.HIGH,
                confidence=0.6 if weak else (0.96 if anchor else 0.85),
                page=pdf_image.page,
                pdf_element_id=pdf_image.id,
                location=f"PDF page {pdf_image.page}",
                description=(f"Figure on PDF page {pdf_image.page} has no counterpart in the "
                             f"HTML{' (' + _clip(pdf_image.caption, 60) + ')' if pdf_image.caption else ''}."),
                suggestion="Extract the figure from the PDF, upload it and insert it here.",
                evidence={"caption": pdf_image.caption, "kind": pdf_image.kind,
                          "bbox": pdf_image.bbox.as_tuple() if pdf_image.bbox else None},
            )
            issues.append(self._attach(
                issue, CorrectionAction.INSERT_IMAGE,
                {"pdf_image_id": pdf_image.id, "alt": pdf_image.caption or "",
                 "caption": pdf_image.caption, "position": position},
                target=anchor, auto_fixable=bool(anchor),
            ))

        # A broken placeholder (src="https://", dead link) can still say where a
        # figure belongs: the text just before it. If the PDF has an unmatched
        # figure sitting right below the PDF twin of that text, they are the
        # same figure — repoint the placeholder instead of reporting two issues.
        context_pairs = self._pair_broken_by_context(
            [i for i in unmatched_pdf if i.id not in caption_pairs],
            [h for h in unmatched_html if h.error and h.id not in claimed_html],
        )
        for pdf_image, html_image in context_pairs:
            claimed_html.add(html_image.id)
            caption_pairs[pdf_image.id] = html_image     # not re-reported as missing
            issue = self._issue(
                type=IssueType.IMAGE_MISMATCH,
                severity=Severity.HIGH,
                confidence=0.92,
                page=pdf_image.page,
                dom_path=html_image.dom_path,
                pdf_element_id=pdf_image.id,
                html_element_id=html_image.id,
                location=html_image.dom_path or "",
                description=("A broken image placeholder sits exactly where this PDF "
                             "figure belongs (the surrounding text matches)."),
                suggestion="Point the placeholder at the figure extracted from the PDF.",
                evidence={"src": html_image.src, "error": html_image.error,
                          "caption": html_image.caption},
            )
            issues.append(self._attach(
                issue, CorrectionAction.REPLACE_IMAGE,
                {"pdf_image_id": pdf_image.id,
                 "alt": html_image.alt or html_image.caption or pdf_image.caption or "",
                 "broken_replacement": True},
                target=html_image.dom_path,
            ))

        for html_image in unmatched_html:
            if html_image.id in claimed_html:
                continue        # reported above as IMAGE_MISMATCH
            if html_image.error:
                issue = self._issue(
                    type=IssueType.BROKEN_IMAGE_SRC,
                    severity=Severity.HIGH,
                    confidence=0.98,
                    dom_path=html_image.dom_path,
                    html_element_id=html_image.id,
                    location=html_image.dom_path or "",
                    description=f"Image cannot be loaded: {html_image.error}",
                    suggestion="Repoint the src at a working asset.",
                    evidence={"src": html_image.src, "error": html_image.error},
                )
                issues.append(self._attach(
                    issue, CorrectionAction.FIX_IMAGE_SRC, {},
                    target=html_image.dom_path, auto_fixable=False,
                ))
                continue
            issues.append(self._issue(
                type=IssueType.EXTRA_IMAGE,
                severity=Severity.LOW,
                confidence=0.6,
                dom_path=html_image.dom_path,
                html_element_id=html_image.id,
                location=html_image.dom_path or "",
                description="HTML contains an image with no matching PDF figure.",
                suggestion="Confirm the image belongs here (logo, decoration) or remove it.",
                evidence={"src": html_image.src, "alt": html_image.alt},
            ))

        image_coverage = round(len(self.image_pairs) / max(1, len(pdf_images)), 4)
        return {
            "coverage": image_coverage,
            "matched": len(self.image_pairs),
            "pdf_images": len(pdf_images),
            "html_images": len(html_images),
            "issues": issues,
        }

    def _pair_broken_by_context(self, pdf_images: List[ImageElement],
                                broken_html: List[ImageElement]
                                ) -> List[Tuple[ImageElement, ImageElement]]:
        """Match a dead placeholder to a PDF figure through their neighbours.

        The signal: the text block just before the placeholder (by DOM order)
        reads like a neighbour of the figure in the PDF — the block directly
        above it *or* directly below it, since templates often place a figure
        after the paragraph that follows it in print. The comparison is direct
        text similarity, not matched-pair identity: greedy matching sometimes
        assigns a block's twin elsewhere, which used to break perfectly good
        pairs. Displayed equations and short title lines never count as
        neighbours. Best score wins, one-to-one.
        """
        if not pdf_images or not broken_html:
            return []
        block_by_path = {b.dom_path: b for b in self.html.text_elements if b.dom_path}

        def label_like(block: TextElement) -> bool:
            return (len(block.text.split()) <= 3 and len(block.text) <= 30
                    and not block.text.rstrip().endswith((".", "?", "!")))

        def eligible(block: TextElement) -> bool:
            return (block.bbox is not None and block.kind != "furniture"
                    and not _is_symbol_heavy(block.text) and not label_like(block))

        def neighbours(figure: ImageElement):
            candidates = [b for b in self.pdf.text_elements
                          if b.page == figure.page and eligible(b)]
            above = [b for b in candidates
                     if b.bbox.bottom <= figure.bbox.top + 10
                     and figure.bbox.top - b.bbox.bottom <= 380]
            # generous downward tolerance: a wrapped paragraph often *starts*
            # level with the figure's last rows before continuing below it
            below = [b for b in candidates
                     if b.bbox.top >= figure.bbox.bottom - 40
                     and b.bbox.top - figure.bbox.bottom <= 380]
            # a floated figure has its text *beside* it, wrapped around
            beside = [b for b in candidates
                      if min(b.bbox.bottom, figure.bbox.bottom)
                      - max(b.bbox.top, figure.bbox.top)
                      >= 0.3 * figure.bbox.height]
            found = [(n, 1.0) for n in (
                max(above, key=lambda b: b.bbox.bottom) if above else None,
                min(below, key=lambda b: b.bbox.top) if below else None,
            ) if n is not None] + [(n, 1.0) for n in beside]
            # A figure at the top of a page continues the previous page's text —
            # but that neighbour is discounted: it must not outrank (or tie) a
            # same-page match. A one-digit difference ("powers of 4" vs
            # "powers of 7") once stole a figure through exactly this tie.
            if figure.bbox.top < 220 and (figure.page or 1) > 1:
                previous = [b for b in self.pdf.text_elements
                            if b.page == (figure.page or 1) - 1 and eligible(b)]
                if previous:
                    found.append((max(previous, key=lambda b: b.bbox.bottom), 0.95))
            return found

        scored: List[Tuple[float, ImageElement, ImageElement]] = []
        for html_image in broken_html:
            before = block_by_path.get(html_image.preceding_text_path or "")
            if before is None or not before.text.strip():
                continue
            for pdf_image in pdf_images:
                if pdf_image.bbox is None:
                    continue
                score = max(
                    (fuzzy_match(before.text, n.text) * weight
                     for n, weight in neighbours(pdf_image)),
                    default=0.0,
                )
                if score >= 0.8:
                    scored.append((score, pdf_image, html_image))

        scored.sort(key=lambda item: -item[0])
        pairs: List[Tuple[ImageElement, ImageElement]] = []
        used_pdf: set = set()
        used_html: set = set()
        for score, pdf_image, html_image in scored:
            if pdf_image.id in used_pdf or html_image.id in used_html:
                continue
            used_pdf.add(pdf_image.id)
            used_html.add(html_image.id)
            pairs.append((pdf_image, html_image))
        return pairs

    def _caption_twin(self, pdf_image: ImageElement,
                      candidates: Sequence[ImageElement]) -> Optional[ImageElement]:
        """HTML image whose caption/alt matches this PDF figure's caption."""
        if not pdf_image.caption:
            return None
        best, best_score = None, 0.0
        for candidate in candidates:
            for text in (candidate.caption, candidate.alt):
                score = fuzzy_match(pdf_image.caption, text or "")
                if score > best_score:
                    best, best_score = candidate, score
        return best if best_score >= 0.8 else None

    # ----------------------------------------------------------- 3. structure
    def compare_structure(self) -> Dict[str, Any]:
        """Heading-by-heading hierarchy comparison."""
        if not self.pdf_text_reliable:
            # undecoded headings cannot be compared; flagging every HTML
            # heading as "extra" would just be the same failure repeated
            return {"similarity": 0.0, "matched": 0, "pdf_sections": 0,
                    "html_sections": len(self.html.structure), "issues": []}
        pdf_nodes = self.pdf.structure
        html_nodes = self.html.structure
        result = match_blocks(
            [n.title for n in pdf_nodes], [n.title for n in html_nodes],
            STRUCTURE_MATCH_THRESHOLD,
        )
        self.structure_pairs = [
            (pdf_nodes[p], html_nodes[h], score) for p, h, score in result["matches"]
        ]

        issues: List[Issue] = []
        for pdf_node, html_node, score in self.structure_pairs:
            if pdf_node.level == html_node.level:
                continue
            issue = self._issue(
                type=IssueType.HEADING_LEVEL_MISMATCH,
                severity=Severity.MEDIUM,
                confidence=0.96,
                page=pdf_node.page,
                dom_path=html_node.dom_path,
                location=html_node.dom_path or "",
                description=(f"“{_clip(pdf_node.title, 60)}” is a level {pdf_node.level} heading "
                             f"in the PDF but <{html_node.tag}> in the HTML."),
                suggestion=f"Change <{html_node.tag}> to <h{pdf_node.level}>.",
                evidence={"pdf_level": pdf_node.level, "html_level": html_node.level,
                          "title": pdf_node.title},
            )
            issues.append(self._attach(
                issue, CorrectionAction.FIX_HEADING_LEVEL,
                {"level": pdf_node.level}, target=html_node.dom_path,
            ))

        for p_idx in result["unmatched_source"]:
            node = pdf_nodes[p_idx]
            anchor, position = self._anchor_for_section(node)
            issue = self._issue(
                type=IssueType.MISSING_SECTION,
                severity=Severity.HIGH,
                confidence=0.9,
                page=node.page,
                location=f"PDF page {node.page}",
                description=f"Section “{_clip(node.title, 70)}” is missing from the HTML.",
                suggestion="Add the section heading (and its body text) to the HTML.",
                evidence={"level": node.level, "title": node.title},
            )
            issues.append(self._attach(
                issue, CorrectionAction.INSERT_SECTION,
                {"title": node.title, "level": node.level, "position": position},
                target=anchor, auto_fixable=bool(anchor),
            ))

        for h_idx in result["unmatched_target"]:
            node = html_nodes[h_idx]
            issues.append(self._issue(
                type=IssueType.STRUCTURE_MISMATCH,
                severity=Severity.LOW,
                confidence=0.6,
                dom_path=node.dom_path,
                location=node.dom_path or "",
                description=f"HTML heading “{_clip(node.title, 70)}” has no PDF counterpart.",
                suggestion="Confirm this heading belongs in the chapter.",
                evidence={"level": node.level},
            ))

        # a heading sequence that jumps levels (h2 -> h4) breaks navigation
        previous_level = 0
        for node in html_nodes:
            if previous_level and node.level > previous_level + 1:
                already = any(i.dom_path == node.dom_path and
                              i.type == IssueType.HEADING_LEVEL_MISMATCH for i in issues)
                if not already:
                    issue = self._issue(
                        type=IssueType.HEADING_LEVEL_MISMATCH,
                        severity=Severity.LOW,
                        confidence=0.88,
                        dom_path=node.dom_path,
                        location=node.dom_path or "",
                        description=(f"Heading level jumps from h{previous_level} to "
                                     f"h{node.level} at “{_clip(node.title, 50)}”."),
                        suggestion=f"Use h{previous_level + 1} to keep the outline sequential.",
                        evidence={"previous_level": previous_level, "level": node.level},
                    )
                    issues.append(self._attach(
                        issue, CorrectionAction.FIX_HEADING_LEVEL,
                        {"level": previous_level + 1}, target=node.dom_path,
                        auto_fixable=False,
                    ))
            previous_level = node.level

        similarity = round(len(self.structure_pairs) / max(1, len(pdf_nodes)), 4) if pdf_nodes else 1.0
        return {
            "similarity": similarity,
            "matched": len(self.structure_pairs),
            "pdf_sections": len(pdf_nodes),
            "html_sections": len(html_nodes),
            "issues": issues,
        }

    # --------------------------------------------------------------- 4. order
    def compare_order(self) -> Dict[str, Any]:
        """Do the matched elements appear in the same sequence on both sides?

        Ordering is checked at two levels. Whole sections are checked first: a
        swapped section is one defect, and its fix moves the heading with all
        its content. Only blocks *outside* a section that is already being moved
        are then checked individually, so one displacement is not reported a
        dozen times.
        """
        if not self.text_pairs:
            self.compare_text()
        if not self.structure_pairs:
            self.compare_structure()

        issues: List[Issue] = []
        moved_sections: set = set()

        sections = sorted(self.structure_pairs, key=lambda pair: pair[0].order_index)
        section_order = [html_node.order_index for _, html_node, _ in sections]
        in_place = set(_longest_increasing_subsequence(section_order))
        for position, (pdf_node, html_node, _) in enumerate(sections):
            if position in in_place or position == 0:
                continue
            previous_pdf, previous_html, _ = sections[position - 1]
            issue = self._issue(
                type=IssueType.ORDER_MISMATCH,
                severity=Severity.MEDIUM,
                confidence=0.9,
                page=pdf_node.page,
                dom_path=html_node.dom_path,
                location=html_node.dom_path or "",
                description=(f"Section “{_clip(pdf_node.title, 60)}” appears out of sequence; "
                             f"in the PDF it follows “{_clip(previous_pdf.title, 40)}”."),
                suggestion=f"Move the section (with its content) after “{_clip(previous_pdf.title, 40)}”.",
                evidence={"title": pdf_node.title, "expected_after": previous_pdf.title,
                          "pdf_position": position,
                          "html_position": html_node.order_index},
            )
            issues.append(self._attach(
                issue, CorrectionAction.REORDER_ELEMENT,
                {"scope": "section", "after_scope": "section",
                 "after_dom_path": previous_html.dom_path,
                 "after_text": previous_html.title},
                target=html_node.dom_path, auto_fixable=bool(previous_html.dom_path),
            ))
            moved_sections.add(normalize_text(html_node.title))

        # element-level ordering, ignoring anything inside a section already moving
        enclosing = self._enclosing_headings()
        sequence: List[Tuple[int, int, str, str]] = []
        for pdf_block, html_block, _ in self.text_pairs:
            if pdf_block.kind == "heading":
                continue
            if normalize_text(enclosing.get(html_block.id, "")) in moved_sections:
                continue
            sequence.append((pdf_block.order_index, html_block.order_index,
                             html_block.dom_path or "", _clip(pdf_block.text, 50)))
        sequence.sort()

        html_order = [item[1] for item in sequence]
        keepers = set(_longest_increasing_subsequence(html_order))
        for position, (_, html_index, dom_path, label) in enumerate(sequence):
            if position in keepers or position == 0:
                continue
            expected_after = sequence[position - 1][2]
            issue = self._issue(
                type=IssueType.ORDER_MISMATCH,
                severity=Severity.MEDIUM,
                confidence=0.88,
                dom_path=dom_path,
                location=dom_path,
                description=f"“{label}” appears out of sequence relative to the PDF.",
                suggestion="Move the element back into the PDF's reading order.",
                evidence={"html_index": html_index, "expected_position": position,
                          "expected_after": expected_after},
            )
            issues.append(self._attach(
                issue, CorrectionAction.REORDER_ELEMENT,
                {"after_dom_path": expected_after,
                 "after_text": self._html_text_by_path.get(expected_after or "")},
                target=dom_path, auto_fixable=bool(expected_after),
            ))
            if len(issues) >= 25:
                break

        # the score still reflects every matched element, moved sections included
        full_sequence = sorted(
            [(p.order_index, h.order_index) for p, h, _ in self.structure_pairs]
            + [(p.order_index, h.order_index) for p, h, _ in self.text_pairs
               if p.kind != "heading"]
        )
        similarity = order_similarity(
            [item[1] for item in full_sequence], sorted(item[1] for item in full_sequence)
        )
        return {
            "similarity": similarity,
            "compared": len(full_sequence),
            "sections_out_of_order": len(moved_sections),
            "issues": issues,
        }

    def _enclosing_headings(self) -> Dict[str, str]:
        """html text element id -> title of the heading it sits under."""
        current = ""
        mapping: Dict[str, str] = {}
        for element in sorted(self.html.text_elements, key=lambda e: e.order_index):
            if element.kind == "heading":
                current = element.text
            mapping[element.id] = current
        return mapping

    # ------------------------------------------------------------ 5. questions
    def compare_questions(self) -> Dict[str, Any]:
        """Exercise-by-exercise matching, including duplicates and answers."""
        if not self.pdf_text_reliable:
            return {"coverage": 0.0, "matched": 0, "pdf_questions": 0,
                    "html_questions": len(self.html.questions), "issues": []}
        pdf_questions = self.pdf.questions
        html_questions = self.html.questions
        result = match_blocks(
            [q.text for q in pdf_questions], [q.text for q in html_questions],
            QUESTION_MATCH_THRESHOLD,
        )
        issues: List[Issue] = []
        matched_pairs = [(pdf_questions[p], html_questions[h], s) for p, h, s in result["matches"]]

        anchor_list = self._question_list_anchor(html_questions)
        for p_idx in result["unmatched_source"]:
            question = pdf_questions[p_idx]
            issue = self._issue(
                type=IssueType.MISSING_QUESTION,
                severity=Severity.HIGH,
                confidence=0.95 if anchor_list else 0.88,
                page=question.page,
                location=f"PDF page {question.page}",
                description=(f"Exercise {question.number or p_idx + 1} is missing from the HTML: "
                             f"“{_clip(question.text, 90)}”"),
                suggestion="Add the missing exercise to the question list.",
                evidence={"number": question.number, "text": question.text,
                          "options": question.options},
            )
            issues.append(self._attach(
                issue, CorrectionAction.INSERT_TEXT,
                {"text": question.text, "tag": "li", "position": "append",
                 "index": p_idx, "options": question.options},
                target=anchor_list, auto_fixable=bool(anchor_list),
            ))

        seen: Dict[str, QuestionElement] = {}
        for question in html_questions:
            key = normalize_text(question.text)
            if key in seen:
                issues.append(self._issue(
                    type=IssueType.DUPLICATE_QUESTION,
                    severity=Severity.MEDIUM,
                    confidence=0.93,
                    dom_path=question.dom_path,
                    location=question.dom_path or "",
                    description=f"Exercise appears more than once: “{_clip(question.text, 80)}”",
                    suggestion="Remove the duplicate exercise.",
                    evidence={"first_at": seen[key].dom_path},
                ))
            else:
                seen[key] = question

        for pdf_question, html_question, score in matched_pairs:
            if score < 0.9:
                issues.append(self._issue(
                    type=IssueType.QUESTION_MISMATCH,
                    severity=Severity.MEDIUM,
                    confidence=round(min(0.9, 1.05 - score), 3),
                    dom_path=html_question.dom_path,
                    location=html_question.dom_path or "",
                    description=f"Exercise wording differs from the PDF (similarity {score:.2f}).",
                    suggestion="Align the exercise text with the PDF.",
                    evidence={"pdf_text": pdf_question.text, "html_text": html_question.text},
                ))
            if (pdf_question.numbering_explicit and html_question.numbering_explicit
                    and pdf_question.number and html_question.number
                    and pdf_question.number != html_question.number):
                issues.append(self._issue(
                    type=IssueType.QUESTION_MISMATCH,
                    severity=Severity.LOW,
                    confidence=0.85,
                    dom_path=html_question.dom_path,
                    location=html_question.dom_path or "",
                    description=(f"Exercise is numbered {html_question.number} in the HTML but "
                                 f"{pdf_question.number} in the PDF."),
                    suggestion="Renumber the exercise to match the PDF.",
                    evidence={"pdf_number": pdf_question.number,
                              "html_number": html_question.number},
                ))
            if pdf_question.answer and not html_question.answer:
                issues.append(self._issue(
                    type=IssueType.MISSING_ANSWER,
                    severity=Severity.MEDIUM,
                    confidence=0.85,
                    dom_path=html_question.dom_path,
                    location=html_question.dom_path or "",
                    description="The PDF gives an answer for this exercise; the HTML does not.",
                    suggestion="Add the answer/solution from the PDF.",
                    evidence={"answer": pdf_question.answer},
                ))

        question_coverage = round(len(matched_pairs) / max(1, len(pdf_questions)), 4) \
            if pdf_questions else 1.0
        return {
            "coverage": question_coverage,
            "matched": len(matched_pairs),
            "pdf_questions": len(pdf_questions),
            "html_questions": len(html_questions),
            "issues": issues,
        }

    def _question_list_anchor(self, html_questions: Sequence[QuestionElement]) -> Optional[str]:
        """The <ol>/<ul> holding the exercises, so new questions land inside it."""
        for question in html_questions:
            if question.dom_path and " > li:" in question.dom_path:
                return question.dom_path.rsplit(" > ", 1)[0]
        return html_questions[-1].dom_path if html_questions else None

    # ------------------------------------------------------- 6. visual layout
    def compare_visual_layout(self) -> Dict[str, Any]:
        """Spatial agreement for matched figures: alignment and relative position."""
        if not self.image_pairs:
            self.compare_images()

        pdf_pages = {p.page: p for p in self.pdf.pages}
        html_page = self.html.pages[0] if self.html.pages else None
        issues: List[Issue] = []
        scores: List[float] = []

        for pdf_image, html_image, _ in self.image_pairs:
            page = pdf_pages.get(pdf_image.page or 1)
            if not (page and html_page and pdf_image.bbox and html_image.bbox):
                continue
            pdf_align = bbox_utils.horizontal_alignment(pdf_image.bbox, page.width)
            html_align = bbox_utils.horizontal_alignment(html_image.bbox, html_page.width)
            pdf_norm = bbox_utils.normalize_bbox(pdf_image.bbox, page.width, page.height)
            html_norm = bbox_utils.normalize_bbox(html_image.bbox, html_page.width, html_page.height)

            width_ratio_pdf = pdf_image.bbox.width / max(1.0, page.width)
            width_ratio_html = html_image.bbox.width / max(1.0, html_page.width)
            width_gap = abs(width_ratio_pdf - width_ratio_html)
            horizontal_score = 1.0 - min(1.0, abs(pdf_norm.x0 - html_norm.x0) + width_gap) \
                if pdf_norm and html_norm else 0.0
            scores.append(max(0.0, horizontal_score))

            if pdf_align != html_align and "unknown" not in (pdf_align, html_align):
                issue = self._issue(
                    type=IssueType.ALIGNMENT,
                    severity=Severity.LOW,
                    confidence=0.9,
                    page=pdf_image.page,
                    dom_path=html_image.dom_path,
                    location=html_image.dom_path or "",
                    description=(f"Figure is {html_align}-aligned in the HTML but {pdf_align}-"
                                 f"aligned in the PDF."),
                    suggestion=f"Set the figure alignment to {pdf_align}.",
                    evidence={"pdf_alignment": pdf_align, "html_alignment": html_align},
                )
                issues.append(self._attach(
                    issue, CorrectionAction.ADJUST_ALIGNMENT,
                    {"align": pdf_align}, target=html_image.dom_path,
                ))

            if width_gap > 0.35:
                issues.append(self._issue(
                    type=IssueType.LAYOUT_MISMATCH,
                    severity=Severity.LOW,
                    confidence=0.75,
                    page=pdf_image.page,
                    dom_path=html_image.dom_path,
                    location=html_image.dom_path or "",
                    description=("Figure occupies a very different share of the page width "
                                 f"({width_ratio_html:.0%} in HTML vs {width_ratio_pdf:.0%} in PDF)."),
                    suggestion="Resize the figure to match the PDF proportions.",
                    evidence={"pdf_width_ratio": round(width_ratio_pdf, 3),
                              "html_width_ratio": round(width_ratio_html, 3)},
                ))

        similarity = round(sum(scores) / len(scores), 4) if scores else 1.0
        return {"similarity": similarity, "compared": len(scores), "issues": issues}

    # ------------------------------------------------------------- watermarks
    def compare_watermarks(self) -> Dict[str, Any]:
        """Watermark-like text present in the HTML (and not genuine PDF content)."""
        issues: List[Issue] = []
        pdf_texts = [normalize_text(t.text) for t in self.pdf.text_elements]
        for candidate in self.html.stats.get("watermarks", []):
            text = normalize_text(candidate.get("text", ""))
            in_pdf = any(fuzzy_match(text, other) > 0.9 for other in pdf_texts)
            confidence = 0.96 if (candidate.get("reason") == "hint" and not in_pdf) else 0.7
            issue = self._issue(
                type=IssueType.WATERMARK,
                severity=Severity.MEDIUM,
                confidence=confidence,
                dom_path=candidate.get("dom_path"),
                location=candidate.get("dom_path") or "",
                description=f"Watermark-like text in the HTML: “{_clip(candidate.get('text', ''), 70)}”",
                suggestion="Remove the watermark element from the published HTML.",
                evidence={**candidate, "present_in_pdf": in_pdf},
            )
            issues.append(self._attach(
                issue, CorrectionAction.REMOVE_WATERMARK, {},
                target=candidate.get("dom_path"), auto_fixable=not in_pdf,
            ))
        return {"issues": issues}

    # ------------------------------------------------------------- anchoring
    def _anchor_for(self, pdf_block: TextElement) -> Tuple[Optional[str], str]:
        """Where in the HTML a missing PDF block belongs.

        Uses the nearest preceding PDF block that *did* match, so insertion
        lands in the right place even when several blocks are missing.
        """
        candidates = [
            b for b in self.pdf.text_elements
            if b.order_index < pdf_block.order_index and b.id in self.text_map
        ]
        if candidates:
            nearest = max(candidates, key=lambda b: b.order_index)
            return self.text_map[nearest.id], "after"
        following = [
            b for b in self.pdf.text_elements
            if b.order_index > pdf_block.order_index and b.id in self.text_map
        ]
        if following:
            nearest = min(following, key=lambda b: b.order_index)
            return self.text_map[nearest.id], "before"
        return None, "after"

    def _anchor_for_image(self, pdf_image: ImageElement) -> Tuple[Optional[str], str]:
        """Anchor for a missing figure: its caption if present, else the text above it."""
        if pdf_image.caption:
            for pdf_block, html_block, _ in self.text_pairs:
                if fuzzy_match(pdf_image.caption, pdf_block.text) > 0.9 and html_block.dom_path:
                    # insert before the caption's figure wrapper when there is one
                    return html_block.dom_path, "before"
        if pdf_image.bbox:
            above = [
                b for b in self.pdf.text_elements
                if b.page == pdf_image.page and b.bbox
                and b.bbox.bottom <= pdf_image.bbox.top + 2 and b.id in self.text_map
            ]
            if above:
                nearest = max(above, key=lambda b: b.bbox.bottom)
                return self.text_map[nearest.id], "after"
        return None, "after"

    def _anchor_for_section(self, node: StructureElement) -> Tuple[Optional[str], str]:
        preceding = [
            b for b in self.pdf.text_elements
            if b.page and node.page and b.page <= node.page and b.id in self.text_map
        ]
        if preceding:
            nearest = max(preceding, key=lambda b: (b.page, b.order_index))
            return self.text_map[nearest.id], "after"
        return None, "after"

    def _suppress_overlaps(self, issues: List[Issue]) -> List[Issue]:
        """Drop generic reports of a defect a more specific level already covers.

        A dropped exercise shows up as both MISSING_QUESTION and MISSING_TEXT; a
        dropped figure drags its caption along as MISSING_TEXT; a watermark is
        also 'extra' text. Reporting each once keeps the review queue honest.
        """
        specific_texts = [
            normalize_text(issue.evidence.get("text") or issue.evidence.get("caption") or "")
            for issue in issues
            if issue.type in (IssueType.MISSING_QUESTION, IssueType.MISSING_IMAGE)
        ]
        specific_texts = [t for t in specific_texts if t]
        watermark_paths = {i.dom_path for i in issues if i.type == IssueType.WATERMARK}

        kept: List[Issue] = []
        for issue in issues:
            if issue.type == IssueType.MISSING_TEXT:
                text = normalize_text(issue.evidence.get("text", "")) or normalize_text(
                    (issue.correction.payload.get("text") if issue.correction else "") or ""
                )
                if text and any(fuzzy_match(text, other) > 0.85 for other in specific_texts):
                    continue
            if issue.type == IssueType.EXTRA_TEXT and issue.dom_path in watermark_paths:
                continue
            kept.append(issue)
        return kept

    # ------------------------------------------------------------------- all
    def generate_issues(self) -> ComparisonResult:
        """Run all six comparisons plus watermarks and return the full result."""
        text = self.compare_text()
        images = self.compare_images()
        structure = self.compare_structure()
        order = self.compare_order()
        questions = self.compare_questions()
        layout = self.compare_visual_layout()
        watermarks = self.compare_watermarks()

        issues: List[Issue] = []
        for part in (images, text, structure, questions, order, layout, watermarks):
            issues.extend(part["issues"])
        issues = _dedupe(issues)
        issues = self._suppress_overlaps(issues)
        issues.sort(key=lambda i: (_SEVERITY_ORDER[i.severity], -i.confidence))

        return ComparisonResult(
            text_similarity=text["similarity"],
            image_coverage=images["coverage"],
            structure_similarity=structure["similarity"],
            order_similarity=order["similarity"],
            question_coverage=questions["coverage"],
            layout_similarity=layout["similarity"],
            matched_text=text["matched"],
            matched_images=images["matched"],
            issues=issues,
            details={
                "text": {k: v for k, v in text.items() if k != "issues"},
                "images": {k: v for k, v in images.items() if k != "issues"},
                "structure": {k: v for k, v in structure.items() if k != "issues"},
                "order": {k: v for k, v in order.items() if k != "issues"},
                "questions": {k: v for k, v in questions.items() if k != "issues"},
                "layout": {k: v for k, v in layout.items() if k != "issues"},
            },
        )


_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


def _dedupe(issues: Sequence[Issue]) -> List[Issue]:
    """Collapse issues that describe the same defect at the same place."""
    best: Dict[Tuple, Issue] = {}
    for issue in issues:
        key = (issue.type, issue.dom_path or "", issue.pdf_element_id or "",
               issue.html_element_id or "", issue.page)
        current = best.get(key)
        if current is None or issue.confidence > current.confidence:
            best[key] = issue
    return list(best.values())


def _longest_increasing_subsequence(values: Sequence[int]) -> List[int]:
    """Indices of a longest increasing subsequence — the elements already in order."""
    if not values:
        return []
    tails: List[int] = []          # index in `values` of the smallest tail per length
    predecessors: List[int] = [-1] * len(values)
    for index, value in enumerate(values):
        low, high = 0, len(tails)
        while low < high:
            middle = (low + high) // 2
            if values[tails[middle]] < value:
                low = middle + 1
            else:
                high = middle
        if low > 0:
            predecessors[index] = tails[low - 1]
        if low == len(tails):
            tails.append(index)
        else:
            tails[low] = index
    result: List[int] = []
    cursor = tails[-1] if tails else -1
    while cursor != -1:
        result.append(cursor)
        cursor = predecessors[cursor]
    return list(reversed(result))


def _best_score(pdf_image: ImageElement, html_image: ImageElement,
                pixel_cache: Dict[str, Any]) -> float:
    from utils.image_matcher import similarity_score

    score, _ = similarity_score(
        pdf_image, html_image, pixel_cache.get(pdf_image.id), pixel_cache.get(html_image.id)
    )
    return score


def _clip(text: str, limit: int = 110) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"
