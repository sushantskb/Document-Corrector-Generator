"""Checksums and perceptual hashes."""

from __future__ import annotations

import hashlib
import io
import logging
from typing import Optional, Union

logger = logging.getLogger(__name__)

BytesLike = Union[bytes, bytearray, memoryview]


def calculate_sha256(data: Union[BytesLike, str]) -> str:
    """SHA-256 of raw bytes or of a file when given a path."""
    if isinstance(data, str):
        return sha256_file(data)
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def calculate_phash(image) -> Optional[str]:
    """Perceptual hash (pHash, 8x8 DCT) of a PIL image, path or raw bytes."""
    try:
        import imagehash
        from PIL import Image

        if isinstance(image, (bytes, bytearray, memoryview)):
            image = Image.open(io.BytesIO(bytes(image)))
        elif isinstance(image, str):
            image = Image.open(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        return str(imagehash.phash(image, hash_size=8))
    except Exception as exc:  # corrupt/unsupported image must not kill a job
        logger.warning("phash failed: %s", exc)
        return None


def calculate_dhash(image) -> Optional[str]:
    """Difference hash — complements pHash on flat/line-art figures."""
    try:
        import imagehash
        from PIL import Image

        if isinstance(image, (bytes, bytearray, memoryview)):
            image = Image.open(io.BytesIO(bytes(image)))
        elif isinstance(image, str):
            image = Image.open(image)
        return str(imagehash.dhash(image.convert("RGB"), hash_size=8))
    except Exception as exc:
        logger.warning("dhash failed: %s", exc)
        return None


def hamming_distance(hash_a: Optional[str], hash_b: Optional[str]) -> Optional[int]:
    """Bit distance between two hex hash strings (None when either is missing)."""
    if not hash_a or not hash_b or len(hash_a) != len(hash_b):
        return None
    try:
        return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")
    except ValueError:
        return None


def hash_similarity(hash_a: Optional[str], hash_b: Optional[str], bits: int = 64) -> float:
    """0..1 similarity derived from the hamming distance."""
    distance = hamming_distance(hash_a, hash_b)
    if distance is None:
        return 0.0
    return max(0.0, 1.0 - distance / float(bits))
