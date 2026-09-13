"""Concrete Vertex AI-backed `EmbeddingProvider` (6A.5).

MODEL DECISION (§12/§46, verified against the real GCP project/location
before being committed to, not assumed from remembered documentation):
`text-embedding-005` -- Google's current general-purpose Vertex AI text
embedding model, verified by a real, live `embed_content` call during
this milestone's own AUDIT phase (project `pr-msn-dev-gl-slopai-01`,
location `europe-west3`, matching this repository's existing
`GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION` environment convention) --
returned real 768-dimensional vectors, and a real 3-way similarity check
(query vs. a semantically-related sentence vs. an unrelated sentence)
produced the expected ordering (0.63 vs. 0.38 cosine similarity),
confirming the model is genuinely usable for TELCO-shaped operational
text, not merely reachable.

CLIENT CONSTRUCTION: a single, process-lifetime-cached `google.genai
.Client()` -- mirrors `get_shared_llm()`'s own "construct once, cache,
reuse" discipline (`backend/config/settings.py`), but for the embeddings
API surface specifically (a plain request/response call, not a
generative Agent/Runner conversation -- ADK's `Agent`/`Runner` machinery,
used elsewhere in this codebase for one-shot text generation such as
A5's `gemini_image_interpreter.py`, is not the right tool for a request
that is not a conversation at all). Relies on the SAME `GOOGLE_GENAI_USE_
VERTEXAI`/`GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION` environment
variables the rest of this backend's Gemini/Vertex configuration already
uses -- no new configuration surface introduced.

NO OCR, NO IMAGE EMBEDDING: this provider embeds TEXT ONLY (indexable
text already built by `indexable_text.py` from source/derived section
content) -- it never receives or embeds raw image bytes (§16: images
remain represented via their own DERIVED text interpretation, never a
binary image embedding).
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from backend.knowledge.hybrid_retrieval.embedding import EmbeddingVector

_logger = logging.getLogger(__name__)

MODEL_IDENTIFIER = "text-embedding-005"
"""Pinned, deliberately -- see this module's own docstring for the
real-API verification this pin is based on."""

EXPECTED_DIMENSIONS = 768

_client_cache: list[Any] = []


def _client() -> Any:
    if not _client_cache:
        from google import genai

        _client_cache.append(genai.Client())
    return _client_cache[0]


class VertexTextEmbeddingProvider:
    """The real, live embedding provider used by 6A.5's indexing pipeline
    outside of tests. A test double (see `backend/tests/knowledge/
    hybrid_retrieval/` fixtures) implements the same `EmbeddingProvider`
    Protocol without ever importing this module.
    """

    async def embed(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        if not texts:
            return []
        client = _client()
        result = await client.aio.models.embed_content(model=MODEL_IDENTIFIER, contents=list(texts))
        vectors: list[EmbeddingVector] = []
        for embedding in result.embeddings:
            values = tuple(embedding.values)
            if len(values) != EXPECTED_DIMENSIONS:
                _logger.warning(
                    "embedding dimensionality mismatch: expected %d, got %d for model %s",
                    EXPECTED_DIMENSIONS,
                    len(values),
                    MODEL_IDENTIFIER,
                )
            vectors.append(
                EmbeddingVector(values=values, model=MODEL_IDENTIFIER, model_version=MODEL_IDENTIFIER, dimensions=len(values))
            )
        return vectors
