"""POST-5.1 B2: `backend.attachments.validation` -- actual image
decode/verify, declared-vs-actual MIME matching, format exclusion
(GIF/SVG/corrupt), and safe filename normalization. All synthetic
images are generated in-memory via Pillow -- no real screenshots, no
external files.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from backend.attachments.validation import InvalidImageError, normalize_filename, validate_image_bytes


def _make_image_bytes(fmt: str, size: tuple[int, int] = (4, 4)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(10, 20, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


PNG_BYTES = _make_image_bytes("PNG")
JPEG_BYTES = _make_image_bytes("JPEG")
WEBP_BYTES = _make_image_bytes("WEBP")
GIF_BYTES = _make_image_bytes("GIF")


# --- valid images -----------------------------------------------------


def test_valid_png_accepted_with_matching_declared_type() -> None:
    assert validate_image_bytes(PNG_BYTES, "image/png") == "image/png"


def test_valid_jpeg_accepted_with_matching_declared_type() -> None:
    assert validate_image_bytes(JPEG_BYTES, "image/jpeg") == "image/jpeg"


def test_valid_webp_accepted_with_matching_declared_type() -> None:
    assert validate_image_bytes(WEBP_BYTES, "image/webp") == "image/webp"


def test_valid_png_accepted_with_no_declared_type() -> None:
    """Actual-decode becomes authoritative when nothing was declared."""
    assert validate_image_bytes(PNG_BYTES, None) == "image/png"


# --- spoofed / mismatched MIME -----------------------------------------


def test_spoofed_mime_png_declared_actual_jpeg_is_rejected() -> None:
    with pytest.raises(InvalidImageError):
        validate_image_bytes(JPEG_BYTES, "image/png")


def test_spoofed_mime_jpeg_declared_actual_png_is_rejected() -> None:
    with pytest.raises(InvalidImageError):
        validate_image_bytes(PNG_BYTES, "image/jpeg")


# --- unsupported declared / actual format -------------------------------


def test_unsupported_declared_mime_type_is_rejected() -> None:
    with pytest.raises(InvalidImageError):
        validate_image_bytes(PNG_BYTES, "image/gif")


def test_gif_actual_bytes_rejected_even_with_no_declared_type() -> None:
    with pytest.raises(InvalidImageError):
        validate_image_bytes(GIF_BYTES, None)


def test_svg_bytes_rejected() -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    with pytest.raises(InvalidImageError):
        validate_image_bytes(svg, None)


def test_svg_bytes_rejected_even_with_image_png_declared() -> None:
    """A spoofed declared type doesn't help SVG bytes masquerade as PNG."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    with pytest.raises(InvalidImageError):
        validate_image_bytes(svg, "image/png")


# --- corrupt image -----------------------------------------------------


def test_corrupt_image_bytes_rejected() -> None:
    corrupt = PNG_BYTES[: len(PNG_BYTES) // 2]
    with pytest.raises(InvalidImageError):
        validate_image_bytes(corrupt, None)


def test_completely_non_image_bytes_rejected() -> None:
    with pytest.raises(InvalidImageError):
        validate_image_bytes(b"not an image at all, just plain text bytes", None)


def test_empty_bytes_rejected() -> None:
    with pytest.raises(InvalidImageError):
        validate_image_bytes(b"", None)


# --- decompression-bomb protection ---------------------------------------


def test_decompression_bomb_warning_is_promoted_to_rejection() -> None:
    """An image just over Pillow's own `Image.MAX_IMAGE_PIXELS` default
    threshold triggers `DecompressionBombWarning` -- this must be
    promoted to a hard `InvalidImageError`, never merely a printed
    warning that validation otherwise ignores (instruction: "treat
    DecompressionBombWarning/Error conservatively", never loosened).
    Uses a real oversized image, not a mock -- proves the actual Pillow
    12.3.0 behavior, not an assumption carried over from 9.5.0.
    """
    from PIL import Image

    # Pillow warns once total pixels exceed MAX_IMAGE_PIXELS (89,478,485
    # by default) -- construct something just over that, as a real
    # (if huge, single-color, cheaply-compressible) PNG.
    side = 9500  # 9500*9500 = 90,250,000 > 89,478,485
    buffer = io.BytesIO()
    Image.MAX_IMAGE_PIXELS = None  # temporarily lift Pillow's own guard just to construct the fixture itself
    try:
        Image.new("L", (side, side), color=0).save(buffer, format="PNG")
    finally:
        Image.MAX_IMAGE_PIXELS = 89478485  # restore Pillow's own conservative default before validating

    with pytest.raises(InvalidImageError):
        validate_image_bytes(buffer.getvalue(), None)


# --- filename normalization ---------------------------------------------


def test_normalize_filename_keeps_simple_name() -> None:
    assert normalize_filename("screenshot.png") == "screenshot.png"


def test_normalize_filename_strips_unix_directory_traversal() -> None:
    assert normalize_filename("../../etc/passwd") == "passwd"


def test_normalize_filename_strips_windows_directory_components() -> None:
    assert normalize_filename("C:\\Users\\eve\\secret.png") == "secret.png"


def test_normalize_filename_strips_control_characters() -> None:
    result = normalize_filename("evil\r\nX-Injected-Header: yes.png")
    assert "\r" not in result
    assert "\n" not in result


def test_normalize_filename_falls_back_when_empty() -> None:
    assert normalize_filename("") == "attachment"


def test_normalize_filename_falls_back_when_only_control_chars() -> None:
    assert normalize_filename("\r\n\t") == "attachment"


def test_normalize_filename_caps_length() -> None:
    long_name = "a" * 500 + ".png"
    result = normalize_filename(long_name)
    assert len(result) <= 200
