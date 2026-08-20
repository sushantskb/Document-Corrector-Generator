"""Cloudinary uploads for extracted figures and corrected HTML.

Every call is wrapped so a missing or misconfigured Cloudinary account degrades
the pipeline (figures fall back to inline data URIs) instead of failing the job.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import zipfile
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# https://res.cloudinary.com/<cloud>/<resource_type>/<type>/[transformation/][v123/]<public_id>.<ext>
_DELIVERY_HOSTS = ("res.cloudinary.com",)
_VERSION_RE = re.compile(r"^v\d+$")
_TRANSFORMATION_RE = re.compile(r"^[a-z]{1,3}_[^/]+$")


def parse_delivery_url(url: str) -> Optional[Dict[str, str]]:
    """Pull the public id, resource type and delivery type out of an asset URL."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.hostname not in _DELIVERY_HOSTS:
        return None
    parts = [unquote(p) for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 4:
        return None
    _cloud, resource_type, delivery_type, *rest = parts
    while rest and (_VERSION_RE.match(rest[0]) or _TRANSFORMATION_RE.match(rest[0])):
        rest.pop(0)
    if not rest:
        return None
    public_id = "/".join(rest)
    # raw assets keep their extension in the public id; image/video do not
    if resource_type != "raw" and "." in public_id.rsplit("/", 1)[-1]:
        public_id = public_id.rsplit(".", 1)[0]
    return {
        "public_id": public_id,
        "resource_type": resource_type,
        "type": delivery_type,
    }

_CONFIGURED: Optional[bool] = None


def configure() -> bool:
    """Configure the SDK from the environment. Returns True when usable."""
    global _CONFIGURED
    if _CONFIGURED is not None:
        return _CONFIGURED
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")
    if not (cloud_name and api_key and api_secret):
        logger.warning("Cloudinary is not configured; uploads will fall back to data URIs")
        _CONFIGURED = False
        return False
    try:
        import cloudinary

        cloudinary.config(cloud_name=cloud_name, api_key=api_key,
                          api_secret=api_secret, secure=True)
        _CONFIGURED = True
    except Exception as exc:
        logger.error("Cloudinary configuration failed: %s", exc)
        _CONFIGURED = False
    return _CONFIGURED


def is_configured() -> bool:
    return configure()


class CloudinaryClient:
    """Thin async wrapper around the (blocking) Cloudinary uploader."""

    def __init__(self, folder: str = "document-correction"):
        self.folder = folder
        self.enabled = configure()

    async def upload_bytes(self, data: bytes, *, public_id: Optional[str] = None,
                           subfolder: str = "", resource_type: str = "image",
                           fmt: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Upload raw bytes. Returns {publicId, url, bytes} or None when disabled."""
        if not self.enabled:
            return None
        folder = f"{self.folder}/{subfolder}".rstrip("/")
        options: Dict[str, Any] = {
            "folder": folder,
            "resource_type": resource_type,
            "overwrite": True,
            "unique_filename": public_id is None,
        }
        if public_id:
            options["public_id"] = public_id
        if fmt:
            options["format"] = fmt
        try:
            import cloudinary.uploader

            result = await asyncio.to_thread(cloudinary.uploader.upload, data, **options)
            return {
                "publicId": result.get("public_id"),
                "url": result.get("secure_url") or result.get("url"),
                "bytes": result.get("bytes"),
                "format": result.get("format"),
                "width": result.get("width"),
                "height": result.get("height"),
            }
        except Exception as exc:
            logger.error("Cloudinary upload failed (%s): %s", folder, exc)
            return None

    async def upload_file(self, path: str, **kwargs) -> Optional[Dict[str, Any]]:
        with open(path, "rb") as fh:
            return await self.upload_bytes(fh.read(), **kwargs)

    async def upload_html(self, html: str, *, public_id: Optional[str] = None,
                          subfolder: str = "corrected") -> Optional[Dict[str, Any]]:
        """Store a corrected HTML document as a raw asset."""
        return await self.upload_bytes(
            html.encode("utf-8"), public_id=public_id, subfolder=subfolder,
            resource_type="raw", fmt="html",
        )

    async def fetch_restricted(self, url: str) -> Optional[Tuple[bytes, str]]:
        """Fetch an asset whose public delivery is blocked, using the Admin API.

        Cloudinary accounts disallow PDF (and ZIP) delivery by default, so a
        perfectly good upload answers 401 on its `res.cloudinary.com` URL — and
        signing the URL does not help, because the restriction is account-wide.
        The authenticated archive endpoint is not part of that delivery path, so
        it still returns the bytes.
        """
        if not self.enabled:
            return None
        asset = parse_delivery_url(url)
        if not asset:
            return None
        try:
            import cloudinary.utils
            import httpx

            archive_url = await asyncio.to_thread(
                cloudinary.utils.download_archive_url,
                public_ids=[asset["public_id"]],
                resource_type=asset["resource_type"],
                type=asset["type"],
                target_format="zip",
            )
            async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
                response = await client.get(archive_url)
                response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                names = [n for n in archive.namelist() if not n.endswith("/")]
                if not names:
                    return None
                data = archive.read(names[0])
            logger.info("fetched restricted asset %s via the Admin API (%s bytes)",
                        asset["public_id"], len(data))
            return data, names[0]
        except Exception as exc:
            logger.error("authenticated fetch failed for %s: %s", url, exc)
            return None

    async def delete(self, public_id: str, resource_type: str = "image") -> bool:
        if not self.enabled:
            return False
        try:
            import cloudinary.uploader

            await asyncio.to_thread(
                cloudinary.uploader.destroy, public_id, resource_type=resource_type
            )
            return True
        except Exception as exc:
            logger.error("Cloudinary delete failed for %s: %s", public_id, exc)
            return False
