"""A5 Layer B: opaque, content-hash-addressed object-key naming, no-PII-
in-path invariants, and `SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET`/ingestion-
limit settings behavior. Never touches a real GCS bucket --
`KnowledgeArtifactStorage`'s underlying client is only ever constructed
lazily on first real operation, which these tests never trigger for the
"unconfigured" cases. Mirrors backend/tests/test_attachments_storage.py's
convention exactly. Lives at this top level (not under
backend/tests/knowledge/) because it tests a CONCRETE, cloud-SDK-
dependent module (backend/knowledge_ingestion/artifact_storage.py),
deliberately outside the generic backend/knowledge/ package -- the same
convention backend/tests/test_p5_1j_knowledge_tools.py already
established for backend/tools/knowledge/.
"""
from __future__ import annotations

import pytest

from backend.config.settings import Settings
from backend.knowledge_ingestion.artifact_storage import (
    KnowledgeArtifactStorage,
    KnowledgeArtifactStorageUnavailableError,
    build_artifact_object_name,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64


def test_object_name_is_opaque_and_deterministic() -> None:
    name = build_artifact_object_name(_HASH_A)
    assert name == f"knowledge-artifacts/aa/{_HASH_A}"


def test_object_name_never_contains_filename_or_owner() -> None:
    name = build_artifact_object_name(_HASH_A)
    assert "@" not in name
    assert ".docx" not in name
    assert ".png" not in name


def test_same_content_hash_yields_same_object_name() -> None:
    # This IS the deduplication mechanism (module docstring, Case C):
    # two artifacts from different parent documents with identical
    # content resolve to the identical GCS object key.
    assert build_artifact_object_name(_HASH_A) == build_artifact_object_name(_HASH_A)


def test_different_content_hash_yields_different_object_name() -> None:
    assert build_artifact_object_name(_HASH_A) != build_artifact_object_name(_HASH_B)


def test_object_name_rejects_too_short_hash() -> None:
    with pytest.raises(ValueError):
        build_artifact_object_name("a")


def test_storage_reports_unconfigured_when_bucket_unset() -> None:
    storage = KnowledgeArtifactStorage(bucket_name=None)
    assert storage.is_configured is False


def test_storage_reports_configured_when_bucket_set() -> None:
    storage = KnowledgeArtifactStorage(bucket_name="slopanoc-knowledge-artifacts-sandbox01")
    assert storage.is_configured is True


def test_put_bytes_if_absent_raises_clearly_when_unconfigured() -> None:
    storage = KnowledgeArtifactStorage(bucket_name=None)
    with pytest.raises(KnowledgeArtifactStorageUnavailableError):
        storage.put_bytes_if_absent(build_artifact_object_name(_HASH_A), b"data", "image/png")


def test_get_bytes_raises_clearly_when_unconfigured() -> None:
    storage = KnowledgeArtifactStorage(bucket_name=None)
    with pytest.raises(KnowledgeArtifactStorageUnavailableError):
        storage.get_bytes(build_artifact_object_name(_HASH_A))


def test_uri_for_raises_clearly_when_unconfigured() -> None:
    storage = KnowledgeArtifactStorage(bucket_name=None)
    with pytest.raises(KnowledgeArtifactStorageUnavailableError):
        storage.uri_for(build_artifact_object_name(_HASH_A))


def test_uri_for_formats_gs_uri_when_configured() -> None:
    storage = KnowledgeArtifactStorage(bucket_name="slopanoc-knowledge-artifacts-sandbox01")
    uri = storage.uri_for(build_artifact_object_name(_HASH_A))
    assert uri == f"gs://slopanoc-knowledge-artifacts-sandbox01/knowledge-artifacts/aa/{_HASH_A}"


# --- Settings: bucket ----------------------------------------------------


def test_knowledge_artifacts_bucket_defaults_to_none_when_unset() -> None:
    assert Settings(env={}).knowledge_artifacts_bucket is None


def test_knowledge_artifacts_bucket_returns_configured_value() -> None:
    settings = Settings(env={"SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET": "slopanoc-knowledge-artifacts-sandbox01"})
    assert settings.knowledge_artifacts_bucket == "slopanoc-knowledge-artifacts-sandbox01"


def test_knowledge_artifacts_bucket_treats_blank_as_unset() -> None:
    assert Settings(env={"SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET": "   "}).knowledge_artifacts_bucket is None


def test_knowledge_artifacts_bucket_is_independent_of_chat_attachments_bucket() -> None:
    # The two settings must never fall back to each other -- separate
    # domains (module docstring), mirroring the KM database URL's own
    # documented never-implicitly-fall-back policy.
    settings = Settings(env={"SLOPANOC_CHAT_ATTACHMENTS_BUCKET": "slopanoc-chat-attachments-sandbox01"})
    assert settings.knowledge_artifacts_bucket is None
    assert settings.chat_attachments_bucket == "slopanoc-chat-attachments-sandbox01"


def test_ordinary_settings_still_work_when_bucket_unset() -> None:
    settings = Settings(env={})
    assert settings.knowledge_artifacts_bucket is None
    assert settings.resolve_database_url().startswith("sqlite+aiosqlite:///")


# --- Settings: ingestion limits -------------------------------------------


def test_ingestion_limits_default_to_documented_values() -> None:
    settings = Settings(env={})
    assert settings.knowledge_ingestion_max_recursion_depth == 6
    assert settings.knowledge_ingestion_max_artifacts_per_root == 500
    assert settings.knowledge_ingestion_max_artifact_bytes == 25 * 1024 * 1024
    assert settings.knowledge_ingestion_max_total_expanded_bytes == 200 * 1024 * 1024


def test_ingestion_limits_are_configurable() -> None:
    settings = Settings(
        env={
            "SLOPANOC_KNOWLEDGE_INGESTION_MAX_RECURSION_DEPTH": "2",
            "SLOPANOC_KNOWLEDGE_INGESTION_MAX_ARTIFACTS_PER_ROOT": "10",
            "SLOPANOC_KNOWLEDGE_INGESTION_MAX_ARTIFACT_BYTES": "1024",
            "SLOPANOC_KNOWLEDGE_INGESTION_MAX_TOTAL_EXPANDED_BYTES": "4096",
        }
    )
    assert settings.knowledge_ingestion_max_recursion_depth == 2
    assert settings.knowledge_ingestion_max_artifacts_per_root == 10
    assert settings.knowledge_ingestion_max_artifact_bytes == 1024
    assert settings.knowledge_ingestion_max_total_expanded_bytes == 4096
