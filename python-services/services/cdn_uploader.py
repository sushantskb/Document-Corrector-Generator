"""Push added figures to the publisher's CDN upload service.

The delivery workflow expects every added image to exist on the CloudFront
bucket under its kerla_new_NN.png name. The publisher runs a small upload
service (multipart POST, field "files") that stores each file under
kerala_v2/html-images/<filename> and answers with the delivery URL:

    {"results": [{"filename": "...", "status": "uploaded",
                  "key": "kerala_v2/html-images/...", "url": "https://..."}]}

Pushing the figures right after naming them means the deliverable's CDN URLs
resolve immediately — no manual zip-upload step. A push failure is never
fatal: the image bundle download remains the manual fallback.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CDN_UPLOAD_URL = "http://3.110.44.176/upload"


def _upload_url() -> str:
    # read at call time so tests and deployments can switch it off ("")
    return os.getenv("CDN_UPLOAD_URL", DEFAULT_CDN_UPLOAD_URL).strip()


async def _figure_bytes(client: httpx.AsyncClient, src: str) -> bytes:
    if src.startswith("data:"):
        return base64.b64decode(src.split(",", 1)[1])
    response = await client.get(src)
    response.raise_for_status()
    return response.content


async def push_images_to_cdn(mapping: List[Dict[str, Any]], *,
                             client: Optional[httpx.AsyncClient] = None) -> int:
    """Upload every not-yet-pushed figure in `mapping` under its delivery name.

    Marks successful entries with cdnUploaded=True (and the URL the service
    reports). Entries already pushed are skipped, so calling this after each
    deliverable is cheap. Returns how many images were uploaded now.
    """
    url = _upload_url()
    if not url or not mapping:
        return 0
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=60.0, follow_redirects=True)
    pushed = 0
    try:
        for entry in mapping:
            if entry.get("cdnUploaded"):
                continue
            try:
                data = await _figure_bytes(client, entry["src"])
                response = await client.post(
                    url, files={"files": (entry["name"], data, "image/png")})
                response.raise_for_status()
                results = (response.json() or {}).get("results") or []
                result = results[0] if results else {}
                if result.get("status") == "uploaded":
                    entry["cdnUploaded"] = True
                    if result.get("url"):
                        entry["cdnUrl"] = result["url"]
                    pushed += 1
                else:
                    logger.warning("CDN upload service did not accept %s: %s",
                                   entry.get("name"), result or response.text[:200])
            except Exception:
                logger.exception(
                    "CDN upload failed for %s; the deliverable still references "
                    "its CDN URL — upload the image bundle manually",
                    entry.get("name"))
    finally:
        if own_client:
            await client.aclose()
    if pushed:
        logger.info("pushed %s figure(s) to the CDN upload service", pushed)
    return pushed


def rewrite_to_cdn(html: Optional[str], mapping: List[Dict[str, Any]]) -> Optional[str]:
    """Point figures that are on the CDN at their delivery URLs.

    Only entries confirmed uploaded are rewritten, so a document never
    references a CDN file that does not exist; the rest keep their hosted
    sources and are rewritten at download time instead.
    """
    if not html:
        return html
    for entry in mapping:
        if entry.get("cdnUploaded") and entry.get("cdnUrl") and entry.get("src"):
            html = html.replace(entry["src"], entry["cdnUrl"])
    return html
