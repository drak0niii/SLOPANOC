"""Structured governed-evidence identity.

    EvidenceIdentity = (knowledge_id, version_label, section_id)

A governed section is identified by that exact tuple, never by its canonical display string
`knowledge_id:version_label:section_id`: every component may itself contain `:` (live: section
`25:A5-...:section-0000` of version `v1` was re-read as version `v1:25` + section
`A5-...:section-0000`), so the string cannot be split back into its parts and two different tuples
can render the same string. The canonical string exists for display, logging and diagnostics only;
it is never parsed, and identities are only ever built from structured fields of server-owned
objects (evidence metadata, KM references, ProcedureAction sources, selection keys).

Equality is the exact tuple (no normalization). A missing, blank or non-string component is a
malformed identity: `identity_from_fields` / `identity_of` return None and callers fail closed.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class EvidenceIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    knowledge_id: str
    version_label: str
    section_id: str

    @field_validator("knowledge_id", "version_label", "section_id")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-blank string")
        return value

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.knowledge_id, self.version_label, self.section_id)

    @property
    def canonical(self) -> str:
        """Display / logging / diagnostics only -- never parsed back, never an identity."""
        return f"{self.knowledge_id}:{self.version_label}:{self.section_id}"

    def selection_key(self) -> dict[str, str]:
        """The exact `knowledge_select_evidence` selection key of this section."""
        return {"knowledge_id": self.knowledge_id, "version_label": self.version_label, "section_id": self.section_id}


def identity_from_fields(knowledge_id: Any, version_label: Any, section_id: Any) -> Optional[EvidenceIdentity]:
    """The identity of three structured fields; None when any is missing, blank or not a string."""
    if not all(isinstance(v, str) and v.strip() for v in (knowledge_id, version_label, section_id)):
        return None
    return EvidenceIdentity(knowledge_id=knowledge_id, version_label=version_label, section_id=section_id)


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def identity_of(obj: Any) -> Optional[EvidenceIdentity]:
    """Structured identity of a server-owned object -- an EvidenceIdentity, a governed evidence
    reference (its `metadata` fields), a KM evidence item (its `reference`), or anything carrying
    `knowledge_id` / `version_label` / `section_id` (selection key, ProcedureAction source, KM
    reference). Never read from a canonical `source_id` string."""
    if obj is None or isinstance(obj, str):
        return None
    if isinstance(obj, EvidenceIdentity):
        return obj
    meta = _get(obj, "metadata")
    if isinstance(meta, Mapping) and _get(obj, "source_type") is not None:
        return identity_from_fields(meta.get("knowledge_id"), meta.get("version_label"), meta.get("section_id"))
    reference = _get(obj, "reference")
    if reference is not None and not isinstance(reference, str):
        return identity_of(reference)
    return identity_from_fields(_get(obj, "knowledge_id"), _get(obj, "version_label"), _get(obj, "section_id"))


def stored_identity(value: Any) -> Optional[EvidenceIdentity]:
    """A persisted identity as loaded from storage: an EvidenceIdentity, or a mapping with exactly the
    three fields. Anything else (a canonical string, extra / missing / blank fields) is malformed and
    yields None -- it is rejected without breaking the load of the record that holds it."""
    if isinstance(value, EvidenceIdentity):
        return value
    if not isinstance(value, Mapping) or set(value) != {"knowledge_id", "version_label", "section_id"}:
        return None
    return identity_from_fields(value["knowledge_id"], value["version_label"], value["section_id"])


def identities_of(objs: Iterable[Any]) -> list[EvidenceIdentity]:
    """Distinct identities of `objs` in first-seen order (malformed ones are skipped)."""
    out: list[EvidenceIdentity] = []
    for obj in objs or ():
        identity = identity_of(obj)
        if identity is not None and identity not in out:
            out.append(identity)
    return out
