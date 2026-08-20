"""Apply corrections to the HTML.

The engine is deliberately dumb about *what* is wrong — the comparison engine
already decided that and attached a :class:`Correction` to each issue. This
module only knows how to carry out those instructions safely against a
BeautifulSoup tree, upload any figures the fix needs, and hand back the patched
document together with a per-issue account of what happened.

Only issues whose confidence clears ``auto_fix_threshold`` (0.95 by default) are
applied automatically; everything else waits for a human decision and is applied
later through the same code path when it is approved.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from bs4 import BeautifulSoup, Comment, Tag

from models.models import (
    Correction, CorrectionAction, DocumentAnalysis, ImageElement, Issue, IssueStatus,
)
from services.cloudinary_client import CloudinaryClient
from utils.hash_utils import calculate_sha256
from utils.text_matcher import fuzzy_match, normalize_text

logger = logging.getLogger(__name__)

AUTO_FIX_THRESHOLD = float(os.getenv("AUTO_FIX_CONFIDENCE", "0.95"))
INSERTED_MARKER = "data-dcp-inserted"       # lets a later run recognise our own work

# Chapter content never belongs in the site's furniture. An anchor that lands
# here means the match was against a banner or a menu label, not the body text.
CHROME_TAGS = frozenset({"header", "nav", "footer", "aside"})

# Inserted blocks must not become flex/grid children of whatever surrounds them,
# or they get stretched into columns by the host page's layout.
INSERTED_STYLE = "display:block;flex-basis:100%;grid-column:1/-1;max-width:100%"


class CorrectionEngine:
    """Patch one HTML document using corrections produced by the comparison engine."""

    def __init__(self, html: str, pdf_analysis: Optional[DocumentAnalysis] = None,
                 html_analysis: Optional[DocumentAnalysis] = None,
                 uploader: Optional[CloudinaryClient] = None,
                 auto_fix_threshold: float = AUTO_FIX_THRESHOLD,
                 job_id: Optional[str] = None,
                 strip_base_tag: bool = False,
                 freeze_scripts: bool = False,
                 reveal_paths: Optional[Sequence[str]] = None,
                 unstick_paths: Optional[Sequence[str]] = None):
        self.original_html = html
        self.pdf_analysis = pdf_analysis
        self.html_analysis = html_analysis
        self.uploader = uploader or CloudinaryClient()
        self.auto_fix_threshold = auto_fix_threshold
        self.job_id = job_id
        self.strip_base_tag = strip_base_tag
        self.freeze_scripts = freeze_scripts
        self.reveal_paths = list(reveal_paths or ())
        self.unstick_paths = list(unstick_paths or ())
        self.soup = BeautifulSoup(html, "lxml")
        self.applied: List[Correction] = []
        self.skipped: List[Tuple[Issue, str]] = []
        self.warnings: List[str] = []
        self._uploaded: Dict[str, str] = {}          # pdf image id -> hosted url
        self._insertion_chains: Dict[int, Tag] = {}  # anchor -> last block inserted after it
        self._pdf_images: Dict[str, ImageElement] = {
            image.id: image for image in (pdf_analysis.images if pdf_analysis else [])
        }

    # ------------------------------------------------------------------ public
    async def patch_html(self, issues: Sequence[Issue],
                         approved: Optional[Iterable[str]] = None,
                         rejected: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """Apply every eligible correction and return the patched document.

        ``approved`` forces an issue to be applied regardless of confidence;
        ``rejected`` blocks one that would otherwise be auto-fixed.
        """
        approved_ids: Set[str] = set(approved or ())
        rejected_ids: Set[str] = set(rejected or ())

        ordered = self._order_for_application(issues)
        for issue in ordered:
            if issue.id in rejected_ids:
                issue.status = IssueStatus.REJECTED
                self.skipped.append((issue, "rejected by reviewer"))
                continue
            if not issue.correction:
                issue.status = IssueStatus.UNFIXABLE
                self.skipped.append((issue, "no automated correction available"))
                continue
            forced = issue.id in approved_ids
            if not forced:
                if not issue.auto_fixable:
                    self.skipped.append((issue, "needs manual review"))
                    continue
                if issue.confidence < self.auto_fix_threshold:
                    self.skipped.append((
                        issue,
                        f"confidence {issue.confidence:.2f} below "
                        f"auto-fix threshold {self.auto_fix_threshold:.2f}",
                    ))
                    continue
            try:
                applied = await self._apply(issue, issue.correction)
            except Exception as exc:               # one bad fix must not lose the rest
                logger.exception("correction failed for %s", issue.id)
                issue.correction.error = str(exc)
                self.skipped.append((issue, f"correction error: {exc}"))
                continue
            if applied:
                issue.correction.applied = True
                issue.correction.applied_at = datetime.now(timezone.utc)
                issue.status = IssueStatus.APPROVED if forced else IssueStatus.AUTO_FIXED
                self.applied.append(issue.correction)
            else:
                self.skipped.append((issue, issue.correction.error or "target not found"))

        return {
            "html": self.generate_corrected_html(),
            "applied": self.applied,
            "skipped": self.skipped,
            "warnings": self.warnings,
        }

    def generate_corrected_html(self) -> str:
        """Serialize the patched document with a provenance comment."""
        if self.strip_base_tag:
            for tag in self.soup.find_all("base"):
                tag.decompose()
        if self.freeze_scripts and self.applied:
            self._freeze()
        from services.html_generator import strip_blank_placeholders

        strip_blank_placeholders(self.soup)
        head = self.soup.head
        if head is not None:
            for existing in head.find_all(
                string=lambda s: isinstance(s, Comment) and "document-correction-platform" in s
            ):
                existing.extract()
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            note = (f" document-correction-platform: {len(self.applied)} correction(s) applied "
                    f"{stamp}{' for job ' + self.job_id if self.job_id else ''} ")
            head.append(Comment(note))
        return str(self.soup)

    # ------------------------------------------------------------- dispatching
    _ORDER = {
        # content first, then structure, then cosmetics: moving elements after
        # inserting them keeps anchors valid
        CorrectionAction.SET_ALT_TEXT: 0,
        CorrectionAction.FIX_IMAGE_SRC: 1,
        CorrectionAction.REPLACE_IMAGE: 1,
        CorrectionAction.INSERT_IMAGE: 2,
        CorrectionAction.INSERT_TEXT: 3,
        CorrectionAction.INSERT_SECTION: 3,
        CorrectionAction.REMOVE_WATERMARK: 4,
        CorrectionAction.REMOVE_ELEMENT: 4,
        CorrectionAction.FIX_HEADING_LEVEL: 5,
        CorrectionAction.ADJUST_ALIGNMENT: 6,
        CorrectionAction.REORDER_ELEMENT: 7,
        CorrectionAction.MANUAL: 9,
    }

    def _order_for_application(self, issues: Sequence[Issue]) -> List[Issue]:
        return sorted(
            issues,
            key=lambda i: (self._ORDER.get(i.correction.action, 8) if i.correction else 8,
                           -i.confidence),
        )

    async def _apply(self, issue: Issue, correction: Correction) -> bool:
        action = correction.action
        if action == CorrectionAction.SET_ALT_TEXT:
            return self._set_alt_text(correction)
        if action in (CorrectionAction.REPLACE_IMAGE, CorrectionAction.FIX_IMAGE_SRC):
            return await self.replace_image(correction)
        if action == CorrectionAction.INSERT_IMAGE:
            return await self.insert_image(correction)
        if action == CorrectionAction.INSERT_TEXT:
            return self._insert_text(correction)
        if action == CorrectionAction.INSERT_SECTION:
            return self.fix_structure(correction)
        if action == CorrectionAction.FIX_HEADING_LEVEL:
            return self.fix_structure(correction)
        if action == CorrectionAction.REORDER_ELEMENT:
            return self._reorder(correction)
        if action == CorrectionAction.ADJUST_ALIGNMENT:
            return self.fix_alignment(correction)
        if action == CorrectionAction.REMOVE_WATERMARK:
            return self.watermark_removal(correction)
        if action == CorrectionAction.REMOVE_ELEMENT:
            return self._remove(correction)
        correction.error = f"unsupported action {action}"
        return False

    # ------------------------------------------------------------- resolution
    def _resolve(self, correction: Correction,
                 fallback_text: Optional[str] = None,
                 content_only: bool = False) -> Optional[Tag]:
        """Resolve a correction's target element.

        ``content_only`` refuses anchors inside the page's header, nav or
        footer: inserting chapter text or a figure there is worse than not
        inserting it at all, so those fixes are left for a human instead.
        """
        found = self._resolve_path(
            correction.target_dom_path,
            expected_text=fallback_text or correction.payload.get("target_text"),
            expected_src=correction.payload.get("target_src"),
            content_only=content_only,
        )
        if found is None:
            correction.error = (
                f"no usable anchor in the page content for '{correction.target_dom_path}'"
                if content_only else
                f"could not resolve target '{correction.target_dom_path}'"
            )
        return found

    @staticmethod
    def _in_chrome(tag: Tag) -> bool:
        return any(parent.name in CHROME_TAGS for parent in tag.parents if isinstance(parent, Tag))

    def _resolve_path(self, path: Optional[str], expected_text: Optional[str] = None,
                      expected_src: Optional[str] = None,
                      content_only: bool = False) -> Optional[Tag]:
        """Find an element by DOM path, verified against what should be there.

        An nth-of-type path is only valid for the document it was computed from.
        Once an earlier fix inserts or retags an element the path can silently
        address the *wrong* node, so whenever the comparison engine recorded
        what the target should contain, the match is checked — and a failed
        check falls back to finding the element by its content.
        """
        candidate: Optional[Tag] = None
        if path:
            try:
                candidate = self.soup.select_one(path)
            except Exception:
                candidate = None
        if candidate is not None and not self._matches(candidate, expected_text, expected_src):
            candidate = None            # stale path: it points at something else now
        if candidate is not None and content_only and self._in_chrome(candidate):
            candidate = None            # a banner or menu is not where content goes
        if candidate is not None:
            return candidate
        if expected_src:
            for tag in (self.soup.body or self.soup).find_all("img"):
                if _same_src(tag.get("src"), expected_src):
                    return tag
        if expected_text and normalize_text(expected_text):
            target = normalize_text(expected_text)
            root = self._content_root() if content_only else (self.soup.body or self.soup)
            best, best_score = None, 0.0
            for tag in root.find_all(True):
                if tag.name in ("script", "style", "head", "body", "html"):
                    continue
                if content_only and (tag.name in CHROME_TAGS or self._in_chrome(tag)):
                    continue
                # score each element on its own text, not its subtree: matching a
                # container that merely *contains* the text would anchor content
                # to a card or a grid, and the insertion lands unstyled beside it
                own = _own_text(tag)
                if not own:
                    continue
                score = fuzzy_match(target, own)
                if score > best_score:
                    best, best_score = tag, score
            if best is not None and best_score >= 0.9:
                return best
        return None

    def _content_root(self) -> Tag:
        """The page's main content container, when it declares one."""
        soup = self.soup
        return (soup.find("main") or soup.find(id="content")
                or soup.find("article") or soup.body or soup)

    @staticmethod
    def _matches(tag: Tag, expected_text: Optional[str],
                 expected_src: Optional[str]) -> bool:
        """Is this the element the correction was written against?"""
        if expected_src:
            candidate = tag if tag.name == "img" else (tag.find("img") or tag)
            return _same_src(candidate.get("src"), expected_src)
        if expected_text and normalize_text(expected_text):
            return fuzzy_match(expected_text, tag.get_text(" ", strip=True)) >= 0.6
        return True

    # ----------------------------------------------------------------- images
    async def _host_image(self, correction: Correction) -> Optional[Dict[str, Any]]:
        """Resolve the hosted URL for the figure a correction needs.

        The URL is written back into the correction payload, so a fix approved
        days later can be applied without re-extracting anything from the PDF.
        """
        hosted = correction.payload.get("hosted_url")
        if hosted:
            return {"url": hosted}
        pdf_image_id = correction.payload.get("pdf_image_id")
        if not pdf_image_id:
            return None
        if pdf_image_id in self._uploaded:
            correction.payload["hosted_url"] = self._uploaded[pdf_image_id]
            return {"url": self._uploaded[pdf_image_id]}
        image = self._pdf_images.get(pdf_image_id)
        if image is None or not image.local_path or not os.path.exists(image.local_path):
            return None
        with open(image.local_path, "rb") as fh:
            data = fh.read()
        # Name the asset after its content: re-running a job then reuses the
        # same upload instead of leaving an orphaned copy behind every time.
        digest = image.sha256 or calculate_sha256(data)
        result = await self.uploader.upload_bytes(
            data, subfolder="figures", public_id=digest[:32],
        )
        if result and result.get("url"):
            image.cloudinary_url = result["url"]
            self._uploaded[pdf_image_id] = result["url"]
            correction.payload["hosted_url"] = result["url"]
            correction.payload["hosted_public_id"] = result.get("publicId")
            return result
        # Cloudinary unavailable: keep the document self-contained
        import base64

        data_uri = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        self._uploaded[pdf_image_id] = data_uri
        correction.payload["hosted_url"] = data_uri
        self._warn("Cloudinary unavailable; figure embedded as a data URI")
        return {"url": data_uri, "publicId": None, "inline": True}

    async def prepare_figures(self, issues: Sequence[Issue]) -> int:
        """Upload every figure any correction might need, fixed now or later.

        Reviewers approve fixes long after the temp files are gone, so the
        pixels have to be hosted while we still hold them.
        """
        uploaded = 0
        for issue in issues:
            correction = issue.correction
            if not correction or correction.action not in (
                CorrectionAction.INSERT_IMAGE, CorrectionAction.REPLACE_IMAGE,
                CorrectionAction.FIX_IMAGE_SRC,
            ):
                continue
            if correction.payload.get("hosted_url"):
                continue
            if await self._host_image(correction):
                uploaded += 1
        return uploaded

    async def insert_images(self, corrections: Sequence[Correction]) -> int:
        """Insert several missing figures; returns how many landed."""
        inserted = 0
        for correction in corrections:
            if await self.insert_image(correction):
                inserted += 1
        return inserted

    async def insert_image(self, correction: Correction) -> bool:
        """Add a figure from the PDF at the anchor the comparison engine chose."""
        anchor = self._resolve(correction, content_only=True)
        if anchor is None:
            return False
        hosted = await self._host_image(correction)
        if not hosted:
            correction.error = "figure pixels are not available to upload"
            return False

        figure = self.soup.new_tag("figure")
        figure[INSERTED_MARKER] = "image"
        figure["style"] = INSERTED_STYLE
        img = self.soup.new_tag("img", src=hosted["url"])
        img[INSERTED_MARKER] = "image"
        img["alt"] = correction.payload.get("alt") or correction.payload.get("caption") or ""
        img["loading"] = "lazy"
        img["style"] = "max-width:100%;height:auto"
        figure.append(img)
        caption = correction.payload.get("caption")
        if caption:
            figcaption = self.soup.new_tag("figcaption")
            figcaption.string = caption
            figure.append(figcaption)

        # if the caption already exists in the HTML, put the picture inside its figure
        host_figure = anchor.find_parent("figure")
        if host_figure is not None and anchor.name in ("figcaption", "caption"):
            if caption and normalize_text(anchor.get_text(" ", strip=True)) == normalize_text(caption):
                figure.find("figcaption") and figure.find("figcaption").decompose()
            anchor.insert_before(img)
            correction.note = "image inserted into the existing figure"
            return True

        if correction.payload.get("position") == "before":
            (host_figure or anchor).insert_before(figure)
        else:
            (host_figure or anchor).insert_after(figure)
        correction.note = "figure inserted"
        return True

    async def replace_image(self, correction: Correction) -> bool:
        """Repoint an <img> at the correct figure taken from the PDF."""
        target = self._resolve(correction)
        if target is None:
            return False
        if target.name != "img":
            nested = target.find("img")
            if nested is None:
                correction.error = "target is not an image"
                return False
            target = nested
        hosted = await self._host_image(correction)
        if not hosted:
            correction.error = "no replacement figure available"
            return False
        target["src"] = hosted["url"]
        for attr in ("srcset", "data-src", "data-srcset", "data-original", "data-lazy-src"):
            if target.has_attr(attr):
                del target[attr]
        alt = correction.payload.get("alt")
        if alt:
            target["alt"] = alt
        target[INSERTED_MARKER] = "replaced"
        correction.note = "image source replaced with the PDF figure"
        return True

    def _set_alt_text(self, correction: Correction) -> bool:
        target = self._resolve(correction)
        if target is None:
            return False
        if target.name != "img":
            target = target.find("img") or target
        target["alt"] = correction.payload.get("alt", "")
        correction.note = "alt text set from the PDF caption"
        return True

    # ------------------------------------------------------------------- text
    def _insert_text(self, correction: Correction) -> bool:
        anchor = self._resolve(correction, content_only=True)
        if anchor is None:
            return False
        tag_name = correction.payload.get("tag") or "p"
        element = self.soup.new_tag(tag_name)
        element.string = correction.payload.get("text", "")
        element[INSERTED_MARKER] = "text"
        element["style"] = INSERTED_STYLE

        options = correction.payload.get("options") or []
        if options:
            wrapper = self.soup.new_tag("span")
            wrapper.string = " " + "  ".join(options)
            element.append(wrapper)

        position = correction.payload.get("position", "after")
        if position == "append" or (tag_name == "li" and anchor.name in ("ol", "ul")):
            container = anchor if anchor.name in ("ol", "ul") else anchor.parent
            index = correction.payload.get("index")
            siblings = container.find_all("li", recursive=False) if container else []
            if isinstance(index, int) and 0 <= index < len(siblings):
                siblings[index].insert_before(element)
            elif container is not None:
                container.append(element)
            else:
                anchor.insert_after(element)
        elif position == "before":
            anchor = self._escape_list(anchor, tag_name)
            anchor.insert_before(element)
        else:
            self._insert_after(anchor, element, tag_name)
        correction.note = f"inserted <{tag_name}>"
        return True

    @staticmethod
    def _escape_list(anchor: Tag, tag_name: str) -> Tag:
        """Only an <li> may live inside a list; anything else anchors to the list."""
        if tag_name != "li" and anchor.name == "li":
            list_tag = anchor.find_parent(("ul", "ol"))
            if list_tag is not None:
                return list_tag
        return anchor

    def _insert_after(self, anchor: Tag, element: Tag, tag_name: str) -> None:
        """Place a block after its anchor — outside any list, in reading order.

        Two hard-won rules live here. A paragraph anchored to a list item must
        go after the *list*, not between its items — a <p> inside a <ul> renders
        as a stray line in whatever card the list happens to be in. And when
        many missing blocks share one anchor (sparse matches make that common),
        each goes after the previously inserted one, so the sequence reads in
        PDF order instead of reversed.
        """
        anchor = self._escape_list(anchor, tag_name)
        chain_key = id(anchor)
        previous = self._insertion_chains.get(chain_key)
        if previous is not None and previous.parent is not None:
            previous.insert_after(element)
        else:
            anchor.insert_after(element)
        self._insertion_chains[chain_key] = element

    def _remove(self, correction: Correction) -> bool:
        target = self._resolve(correction)
        if target is None:
            return False
        target.decompose()
        correction.note = "element removed"
        return True

    # -------------------------------------------------------------- structure
    def fix_structure(self, correction: Correction) -> bool:
        """Fix a heading level, or insert a missing section heading."""
        if correction.action == CorrectionAction.FIX_HEADING_LEVEL:
            target = self._resolve(correction)
            if target is None:
                return False
            level = int(correction.payload.get("level", 2))
            level = max(1, min(6, level))
            previous = target.name
            target.name = f"h{level}"
            correction.note = f"<{previous}> retagged as <h{level}>"
            return True

        anchor = self._resolve(correction, content_only=True)
        if anchor is None:
            return False
        level = max(1, min(6, int(correction.payload.get("level", 2))))
        heading = self.soup.new_tag(f"h{level}")
        heading.string = correction.payload.get("title", "")
        heading[INSERTED_MARKER] = "section"
        heading["style"] = INSERTED_STYLE
        if correction.payload.get("position") == "before":
            anchor.insert_before(heading)
        else:
            anchor.insert_after(heading)
        correction.note = f"section heading <h{level}> inserted"
        return True

    def _reorder(self, correction: Correction) -> bool:
        """Move an element — or a whole section — back to its PDF position."""
        target = self._resolve(correction, content_only=True)
        if target is None:
            return False
        anchor = self._resolve_path(
            correction.payload.get("after_dom_path"),
            expected_text=correction.payload.get("after_text"),
            content_only=True,
        )
        if anchor is None or anchor is target:
            correction.error = "reorder anchor not found"
            return False

        if correction.payload.get("scope") == "section":
            return self._reorder_section(correction, target, anchor)

        if anchor in target.descendants or target in anchor.descendants:
            correction.error = "reorder anchor is nested in the target"
            return False
        anchor.insert_after(target.extract())
        correction.note = "element moved into the PDF reading order"
        return True

    def _reorder_section(self, correction: Correction, heading: Tag, anchor: Tag) -> bool:
        """Move a heading together with everything that belongs under it.

        Moving a heading on its own would strand its paragraphs and figures in
        the wrong section, so the whole block travels as a unit.
        """
        block = _section_block(heading)
        if not block:
            correction.error = "section block could not be determined"
            return False
        if any(anchor is node or anchor in node.descendants for node in block):
            correction.error = "the anchor sits inside the section being moved"
            return False

        insert_after = anchor
        if correction.payload.get("after_scope") == "section":
            anchor_block = _section_block(anchor)
            if anchor_block:
                insert_after = anchor_block[-1]
        if any(insert_after is node for node in block):
            correction.error = "anchor and target sections overlap"
            return False

        for node in block:
            insert_after.insert_after(node.extract())
            insert_after = node
        correction.note = f"section moved with its {len(block) - 1} following element(s)"
        return True

    # -------------------------------------------------------------- alignment
    def fix_alignment(self, correction: Correction) -> bool:
        """Match the PDF's horizontal placement for a figure or block."""
        target = self._resolve(correction)
        if target is None:
            return False
        align = correction.payload.get("align", "center")
        container = target.find_parent("figure") or target
        rules = {
            "center": "display:block;margin-left:auto;margin-right:auto;text-align:center",
            "left": "display:block;margin-left:0;margin-right:auto;text-align:left",
            "right": "display:block;margin-left:auto;margin-right:0;text-align:right",
        }.get(align, "")
        if not rules:
            correction.error = f"unknown alignment '{align}'"
            return False
        container["style"] = _merge_style(container.get("style"), rules)
        correction.note = f"alignment set to {align}"
        return True

    def fix_text_wrapping(self, dom_path: str, style: str = "overflow-wrap:anywhere") -> bool:
        """Utility fix for blocks whose content overflows its container."""
        try:
            target = self.soup.select_one(dom_path)
        except Exception:
            target = None
        if target is None:
            return False
        target["style"] = _merge_style(target.get("style"), style)
        return True

    # -------------------------------------------------------------- watermarks
    def watermark_removal(self, correction: Correction) -> bool:
        """Remove a watermark element, or the watermark phrase inside it."""
        target = self._resolve(correction)
        if target is None:
            return False
        text = target.get_text(" ", strip=True)
        if len(text) > 160:
            # a long block that merely contains the phrase: strip the phrase only
            phrase = correction.payload.get("text")
            if phrase:
                for node in target.find_all(string=True):
                    if phrase.lower() in node.lower():
                        node.replace_with(re.sub(re.escape(phrase), "", node, flags=re.I))
                correction.note = "watermark phrase removed"
                return True
        target.decompose()
        correction.note = "watermark element removed"
        return True

    def _freeze(self) -> None:
        """Remove behaviour from a document whose scripts would undo the fixes.

        When a page builds its own content on load, reopening the corrected copy
        re-runs that build and throws every correction away. Freezing keeps the
        rendered result — corrections included — at the cost of the page's
        interactivity, which is the right trade for an artifact whose purpose is
        to be verified and published.
        """
        removed = 0
        for tag in self.soup.find_all("script"):
            if (tag.get("type") or "").lower() in ("application/json", "application/ld+json"):
                continue        # data, not behaviour
            tag.decompose()
            removed += 1
        self._reveal_hidden()
        self._unstick_pinned()
        self._restore_navigation()
        for tag in self.soup.find_all(True):
            for attribute in [a for a in tag.attrs if a.lower().startswith("on")]:
                del tag[attribute]
        if removed:
            head = self.soup.head
            if head is not None:
                head.append(Comment(
                    f" {removed} script(s) removed: this page rebuilt its own content on "
                    "load, which would have discarded the corrections "
                ))
            self._warn(
                f"removed {removed} script(s) so the corrections survive; interactive "
                "behaviour (tabs, menus) will not work in the corrected copy"
            )

    def _reveal_hidden(self) -> None:
        """Show the sections the page's now-removed navigation used to reveal.

        Without this the frozen copy shows one tab's worth of a chapter and
        hides the rest behind dead buttons — the corrections would be in the
        file but invisible to whoever opens it.
        """
        revealed = 0
        for path in self.reveal_paths:
            try:
                target = self.soup.select_one(path)
            except Exception:
                target = None
            if target is None:
                continue
            target["data-dcp-revealed"] = "1"
            revealed += 1
        if not revealed:
            return
        head = self.soup.head or self.soup.body
        if head is not None:
            style = self.soup.new_tag("style")
            style.string = ("[data-dcp-revealed]{display:block !important;"
                            "visibility:visible !important;opacity:1 !important;"
                            "height:auto !important;max-height:none !important}")
            head.append(style)
        self._warn(f"revealed {revealed} section(s) that the page's own navigation hid, "
                   "so the whole chapter is readable without its scripts")

    def _restore_navigation(self) -> None:
        """Turn dead tab buttons into anchor links to their sections.

        Freezing removes the scripts the tab bar relied on, and revealing shows
        every section at once — so the buttons would sit there doing nothing.
        When the buttons and the revealed sections line up one-to-one (in
        document order), each button becomes a plain link that jumps to its
        section. Anchors need no JavaScript, so nothing can rebuild the page.
        """
        nav = self.soup.find("nav")
        if nav is None or not self.reveal_paths:
            return
        buttons = [tag for tag in nav.find_all(["button", "a"])
                   if tag.get_text(strip=True)]

        first_hidden = None
        try:
            first_hidden = self.soup.select_one(self.reveal_paths[0])
        except Exception:
            pass
        if first_hidden is None or first_hidden.parent is None:
            return
        sections = [child for child in first_hidden.parent.find_all(True, recursive=False)
                    if child.get_text(strip=True)]
        if len(buttons) != len(sections) or len(buttons) < 2:
            return      # no confident mapping; leave the buttons inert

        for index, (button, section) in enumerate(zip(buttons, sections)):
            section_id = section.get("id") or f"dcp-section-{index}"
            section["id"] = section_id
            link = self.soup.new_tag("a", href=f"#{section_id}")
            for attribute, value in button.attrs.items():
                if attribute in ("href", "type", "onclick", "disabled"):
                    continue
                link[attribute] = value
            link["style"] = _merge_style(link.get("style"),
                                         "text-decoration:none;cursor:pointer")
            for child in list(button.children):
                link.append(child.extract())
            button.replace_with(link)
        self._warn(f"tab buttons converted to jump links: the {len(sections)} sections "
                   "are all visible, and each tab now scrolls to its section")

    def _unstick_pinned(self) -> None:
        """Let pinned bars scroll away in a frozen copy.

        A sticky tab bar earns its place while it works. Once its scripts are
        gone it cannot navigate anywhere, and because every section is now shown
        at once the page is long — so it just hovers over the text underneath.
        """
        if not self.unstick_paths:
            return
        unstuck = 0
        for path in self.unstick_paths:
            try:
                target = self.soup.select_one(path)
            except Exception:
                target = None
            if target is None:
                continue
            target["data-dcp-unstuck"] = "1"
            unstuck += 1
        if not unstuck:
            return
        head = self.soup.head or self.soup.body
        if head is not None:
            style = self.soup.new_tag("style")
            style.string = "[data-dcp-unstuck]{position:static !important}"
            head.append(style)
        self._warn(f"unpinned {unstuck} sticky/fixed element(s) that would otherwise "
                   "hover over the text in the frozen copy")

    # ------------------------------------------------------------------ misc
    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            logger.warning("[correction] %s", message)
            self.warnings.append(message)


_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_BLOCKISH = frozenset({
    "p", "div", "li", "td", "th", "caption", "figcaption", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "section", "article",
    "header", "footer", "aside", "figure", "ul", "ol", "table", "nav", "main",
})


def _own_text(tag: Tag) -> str:
    """A block's direct inline text, excluding nested blocks — the same measure
    the analyzer used when it recorded each target's text."""
    from bs4 import NavigableString

    parts: List[str] = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag) and child.name not in _BLOCKISH \
                and child.name not in ("script", "style"):
            parts.append(child.get_text(" ", strip=False))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _section_block(heading: Tag) -> List[Tag]:
    """A heading plus its following siblings, up to the next heading of the
    same or higher rank — i.e. everything that reads as part of that section."""
    if heading.name not in _HEADINGS:
        return [heading]
    level = int(heading.name[1])
    block = [heading]
    for sibling in heading.find_next_siblings():
        if sibling.name in _HEADINGS and int(sibling.name[1]) <= level:
            break
        block.append(sibling)
    return block


def _same_src(actual: Optional[str], expected: Optional[str]) -> bool:
    """Compare image sources across raw attributes and browser-resolved URLs."""
    if not actual or not expected:
        return False
    if actual == expected:
        return True
    def tail(value: str) -> str:
        value = value.split("?")[0].split("#")[0]
        for prefix in ("file://", "https://", "http://"):
            if value.startswith(prefix):
                value = value[len(prefix):]
        return value.rstrip("/").rsplit("/", 1)[-1]
    return bool(tail(actual)) and tail(actual) == tail(expected)


def _merge_style(existing: Optional[str], additions: str) -> str:
    """Merge CSS declarations, letting the new ones win."""
    declarations: Dict[str, str] = {}
    for chunk in (existing or "").split(";"):
        if ":" in chunk:
            key, _, value = chunk.partition(":")
            declarations[key.strip().lower()] = value.strip()
    for chunk in additions.split(";"):
        if ":" in chunk:
            key, _, value = chunk.partition(":")
            declarations[key.strip().lower()] = value.strip()
    return ";".join(f"{k}:{v}" for k, v in declarations.items() if k and v)
