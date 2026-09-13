"""Closed vocabularies for the TELCO Context domain.

Unlike Generic Knowledge's own `Applicability.dimensions` (deliberately
OPEN/domain-agnostic -- see docs/KNOWLEDGE_CONTRACT.md #11.2), TELCO
Context dimensions ARE a canonical, controlled vocabulary here, per
6A.2's own explicit instruction: a fixed set is needed so each dimension
can carry a known, deterministic CARDINALITY rule (see
`DIMENSION_CARDINALITY` below) -- something an open string bag cannot
express. This does not contradict Generic KM's own open-dimension
invariant: TELCO Context is a NEW, separate domain, not the Generic KM
layer, and is free to make a different, deliberate design choice for its
own vocabulary. Extending this enum later is a genuine code change, not a
runtime string -- exactly the same closed-enum discipline this codebase
already uses for `LifecycleStatus`/`ArtifactExtractionStatus`.
"""
from __future__ import annotations

from enum import Enum


class ContextDimension(str, Enum):
    """The canonical TELCO Context dimension vocabulary (6A.2 instruction
    section 10). `CUSTOMER`/`ACCOUNT` are kept as two distinct dimensions
    even though nothing in the current SLOPANOC repository already
    distinguishes them operationally (audited: no existing `account`
    concept exists anywhere in `backend/cases`/`backend/knowledge`) --
    preserved conceptually per instruction, since a future multi-tenant
    model may need both; today, only `CUSTOMER` needs to actually be
    populated for a real operational profile, and `ACCOUNT` typically
    remains `UNKNOWN`.
    """

    CUSTOMER = "customer"
    ACCOUNT = "account"
    DOMAIN = "domain"
    SUBDOMAIN = "subdomain"
    VENDOR = "vendor"
    TECHNOLOGY = "technology"
    PRODUCT = "product"
    NETWORK_ELEMENT = "network_element"
    HARDWARE = "hardware"
    SOFTWARE = "software"
    RELEASE = "release"
    SITE = "site"
    CELL = "cell"
    SECTOR = "sector"
    BAND = "band"
    ALARM = "alarm"
    FAULT = "fault"
    SYMPTOM = "symptom"
    SERVICE_IMPACT = "service_impact"
    RECENT_CHANGE = "recent_change"
    MAINTENANCE = "maintenance"


class DimensionCardinality(str, Enum):
    """Whether a dimension's KNOWN state accepts exactly one canonical
    value at a time (SINGULAR) or may legitimately accept several
    simultaneously-true distinct values (MULTI) -- see `models.py`'s
    `reduce_dimension` for exactly how this changes merge/conflict
    behavior. Two genuinely different SINGULAR values is a conflict
    (e.g. two different vendors for the same node); two genuinely
    different MULTI values is simply two accepted facts (e.g. two
    concurrent alarms are both real).
    """

    SINGULAR = "singular"
    MULTI = "multi"


# 6A.2 instruction section 11's own explicit examples are authoritative
# where given (`customer`/`account`/`vendor`/`domain` = SINGULAR;
# `alarm`/`fault`/`symptom`/`cell`/`site`/`technology`/`network_element`
# = MULTI). Every other dimension is a reasoned-by-analogy v1 default,
# documented per-line below -- a future milestone may revise these
# without changing this module's public shape.
DIMENSION_CARDINALITY: dict[ContextDimension, DimensionCardinality] = {
    ContextDimension.CUSTOMER: DimensionCardinality.SINGULAR,  # instruction-explicit
    ContextDimension.ACCOUNT: DimensionCardinality.SINGULAR,  # instruction-explicit
    ContextDimension.DOMAIN: DimensionCardinality.SINGULAR,  # instruction-explicit
    ContextDimension.SUBDOMAIN: DimensionCardinality.SINGULAR,  # analogous to DOMAIN
    ContextDimension.VENDOR: DimensionCardinality.SINGULAR,  # instruction-explicit
    ContextDimension.TECHNOLOGY: DimensionCardinality.MULTI,  # instruction-explicit
    ContextDimension.PRODUCT: DimensionCardinality.MULTI,  # analogous to NETWORK_ELEMENT
    ContextDimension.NETWORK_ELEMENT: DimensionCardinality.MULTI,  # instruction-explicit
    ContextDimension.HARDWARE: DimensionCardinality.MULTI,  # analogous to NETWORK_ELEMENT
    ContextDimension.SOFTWARE: DimensionCardinality.MULTI,  # analogous to RELEASE
    ContextDimension.RELEASE: DimensionCardinality.MULTI,  # multiple NEs may run different releases
    ContextDimension.SITE: DimensionCardinality.MULTI,  # instruction-explicit
    ContextDimension.CELL: DimensionCardinality.MULTI,  # instruction-explicit
    ContextDimension.SECTOR: DimensionCardinality.MULTI,  # analogous to CELL
    ContextDimension.BAND: DimensionCardinality.MULTI,  # analogous to CELL
    ContextDimension.ALARM: DimensionCardinality.MULTI,  # instruction-explicit
    ContextDimension.FAULT: DimensionCardinality.MULTI,  # instruction-explicit
    ContextDimension.SYMPTOM: DimensionCardinality.MULTI,  # instruction-explicit
    ContextDimension.SERVICE_IMPACT: DimensionCardinality.SINGULAR,  # one current overall assessment
    ContextDimension.RECENT_CHANGE: DimensionCardinality.MULTI,  # multiple changes may be relevant
    ContextDimension.MAINTENANCE: DimensionCardinality.MULTI,  # multiple maintenance windows may be relevant
}


class ContextState(str, Enum):
    """The four-state vocabulary (6A.2 instruction section 6 /
    docs/INTELLIGENCE_ARCHITECTURE.md #6). A strict closed enum -- never
    an arbitrary string -- so "UNKNOWN" can never be spelled three
    different ways across this codebase.
    """

    KNOWN = "known"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"
    NOT_APPLICABLE = "not_applicable"


class AssertionKind(str, Enum):
    """What kind of claim one `ContextAssertion` makes about its
    dimension. `VALUE` asserts a concrete raw/canonical value.
    `NOT_APPLICABLE` asserts that the dimension itself does not apply to
    the current situation at all -- structurally distinct from simply
    having no assertions (`UNKNOWN`), per instruction section 6's
    explicit KNOWN/UNKNOWN/CONFLICTING/NOT_APPLICABLE distinction.
    """

    VALUE = "value"
    NOT_APPLICABLE = "not_applicable"


class ContextOrigin(str, Enum):
    """Where a `ContextAssertion` came from -- the 6A.2 instruction's own
    forward-compatible origin list (section 8). Deliberately mirrors the
    SHAPE of `backend.cases.schemas.SourceType` (same "small closed
    provenance-origin enum" pattern) without importing it, for the same
    domain-independence reason `_shared.py` duplicates `require_non_blank`
    rather than cross-importing. Only `USER`/`CASE`/`TEAMS`/`KNOWLEDGE`/
    `SYSTEM` are reachable through any capability that exists today;
    `ITSM`/`ALARM`/`TOPOLOGY`/`INVENTORY`/`KPI`/`CHANGE`/`HANDOVER` exist
    so this enum does not need to change shape when those future
    Operational Context integrations (5.2-5.7) are built -- no connector
    for any of them is implemented by this milestone.
    """

    USER = "user"
    CASE = "case"
    TEAMS = "teams"
    KNOWLEDGE = "knowledge"
    SYSTEM = "system"
    ITSM = "itsm"
    ALARM = "alarm"
    TOPOLOGY = "topology"
    INVENTORY = "inventory"
    KPI = "kpi"
    CHANGE = "change"
    HANDOVER = "handover"


class ContextProfileOwnerKind(str, Enum):
    """A `TelcoContextProfile` is owned by exactly one scope at a time --
    mirroring the existing, already-proven Case/session relationship
    (`backend.cases`: a session is linked to at most one Case at a time).
    See `models.py`'s `TelcoContextProfile` docstring for the full
    ownership-model rationale (6A.2 instruction section 22).
    """

    SESSION = "session"
    CASE = "case"
