"""Downloading, temp-file management and text decoding."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    """A download that failed, carrying the HTTP status when there was one."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

DEFAULT_TIMEOUT = float(os.getenv("DOWNLOAD_TIMEOUT", "60"))
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_BYTES", str(200 * 1024 * 1024)))
_RETRIES = 3

# Every temp path we create is tracked so a job can clean up after itself.
_TEMP_PATHS: List[str] = []
_TEMP_DIRS: List[str] = []


def is_url(value: Optional[str]) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def make_temp_dir(prefix: str = "docfix_") -> str:
    path = tempfile.mkdtemp(prefix=prefix)
    _TEMP_DIRS.append(path)
    return path


def save_temp_file(data: bytes, suffix: str = "", directory: Optional[str] = None) -> str:
    """Write bytes to a tracked temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=suffix, dir=directory)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    _TEMP_PATHS.append(path)
    return path


def cleanup_temp_files(paths: Optional[List[str]] = None) -> int:
    """Remove tracked temp files/dirs (or just the given paths). Returns count removed."""
    removed = 0
    targets = paths if paths is not None else list(_TEMP_PATHS)
    for path in targets:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
                removed += 1
        except OSError as exc:
            logger.debug("could not remove temp file %s: %s", path, exc)
        if paths is None and path in _TEMP_PATHS:
            _TEMP_PATHS.remove(path)
    if paths is None:
        for directory in list(_TEMP_DIRS):
            shutil.rmtree(directory, ignore_errors=True)
            _TEMP_DIRS.remove(directory)
            removed += 1
    return removed


async def download_bytes(url: str, *, timeout: float = DEFAULT_TIMEOUT,
                         headers: Optional[Dict[str, str]] = None) -> Tuple[bytes, str]:
    """Fetch a URL with retries. Returns (content, content_type)."""
    if not is_url(url):
        # local path or file:// URL — used by tests, and when Cloudinary is
        # unavailable and a corrected document was written to disk instead
        if url.startswith("file://"):
            url = url[7:]
        with open(url, "rb") as fh:
            content = fh.read()
        return content, mimetypes.guess_type(url)[0] or "application/octet-stream"

    last_error: Optional[Exception] = None
    status: Optional[int] = None
    for attempt in range(1, _RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url, headers=headers or {})
                response.raise_for_status()
                content = response.content
                if len(content) > MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"download exceeds {MAX_DOWNLOAD_BYTES} bytes: {len(content)}"
                    )
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                return content, content_type or "application/octet-stream"
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_error = exc
            # 401/403/404 will not fix themselves; only 429 is worth waiting out
            if status < 500 and status != 429:
                break
        except Exception as exc:  # network flakiness is expected against Cloudinary
            last_error = exc
        if attempt < _RETRIES:
            await asyncio.sleep(0.5 * attempt)
        logger.warning("download attempt %s/%s failed for %s: %s",
                       attempt, _RETRIES, url, last_error)
    raise DownloadError(f"failed to download {url}: {last_error}", status)


async def download_file(url: str, *, suffix: Optional[str] = None,
                        directory: Optional[str] = None) -> str:
    """Download to a tracked temp file and return the local path."""
    content, content_type = await download_bytes(url)
    if suffix is None:
        suffix = guess_suffix(url, content_type)
    return save_temp_file(content, suffix=suffix, directory=directory)


def guess_suffix(url: str, content_type: Optional[str] = None) -> str:
    path_suffix = os.path.splitext(urlparse(url).path)[1]
    if path_suffix and len(path_suffix) <= 6:
        return path_suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type)
        if guessed:
            return guessed
    return ".bin"


def decode_text(content: bytes, content_type: Optional[str] = None) -> str:
    """Best-effort decode of HTML/text bytes.

    Order: charset from the HTTP header, then a `<meta charset>` sniff, then
    UTF-8, then latin-1 (which never raises) so a job is never lost to encoding.
    """
    charsets: List[str] = []
    if content_type and "charset=" in content_type:
        charsets.append(content_type.split("charset=")[-1].strip().strip('"'))
    head = content[:4096].lower()
    marker = b"charset="
    if marker in head:
        raw = head.split(marker, 1)[1][:40]
        candidate = bytes(c for c in raw if chr(c).isalnum() or chr(c) in "-_").decode(
            "ascii", "ignore"
        )
        if candidate:
            charsets.append(candidate)
    charsets += ["utf-8", "utf-8-sig", "cp1252", "latin-1"]
    for charset in charsets:
        try:
            return content.decode(charset)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


async def download_text(url: str) -> str:
    content, content_type = await download_bytes(url)
    return decode_text(content, content_type)


def write_text_file(text: str, suffix: str = ".html", directory: Optional[str] = None) -> str:
    return save_temp_file(text.encode("utf-8"), suffix=suffix, directory=directory)


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
