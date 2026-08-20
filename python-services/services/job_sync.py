"""Translation between the engine's vocabulary and the frontend's schema.

The Next.js app owns `jobs`, `issues` and `corrections`, and its Mongoose
schemas use a smaller, product-facing vocabulary than the analysis engine: eight
issue types instead of nineteen, four severities instead of three, five job
stages instead of the pipeline's nine. Everything the UI reads has to be written
in *its* terms.

The engine's own richer record travels along in an ``engine`` sub-document that
Mongoose ignores, so a fix approved in the UI can still be replayed by the
correction engine later without a second collection to keep in sync.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from models.models import Issue, IssueStatus, IssueType, Severity

# --------------------------------------------------------------------------- #
# Issue type: 19 engine types -> the 8 the UI knows how to label
# --------------------------------------------------------------------------- #
TYPE_MAP: Dict[IssueType, str] = {
    IssueType.MISSING_IMAGE: "IMAGE_MISSING",
    IssueType.BROKEN_IMAGE_SRC: "IMAGE_MISSING",
    IssueType.IMAGE_MISMATCH: "IMAGE_MISMATCH",
    IssueType.EXTRA_IMAGE: "IMAGE_MISMATCH",
    IssueType.MISSING_TEXT: "MISSING_TEXT",
    IssueType.MISSING_SECTION: "MISSING_TEXT",
    IssueType.MISSING_QUESTION: "MISSING_TEXT",
    IssueType.MISSING_ANSWER: "MISSING_TEXT",
    IssueType.EXTRA_TEXT: "EXTRA_TEXT",
    IssueType.DUPLICATE_QUESTION: "EXTRA_TEXT",
    IssueType.WATERMARK: "EXTRA_TEXT",
    IssueType.TEXT_MISMATCH: "TEXT_MISMATCH",
    IssueType.QUESTION_MISMATCH: "TEXT_MISMATCH",
    IssueType.MISSING_ALT_TEXT: "FORMATTING",
    IssueType.HEADING_LEVEL_MISMATCH: "FORMATTING",
    IssueType.STRUCTURE_MISMATCH: "FORMATTING",
    IssueType.ALIGNMENT: "FORMATTING",
    IssueType.LAYOUT_MISMATCH: "FORMATTING",
    IssueType.ORDER_MISMATCH: "ORDER_MISMATCH",
    IssueType.EXTRACTION_WARNING: "FORMATTING",
}

STATUS_TO_UI: Dict[IssueStatus, str] = {
    IssueStatus.OPEN: "PENDING_REVIEW",
    IssueStatus.UNFIXABLE: "PENDING_REVIEW",
    IssueStatus.AUTO_FIXED: "AUTO_FIXED",
    IssueStatus.APPROVED: "APPROVED",
    IssueStatus.REJECTED: "REJECTED",
}

# Reading a UI decision back: an approval only means "apply it" to the engine.
STATUS_FROM_UI: Dict[str, IssueStatus] = {
    "PENDING_REVIEW": IssueStatus.OPEN,
    "AUTO_FIXED": IssueStatus.AUTO_FIXED,
    "APPROVED": IssueStatus.APPROVED,
    "REJECTED": IssueStatus.REJECTED,
}

# The pipeline reports nine stages; the UI's progress stepper knows five.
STAGE_MAP: Dict[str, Optional[str]] = {
    "queued": None,
    "resolving": "ANALYZING_PDF",
    "downloading": "ANALYZING_PDF",
    "analyzing-pdf": "ANALYZING_PDF",
    "analyzing-html": "ANALYZING_HTML",
    "comparing": "COMPARING",
    "correcting": "CORRECTING",
    "verifying": "VERIFYING",
    "publishing": "VERIFYING",
    "completed": None,
    "failed": None,
    "cancelled": None,
}


def ui_severity(issue: Issue) -> str:
    """HIGH/MEDIUM/LOW plus confidence -> CRITICAL/MAJOR/MINOR/INFO.

    A low-severity finding the engine is unsure about (an 'extra' paragraph that
    may just be site chrome) is informational, not a defect to fix.
    """
    if issue.severity == Severity.HIGH:
        return "CRITICAL"
    if issue.severity == Severity.MEDIUM:
        return "MAJOR"
    return "MINOR" if issue.confidence >= 0.7 else "INFO"


def ui_type(issue: Issue) -> str:
    return TYPE_MAP.get(issue.type, "FORMATTING")


def _evidence_text(issue: Issue, *keys: str) -> Optional[str]:
    for key in keys:
        value = (issue.evidence or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value[:2000]
    return None


def issue_document(issue: Issue, job_oid: Any, project_oid: Any,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """One `issues` document in the shape the frontend's Mongoose model expects.

    Deliberately carries no timestamp: the store stamps `updatedAt` only on
    documents that actually changed, so a rebuild does not restamp the whole
    issue list and lose when each one was really reviewed.
    """
    correction = issue.correction
    return {
        "projectId": project_oid,
        "jobId": job_oid,
        "type": ui_type(issue),
        "severity": ui_severity(issue),
        "status": STATUS_TO_UI.get(issue.status, "PENDING_REVIEW"),
        "confidence": round(float(issue.confidence), 4),
        "page": issue.page,
        "selector": issue.dom_path or issue.location or None,
        "message": issue.description,
        "pdfText": _evidence_text(issue, "pdf_text", "text", "caption", "title"),
        "htmlText": _evidence_text(issue, "html_text", "src", "html_src"),
        "suggestion": issue.suggestion or None,
        # everything the correction engine needs to replay this fix later
        "engine": {
            **issue.model_dump(mode="json"),
            "autoFixable": issue.auto_fixable,
            "action": correction.action.value if correction else None,
        },
    }


def correction_document(issue: Issue, job_oid: Any, project_oid: Any,
                        issue_oid: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """One `corrections` document describing a fix that was actually applied."""
    now = now or datetime.now(timezone.utc)
    correction = issue.correction
    payload = (correction.payload if correction else {}) or {}
    after = (payload.get("hosted_url") or payload.get("text") or payload.get("alt")
             or payload.get("title") or (f"h{payload['level']}" if "level" in payload else "")
             or (correction.note if correction else ""))
    return {
        "projectId": project_oid,
        "jobId": job_oid,
        "issueId": issue_oid,
        "selector": (correction.target_dom_path if correction else None) or issue.dom_path,
        "before": _evidence_text(issue, "html_text", "src", "html_src") or "",
        "after": str(after)[:2000],
        "appliedBy": "MANUAL" if issue.status == IssueStatus.APPROVED else "AUTO",
        "status": "APPLIED",
        "updatedAt": now,
    }


def job_document(state: Dict[str, Any]) -> Dict[str, Any]:
    """Map the pipeline's job state onto the frontend's `jobs` fields."""
    update: Dict[str, Any] = {}
    for key in ("status", "progress", "error", "startedAt", "completedAt"):
        if key in state:
            update[key] = state[key]      # None is meaningful here: it clears the field
    for url_field in ("correctedHtmlUrl", "generatedHtmlUrl", "imageMap", "imageUrlBase"):
        if state.get(url_field):
            # never blank an existing document link because an update omits it
            update[url_field] = state[url_field]

    if "stage" in state:
        stage = STAGE_MAP.get(state["stage"], None)
        if stage:
            update["stage"] = stage
        elif state.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
            update["stage"] = None      # the stepper hides itself once terminal

    stats = {
        "issuesFound": state.get("issuesFound"),
        "autoFixed": state.get("issuesAutoFixed"),
        "pendingReview": state.get("issuesNeedingReview"),
        "qualityScore": (round(state["qualityScore"] * 100)
                         if isinstance(state.get("qualityScore"), (int, float)) else None),
    }
    for key, value in stats.items():
        if value is not None:
            update[f"stats.{key}"] = value
    return update


def log_entry(message: str, level: str = "INFO",
              at: Optional[datetime] = None) -> Dict[str, Any]:
    return {"at": at or datetime.now(timezone.utc), "level": level, "message": message}


STAGE_MESSAGES: Dict[str, str] = {
    "resolving": "Resolving source documents",
    "downloading": "Downloading PDF and HTML",
    "analyzing-pdf": "Analyzing the PDF",
    "analyzing-html": "Analyzing the HTML",
    "comparing": "Comparing both documents",
    "correcting": "Applying high-confidence corrections",
    "verifying": "Verifying the corrected HTML",
    "publishing": "Publishing the corrected document",
    "completed": "Processing complete",
}


def stage_log(stage: str, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A human-readable log line for a stage transition, if it deserves one."""
    message = STAGE_MESSAGES.get(stage)
    if not message:
        return None
    if stage == "correcting" and state.get("issuesFound") is not None:
        message = f"{message} ({state['issuesFound']} issue(s) found)"
    if stage == "completed" and state.get("issuesAutoFixed") is not None:
        message = (f"{message}: {state['issuesAutoFixed']} auto-fixed, "
                   f"{state.get('issuesNeedingReview', 0)} to review")
    return log_entry(message)
