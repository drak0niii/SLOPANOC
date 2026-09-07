"""Deterministic input hygiene for uploaded chat attachment images
(POST-5.1 B2).

NOT PHASE 4H: this is basic correctness/input-hygiene validation (is this
actually a decodable PNG/JPEG/WebP, does its real format match what the
client claimed) -- not adversarial/prompt-injection isolation, which
remains Phase 4H's scope entirely untouched here.

DECLARED VS. ACTUAL MIME (instruction section 13, decision documented):
strict by default -- a declared MIME type that isn't one of the three
supported types is rejected immediately (never silently reinterpreted),
and if a declared type IS one of the three, it must match the type
Pillow actually decodes from the bytes, or the upload is rejected. A
missing/absent declared type (`None` -- e.g. `application/octet-stream`
or no `Content-Type` on the multipart part) is accepted and the decoded
format becomes authoritative; browsers commonly send accurate image
`Content-Type`s for `<input type="file">`/paste flows, so this repo has
no concrete compatibility reason yet to weaken the mismatch check itself
-- only to tolerate its absence. Revisit if B3 hits a real browser case
that needs otherwise.
"""
from __future__ import annotations

import io
import warnings
from typing import Optional

from PIL import Image

SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/webp"})

_PILLOW_FORMAT_TO_MIME: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


class InvalidImageError(ValueError):
    """The uploaded bytes are not a valid, supported image -- corrupt,
    unsupported format, or a declared MIME type that doesn't match the
    actually-decoded format. Never constructed with raw Pillow/library
    exception text (instruction: never leak internal detail) -- always a
    fixed, descriptive-but-safe message.
    """


def validate_image_bytes(data: bytes, declared_mime_type: Optional[str]) -> str:
    """Validates `data` is a genuinely decodable PNG/JPEG/WebP image, and
    that it matches `declared_mime_type` if one was supplied. Returns the
    authoritative MIME type (derived from the actual decoded format,
    never merely echoed back from the caller) on success; raises
    `InvalidImageError` otherwise.

    Decompression-bomb protection is never disabled or loosened --
    `Image.MAX_IMAGE_PIXELS` stays at Pillow's own conservative default,
    and `DecompressionBombWarning` is promoted to a hard error here
    rather than left as a easily-missed stderr warning (instruction:
    "treat DecompressionBombWarning/Error conservatively").
    """
    if declared_mime_type is not None and declared_mime_type not in SUPPORTED_MIME_TYPES:
        raise InvalidImageError(f"unsupported declared media type: {declared_mime_type!r}")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # First pass: `verify()` checks structural integrity without
            # fully decoding pixel data -- but Pillow's own documented
            # contract is that the `Image` object is unusable for
            # anything else afterward, so a second `Image.open` is
            # required for the real decode below. This two-pass shape is
            # Pillow's own established idiom for "verify then use."
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()

            with Image.open(io.BytesIO(data)) as decoded:
                decoded.load()  # force full pixel decode -- catches truncated/corrupt data verify() alone can miss.
                actual_format = decoded.format
    except InvalidImageError:
        raise
    except Exception as exc:  # noqa: BLE001 -- Pillow raises many distinct exception types for "not a valid image".
        raise InvalidImageError("the uploaded file is not a valid, decodable image") from exc

    actual_mime_type = _PILLOW_FORMAT_TO_MIME.get(actual_format or "")
    if actual_mime_type is None:
        raise InvalidImageError(f"unsupported image format: {actual_format!r}")

    if declared_mime_type is not None and declared_mime_type != actual_mime_type:
        raise InvalidImageError(
            "declared media type does not match the actual image format -- refusing to reinterpret it"
        )

    return actual_mime_type


_MAX_FILENAME_LENGTH = 200
_DEFAULT_FILENAME = "attachment"


def normalize_filename(original_filename: str) -> str:
    """Safe, display-only filename normalization -- NEVER used to derive
    a storage path (see `storage.py`'s own docstring: object keys are
    always opaque, server-generated ids) and never used for
    authorization. Strips any directory-traversal significance (keeps
    only the final path segment), strips CR/LF and other control
    characters (header-injection hygiene for the future
    `Content-Disposition` use in the content-retrieval endpoint), and
    bounds length. Falls back to a fixed default if nothing usable
    remains.
    """
    # Keep only the final path segment -- defeats both `/` and `\`
    # (Windows-style) directory-traversal-looking input; this is display
    # hygiene, not a security boundary (the object path never uses this
    # value at all).
    candidate = original_filename.replace("\\", "/").rsplit("/", 1)[-1]
    # Strip control characters (CR/LF and friends) -- these are the ones
    # that matter for a value that may later appear in an HTTP header.
    candidate = "".join(ch for ch in candidate if ch.isprintable())
    candidate = candidate.strip()
    if not candidate:
        return _DEFAULT_FILENAME
    return candidate[:_MAX_FILENAME_LENGTH]
