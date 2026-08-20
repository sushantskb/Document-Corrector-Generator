"""Image download, hashing and perceptual matching."""

from __future__ import annotations

import io
import logging
from typing import Dict, List, Optional, Sequence, Tuple

from models.models import ImageElement
from utils.file_utils import download_bytes, is_url, save_temp_file
from utils.hash_utils import calculate_dhash, calculate_phash, calculate_sha256, hash_similarity

logger = logging.getLogger(__name__)

# Images smaller than this are spacers/bullets/tracking pixels, not content.
MIN_CONTENT_PIXELS = 32
DEFAULT_MATCH_THRESHOLD = 0.75
_SSIM_SIZE = (256, 256)


def load_image(data: bytes):
    """Decode bytes into an RGB PIL image (None on failure)."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        image.load()
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        return image
    except Exception as exc:
        logger.warning("could not decode image: %s", exc)
        return None


async def download_image(url: str) -> Tuple[Optional[object], Optional[bytes], Optional[str]]:
    """Fetch an image URL. Returns (PIL image, raw bytes, error message)."""
    if url.startswith(("http://", "https://")) and not is_url(url):
        # e.g. src="https://" — a placeholder the page never filled in
        return None, None, f"malformed image URL: {url!r}"
    if url.startswith("data:"):
        try:
            import base64

            header, _, payload = url.partition(",")
            raw = base64.b64decode(payload) if "base64" in header else payload.encode()
            return load_image(raw), raw, None
        except Exception as exc:
            return None, None, f"invalid data URI: {exc}"
    try:
        content, _ = await download_bytes(url)
    except Exception as exc:
        return None, None, str(exc)
    image = load_image(content)
    if image is None:
        return None, content, "unsupported or corrupt image data"
    return image, content, None


def describe_image(image, data: Optional[bytes] = None) -> Dict[str, object]:
    """phash / dhash / sha256 / dimensions for one decoded image."""
    info: Dict[str, object] = {}
    if image is not None:
        info["width"], info["height"] = image.size
        info["phash"] = calculate_phash(image)
        info["dhash"] = calculate_dhash(image)
    if data:
        info["sha256"] = calculate_sha256(data)
    return info


def is_content_image(image, data: Optional[bytes] = None) -> bool:
    """Filter out spacers, bullets and tracking pixels."""
    if image is None:
        return False
    width, height = image.size
    if width < MIN_CONTENT_PIXELS or height < MIN_CONTENT_PIXELS:
        return False
    if data is not None and len(data) < 256:
        return False
    return True


def is_blank(image, variance_threshold: float = 3.0) -> bool:
    """True for a flat single-colour crop (blank page region)."""
    try:
        import numpy as np

        array = np.asarray(image.convert("L"), dtype="float32")
        return float(array.std()) < variance_threshold
    except Exception:
        return False


def calculate_ssim(image_a, image_b) -> float:
    """Structural similarity of two PIL images, 0..1 (0 when it cannot be computed)."""
    if image_a is None or image_b is None:
        return 0.0
    try:
        import numpy as np
        from skimage.metrics import structural_similarity

        a = np.asarray(image_a.convert("L").resize(_SSIM_SIZE), dtype="float32")
        b = np.asarray(image_b.convert("L").resize(_SSIM_SIZE), dtype="float32")
        score = structural_similarity(a, b, data_range=255.0)
        return max(0.0, min(1.0, float(score)))
    except Exception as exc:
        logger.warning("ssim failed: %s", exc)
        return 0.0


def similarity_score(a: ImageElement, b: ImageElement,
                     image_a=None, image_b=None) -> Tuple[float, Dict[str, float]]:
    """Combined confidence that two image elements are the same picture.

    Identical bytes short-circuit to 1.0. Otherwise pHash and dHash carry the
    match (robust to rescaling and recompression) and SSIM refines it when both
    sets of pixels are in hand.
    """
    parts: Dict[str, float] = {}
    if a.sha256 and b.sha256 and a.sha256 == b.sha256:
        return 1.0, {"sha256": 1.0}

    phash_score = hash_similarity(a.phash, b.phash)
    parts["phash"] = round(phash_score, 4)

    if a.dhash and b.dhash:
        parts["dhash"] = round(hash_similarity(a.dhash, b.dhash), 4)

    ssim_score = calculate_ssim(image_a, image_b) if (image_a and image_b) else None
    if ssim_score is not None:
        parts["ssim"] = round(ssim_score, 4)

    aspect = 0.0
    if a.width and a.height and b.width and b.height:
        ratio_a = a.width / max(1.0, a.height)
        ratio_b = b.width / max(1.0, b.height)
        aspect = max(0.0, 1.0 - abs(ratio_a - ratio_b) / max(ratio_a, ratio_b, 1e-6))
        parts["aspect"] = round(aspect, 4)

    if ssim_score is not None:
        score = 0.5 * phash_score + 0.35 * ssim_score + 0.15 * aspect
        if "dhash" in parts:
            score = 0.4 * phash_score + 0.2 * parts["dhash"] + 0.3 * ssim_score + 0.1 * aspect
    else:
        score = 0.8 * phash_score + 0.2 * aspect
        if "dhash" in parts:
            score = 0.55 * phash_score + 0.25 * parts["dhash"] + 0.2 * aspect
    return round(max(0.0, min(1.0, score)), 4), parts


def match_images(source: Sequence[ImageElement], target: Sequence[ImageElement],
                 threshold: float = DEFAULT_MATCH_THRESHOLD,
                 pixel_cache: Optional[Dict[str, object]] = None) -> Dict[str, list]:
    """Greedy best-first one-to-one matching between two image sets.

    ``pixel_cache`` maps element id -> PIL image so SSIM can be used where the
    pixels were successfully decoded.
    """
    pixel_cache = pixel_cache or {}
    scored: List[Tuple[float, int, int, Dict[str, float]]] = []
    for s_idx, s_img in enumerate(source):
        for t_idx, t_img in enumerate(target):
            score, parts = similarity_score(
                s_img, t_img, pixel_cache.get(s_img.id), pixel_cache.get(t_img.id)
            )
            if score >= threshold:
                scored.append((score, s_idx, t_idx, parts))
    scored.sort(key=lambda item: item[0], reverse=True)

    matches: List[Dict[str, object]] = []
    used_source, used_target = set(), set()
    for score, s_idx, t_idx, parts in scored:
        if s_idx in used_source or t_idx in used_target:
            continue
        used_source.add(s_idx)
        used_target.add(t_idx)
        matches.append({
            "source_index": s_idx,
            "target_index": t_idx,
            "source_id": source[s_idx].id,
            "target_id": target[t_idx].id,
            "confidence": score,
            "scores": parts,
        })
    matches.sort(key=lambda m: m["source_index"])
    return {
        "matches": matches,
        "unmatched_source": [i for i in range(len(source)) if i not in used_source],
        "unmatched_target": [i for i in range(len(target)) if i not in used_target],
    }


def save_image_bytes(image, fmt: str = "PNG") -> Tuple[bytes, str]:
    """Serialize a PIL image and persist it to a temp file. Returns (bytes, path)."""
    buffer = io.BytesIO()
    save_image = image
    if fmt.upper() in ("JPEG", "JPG") and image.mode in ("RGBA", "P", "LA"):
        save_image = image.convert("RGB")
    save_image.save(buffer, format=fmt)
    data = buffer.getvalue()
    path = save_temp_file(data, suffix=f".{fmt.lower()}")
    return data, path
