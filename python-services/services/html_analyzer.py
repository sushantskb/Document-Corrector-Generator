"""HTML extraction built on BeautifulSoup, with Playwright for rendered pages.

Static parsing alone cannot answer two questions Phase 2 depends on: what the
page looks like *after* its JavaScript runs, and where each element actually
sits on screen. When Chromium is available the analyzer renders the page, pulls
per-element geometry and visibility out of the live DOM, and parses that
rendered markup instead; otherwise it degrades to static parsing and records a
warning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from models.models import (
    BBox, DocumentAnalysis, DocumentMetadata, DocumentType, ImageElement,
    PageLayout, QuestionElement, StructureElement, TableElement, TextElement,
)
from utils.file_utils import decode_text, download_text, is_url, save_temp_file
from utils.image_matcher import describe_image, download_image, is_content_image
from utils.text_matcher import (
    detect_watermark_text, has_question_hint, looks_like_question, normalize_text, parse_question,
)

logger = logging.getLogger(__name__)

RENDER_TIMEOUT_MS = int(os.getenv("HTML_RENDER_TIMEOUT_MS", "30000"))
IMAGE_FETCH_CONCURRENCY = int(os.getenv("IMAGE_FETCH_CONCURRENCY", "6"))
VIEWPORT = {"width": 1280, "height": 1800}

BLOCK_TAGS = {
    "p", "div", "li", "td", "th", "caption", "figcaption", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "section", "article",
    "header", "footer", "aside", "label", "summary", "legend", "figure",
}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "meta", "link", "title"}
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Same algorithm as _dom_path below, executed inside the page so the paths line up.
_GEOMETRY_SCRIPT = """
() => {
  const pathOf = (el) => {
    const parts = [];
    while (el && el.nodeType === 1) {
      const tag = el.tagName.toLowerCase();
      if (tag === 'html') { parts.unshift('html'); break; }
      let index = 1;
      let sibling = el.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === el.tagName) index++;
        sibling = sibling.previousElementSibling;
      }
      parts.unshift(`${tag}:nth-of-type(${index})`);
      el = el.parentElement;
    }
    return parts.join(' > ');
  };
  const out = [];
  document.querySelectorAll('*').forEach((el) => {
    const tag = el.tagName.toLowerCase();
    if (['script', 'style', 'noscript', 'template', 'meta', 'link'].includes(tag)) return;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    out.push({
      path: pathOf(el),
      tag: tag,
      x: rect.x + window.scrollX,
      y: rect.y + window.scrollY,
      width: rect.width,
      height: rect.height,
      position: style.position,
      visible: !(style.display === 'none' || style.visibility === 'hidden' ||
                 parseFloat(style.opacity || '1') < 0.05 ||
                 (rect.width === 0 && rect.height === 0)),
      opacity: parseFloat(style.opacity || '1'),
      textAlign: style.textAlign,
      fontSize: parseFloat(style.fontSize) || null,
      fontWeight: style.fontWeight,
      color: style.color,
      background: (style.backgroundImage && style.backgroundImage !== 'none')
        ? style.backgroundImage : null,
      currentSrc: el.currentSrc || null,
      naturalWidth: el.naturalWidth || null,
      naturalHeight: el.naturalHeight || null,
    });
  });
  return {
    elements: out,
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
    title: document.title,
    lang: document.documentElement.lang || null,
  };
}
"""


class HTMLAnalyzer:
    """Analyze one HTML document from markup, a local path or a URL."""

    def __init__(self, html: Optional[str] = None, url: Optional[str] = None,
                 path: Optional[str] = None, base_url: Optional[str] = None,
                 render_js: bool = True, fetch_images: bool = True):
        if html is None and url is None and path is None:
            raise ValueError("HTMLAnalyzer needs html, a path or a url")
        self.html = html
        self.url = url
        self.path = path
        self.base_url = base_url or url or (f"file://{os.path.abspath(path)}" if path else None)
        self.render_js = render_js
        self.fetch_images = fetch_images
        self.source = url or path
        self.rendered_html: Optional[str] = None
        self.geometry: Dict[str, Dict[str, Any]] = {}
        self.page_size: Tuple[float, float] = (VIEWPORT["width"], VIEWPORT["height"])
        self.warnings: List[str] = []
        self.pixel_cache: Dict[str, Any] = {}
        self.injected_base = False   # a <base> we added for rendering must not ship
        self.js_generated = False    # True when the content only exists after JS runs
        self.hidden_content: List[str] = []   # panels the page's own navigation hides
        self.pinned_elements: List[str] = []  # sticky/fixed bars that would cover content
        self._soup: Optional[BeautifulSoup] = None

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    async def from_url(cls, url: str, **kwargs) -> "HTMLAnalyzer":
        if is_url(url):
            html = await download_text(url)
            return cls(html=html, url=url, base_url=url, **kwargs)
        with open(url, "rb") as fh:
            html = decode_text(fh.read())
        return cls(html=html, path=url, **kwargs)

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            logger.warning("[html] %s", message)
            self.warnings.append(message)

    @property
    def raw_html(self) -> str:
        if self.html is None and self.path:
            with open(self.path, "rb") as fh:
                self.html = decode_text(fh.read())
        return self.html or ""

    # ------------------------------------------------------------------- public
    async def analyze(self) -> DocumentAnalysis:
        """Render (when possible), then extract everything."""
        if self.render_js:
            await self.render_dynamic_content()
        soup = self.parse_static_html()

        text_elements = self.extract_text(soup)
        self._mark_tab_content_visible(text_elements)
        images = await self.extract_images(soup)
        structure = self.extract_structure(soup)
        questions = self.extract_questions(soup)
        tables = self.extract_tables(soup)
        metadata = self.extract_metadata(soup)
        embedded = self.extract_embedded_data(soup)

        width, height = self.page_size
        layout = [PageLayout(
            page=1, width=float(width), height=float(height),
            text_blocks=len(text_elements), images=len(images), tables=len(tables),
            rects=0, lines=0, curves=0,
            columns=1,
        )]

        return DocumentAnalysis(
            doc_type=DocumentType.HTML,
            source=self.source,
            metadata=metadata,
            text_elements=text_elements,
            images=images,
            structure=structure,
            questions=questions,
            tables=tables,
            pages=layout,
            embedded_data=embedded,
            warnings=list(self.warnings),
            stats={
                "text_blocks": len(text_elements),
                "words": sum(len(el.text.split()) for el in text_elements),
                "images": len(images),
                "broken_images": sum(1 for i in images if i.error),
                "hidden_blocks": sum(1 for el in text_elements if not el.visible),
                "tables": len(tables),
                "headings": len(structure),
                "questions": len(questions),
                "rendered": self.rendered_html is not None,
                "document_height": height,
                "watermarks": self.detect_watermarks(text_elements),
            },
        )

    # ------------------------------------------------------------------ parsing
    def parse_static_html(self) -> BeautifulSoup:
        """BeautifulSoup tree of the rendered markup when available, else the raw markup."""
        if self._soup is None:
            markup = self.rendered_html or self.raw_html
            try:
                self._soup = BeautifulSoup(markup, "lxml")
            except Exception as exc:
                self._warn(f"lxml parser unavailable ({exc}); using html.parser")
                self._soup = BeautifulSoup(markup, "html.parser")
        return self._soup

    async def render_dynamic_content(self) -> Optional[str]:
        """Load the page in Chromium and capture post-JS markup plus geometry."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self._warn("playwright is not installed; JavaScript content was not rendered")
            return None

        target = self.url if is_url(self.url or "") else None
        temp_path: Optional[str] = None
        if target is None:
            markup = self.raw_html
            if self.base_url and "<base " not in markup.lower():
                markup, replacements = re.subn(
                    r"(<head[^>]*>)", rf"\1<base href=\"{self.base_url}\">", markup,
                    count=1, flags=re.IGNORECASE,
                )
                self.injected_base = bool(replacements)
            if self.path:
                target = f"file://{os.path.abspath(self.path)}"
            else:
                temp_path = save_temp_file(markup.encode("utf-8"), suffix=".html")
                target = f"file://{temp_path}"

        browser = None
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                page = await browser.new_page(viewport=VIEWPORT)
                page.set_default_timeout(RENDER_TIMEOUT_MS)
                # `domcontentloaded` is the reliable milestone: a single CDN
                # resource that never resolves keeps `load` from ever firing,
                # and losing the render means losing every hidden tab panel
                await page.goto(target, wait_until="domcontentloaded",
                                timeout=RENDER_TIMEOUT_MS)
                for state, budget in (("load", 12000), ("networkidle", 8000)):
                    try:
                        await page.wait_for_load_state(state, timeout=budget)
                    except Exception:
                        break    # best effort; the DOM is already there
                await page.evaluate(
                    "() => window.scrollTo(0, document.body.scrollHeight)"
                )
                await page.wait_for_timeout(400)      # let lazy images swap in
                data = await page.evaluate(_GEOMETRY_SCRIPT)
                self.rendered_html = await page.content()
                self._soup = None
                self._ingest_geometry(data)
                self._detect_js_generated()
                self._find_hidden_content()
                return self.rendered_html
        except Exception as exc:
            self._warn(f"JavaScript rendering failed ({exc}); analyzed static HTML instead")
            return None
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    def _find_hidden_content(self, min_chars: int = 200) -> None:
        """Locate content the page shows only through its own navigation.

        A tabbed chapter keeps every section in the DOM and reveals one at a
        time. If the corrected copy has to drop the scripts, those sections
        become unreachable — so they are recorded here and un-hidden when the
        document is frozen. Only the outermost hidden container is recorded, so
        one tab panel does not produce a hundred entries.
        """
        soup = self.parse_static_html()
        hidden: List[str] = []
        for tag in (soup.body or soup).find_all(True):
            if tag.name in SKIP_TAGS or tag.name in ("body", "html"):
                continue
            geo = self._geometry_for(tag)
            if geo is None or geo.get("visible", True):
                continue
            if len(tag.get_text(" ", strip=True)) < min_chars:
                continue
            path = self._dom_path(tag)
            if any(path.startswith(existing + " > ") for existing in hidden):
                continue        # already covered by an ancestor
            hidden.append(path)
        self.hidden_content = hidden
        if hidden:
            logger.info("[html] %s hidden content panel(s) recorded for the frozen copy",
                        len(hidden))

    def _mark_tab_content_visible(self, text_elements: List[TextElement]) -> None:
        """Content behind the page's own navigation is visible content.

        A tabbed page keeps five of its six panels at ``display:none`` until a
        tab is clicked. Geometrically that is invisible — but a reader reaches
        all of it, so treating it as hidden would make the comparison blind to
        most of the document and report the whole chapter as missing.
        """
        if not self.hidden_content:
            return
        marked = 0
        for element in text_elements:
            if element.visible or not element.dom_path:
                continue
            if any(element.dom_path == panel or element.dom_path.startswith(panel + " > ")
                   for panel in self.hidden_content):
                element.visible = True
                marked += 1
        if marked:
            logger.info("[html] %s text block(s) behind tab navigation treated as visible",
                        marked)

    def _detect_js_generated(self) -> None:
        """Did the page's own scripts build most of its content?

        A document whose markup is nearly empty until JavaScript runs cannot
        keep both its scripts and any corrections: reopening it would rebuild
        the DOM and discard them.
        """
        try:
            static = BeautifulSoup(self.raw_html, "lxml")
            for tag in static.find_all(["script", "style", "noscript"]):
                tag.decompose()
            static_length = len(static.get_text(" ", strip=True))
            rendered = BeautifulSoup(self.rendered_html or "", "lxml")
            for tag in rendered.find_all(["script", "style", "noscript"]):
                tag.decompose()
            rendered_length = len(rendered.get_text(" ", strip=True))
        except Exception:
            return
        if rendered_length > 500 and static_length < rendered_length * 0.5:
            self.js_generated = True
            self._warn(
                f"content is generated by JavaScript ({static_length} characters in the "
                f"markup, {rendered_length} after rendering)"
            )

    def _ingest_geometry(self, data: Dict[str, Any]) -> None:
        for item in data.get("elements", []):
            self.geometry[item["path"]] = item
            if item.get("position") in ("sticky", "fixed"):
                self.pinned_elements.append(item["path"])
        self.page_size = (
            float(data.get("scrollWidth") or VIEWPORT["width"]),
            float(data.get("scrollHeight") or VIEWPORT["height"]),
        )
        self._rendered_title = data.get("title")
        self._rendered_lang = data.get("lang")

    # ----------------------------------------------------------------- dom paths
    @staticmethod
    def _dom_path(tag: Tag) -> str:
        """`html > body > div:nth-of-type(1) > p:nth-of-type(2)` — matches the JS side."""
        parts: List[str] = []
        node: Optional[Tag] = tag
        while isinstance(node, Tag) and node.name:
            name = node.name.lower()
            if name in ("[document]",):
                break
            if name == "html":
                parts.insert(0, "html")
                break
            index = 1
            sibling = node.previous_sibling
            while sibling is not None:
                if isinstance(sibling, Tag) and sibling.name == node.name:
                    index += 1
                sibling = sibling.previous_sibling
            parts.insert(0, f"{name}:nth-of-type({index})")
            node = node.parent
        return " > ".join(parts)

    def _geometry_for(self, tag: Tag) -> Optional[Dict[str, Any]]:
        return self.geometry.get(self._dom_path(tag))

    def _bbox_for(self, tag: Tag) -> Optional[BBox]:
        geo = self._geometry_for(tag)
        if not geo:
            return None
        return BBox.from_tuple((
            geo.get("x", 0.0), geo.get("y", 0.0),
            geo.get("x", 0.0) + geo.get("width", 0.0),
            geo.get("y", 0.0) + geo.get("height", 0.0),
        ))

    def _is_visible(self, tag: Tag) -> bool:
        geo = self._geometry_for(tag)
        if geo is not None:
            return bool(geo.get("visible", True))
        # static fallback: honour hidden attributes and obvious inline styles
        for node in [tag, *tag.parents]:
            if not isinstance(node, Tag):
                continue
            if node.has_attr("hidden") or node.get("aria-hidden") == "true":
                return False
            style = (node.get("style") or "").replace(" ", "").lower()
            if "display:none" in style or "visibility:hidden" in style or "opacity:0" in style:
                return False
        return True

    # --------------------------------------------------------------------- text
    def extract_text(self, soup: Optional[BeautifulSoup] = None) -> List[TextElement]:
        """One TextElement per block, holding only that block's own inline text."""
        soup = soup or self.parse_static_html()
        elements: List[TextElement] = []
        order = 0
        body = soup.body or soup
        for tag in body.find_all(True):
            if tag.name in SKIP_TAGS or tag.name not in BLOCK_TAGS:
                continue
            text = self._own_text(tag)
            if not text:
                continue
            geo = self._geometry_for(tag) or {}
            weight = str(geo.get("fontWeight") or "")
            element = TextElement(
                text=text,
                tag=tag.name,
                dom_path=self._dom_path(tag),
                bbox=self._bbox_for(tag),
                size=geo.get("fontSize"),
                color=geo.get("color"),
                bold=weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600),
                visible=self._is_visible(tag),
                order_index=order,
                kind=self._kind_for(tag),
            )
            elements.append(element)
            order += 1
        return elements

    @staticmethod
    def _own_text(tag: Tag) -> str:
        """Text of a block minus the text of nested blocks, so nothing is counted twice."""
        parts: List[str] = []
        for child in tag.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                if child.name in SKIP_TAGS or child.name in BLOCK_TAGS:
                    continue
                parts.append(child.get_text(" ", strip=False))
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    @staticmethod
    def _kind_for(tag: Tag) -> str:
        if tag.name in HEADING_TAGS:
            return "heading"
        if tag.name == "li":
            return "list_item"
        if tag.name in ("figcaption", "caption"):
            return "caption"
        if tag.name in ("td", "th"):
            return "table"
        return "paragraph"

    # ------------------------------------------------------------------- images
    async def extract_images(self, soup: Optional[BeautifulSoup] = None) -> List[ImageElement]:
        """<img>, <picture>, CSS background images and inline SVG, with pixels fetched."""
        soup = soup or self.parse_static_html()
        elements: List[ImageElement] = []
        order = 0

        for tag in (soup.body or soup).find_all(["img", "image"]):
            src = self._resolve_src(tag)
            geo = self._geometry_for(tag) or {}
            element = ImageElement(
                source="HTML", kind="raster", order_index=order,
                src=src,
                alt=(tag.get("alt") if tag.has_attr("alt") else None),
                dom_path=self._dom_path(tag),
                bbox=self._bbox_for(tag),
                width=_to_float(geo.get("naturalWidth") or tag.get("width") or geo.get("width")),
                height=_to_float(geo.get("naturalHeight") or tag.get("height") or geo.get("height")),
                caption=self._caption_for(tag),
                is_decorative=(tag.get("alt") == "" and tag.get("role") == "presentation"),
                preceding_text_path=self._preceding_block_path(tag),
            )
            if not src:
                element.error = "img tag has no usable src"
            elements.append(element)
            order += 1

        for tag in (soup.body or soup).find_all(style=True):
            match = re.search(r"background(?:-image)?\s*:\s*url\(['\"]?([^'\")]+)", tag["style"], re.I)
            if not match:
                continue
            elements.append(ImageElement(
                source="HTML", kind="region", order_index=order,
                src=self._absolute(match.group(1)),
                dom_path=self._dom_path(tag), bbox=self._bbox_for(tag),
                alt=tag.get("aria-label"),
            ))
            order += 1

        for tag in (soup.body or soup).find_all("svg"):
            elements.append(ImageElement(
                source="HTML", kind="vector", order_index=order,
                dom_path=self._dom_path(tag), bbox=self._bbox_for(tag),
                alt=(tag.find("title").get_text(strip=True) if tag.find("title") else None),
                src=None,
            ))
            order += 1

        if self.fetch_images:
            await self._fetch_pixels(elements)
        return elements

    def _preceding_block_path(self, tag: Tag) -> Optional[str]:
        """The dom path of the text block just before this image, by DOM order.

        Geometry cannot answer this for content inside a hidden tab panel —
        every rect there is 0×0 — but document order always can, and "the text
        right before the image" is what identifies which figure belongs where.
        """
        for candidate in tag.find_all_previous(True):
            if candidate.name in SKIP_TAGS or candidate.name not in BLOCK_TAGS:
                continue
            if self._own_text(candidate):
                return self._dom_path(candidate)
        return None

    def _resolve_src(self, tag: Tag) -> Optional[str]:
        geo = self._geometry_for(tag) or {}
        if geo.get("currentSrc"):
            return geo["currentSrc"]
        placeholder: Optional[str] = None
        for attr in ("src", "data-src", "data-original", "data-lazy-src", "xlink:href"):
            value = (tag.get(attr) or "").strip()
            if not value:
                continue
            if value.startswith("data:image/gif;base64,R0lGOD"):
                # the 1x1 spacer lazy-loaders park in src until the real image
                # arrives; keep it only if no genuine source turns up
                placeholder = placeholder or value
                continue
            return self._absolute(value)
        srcset = tag.get("srcset") or tag.get("data-srcset")
        if srcset:
            candidates = [part.strip().split(" ")[0] for part in srcset.split(",") if part.strip()]
            if candidates:
                return self._absolute(candidates[-1])
        parent = tag.parent
        if isinstance(parent, Tag) and parent.name == "picture":
            source = parent.find("source")
            if source and source.get("srcset"):
                return self._absolute(source["srcset"].split(",")[0].strip().split(" ")[0])
        return placeholder

    def _absolute(self, src: str) -> str:
        if not src or src.startswith(("data:", "http://", "https://", "file://")):
            return src
        if self.base_url:
            return urljoin(self.base_url, src)
        if os.path.isabs(src):
            return f"file://{src}"
        return src

    @staticmethod
    def _caption_for(tag: Tag) -> Optional[str]:
        figure = tag.find_parent("figure")
        if figure:
            caption = figure.find("figcaption")
            if caption:
                return caption.get_text(" ", strip=True)
        sibling = tag.find_next_sibling()
        if isinstance(sibling, Tag) and sibling.name in ("figcaption", "caption", "small"):
            return sibling.get_text(" ", strip=True)
        return None

    async def _fetch_pixels(self, elements: List[ImageElement]) -> None:
        """Download every referenced image so it can be hashed and compared."""
        semaphore = asyncio.Semaphore(IMAGE_FETCH_CONCURRENCY)

        async def load(element: ImageElement) -> None:
            if not element.src:
                return
            src = element.src
            if src.startswith(("http://", "https://")) and not is_url(src):
                element.error = f"malformed image URL: {src!r}"
                return
            if src.startswith("file://"):
                local = src[7:]
                try:
                    with open(local, "rb") as fh:
                        data = fh.read()
                    from utils.image_matcher import load_image
                    image, error = load_image(data), None
                except OSError as exc:
                    image, data, error = None, None, str(exc)
            else:
                async with semaphore:
                    image, data, error = await download_image(src)
            if image is None:
                element.error = error or "image could not be decoded"
                return
            info = describe_image(image, data)
            element.phash = info.get("phash")
            element.dhash = info.get("dhash")
            element.sha256 = info.get("sha256")
            if not element.width or not element.height:
                element.width, element.height = float(image.size[0]), float(image.size[1])
            if not is_content_image(image, data):
                element.is_decorative = True
            if data:
                element.local_path = save_temp_file(data, suffix=".img")
            self.pixel_cache[element.id] = image

        await asyncio.gather(*(load(element) for element in elements))

    # ---------------------------------------------------------------- structure
    def extract_structure(self, soup: Optional[BeautifulSoup] = None) -> List[StructureElement]:
        soup = soup or self.parse_static_html()
        nodes: List[StructureElement] = []
        stack: List[StructureElement] = []
        for order, tag in enumerate((soup.body or soup).find_all(HEADING_TAGS)):
            level = int(tag.name[1])
            node = StructureElement(
                title=tag.get_text(" ", strip=True), level=level, order_index=order,
                dom_path=self._dom_path(tag), tag=tag.name, page=1,
            )
            while stack and stack[-1].level >= level:
                stack.pop()
            if stack:
                node.parent_id = stack[-1].id
                stack[-1].children.append(node.id)
            stack.append(node)
            nodes.append(node)
        return nodes

    # ----------------------------------------------------------------- questions
    def extract_questions(self, soup: Optional[BeautifulSoup] = None) -> List[QuestionElement]:
        """Numbered/lettered questions from prose plus <ol><li> exercise lists."""
        soup = soup or self.parse_static_html()
        questions: List[QuestionElement] = []
        seen: set = set()

        for ordered_list in (soup.body or soup).find_all("ol"):
            for index, item in enumerate(ordered_list.find_all("li", recursive=False)):
                text = item.get_text(" ", strip=True)
                if not text or not has_question_hint(text):
                    continue
                parsed = parse_question(text) or {}
                key = normalize_text(parsed.get("text") or text)
                if key in seen:
                    continue
                seen.add(key)
                questions.append(QuestionElement(
                    number=parsed.get("number") or str(index + 1),
                    text=parsed.get("text") or text,
                    options=parsed.get("options") or [],
                    numbering_explicit=bool(parsed.get("number")),
                    dom_path=self._dom_path(item),
                    page=1, order_index=len(questions),
                ))

        for tag in (soup.body or soup).find_all(["p", "li", "div", "td"]):
            text = self._own_text(tag)
            if not looks_like_question(text):
                continue
            parsed = parse_question(text) or {}
            key = normalize_text(parsed.get("text") or text)
            if key in seen:
                continue
            seen.add(key)
            questions.append(QuestionElement(
                number=parsed.get("number"),
                text=parsed.get("text") or text,
                options=parsed.get("options") or [],
                dom_path=self._dom_path(tag),
                page=1, order_index=len(questions),
            ))
        questions.sort(key=lambda q: q.order_index)
        return questions

    # -------------------------------------------------------------------- tables
    def extract_tables(self, soup: Optional[BeautifulSoup] = None) -> List[TableElement]:
        soup = soup or self.parse_static_html()
        tables: List[TableElement] = []
        for tag in (soup.body or soup).find_all("table"):
            rows = tag.find_all("tr")
            cols = max((len(r.find_all(["td", "th"])) for r in rows), default=0)
            tables.append(TableElement(
                page=1, bbox=self._bbox_for(tag), rows=len(rows), cols=cols,
                dom_path=self._dom_path(tag),
                text=" | ".join(r.get_text(" ", strip=True) for r in rows),
            ))
        return tables

    # ------------------------------------------------------------------ metadata
    def extract_metadata(self, soup: Optional[BeautifulSoup] = None) -> DocumentMetadata:
        soup = soup or self.parse_static_html()
        meta: Dict[str, str] = {}
        for tag in soup.find_all("meta"):
            key = tag.get("name") or tag.get("property") or tag.get("http-equiv")
            if key and tag.get("content"):
                meta[key.lower()] = tag["content"]
        html_tag = soup.find("html")
        title_tag = soup.find("title")
        return DocumentMetadata(
            title=(title_tag.get_text(strip=True) if title_tag else None)
            or getattr(self, "_rendered_title", None),
            author=meta.get("author"),
            subject=meta.get("description"),
            language=(html_tag.get("lang") if html_tag else None)
            or getattr(self, "_rendered_lang", None),
            page_count=1,
            extra=meta,
        )

    # ------------------------------------------------------------- embedded data
    def extract_embedded_data(self, soup: Optional[BeautifulSoup] = None) -> List[Dict[str, Any]]:
        """JSON payloads in <script> tags — often where an SPA keeps its content."""
        soup = soup or self.parse_static_html()
        payloads: List[Dict[str, Any]] = []
        for tag in soup.find_all("script"):
            script_type = (tag.get("type") or "").lower()
            raw = tag.string or tag.get_text() or ""
            raw = raw.strip()
            if not raw:
                continue
            if script_type in ("application/json", "application/ld+json") or tag.get("id"):
                parsed = _safe_json(raw)
                if parsed is not None:
                    payloads.append({
                        "id": tag.get("id"), "type": script_type or "inline",
                        "data": parsed,
                    })
                    continue
            for match in re.finditer(
                r"(?:window|self|globalThis)\.([A-Za-z0-9_$]+)\s*=\s*({.*?});", raw, re.S
            ):
                parsed = _safe_json(match.group(2))
                if parsed is not None:
                    payloads.append({"id": match.group(1), "type": "window-global", "data": parsed})
        return payloads[:50]

    # -------------------------------------------------------------------- domtree
    def get_dom_tree(self, soup: Optional[BeautifulSoup] = None, max_depth: int = 12) -> Dict[str, Any]:
        """Nested tag tree with paths, geometry and text sizes (depth limited)."""
        soup = soup or self.parse_static_html()
        root = soup.body or soup

        def walk(tag: Tag, depth: int) -> Dict[str, Any]:
            geo = self._geometry_for(tag) or {}
            node: Dict[str, Any] = {
                "tag": tag.name,
                "path": self._dom_path(tag),
                "id": tag.get("id"),
                "classes": tag.get("class") or [],
                "text_length": len(self._own_text(tag)),
                "visible": bool(geo.get("visible", True)),
            }
            if geo:
                node["rect"] = {k: geo.get(k) for k in ("x", "y", "width", "height")}
            if depth < max_depth:
                children = [walk(child, depth + 1) for child in tag.find_all(True, recursive=False)
                            if child.name not in SKIP_TAGS]
                if children:
                    node["children"] = children
            return node

        return walk(root, 0) if isinstance(root, Tag) else {}

    # ---------------------------------------------------------------- watermarks
    def detect_watermarks(self, text_elements: List[TextElement]) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        for element in text_elements:
            hint = detect_watermark_text(element.text)
            geo = self.geometry.get(element.dom_path or "", {})
            faint = 0 < float(geo.get("opacity", 1.0) or 1.0) < 0.35
            if hint or faint:
                found.append({
                    "text": element.text[:120],
                    "dom_path": element.dom_path,
                    "reason": "hint" if hint else "very-low-opacity",
                    "confidence": 0.9 if hint else 0.6,
                })
        return found[:20]


def _safe_json(raw: str) -> Optional[Any]:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace("px", "").strip())
    except (TypeError, ValueError):
        return None
