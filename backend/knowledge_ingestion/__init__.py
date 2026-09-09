"""Concrete, vendor/cloud-SDK-dependent Knowledge ingestion machinery
(A5) -- deliberately OUTSIDE `backend/knowledge/`.

Mirrors an existing, already-established split in this codebase:
`backend/knowledge/tools/` (generic, ADK-independent) vs
`backend/tools/knowledge/` (concrete, ADK-facing). The equivalent split
for ingestion is `backend/knowledge/ingestion/` (generic contracts,
format-agnostic extractors -- python-docx/openpyxl/pypdf are document-
format parsers, not cloud/vendor SDKs, so they may live there) vs THIS
package (concrete cloud storage, and the concrete local-file admin/dev
source adapter that uses it) -- enforced automatically by
`backend/tests/knowledge/test_dependency_boundary.py`'s
`_FORBIDDEN_CLOUD_SDK_PREFIXES` check, which forbids
`google.cloud`/`azure`/`boto3`/`msal` imports anywhere under
`backend/knowledge/ingestion/`.

  - artifact_storage.py -- private GCS storage for durable Knowledge-
    artifact binaries.
  - local_file_adapter.py -- the ADMIN/DEVELOPER local-file
    `KnowledgeSourceAdapter` implementation (A5 instruction section 11:
    "an ADMIN/DEVELOPMENT validation source adapter, not normal user
    behavior").
"""
