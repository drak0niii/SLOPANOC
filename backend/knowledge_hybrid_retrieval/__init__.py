"""Phase 6A.5 concrete embedding provider -- lives OUTSIDE
`backend/knowledge/` entirely, mirroring `backend/knowledge_ingestion/`'s
own precedent for the identical reason (the `google.adk`/`google.genai`
dependency-boundary rule `backend/tests/knowledge/test_dependency_
boundary.py` enforces across the whole `backend/knowledge/` tree).
"""
