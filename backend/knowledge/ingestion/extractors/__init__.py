"""Format-specific compound-document extractors (A5 Layers C/D).

Each module exposes one `extract_*(data: bytes, ...) -> tuple[str, list[KnowledgeArtifact]]`
function: the format's own readable text rendering, plus any child
`KnowledgeArtifact` nodes discovered inside it (images, embedded
documents/spreadsheets, pages, sheets). See
`backend/knowledge/ingestion/extractors/dispatch.py` for the recursive
orchestrator that ties these together and enforces
`backend/knowledge/ingestion/extraction.py`'s defensive limits.

None of these modules ever execute macros/VBA/formulas/embedded
scripts, follow a hyperlink, or write to the filesystem -- every
extractor reads only from an in-memory `bytes` buffer.
"""
