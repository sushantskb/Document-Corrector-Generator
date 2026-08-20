"""Pydantic models shared by every Phase 2 service.

The vocabulary here mirrors the MongoDB collections written by the Next.js app
(`projects`, `documents`, `jobs`) and adds the Phase 2 collections
(`issues`, `reports`, `analyses`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class DocumentType(str, Enum):
    PDF = "PDF"
    HTML = "HTML"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class IssueStatus(str, Enum):
    OPEN = "OPEN"                # detected, awaiting decision
    AUTO_FIXED = "AUTO_FIXED"    # fixed automatically (confidence above threshold)
    APPROVED = "APPROVED"        # human approved -> correction applied
    REJECTED = "REJECTED"        # human rejected -> correction reverted/skipped
    UNFIXABLE = "UNFIXABLE"      # no automated correction exists


class IssueType(str, Enum):
    MISSING_IMAGE = "MISSING_IMAGE"
    EXTRA_IMAGE = "EXTRA_IMAGE"
    IMAGE_MISMATCH = "IMAGE_MISMATCH"
    BROKEN_IMAGE_SRC = "BROKEN_IMAGE_SRC"
    MISSING_ALT_TEXT = "MISSING_ALT_TEXT"
    MISSING_TEXT = "MISSING_TEXT"
    EXTRA_TEXT = "EXTRA_TEXT"
    TEXT_MISMATCH = "TEXT_MISMATCH"
    STRUCTURE_MISMATCH = "STRUCTURE_MISMATCH"
    MISSING_SECTION = "MISSING_SECTION"
    HEADING_LEVEL_MISMATCH = "HEADING_LEVEL_MISMATCH"
    ORDER_MISMATCH = "ORDER_MISMATCH"
    MISSING_QUESTION = "MISSING_QUESTION"
    DUPLICATE_QUESTION = "DUPLICATE_QUESTION"
    QUESTION_MISMATCH = "QUESTION_MISMATCH"
    MISSING_ANSWER = "MISSING_ANSWER"
    ALIGNMENT = "ALIGNMENT"
    LAYOUT_MISMATCH = "LAYOUT_MISMATCH"
    WATERMARK = "WATERMARK"
    EXTRACTION_WARNING = "EXTRACTION_WARNING"


class CorrectionAction(str, Enum):
    INSERT_IMAGE = "INSERT_IMAGE"
    REPLACE_IMAGE = "REPLACE_IMAGE"
    FIX_IMAGE_SRC = "FIX_IMAGE_SRC"
    SET_ALT_TEXT = "SET_ALT_TEXT"
    INSERT_TEXT = "INSERT_TEXT"
    REPLACE_TEXT = "REPLACE_TEXT"
    REMOVE_ELEMENT = "REMOVE_ELEMENT"
    REORDER_ELEMENT = "REORDER_ELEMENT"
    FIX_HEADING_LEVEL = "FIX_HEADING_LEVEL"
    INSERT_SECTION = "INSERT_SECTION"
    ADJUST_ALIGNMENT = "ADJUST_ALIGNMENT"
    REMOVE_WATERMARK = "REMOVE_WATERMARK"
    MANUAL = "MANUAL"


# --------------------------------------------------------------------------- #
# Geometry / elements
# --------------------------------------------------------------------------- #
class BBox(BaseModel):
    """Axis aligned bounding box in PDF points (origin top-left) or CSS pixels."""

    x0: float = 0.0
    top: float = 0.0
    x1: float = 0.0
    bottom: float = 0.0

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x0 + self.x1) / 2.0, (self.top + self.bottom) / 2.0

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return self.x0, self.top, self.x1, self.bottom

    @classmethod
    def from_tuple(cls, values) -> "BBox":
        x0, top, x1, bottom = (float(v) for v in values)
        return cls(x0=min(x0, x1), top=min(top, bottom), x1=max(x0, x1), bottom=max(top, bottom))


class TextElement(BaseModel):
    id: str = Field(default_factory=lambda: _uid("txt"))
    text: str = ""
    page: Optional[int] = None          # PDF: 1-based page number
    bbox: Optional[BBox] = None
    font: Optional[str] = None
    size: Optional[float] = None
    color: Optional[str] = None
    bold: bool = False
    italic: bool = False
    tag: Optional[str] = None           # HTML tag name
    dom_path: Optional[str] = None      # HTML CSS-ish selector path
    order_index: int = 0                # reading order across the document
    visible: bool = True
    kind: str = "paragraph"             # paragraph | heading | list_item | caption | table
    lines: List[str] = Field(default_factory=list)   # the PDF's own line breaks


class ImageElement(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: _uid("img"))
    page: Optional[int] = None
    bbox: Optional[BBox] = None
    src: Optional[str] = None
    alt: Optional[str] = None
    width: Optional[float] = None
    height: Optional[float] = None
    phash: Optional[str] = None
    dhash: Optional[str] = None
    sha256: Optional[str] = None
    order_index: int = 0
    dom_path: Optional[str] = None
    cloudinary_url: Optional[str] = None
    source: str = "PDF"                 # PDF | HTML
    kind: str = "raster"                # raster | vector | region
    caption: Optional[str] = None
    is_decorative: bool = False
    error: Optional[str] = None         # why pixels could not be read (broken src, ...)
    preceding_text_path: Optional[str] = None   # dom path of the block just before it
    local_path: Optional[str] = None    # temp file with the decoded pixels


class StructureElement(BaseModel):
    id: str = Field(default_factory=lambda: _uid("sec"))
    title: str = ""
    level: int = 1                      # 1..6
    page: Optional[int] = None
    order_index: int = 0
    dom_path: Optional[str] = None
    tag: Optional[str] = None
    parent_id: Optional[str] = None
    children: List[str] = Field(default_factory=list)


class QuestionElement(BaseModel):
    id: str = Field(default_factory=lambda: _uid("qst"))
    number: Optional[str] = None        # "1", "Q3", "(iv)" ...
    text: str = ""
    page: Optional[int] = None
    dom_path: Optional[str] = None
    order_index: int = 0
    answer: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    numbering_explicit: bool = True   # False when the number came from an <ol> position


class TableElement(BaseModel):
    id: str = Field(default_factory=lambda: _uid("tbl"))
    page: Optional[int] = None
    bbox: Optional[BBox] = None
    rows: int = 0
    cols: int = 0
    dom_path: Optional[str] = None
    text: str = ""
    cells: List[List[str]] = Field(default_factory=list)


class PageLayout(BaseModel):
    page: int
    width: float
    height: float
    rotation: int = 0
    text_blocks: int = 0
    images: int = 0
    tables: int = 0
    rects: int = 0
    lines: int = 0
    curves: int = 0
    columns: int = 1


# --------------------------------------------------------------------------- #
# Analysis results
# --------------------------------------------------------------------------- #
class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: Optional[str] = None
    author: Optional[str] = None
    subject: Optional[str] = None
    creator: Optional[str] = None
    producer: Optional[str] = None
    language: Optional[str] = None
    page_count: Optional[int] = None
    created_at: Optional[str] = None
    modified_at: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class DocumentAnalysis(BaseModel):
    """Uniform output of both PDFAnalyzer and HTMLAnalyzer."""

    doc_type: DocumentType
    source: Optional[str] = None                 # URL or file path
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    text_elements: List[TextElement] = Field(default_factory=list)
    images: List[ImageElement] = Field(default_factory=list)
    structure: List[StructureElement] = Field(default_factory=list)
    questions: List[QuestionElement] = Field(default_factory=list)
    tables: List[TableElement] = Field(default_factory=list)
    pages: List[PageLayout] = Field(default_factory=list)
    embedded_data: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n".join(el.text for el in self.text_elements if el.text)


# --------------------------------------------------------------------------- #
# Issues / corrections
# --------------------------------------------------------------------------- #
class Issue(BaseModel):
    id: str = Field(default_factory=lambda: _uid("iss"))
    type: IssueType
    severity: Severity = Severity.MEDIUM
    confidence: float = 0.5                      # 0..1 certainty the issue is real
    status: IssueStatus = IssueStatus.OPEN
    page: Optional[int] = None
    dom_path: Optional[str] = None
    location: Optional[str] = None               # human readable location
    description: str = ""
    suggestion: str = ""
    auto_fixable: bool = False
    pdf_element_id: Optional[str] = None
    html_element_id: Optional[str] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)
    correction: Optional["Correction"] = None
    created_at: datetime = Field(default_factory=utcnow)


class Correction(BaseModel):
    id: str = Field(default_factory=lambda: _uid("fix"))
    issue_id: str
    action: CorrectionAction
    target_dom_path: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    applied: bool = False
    applied_at: Optional[datetime] = None
    error: Optional[str] = None
    note: str = ""


Issue.model_rebuild()


class ComparisonResult(BaseModel):
    text_similarity: float = 0.0
    image_coverage: float = 0.0
    structure_similarity: float = 0.0
    order_similarity: float = 0.0
    question_coverage: float = 0.0
    layout_similarity: float = 0.0
    matched_text: int = 0
    matched_images: int = 0
    issues: List[Issue] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)

    @property
    def overall_score(self) -> float:
        weights = {
            "text_similarity": 0.30,
            "image_coverage": 0.25,
            "structure_similarity": 0.15,
            "order_similarity": 0.10,
            "question_coverage": 0.15,
            "layout_similarity": 0.05,
        }
        return round(sum(getattr(self, k) * w for k, w in weights.items()), 4)


class ChecklistItem(BaseModel):
    name: str
    passed: bool
    score: float = 0.0
    detail: str = ""


class VerificationResult(BaseModel):
    passed: bool = False
    quality_score: float = 0.0
    resolved_issue_ids: List[str] = Field(default_factory=list)
    unresolved_issue_ids: List[str] = Field(default_factory=list)
    regression_issue_ids: List[str] = Field(default_factory=list)
    checklist: List[ChecklistItem] = Field(default_factory=list)
    before: Optional[ComparisonResult] = None
    after: Optional[ComparisonResult] = None
    notes: List[str] = Field(default_factory=list)


class ProcessingReport(BaseModel):
    job_id: str
    project_id: Optional[str] = None
    generated_at: datetime = Field(default_factory=utcnow)
    summary: Dict[str, Any] = Field(default_factory=dict)
    issues_by_type: Dict[str, int] = Field(default_factory=dict)
    issues_by_severity: Dict[str, int] = Field(default_factory=dict)
    auto_fixed: int = 0
    needs_review: int = 0
    unfixable: int = 0
    metrics: Dict[str, Any] = Field(default_factory=dict)
    checklist: List[ChecklistItem] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    corrected_html_url: Optional[str] = None
    quality_score: float = 0.0


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class ProcessRequest(BaseModel):
    jobId: Optional[str] = None
    pdfUrl: Optional[str] = None
    htmlUrl: Optional[str] = None
    projectId: Optional[str] = None
    autoFix: bool = True
    autoFixThreshold: float = 0.95
    renderJs: bool = True


class ProcessResponse(BaseModel):
    jobId: str
    status: JobStatus
    progress: int = 0
    message: str = ""


class JobState(BaseModel):
    jobId: str
    projectId: Optional[str] = None
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    stage: str = "queued"
    startedAt: Optional[datetime] = None
    completedAt: Optional[datetime] = None
    error: Optional[str] = None
    pdfUrl: Optional[str] = None
    htmlUrl: Optional[str] = None
    correctedHtmlUrl: Optional[str] = None
    issuesFound: int = 0
    issuesAutoFixed: int = 0
    qualityScore: float = 0.0


class IssueDecision(BaseModel):
    issueId: str
    note: Optional[str] = None
    reapply: bool = True     # rebuild the corrected HTML right away


class ProcessingJob(JobState):
    """Alias kept for the Phase 2 spec naming."""
