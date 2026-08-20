"""Text normalization and fuzzy matching.

Uses rapidfuzz when available (fast C++ implementation, drop-in replacement for
fuzzywuzzy) and falls back to difflib so the service never hard-fails on a
missing optional dependency.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # pragma: no cover - exercised by whichever backend is installed
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process
    HAS_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    _rf_fuzz = None
    _rf_process = None
    HAS_RAPIDFUZZ = False

# Ligatures and typographic characters that differ between PDF and HTML output.
_TRANSLATIONS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    " ": " ", " ": " ", " ": " ", "​": "", "﻿": "",
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "…": "...", "­": "",
}
_TRANS_TABLE = {ord(k): v for k, v in _TRANSLATIONS.items()}

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\s*\n\s*(\w)")

# Question numbering seen in Indian textbook chapters: "1.", "Q3)", "(iv)", "Ex 2.1"
_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:(?:Q(?:uestion)?\s*\.?\s*)?(\d{1,3})\s*[.)\]:]"
    r"|\(([ivxlcdm]{1,7})\)"
    r"|\(([a-z])\)"
    r"|([ivxlcdm]{1,7})\s*[.)]\s)",
    re.IGNORECASE,
)
_QUESTION_HINT_RE = re.compile(
    r"(\?|\bfind\b|\bcalculate\b|\bexplain\b|\bdefine\b|\bwrite\b|\bsolve\b|\bprove\b|"
    r"\bstate\b|\bdescribe\b|\bwhy\b|\bwhat\b|\bhow\b|\bwhich\b|\bwhere\b|\bwhen\b|"
    r"\bname\b|\blist\b|\bidentify\b|\bchoose\b|\bmatch\b|\bdraw\b|\bshow that\b|"
    r"\bcompare\b|\bderive\b|\bdiscuss\b|\bgive reason|\bfill in the blank)",
    re.IGNORECASE,
)
_OPTION_RE = re.compile(r"\(([a-d])\)\s*([^()]{1,80})", re.IGNORECASE)

_WATERMARK_HINTS = (
    "watermark", "sample", "specimen", "draft copy", "do not copy", "confidential",
    "for evaluation only", "demo version", "trial version", "www.", "copyright ©",
)


def normalize_text(text: Optional[str], *, keep_punctuation: bool = False,
                   lowercase: bool = True) -> str:
    """Canonical form used for every text comparison in the pipeline."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_TRANS_TABLE)
    text = _HYPHEN_BREAK_RE.sub(r"\1\2", text)          # rejoin PDF hyphenation
    if lowercase:
        text = text.lower()
    if not keep_punctuation:
        text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def tokenize(text: str) -> List[str]:
    return normalize_text(text).split()


def calculate_similarity(a: Optional[str], b: Optional[str], *, normalize: bool = True) -> float:
    """Similarity of two strings, 0..1. Order sensitive but whitespace agnostic."""
    left = normalize_text(a) if normalize else (a or "")
    right = normalize_text(b) if normalize else (b or "")
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if HAS_RAPIDFUZZ:
        return _rf_fuzz.ratio(left, right) / 100.0
    return SequenceMatcher(None, left, right).ratio()


def token_set_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Similarity ignoring word order and duplicates — good for reflowed blocks."""
    left, right = normalize_text(a), normalize_text(b)
    if not left or not right:
        return 0.0
    if HAS_RAPIDFUZZ:
        return _rf_fuzz.token_set_ratio(left, right) / 100.0
    set_a, set_b = set(left.split()), set(right.split())
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def partial_similarity(a: Optional[str], b: Optional[str]) -> float:
    """How well the shorter string is contained in the longer one."""
    left, right = normalize_text(a), normalize_text(b)
    if not left or not right:
        return 0.0
    if HAS_RAPIDFUZZ:
        return _rf_fuzz.partial_ratio(left, right) / 100.0
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return SequenceMatcher(None, short, long).find_longest_match(
        0, len(short), 0, len(long)
    ).size / max(1, len(short))


def fuzzy_match(needle: Optional[str], haystack: Optional[str]) -> float:
    """Blended score: exact ratio, token-set and containment.

    PDF text and HTML text of the same paragraph routinely differ in line breaks,
    stray spaces and word order after reflow, so no single metric is reliable.
    """
    if not needle or not haystack:
        return 0.0
    ratio = calculate_similarity(needle, haystack)
    token = token_set_similarity(needle, haystack)
    partial = partial_similarity(needle, haystack)
    return round(max(ratio, 0.9 * token, 0.85 * partial), 4)


def best_match(needle: str, candidates: Sequence[str],
               threshold: float = 0.0) -> Tuple[int, float]:
    """Index and score of the best candidate; (-1, 0.0) when nothing clears threshold."""
    if not needle or not candidates:
        return -1, 0.0
    best_idx, best_score = -1, 0.0
    if HAS_RAPIDFUZZ:
        normalized = [normalize_text(c) for c in candidates]
        result = _rf_process.extractOne(
            normalize_text(needle), normalized, scorer=_rf_fuzz.token_set_ratio
        )
        if result:
            best_idx, best_score = result[2], result[1] / 100.0
            # re-score with the blended metric for consistency with fuzzy_match
            best_score = fuzzy_match(needle, candidates[best_idx])
    for idx, candidate in enumerate(candidates):
        score = fuzzy_match(needle, candidate)
        if score > best_score:
            best_idx, best_score = idx, score
    if best_score < threshold:
        return -1, 0.0
    return best_idx, round(best_score, 4)


def match_blocks(source: Sequence[str], target: Sequence[str],
                 threshold: float = 0.75) -> Dict[str, list]:
    """Greedy one-to-one alignment of two block lists.

    Returns matches [(src_idx, tgt_idx, score)], plus the unmatched indices on
    each side — the raw material for MISSING_TEXT / EXTRA_TEXT issues.
    """
    matches: List[Tuple[int, int, float]] = []
    used_target = set()
    scored: List[Tuple[float, int, int]] = []
    for s_idx, s_text in enumerate(source):
        for t_idx, t_text in enumerate(target):
            score = fuzzy_match(s_text, t_text)
            if score >= threshold:
                scored.append((score, s_idx, t_idx))
    scored.sort(reverse=True)
    used_source = set()
    for score, s_idx, t_idx in scored:
        if s_idx in used_source or t_idx in used_target:
            continue
        used_source.add(s_idx)
        used_target.add(t_idx)
        matches.append((s_idx, t_idx, round(score, 4)))
    matches.sort()
    return {
        "matches": matches,
        "unmatched_source": [i for i in range(len(source)) if i not in used_source],
        "unmatched_target": [i for i in range(len(target)) if i not in used_target],
    }


def coverage(source: Sequence[str], target: Sequence[str], threshold: float = 0.75) -> float:
    """Fraction of source blocks that have a counterpart in target."""
    if not source:
        return 1.0
    result = match_blocks(source, target, threshold)
    return round(len(result["matches"]) / len(source), 4)


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?।])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def has_question_hint(text: str) -> bool:
    """True when a line reads like a task/question regardless of its numbering."""
    return bool(text) and bool(_QUESTION_HINT_RE.search(text))


def looks_like_question(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 5:
        return False
    return bool(_QUESTION_PREFIX_RE.match(stripped)) and bool(_QUESTION_HINT_RE.search(stripped))


def parse_question(text: str) -> Optional[Dict[str, object]]:
    """Split '3. What is photosynthesis? (a) .. (b) ..' into number/body/options."""
    if not text:
        return None
    stripped = text.strip()
    match = _QUESTION_PREFIX_RE.match(stripped)
    if not match:
        return None
    number = next((g for g in match.groups() if g), None)
    body = stripped[match.end():].strip()
    options = [f"({letter.lower()}) {value.strip()}"
               for letter, value in _OPTION_RE.findall(body)]
    if options:
        body = _OPTION_RE.sub("", body).strip()
    return {"number": str(number) if number else None, "text": body, "options": options}


def detect_watermark_text(text: str) -> Optional[str]:
    """Return the watermark hint found in a short repeated string, if any."""
    if not text:
        return None
    lowered = normalize_text(text, keep_punctuation=True)
    if len(lowered) > 120:
        return None
    for hint in _WATERMARK_HINTS:
        if hint in lowered:
            return hint
    return None


def longest_common_subsequence(a: Sequence, b: Sequence) -> int:
    """LCS length — used to score element ordering agreement."""
    if not a or not b:
        return 0
    matcher = SequenceMatcher(None, list(a), list(b), autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks())


def order_similarity(sequence_a: Sequence, sequence_b: Sequence) -> float:
    """0..1 agreement between two orderings of the same items."""
    if not sequence_a or not sequence_b:
        return 1.0 if not sequence_a and not sequence_b else 0.0
    lcs = longest_common_subsequence(sequence_a, sequence_b)
    return round(lcs / max(len(sequence_a), len(sequence_b)), 4)


def out_of_order_pairs(sequence: Iterable[int]) -> List[Tuple[int, int]]:
    """Indices whose values break monotonic order (inversion pairs, capped)."""
    values = list(sequence)
    inversions: List[Tuple[int, int]] = []
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            if values[i] > values[j]:
                inversions.append((i, j))
                if len(inversions) >= 200:
                    return inversions
    return inversions


_MATH_LINE_RE = re.compile(r"^[\d\s=+*/×÷·^().,%–—_-]+$")


def looks_like_math_line(line: str) -> bool:
    """Is this line a displayed equation rather than prose?"""
    stripped = (line or "").strip()
    if not 3 <= len(stripped) <= 60:
        return False
    if _MATH_LINE_RE.match(stripped):
        return "=" in stripped or "+" in stripped or "\u00d7" in stripped
    # allow a few letters (variables, "sq. units") as long as symbols dominate
    compact = re.sub(r"\s", "", stripped)
    letters = sum(1 for ch in compact if ch.isalpha())
    return len(compact) > 0 and letters / len(compact) < 0.25 and "=" in stripped


def is_math_stack(lines) -> bool:
    """A run of displayed equations the PDF laid out one per line.

    Flattening `1 = 1 / 1 + 3 = 4 / …` into one sentence is what makes maths
    unreadable in generated output; a block where most lines are equations
    should be rendered line by line instead.
    """
    lines = [line for line in (lines or []) if line and line.strip()]
    if len(lines) < 2:
        return False
    mathy = sum(1 for line in lines if looks_like_math_line(line))
    return mathy >= 2 and mathy / len(lines) >= 0.6


_CID_TOKEN_RE = re.compile(r"\(cid:\d+\)")


def is_unreadable(text: str) -> bool:
    """Did PDF extraction fail to decode this text?

    Fonts without a Unicode mapping come out as `(cid:12)(cid:156)…` or raw
    control bytes. Such "text" must never be compared, inserted or shown — it
    is not the document's content, it is the extractor admitting defeat.
    """
    if not text:
        return False
    tokens = _CID_TOKEN_RE.findall(text)
    if tokens and sum(len(t) for t in tokens) >= len(text) * 0.3:
        return True
    if len(tokens) >= 3:
        return True
    compact = re.sub(r"\s", "", text)
    if not compact:
        return False
    bad = sum(1 for ch in compact
              if ord(ch) < 32 or (0x80 <= ord(ch) <= 0x9f)
              or 0xe000 <= ord(ch) <= 0xf8ff)
    return bad / len(compact) > 0.25


def strip_cid_tokens(text: str) -> str:
    """Remove stray undecoded glyph tokens from otherwise readable text."""
    return re.sub(r"\s{2,}", " ", _CID_TOKEN_RE.sub("", text or "")).strip()
