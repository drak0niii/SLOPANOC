"""The generic, storage-agnostic knowledge repository contract.

Deliberately imports nothing beyond `KnowledgeObject` and stdlib typing
-- no `sqlite3`, no SQLAlchemy, no cloud SDK. This is what lets a future
`PostgresKnowledgeRepository`/`CloudSQLKnowledgeRepository` exist without
ever changing `KnowledgeRepository` itself or any of its callers.
"""
from __future__ import annotations

from typing import Optional, Protocol

from backend.knowledge.domain.models import KnowledgeObject


class KnowledgeRepositoryError(Exception):
    """Base class for every error this package raises. Never constructed
    from a raw underlying storage exception's message -- see the
    concrete implementation's own docstring for how a low-level storage
    error is translated into one of the specific subclasses below.
    """


class KnowledgeVersionAlreadyExistsError(KnowledgeRepositoryError):
    """Raised by `add` when the exact `(knowledge_id, version.label)`
    pair already exists. `add` never silently overwrites -- this
    protects governance history (an existing stored version, whatever
    its lifecycle state, is never implicitly replaced by a second `add`
    call for the identical identity; use `replace` for an intentional
    update of an already-governed object).
    """


class KnowledgeVersionNotFoundError(KnowledgeRepositoryError):
    """Raised by `replace` when the exact `(knowledge_id, version.label)`
    pair does not already exist. `replace` never upserts -- creating a
    new version is exclusively `add`'s job.
    """


class KnowledgeRepositoryCorruptionError(KnowledgeRepositoryError):
    """Raised when a stored payload cannot be reconstructed into a valid
    `KnowledgeObject`, or when a successfully-reconstructed object's own
    `knowledge_id`/`version.label` does not match the storage key it was
    read from. Fails closed -- never returns a partial object, never
    silently drops an invalid field, never fabricates a default.
    """


class KnowledgeRepository(Protocol):
    """The narrow, deterministic set of persistence operations the
    current Generic KM architecture needs -- STORE, GET, LIST VERSION
    FAMILY, REPLACE AN EXISTING GOVERNED VERSION. Nothing about
    applicability, currentness, lifecycle transitions, search, ranking,
    or deletion belongs here -- see this package's own `__init__.py` and
    docs/KNOWLEDGE_CONTRACT.md's Phase 5.1F section for the full
    rationale and the explicit non-goals.

    The logical identity of one governed version is always the pair
    `(knowledge_id, version.label)` -- never a separately-generated row
    id, never inferred from list position or insertion order.
    """

    async def add(self, knowledge_object: KnowledgeObject) -> None:
        """Persist a NEW governed version. Raises
        `KnowledgeVersionAlreadyExistsError` if
        `(knowledge_object.knowledge_id, knowledge_object.version.label)`
        already exists -- never silently overwrites.
        """
        ...

    async def get(self, knowledge_id: str, version_label: str) -> Optional[KnowledgeObject]:
        """Retrieve the EXACT governed version identified by
        `(knowledge_id, version_label)`, or `None` if it does not exist.
        Never falls back to another version, never resolves "latest" or
        "current", never applies a lifecycle or applicability filter --
        exact identity means exact identity.
        """
        ...

    async def replace(self, knowledge_object: KnowledgeObject) -> None:
        """Persist an updated representation of an ALREADY-EXISTING
        governed version, identified by
        `(knowledge_object.knowledge_id, knowledge_object.version.label)`.
        Raises `KnowledgeVersionNotFoundError` if that exact identity
        does not already exist -- never upserts, never creates. This
        does not perform a lifecycle transition itself; it persists
        whatever already-governed `KnowledgeObject`
        `governance/service.py`'s `transition_lifecycle`/
        `approve_version`/`archive_version` already produced.
        """
        ...

    async def list_versions(self, knowledge_id: str) -> list[KnowledgeObject]:
        """Return every persisted version of one logical `knowledge_id`
        -- ALL lifecycle states (`CANDIDATE`/`APPROVED`/`ARCHIVE`),
        never filtered by lifecycle, effective date, applicability, or
        document type. This is identity/family retrieval, not search --
        the complete family this returns is exactly what
        `governance/versioning.py`'s `resolve_current_version`/
        `resolve_supersession_chain` need as their own input. Any
        ordering a concrete implementation applies for reproducibility
        is presentation/storage determinism only and carries ZERO
        governance meaning -- callers must never infer currentness or
        precedence from list position.
        """
        ...

    async def list_all(self) -> list[KnowledgeObject]:
        """SOURCE-OF-TRUTH CORPUS ENUMERATION: return every governed
        `KnowledgeObject` persisted in this repository -- every
        `knowledge_id`, every version, every `LifecycleStatus`
        (`CANDIDATE`/`APPROVED`/`ARCHIVE` alike). This exists so a future
        derived retrieval/search index (5.1G) can be built or rebuilt
        directly from the authoritative repository, without importing
        `SQLiteKnowledgeRepository` or knowing anything about its table
        or schema:

        ```text
        KnowledgeRepository.list_all()
                -> authoritative governed corpus
                -> future derived retrieval/search index built/rebuilt
        ```

        The repository remains the source of truth; any such index
        remains derived, optional, rebuildable, and non-authoritative --
        REPOSITORY != RETRIEVAL ENGINE, VECTOR INDEX != SOURCE OF TRUTH.

        This is identity enumeration, not search: it accepts no query,
        no `top_k`, no document-type filter, no lifecycle filter, and no
        applicability filter -- it performs no ranking, no filtering, no
        currentness resolution, and no applicability evaluation. 5.1G
        decides how candidate retrieval/indexing actually works; this
        operation only provides the complete, unfiltered corpus to build
        it from. Any ordering a concrete implementation applies is
        presentation/storage determinism only, exactly like
        `list_versions` -- it carries ZERO governance or relevance
        meaning, and no caller may infer version precedence from it.
        """
        ...
