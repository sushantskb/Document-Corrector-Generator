"""Verify that corrections actually worked, and report on the whole run.

Verification re-analyzes the corrected HTML and re-runs the full comparison
against the PDF. Issues are tracked across the two runs by a content signature
rather than by id (the second pass produces fresh ids), which is what makes it
possible to say precisely which defects were resolved, which survived, and which
were *introduced* by the corrections themselves.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from models.models import (
    ChecklistItem, ComparisonResult, DocumentAnalysis, Issue, IssueStatus, IssueType,
    ProcessingReport, Severity, VerificationResult,
)
from services.comparison_engine import ComparisonEngine
from services.html_analyzer import HTMLAnalyzer
from utils.text_matcher import normalize_text

logger = logging.getLogger(__name__)

# checklist name -> (metric attribute, pass mark)
_CHECKS: Tuple[Tuple[str, str, float], ...] = (
    ("Text coverage", "text_similarity", 0.95),
    ("Image coverage", "image_coverage", 0.98),
    ("Structure integrity", "structure_similarity", 0.95),
    ("Element order", "order_similarity", 0.90),
    ("Question coverage", "question_coverage", 1.00),
    ("Visual layout", "layout_similarity", 0.80),
)


class VerificationEngine:
    """Re-check a corrected document and produce the reviewer-facing report."""

    def __init__(self, pdf_analysis: DocumentAnalysis, corrected_html: str,
                 before: ComparisonResult, issues: Sequence[Issue],
                 base_url: Optional[str] = None, render_js: bool = True,
                 pdf_pixels: Optional[Dict[str, Any]] = None):
        self.pdf_analysis = pdf_analysis
        self.corrected_html = corrected_html
        self.before = before
        self.issues = list(issues)
        self.base_url = base_url
        self.render_js = render_js
        self.pdf_pixels = pdf_pixels or {}
        self.after: Optional[ComparisonResult] = None
        self.after_analysis: Optional[DocumentAnalysis] = None

    # ------------------------------------------------------------------ verify
    async def verify_corrections(self) -> VerificationResult:
        """Re-analyze the corrected HTML and diff the issue sets."""
        notes: List[str] = []
        try:
            analyzer = HTMLAnalyzer(
                html=self.corrected_html, base_url=self.base_url,
                render_js=self.render_js, fetch_images=True,
            )
            self.after_analysis = await analyzer.analyze()
            engine = ComparisonEngine(
                self.pdf_analysis, self.after_analysis,
                {**self.pdf_pixels, **analyzer.pixel_cache},
            )
            self.after = engine.generate_issues()
            notes.extend(self.after_analysis.warnings)
        except Exception as exc:
            logger.exception("verification re-analysis failed")
            return VerificationResult(
                passed=False, quality_score=0.0, before=self.before,
                notes=[f"verification could not run: {exc}"],
                unresolved_issue_ids=[i.id for i in self.issues],
            )

        before_signatures = {_signature(issue): issue for issue in self.before.issues}
        after_signatures = {_signature(issue): issue for issue in self.after.issues}

        resolved, unresolved = [], []
        for signature, issue in before_signatures.items():
            if signature in after_signatures:
                unresolved.append(issue.id)
            else:
                resolved.append(issue.id)
        # A defect we never fixed keeps generating knock-on observations of its
        # own kind (an un-reordered section makes every element inside it look
        # misplaced). Those are not new damage, so they are noted, not counted.
        unresolved_types = {
            issue.type for signature, issue in before_signatures.items()
            if signature in after_signatures
        }
        regressions, knock_on = [], []
        for signature, issue in after_signatures.items():
            if signature in before_signatures:
                continue
            (knock_on if issue.type in unresolved_types else regressions).append(issue.id)
        if knock_on:
            notes.append(f"{len(knock_on)} new observation(s) are side effects of issues that "
                         "are still open, not regressions")

        for issue in self.issues:
            if issue.id in resolved and issue.status in (
                IssueStatus.AUTO_FIXED, IssueStatus.APPROVED
            ):
                continue    # applied and gone: nothing more to say
            if issue.id in unresolved and issue.status in (
                IssueStatus.AUTO_FIXED, IssueStatus.APPROVED
            ):
                issue.status = IssueStatus.OPEN
                notes.append(f"correction for {issue.type.value} did not resolve the issue")

        checklist = self.create_checklist(self.after, regressions)
        quality = self.quality_score(self.after, regressions)
        passed = all(item.passed for item in checklist) and not regressions

        return VerificationResult(
            passed=passed,
            quality_score=quality,
            resolved_issue_ids=resolved,
            unresolved_issue_ids=unresolved,
            regression_issue_ids=regressions,
            checklist=checklist,
            before=self.before,
            after=self.after,
            notes=notes[:20],
        )

    # --------------------------------------------------------------- checklist
    def create_checklist(self, result: Optional[ComparisonResult] = None,
                         regressions: Optional[Sequence[str]] = None) -> List[ChecklistItem]:
        """Validation checklist a reviewer can sign off on."""
        result = result or self.after or self.before
        regressions = regressions or []
        items: List[ChecklistItem] = []
        for name, attribute, threshold in _CHECKS:
            score = float(getattr(result, attribute, 0.0) or 0.0)
            items.append(ChecklistItem(
                name=name, passed=score >= threshold, score=round(score, 4),
                detail=f"{score:.0%} (needs {threshold:.0%})",
            ))

        remaining_high = [i for i in result.issues if i.severity == Severity.HIGH]
        items.append(ChecklistItem(
            name="No high-severity issues remaining",
            passed=not remaining_high,
            score=1.0 if not remaining_high else 0.0,
            detail=f"{len(remaining_high)} high-severity issue(s) left",
        ))
        items.append(ChecklistItem(
            name="No regressions introduced",
            passed=not regressions,
            score=1.0 if not regressions else 0.0,
            detail=f"{len(regressions)} new issue(s) after correction",
        ))
        broken = [i for i in result.issues if i.type == IssueType.BROKEN_IMAGE_SRC]
        images_ok = (self.after_analysis is None) or not broken
        items.append(ChecklistItem(
            name="All image URLs resolve",
            passed=images_ok,
            score=1.0 if images_ok else 0.0,
            detail=f"{len(broken)} unreachable image(s)",
        ))
        return items

    @staticmethod
    def quality_score(result: ComparisonResult, regressions: Sequence[str] = ()) -> float:
        """Overall 0..1 quality, penalised for anything still severe."""
        base = result.overall_score
        high = sum(1 for i in result.issues if i.severity == Severity.HIGH)
        medium = sum(1 for i in result.issues if i.severity == Severity.MEDIUM)
        penalty = min(0.35, 0.05 * high + 0.02 * medium + 0.04 * len(regressions))
        return round(max(0.0, base - penalty), 4)

    # ------------------------------------------------------------------ report
    def generate_report(self, job_id: str, project_id: Optional[str] = None,
                        verification: Optional[VerificationResult] = None,
                        metrics: Optional[Dict[str, Any]] = None,
                        corrected_html_url: Optional[str] = None) -> ProcessingReport:
        """Assemble the full processing report for the frontend."""
        verification = verification or VerificationResult(before=self.before)
        result = self.after or self.before
        issues = self.issues

        auto_fixed = sum(1 for i in issues if i.status == IssueStatus.AUTO_FIXED)
        approved = sum(1 for i in issues if i.status == IssueStatus.APPROVED)
        rejected = sum(1 for i in issues if i.status == IssueStatus.REJECTED)
        unfixable = sum(1 for i in issues if i.status == IssueStatus.UNFIXABLE)
        needs_review = sum(1 for i in issues if i.status == IssueStatus.OPEN)

        by_type = Counter(i.type.value for i in issues)
        by_severity = Counter(i.severity.value for i in issues)

        report = ProcessingReport(
            job_id=job_id,
            project_id=project_id,
            summary={
                "issues_found": len(issues),
                "auto_fixed": auto_fixed,
                "approved": approved,
                "rejected": rejected,
                "needs_review": needs_review,
                "unfixable": unfixable,
                "auto_fix_rate": round((auto_fixed + approved) / len(issues), 4) if issues else 1.0,
                "resolved": len(verification.resolved_issue_ids),
                "unresolved": len(verification.unresolved_issue_ids),
                "regressions": len(verification.regression_issue_ids),
                "verification_passed": verification.passed,
                "scores_before": _scores(self.before),
                "scores_after": _scores(self.after) if self.after else None,
            },
            issues_by_type=dict(by_type),
            issues_by_severity=dict(by_severity),
            auto_fixed=auto_fixed + approved,
            needs_review=needs_review,
            unfixable=unfixable,
            metrics={
                **(metrics or {}),
                "pdf_pages": self.pdf_analysis.metadata.page_count,
                "pdf_text_blocks": len(self.pdf_analysis.text_elements),
                "pdf_images": len(self.pdf_analysis.images),
                "pdf_questions": len(self.pdf_analysis.questions),
                "html_images_after": len(self.after_analysis.images) if self.after_analysis else None,
            },
            checklist=verification.checklist or self.create_checklist(result),
            recommendations=self.recommendations(issues, verification),
            corrected_html_url=corrected_html_url,
            quality_score=verification.quality_score or self.quality_score(result),
        )
        return report

    @staticmethod
    def recommendations(issues: Sequence[Issue],
                        verification: VerificationResult) -> List[str]:
        """Concrete next steps, ordered by what will move the score most."""
        notes: List[str] = []
        open_issues = [i for i in issues if i.status == IssueStatus.OPEN]
        high = [i for i in open_issues if i.severity == Severity.HIGH]
        if high:
            notes.append(f"Review {len(high)} high-severity issue(s) before publishing; "
                         "they change what the reader sees.")
        by_type = Counter(i.type.value for i in open_issues)
        for issue_type, count in by_type.most_common(4):
            notes.append(f"{count} unresolved {issue_type.replace('_', ' ').lower()} issue(s).")
        if verification.regression_issue_ids:
            notes.append(f"{len(verification.regression_issue_ids)} issue(s) appeared only after "
                         "correction — inspect the applied fixes.")
        borderline = [i for i in open_issues if 0.85 <= i.confidence < 0.95]
        if borderline:
            notes.append(f"{len(borderline)} issue(s) sit just below the auto-fix threshold and "
                         "are good candidates for one-click approval.")
        if not notes:
            notes.append("No outstanding issues — the corrected HTML matches the PDF.")
        return notes


def _scores(result: Optional[ComparisonResult]) -> Optional[Dict[str, float]]:
    if result is None:
        return None
    return {
        "overall": result.overall_score,
        "text": result.text_similarity,
        "images": result.image_coverage,
        "structure": result.structure_similarity,
        "order": result.order_similarity,
        "questions": result.question_coverage,
        "layout": result.layout_similarity,
    }


def _signature(issue: Issue) -> Tuple:
    """Content-based identity so the same defect matches across two runs."""
    evidence = issue.evidence or {}
    key: str = ""
    if issue.type in (IssueType.MISSING_IMAGE, IssueType.IMAGE_MISMATCH, IssueType.EXTRA_IMAGE):
        key = normalize_text(evidence.get("caption") or evidence.get("src") or "")
    elif issue.type in (IssueType.MISSING_TEXT, IssueType.EXTRA_TEXT, IssueType.TEXT_MISMATCH):
        key = normalize_text(evidence.get("pdf_text") or evidence.get("text") or
                             issue.description)[:80]
    elif issue.type in (IssueType.MISSING_QUESTION, IssueType.QUESTION_MISMATCH,
                        IssueType.DUPLICATE_QUESTION, IssueType.MISSING_ANSWER):
        key = normalize_text(evidence.get("text") or evidence.get("pdf_text") or
                             issue.description)[:80]
    elif issue.type in (IssueType.MISSING_SECTION, IssueType.STRUCTURE_MISMATCH,
                        IssueType.HEADING_LEVEL_MISMATCH):
        key = normalize_text(evidence.get("title") or issue.description)[:80]
    elif issue.type == IssueType.WATERMARK:
        key = normalize_text(evidence.get("text") or "")[:60]
    else:
        # DOM paths shift as soon as anything is inserted, so identity has to
        # come from the description text, not the location.
        key = normalize_text(issue.description)[:80]
    return (issue.type.value, issue.page, key)
