"""Closed vocabularies for the Generic Knowledge Management domain.

Both enums here are deliberately just closed sets of state VALUES --
neither carries transition rules, evaluation logic, or governance
behavior (that is 5.1E for `LifecycleStatus`; applicability matching in
5.1B is unrelated to either). See docs/KNOWLEDGE_CONTRACT.md.
"""
from __future__ import annotations

from enum import Enum


class KnowledgeDocumentType(str, Enum):
    """The type/category of one governed operational knowledge object.

    MOP is a DOCUMENT TYPE, not an architecture -- there is deliberately
    no `MopRepository`/`SopRepository`/`RcaRepository` or per-type KM
    pipeline anywhere in this package. Every value here is handled by
    the exact same `KnowledgeObject`/`KnowledgeSection` model and (in
    later 5.1 sub-phases) the exact same ingestion/repository/retrieval
    code -- adding a new document type never requires a new class
    hierarchy, only a new enum member.
    """

    MOP = "mop"
    SOP = "sop"
    RCA = "rca"
    KB_ARTICLE = "kb_article"
    TROUBLESHOOTING_GUIDE = "troubleshooting_guide"
    OPERATIONAL_PROCEDURE = "operational_procedure"
    TECHNICAL_INSTRUCTION = "technical_instruction"
    OTHER = "other"


class LifecycleStatus(str, Enum):
    """The current lifecycle state of one `KnowledgeObject` version.

    Phase 5.1A defines these three state values only. Transition rules
    (e.g. "Candidate may become Approved", "Archive cannot become
    Approved"), "latest Approved wins" resolution, and any approval
    workflow are explicitly out of scope here -- see 5.1E. Nothing in
    this package inspects or enforces a transition between these values.
    """

    CANDIDATE = "candidate"
    APPROVED = "approved"
    ARCHIVE = "archive"
