"""Compare the section structure of two HTML renditions of one chapter.

The delivery instructions require the English and Malayalam HTML of a chapter
to share one common section count, order and structure. Their *text* is in
different languages, so text similarity is useless here — what must line up is
the language-independent skeleton: the number of sections, the sequence of
heading levels, the question numbering, and the images per section. This module
extracts that skeleton from each document and reports where they diverge.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, Tag

# "1." / "1)" / "൧." style leaders on list items or paragraphs mark questions.
_QUESTION_RE = re.compile(r"^\s*(\d{1,3})\s*[.)]")


def _panels(soup: BeautifulSoup) -> List[Tag]:
    """The document's sections: tab panels when the template has them.

    The textbook templates navigate with in-page anchors; each `a[href="#id"]`
    that resolves to an element is one section panel. Documents without that
    structure fall back to a single whole-body section.
    """
    seen: List[Tag] = []
    ids = set()
    for anchor in soup.select('a[href^="#"]'):
        target_id = (anchor.get("href") or "#")[1:]
        if not target_id or target_id in ids:
            continue
        target = soup.find(id=target_id)
        if target is not None and target.find(["p", "h1", "h2", "h3", "li", "img"]):
            ids.add(target_id)
            seen.append(target)
    if seen:
        return seen
    body = soup.body or soup
    return [body]


def _section_profile(panel: Tag) -> Dict[str, Any]:
    headings = [int(h.name[1]) for h in panel.find_all(re.compile(r"^h[1-6]$"))]
    title_tag = panel.find(re.compile(r"^h[1-4]$"))
    questions: List[int] = []
    for node in panel.find_all(["li", "p"]):
        text = node.get_text(" ", strip=True)
        matched = _QUESTION_RE.match(text)
        if matched:
            questions.append(int(matched.group(1)))
    return {
        "id": panel.get("id") or "",
        "title": (title_tag.get_text(" ", strip=True)[:80] if title_tag else ""),
        "headingLevels": headings,
        "images": len(panel.find_all("img")),
        "tables": len(panel.find_all("table")),
        "listItems": len(panel.find_all("li")),
        "questionNumbers": sorted(set(questions)),
    }


def skeleton(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    sections = [_section_profile(panel) for panel in _panels(soup)]
    return {
        "sectionCount": len(sections),
        "sections": sections,
        "totalImages": len(soup.find_all("img")),
        "totalQuestions": sorted({q for s in sections for q in s["questionNumbers"]}),
    }


def _numbering_gaps(numbers: List[int]) -> List[int]:
    """Numbers missing from an otherwise 1..N question sequence."""
    if not numbers:
        return []
    return [n for n in range(1, max(numbers) + 1) if n not in numbers]


def compare_structures(html_a: str, html_b: str,
                       label_a: str = "A", label_b: str = "B") -> Dict[str, Any]:
    """A report of every structural divergence between the two renditions."""
    a, b = skeleton(html_a), skeleton(html_b)
    problems: List[str] = []

    if a["sectionCount"] != b["sectionCount"]:
        problems.append(
            f"section count differs: {label_a} has {a['sectionCount']}, "
            f"{label_b} has {b['sectionCount']} — the instructions require one "
            f"common section sequence for both languages"
        )

    pairs: List[Dict[str, Any]] = []
    for index in range(max(a["sectionCount"], b["sectionCount"])):
        section_a = a["sections"][index] if index < a["sectionCount"] else None
        section_b = b["sections"][index] if index < b["sectionCount"] else None
        row: Dict[str, Any] = {"index": index + 1,
                               label_a: section_a, label_b: section_b}
        if section_a is None or section_b is None:
            present, missing = (label_a, label_b) if section_b is None else (label_b, label_a)
            named = (section_a or section_b or {}).get("title") or f"#{index + 1}"
            problems.append(f"section {named!r} exists only in {present}; "
                            f"missing from {missing}")
        else:
            if section_a["images"] != section_b["images"]:
                problems.append(
                    f"section {index + 1} ({section_a['title'] or section_a['id']}): "
                    f"{section_a['images']} image(s) in {label_a} vs "
                    f"{section_b['images']} in {label_b}")
            if section_a["questionNumbers"] != section_b["questionNumbers"]:
                only_a = sorted(set(section_a["questionNumbers"]) - set(section_b["questionNumbers"]))
                only_b = sorted(set(section_b["questionNumbers"]) - set(section_a["questionNumbers"]))
                detail = []
                if only_a:
                    detail.append(f"questions {only_a} only in {label_a}")
                if only_b:
                    detail.append(f"questions {only_b} only in {label_b}")
                problems.append(f"section {index + 1}: " + "; ".join(detail))
            if len(section_a["headingLevels"]) != len(section_b["headingLevels"]):
                problems.append(
                    f"section {index + 1}: {len(section_a['headingLevels'])} "
                    f"heading(s) in {label_a} vs "
                    f"{len(section_b['headingLevels'])} in {label_b}")
        pairs.append(row)

    for label, sk in ((label_a, a), (label_b, b)):
        gaps = _numbering_gaps(sk["totalQuestions"])
        if gaps:
            problems.append(f"{label}: question number(s) {gaps} appear to be "
                            f"missing from the sequence")

    return {
        "match": not problems,
        "problems": problems,
        label_a: a,
        label_b: b,
        "sections": pairs,
    }
