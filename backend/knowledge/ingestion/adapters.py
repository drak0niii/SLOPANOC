"""The minimal generic source adapter contract (Phase 5.1C).

`KnowledgeSourceAdapter` is a structural `typing.Protocol` (the same
mechanism already used elsewhere in this backend for a minimal interface,
e.g. `backend/api/chat_service.py`'s `_Runner`/`_EventStream`) -- any
object with a matching `fetch_documents` method satisfies it, with no
base class, registration, or plugin framework required.

`fetch_documents` is async because a real future adapter will perform
I/O (a SharePoint/GCS/Drive/... call) -- but nothing here builds that
I/O, or any orchestration around it: no scheduler, worker, queue, retry,
pagination, checkpoint/cursor, or connector framework exists in this
module. It exists ONLY to define the shape a source adapter must expose;
see backend/tests/knowledge/ for fake, test-only adapters that prove the
contract, and this package's own docstring for why no concrete adapter
(SharePoint/GCS/Drive/local file/PDF/DOCX/...) is implemented here.
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol

from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument


class KnowledgeSourceAdapter(Protocol):
    """Structural contract: yield zero or more `IngestedKnowledgeDocument`
    instances. Nothing about this Protocol assumes, names, or branches on
    any particular source system -- `KnowledgeSource.source_system`
    inside each yielded document is an arbitrary, adapter-supplied
    string.
    """

    def fetch_documents(self) -> AsyncIterator[IngestedKnowledgeDocument]: ...
