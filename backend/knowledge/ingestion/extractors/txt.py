"""Plain-text / log artifact extraction (A5 Layer D).

The simplest extractor: no sub-artifacts, no recursion. Line structure
is preserved verbatim (decoding only, never reformatting) -- callers
that need command/output relationships or a parent-artifact link
attach that via the surrounding `KnowledgeArtifact`/`KnowledgeSection`,
never by rewriting the text itself here.
"""
from __future__ import annotations


def extract_txt(data: bytes) -> str:
    """Decodes raw bytes to text. UTF-8 first; falls back to
    `errors="replace"` (never raises on malformed encoding -- a log/txt
    artifact with a few undecodable bytes should still surface its
    otherwise-readable content, not fail the whole extraction).
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")
