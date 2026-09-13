"""Phase 6A.5: the generic embedding-provider boundary.

Mirrors `backend/knowledge/domain/`'s existing "generic Protocol here,
concrete implementation outside `backend/knowledge/`" pattern -- exactly
like A5's `ImageInterpreter` Protocol
(`backend/knowledge/ingestion/image_interpretation.py`) vs. its concrete
`GeminiImageInterpreter` implementation
(`backend/knowledge_ingestion/gemini_image_interpreter.py`, a SIBLING
package outside `backend/knowledge/` specifically because it imports
`google.genai`). The concrete real embedding provider for 6A.5 lives at
`backend/knowledge_hybrid_retrieval/vertex_embedding_provider.py` for the
identical reason -- `backend/knowledge/hybrid_retrieval/` itself imports
no `google.genai`/`google.adk`/cloud SDK anywhere (enforced by this
milestone's own extension of `test_dependency_boundary.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class EmbeddingVector:
    """One real (or, in a test double, synthetic) embedding result,
    always carrying its own model provenance (§13) -- never an opaque
    `list[float]` with no way to know what produced it."""

    values: tuple[float, ...]
    model: str
    model_version: str
    dimensions: int


class EmbeddingProvider(Protocol):
    """Generic embedding boundary -- deliberately async (a real provider
    makes a network call); a test double may still be synchronous-in-
    effect by resolving immediately. Batches (`texts`) to avoid one
    network round-trip per evidence unit (§45 cost control)."""

    async def embed(self, texts: Sequence[str]) -> list[EmbeddingVector]: ...
