"""Legacy OLE2 Compound-File-Binary embedded-object extraction (A5
corrective pass, Correction 1).

A real "Insert Object > Create from File" embed (as opposed to a native
OOXML `word:embeddings/*.docx`/`.xlsx` package) is stored as an OLE2
Compound File Binary (CFB) container -- the same legacy structured-
storage format `.doc`/`.xls` themselves once used, magic bytes
`D0 CF 11 E0 A1 B1 1A E1`. When Word wraps an arbitrary file (e.g. a
`.txt` log) this way, the CFB container holds a single, well-documented
`"\x01Ole10Native"` stream (the classic "OLE 1.0 Package" format) whose
own binary layout carries the ORIGINAL embedded filename and the
embedded file's raw bytes verbatim.

DEPENDENCY: `olefile` (pinned in requirements.txt) -- a narrow, mature,
pure-Python OLE2-CFB READER with no sub-dependencies. It parses only the
container's own directory/FAT structure to expose named streams as raw
bytes; it has no code-execution capability of any kind (no VBA/macro
interpreter, no COM/automation, no ability to invoke Word/Excel or any
other application) -- exactly the "focused OLE compound-file reader"
this milestone calls for, not a broader office-automation framework.

SAFETY: this module performs ONLY read-only binary parsing --
`olefile.OleFileIO(...).openstream(...).read()` plus plain
`struct.unpack`/byte-slicing on the resulting bytes. It never shells
out, never uses COM/Word/Excel automation, never executes a macro, and
never writes the embedded payload to disk -- the extracted bytes are
handed directly, in memory, to the EXISTING recursive dispatch
(`extractors/dispatch.py`'s `extract_embedded_artifact`), so a
supported embedded payload (a TXT/log, or even a nested DOCX/XLSX/PDF/
image should one ever appear inside an OLE Package this way) flows
through the exact same typed-artifact pipeline as an OOXML-native
embedding -- no separate, parallel extraction path.

FAILURE IS ALWAYS SAFE: `extract_ole_package_payload` returns `None`
for anything it cannot confidently, structurally validate (not a CFB
file at all, no `"\x01Ole10Native"` stream, a malformed/inconsistent
length field, or a validation check that fails) -- it never guesses,
and the caller (dispatch.py) falls back to its existing "legacy OLE
object, not further decomposed" SKIPPED artifact in that case, exactly
the same safe behavior as before this module existed.
"""
from __future__ import annotations

import struct
from typing import Optional

_OLE2_COMPOUND_FILE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OLE10_NATIVE_STREAM_NAME = "\x01Ole10Native"
_MAX_FILENAME_LENGTH = 260  # a Windows MAX_PATH-sized bound -- purely a sanity/validation bound, never enforced as a real OS path.


def is_ole_compound_file(data: bytes) -> bool:
    return data[:8] == _OLE2_COMPOUND_FILE_MAGIC


def _read_null_terminated_ascii(buffer: bytes, offset: int, *, max_length: int) -> tuple[str, int]:
    """Reads a NUL-terminated string starting at `offset`, bounded to at
    most `max_length` bytes of search -- raises `ValueError` (never
    silently truncates or wraps) if no terminator is found in bounds, so
    a malformed/hostile stream can never trigger an unbounded scan.
    """
    end = buffer.find(b"\x00", offset, offset + max_length)
    if end == -1:
        raise ValueError(f"no NUL terminator found within {max_length} bytes starting at offset {offset}")
    return buffer[offset:end].decode("latin-1"), end + 1


def _parse_ole10_native_package(stream: bytes) -> tuple[str, bytes]:
    """Parses the classic OLE 1.0 "Package" native-data stream layout:
    a 4-byte total size, a 2-byte version marker, a NUL-terminated
    display filename, a NUL-terminated source path, a short reserved
    block, a NUL-terminated temp path, a 4-byte payload length, then the
    raw embedded file bytes. Raises `ValueError` on ANY structural
    inconsistency (never returns a best-effort/partial guess) --
    callers must treat any exception here as "unsupported, fail safely".
    """
    if len(stream) < 6:
        raise ValueError("stream too short to contain even a header")

    declared_total_size = struct.unpack_from("<I", stream, 0)[0]
    if declared_total_size + 4 > len(stream):
        raise ValueError(f"declared total size {declared_total_size} is inconsistent with actual stream length {len(stream)}")

    pos = 6  # 4-byte size + 2-byte version marker, position only (version value itself is not interpreted).
    filename, pos = _read_null_terminated_ascii(stream, pos, max_length=_MAX_FILENAME_LENGTH)
    if not filename.strip():
        raise ValueError("embedded filename is blank")
    _source_path, pos = _read_null_terminated_ascii(stream, pos, max_length=1024)

    # A short reserved block precedes the temp-path string -- empirically
    # 8 bytes for the real-world Package streams this was validated
    # against (Windows/Office-generated `Insert Object > Create from
    # File`); never interpreted, only skipped. If the resulting position
    # does not yield a valid NUL-terminated temp path within a bounded
    # search, this raises rather than guessing a different skip amount.
    pos += 8
    _temp_path, pos = _read_null_terminated_ascii(stream, pos, max_length=1024)

    if pos + 4 > len(stream):
        raise ValueError("stream too short to contain the payload length field")
    data_length = struct.unpack_from("<I", stream, pos)[0]
    pos += 4

    if data_length == 0:
        raise ValueError("declared payload length is zero")
    if pos + data_length > len(stream):
        raise ValueError(f"declared payload length {data_length} exceeds remaining stream bytes ({len(stream) - pos})")

    payload = stream[pos : pos + data_length]
    return filename, payload


def extract_ole_package_payload(data: bytes) -> Optional[tuple[str, bytes]]:
    """Safely attempts to extract ONE embedded file (filename, raw
    bytes) from an OLE2 CFB container's `"\x01Ole10Native"` stream.
    Returns `None` -- never raises -- for anything not confidently,
    structurally identifiable as this specific, well-documented shape:
    not a real OLE2 CFB file, no such stream present, a corrupt/
    unreadable container, or a stream that fails `_parse_ole10_native_package`'s
    own validation. This is the ONLY function in this module a caller
    needs -- it owns the "never guess" contract completely.
    """
    if not is_ole_compound_file(data):
        return None

    import io

    import olefile  # noqa: PLC0415 -- imported lazily, matching this codebase's "only import a dependency where used" convention.

    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if not ole.exists(_OLE10_NATIVE_STREAM_NAME):
                return None
            stream_bytes = ole.openstream(_OLE10_NATIVE_STREAM_NAME).read()
    except Exception:
        # Any olefile-raised parsing/structural error on a malformed or
        # merely OLE2-magic-prefixed-but-otherwise-invalid container --
        # fail safely, never propagate, never guess.
        return None

    try:
        return _parse_ole10_native_package(stream_bytes)
    except (ValueError, struct.error):
        return None
