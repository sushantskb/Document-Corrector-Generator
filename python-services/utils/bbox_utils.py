"""Bounding box geometry helpers.

All boxes use the top-left origin convention that pdfplumber's ``top``/``bottom``
and the browser's ``getBoundingClientRect`` both follow.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from models.models import BBox

BoxLike = object  # BBox | tuple | dict


def to_bbox(value: BoxLike) -> Optional[BBox]:
    """Coerce tuples/dicts/BBox into a BBox (None passes through)."""
    if value is None:
        return None
    if isinstance(value, BBox):
        return value
    if isinstance(value, dict):
        if {"x0", "top", "x1", "bottom"} <= set(value):
            return BBox.from_tuple((value["x0"], value["top"], value["x1"], value["bottom"]))
        if {"x", "y", "width", "height"} <= set(value):
            x, y = float(value["x"]), float(value["y"])
            return BBox.from_tuple((x, y, x + float(value["width"]), y + float(value["height"])))
        return None
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return BBox.from_tuple(value)
    return None


def normalize_bbox(bbox: BoxLike, page_width: float, page_height: float) -> Optional[BBox]:
    """Scale a box into 0..1 page-relative coordinates for cross-format compares."""
    box = to_bbox(bbox)
    if box is None or page_width <= 0 or page_height <= 0:
        return None
    return BBox(
        x0=_clamp(box.x0 / page_width),
        top=_clamp(box.top / page_height),
        x1=_clamp(box.x1 / page_width),
        bottom=_clamp(box.bottom / page_height),
    )


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def intersection_area(a: BoxLike, b: BoxLike) -> float:
    box_a, box_b = to_bbox(a), to_bbox(b)
    if box_a is None or box_b is None:
        return 0.0
    dx = min(box_a.x1, box_b.x1) - max(box_a.x0, box_b.x0)
    dy = min(box_a.bottom, box_b.bottom) - max(box_a.top, box_b.top)
    if dx <= 0 or dy <= 0:
        return 0.0
    return dx * dy


def check_overlap(a: BoxLike, b: BoxLike, min_iou: float = 0.0) -> bool:
    """True when two boxes overlap (optionally beyond a minimum IoU)."""
    if min_iou <= 0:
        return intersection_area(a, b) > 0
    return iou(a, b) >= min_iou


def iou(a: BoxLike, b: BoxLike) -> float:
    """Intersection over union, 0..1."""
    box_a, box_b = to_bbox(a), to_bbox(b)
    if box_a is None or box_b is None:
        return 0.0
    inter = intersection_area(box_a, box_b)
    union = box_a.area + box_b.area - inter
    return inter / union if union > 0 else 0.0


def calculate_distance(a: BoxLike, b: BoxLike) -> float:
    """Euclidean distance between box centres."""
    box_a, box_b = to_bbox(a), to_bbox(b)
    if box_a is None or box_b is None:
        return float("inf")
    (ax, ay), (bx, by) = box_a.center, box_b.center
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def position_similarity(a: BoxLike, b: BoxLike) -> float:
    """0..1 similarity for two *normalized* boxes (position + size agreement)."""
    box_a, box_b = to_bbox(a), to_bbox(b)
    if box_a is None or box_b is None:
        return 0.0
    centre_gap = calculate_distance(box_a, box_b) / 1.4142  # max distance on a unit page
    size_gap = (abs(box_a.width - box_b.width) + abs(box_a.height - box_b.height)) / 2.0
    return max(0.0, 1.0 - (0.7 * centre_gap + 0.3 * size_gap))


def adjust_position(bbox: BoxLike, dx: float = 0.0, dy: float = 0.0,
                    scale: float = 1.0) -> Optional[BBox]:
    """Translate and/or scale a box about its centre."""
    box = to_bbox(bbox)
    if box is None:
        return None
    cx, cy = box.center
    half_w, half_h = box.width * scale / 2.0, box.height * scale / 2.0
    return BBox(
        x0=cx - half_w + dx, top=cy - half_h + dy,
        x1=cx + half_w + dx, bottom=cy + half_h + dy,
    )


def merge_bboxes(boxes: Iterable[BoxLike]) -> Optional[BBox]:
    """Smallest box containing all inputs."""
    coerced = [b for b in (to_bbox(x) for x in boxes) if b is not None]
    if not coerced:
        return None
    return BBox(
        x0=min(b.x0 for b in coerced), top=min(b.top for b in coerced),
        x1=max(b.x1 for b in coerced), bottom=max(b.bottom for b in coerced),
    )


def horizontal_alignment(bbox: BoxLike, page_width: float, tolerance: float = 0.08) -> str:
    """Classify a box as left / center / right aligned on its page."""
    box = to_bbox(bbox)
    if box is None or page_width <= 0:
        return "unknown"
    left = box.x0 / page_width
    right = 1.0 - box.x1 / page_width
    if abs(left - right) <= tolerance:
        return "center"
    return "left" if left < right else "right"


def reading_order(items: Sequence, page_attr: str = "page", bbox_attr: str = "bbox",
                  line_tolerance: float = 6.0) -> List:
    """Sort elements top-to-bottom, left-to-right, page by page."""
    def key(item):
        page = getattr(item, page_attr, None) or 0
        box = to_bbox(getattr(item, bbox_attr, None))
        if box is None:
            return (page, 0.0, 0.0)
        # snap tops to a line grid so a slightly higher neighbour does not jump ahead
        return (page, round(box.top / line_tolerance), box.x0)

    return sorted(items, key=key)


def detect_columns(boxes: Sequence[BoxLike], page_width: float, gap_ratio: float = 0.06) -> int:
    """Rough column count from horizontal gaps in the text band coverage."""
    spans: List[Tuple[float, float]] = []
    for raw in boxes:
        box = to_bbox(raw)
        if box is not None and box.width > 0:
            spans.append((box.x0, box.x1))
    if not spans or page_width <= 0:
        return 1
    spans.sort()
    merged: List[List[float]] = [list(spans[0])]
    min_gap = page_width * gap_ratio
    for start, end in spans[1:]:
        if start - merged[-1][1] > min_gap:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    # ignore slivers (page numbers, margin notes)
    wide = [m for m in merged if (m[1] - m[0]) > page_width * 0.15]
    return max(1, len(wide))
