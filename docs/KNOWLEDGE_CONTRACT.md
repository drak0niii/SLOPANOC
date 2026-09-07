# Knowledge Contract — Generic Knowledge Management Domain (Phase 5.1A–5.1J)

Status: **Implemented (domain + ingestion + processing + governance +
local persistence + retrieval/ranking + provenance + generic agent-
facing tools + first reference consumer).** This document describes
`backend/knowledge/domain/`, `backend/knowledge/ingestion/`,
`backend/knowledge/processing/`, `backend/knowledge/governance/`,
`backend/knowledge/repository/`, `backend/knowledge/retrieval/`,
`backend/knowledge/provenance/`, `backend/knowledge/tools/`, and (the
concrete, non-generic ADK adapter) `backend/tools/knowledge/` — the
foundational domain model, typed contracts, deterministic applicability
evaluation, the generic ingestion boundary, structural content
processing, versioning/lifecycle governance, the persistence boundary,
the deterministic retrieval/ranking (context-reduction) boundary, the
deterministic evidence-validation boundary, the generic agent-facing
tool boundary, and Incident Manager's concrete integration with it, for
SLOPANOC's Generic Knowledge Management (KM) Layer. §1–§10 cover Phase
5.1A (the domain foundation); §11 covers Phase 5.1B (metadata hardening +
applicability); §12 covers Phase 5.1C (the generic ingestion boundary);
§13 covers Phase 5.1D (structured content processing / segmentation);
§14 covers Phase 5.1E (versioning + lifecycle governance); §15 covers
Phase 5.1F (knowledge repository abstraction); §16 covers Phase 5.1G
(retrieval + ranking); §17 covers Phase 5.1H (knowledge provenance); §18
covers Phase 5.1I (generic agent-facing knowledge tools); §19 covers
Phase 5.1J (Incident Manager, the first reference consumer). See
`docs/TROUBLESHOOTING_STRATEGY.md` for the product principle this layer
ultimately exists to serve.

---

## 1. Phase 5.1A scope

5.1A answers exactly one question: **"What is governed operational
knowledge inside SLOPANOC?"** It defines the domain objects and typed
contracts every later 5.1 sub-phase builds on. It does not answer how
knowledge is ingested, stored, matched for applicability, segmented,
version-resolved, retrieved/ranked, or exposed to an agent — see
[§7](#7-non-goals-of-this-phase) and [§8](#8-mapping-to-later-5-1-sub-phases).

---

## 2. Generic KM purpose

The Generic KM Layer exists to provide **Knowledge Context** — one of
three future context domains (alongside Operational Context and Case
Context) a future Context Engineering Layer would assemble for a
specialist (see `docs/AGENT_CONTRACT.md` §2's "Future agent topology" and
`docs/TROUBLESHOOTING_STRATEGY.md` §9). It is a generic platform
capability, not a feature owned by any one agent:

> **MOP / SOP / RCA / KB are document types behind the same Generic KM
> Layer.**

There is no `MopRepository`, `SopRepository`, or `RcaRepository`, and no
per-document-type pipeline anywhere in this package. Every document type
is represented by the exact same `KnowledgeObject`/`KnowledgeSection`
model — `document_type` is a plain enum member, never a different class
or code path.

```text
Generic KM
        ↓
Knowledge Context
        ↓
future Context Engineering
        ↓
specialist reasoning
```

Context Engineering is **not implemented**. This phase only establishes
the Generic KM / Knowledge Context foundation the diagram above starts
from.

---

## 3. Domain objects

All types live in `backend/knowledge/domain/`:

| Type | Module | Purpose |
|---|---|---|
| `KnowledgeDocumentType` | `enums.py` | Closed set of document types (MOP/SOP/RCA/KB article/...). |
| `LifecycleStatus` | `enums.py` | Closed set of lifecycle state values (Candidate/Approved/Archive). |
| `KnowledgeSource` | `models.py` | Generic source identity (where content originated). |
| `KnowledgeVersion` | `models.py` | Generic version identity (not SemVer-constrained). |
| `KnowledgeMetadata` | `models.py` | Common, extensible metadata (owner, classification, tags, attributes bag). |
| `Applicability` | `models.py` | Extensible applicability *representation* (dimensions bag) — not an evaluator. |
| `KnowledgeSection` | `models.py` | One structured section of a knowledge object's content. |
| `KnowledgeObject` | `models.py` | The central aggregate — one governed knowledge document, of any type. |
| `KnowledgeEvidenceReference` | `contracts.py` | A stable, generic pointer to a real piece of knowledge (document- or section-level). |
| `KnowledgeContextItem` | `contracts.py` | The generic, agent-agnostic shape that leaves the KM domain once knowledge is selected. |

All are plain, mutable `pydantic.BaseModel`s, matching this backend's
existing convention (e.g. `backend/approval/schemas.py`,
`backend/cases/schemas.py`) — never a frozen model. "Immutable after
construction" is achieved the same way the rest of this codebase already
achieves it: never mutate an instance in place; derive a changed copy
with `.model_copy(update={...})`.

---

## 4. Relationships

```text
KnowledgeObject
├── document_type : KnowledgeDocumentType
├── lifecycle_status : LifecycleStatus
├── version : KnowledgeVersion
├── source : KnowledgeSource
├── metadata : KnowledgeMetadata
├── applicability : Applicability
└── sections : KnowledgeSection[]        (each section.knowledge_id == this object's knowledge_id)

KnowledgeEvidenceReference
└── points at (knowledge_id, version_label, optional section_id, source identity)
    -- an identity-only pointer, never an embedded copy of the object/section

KnowledgeContextItem
├── knowledge identity + document_type + version_label + lifecycle_status
├── applicability : Applicability
├── sections : KnowledgeSection[]         (a selected subset, each still knowledge_id-checked)
└── evidence : KnowledgeEvidenceReference[]
```

`KnowledgeContextItem` reuses `KnowledgeSection`/`Applicability` directly
rather than inventing near-duplicate "excerpt" types — it is a
*selection* over a `KnowledgeObject`'s own sections (produced later, by
5.1G/5.1H retrieval and provenance logic), not a separate data model.

---

## 5. Lifecycle values

`LifecycleStatus` defines exactly three state values this phase cares
about:

- `CANDIDATE`
- `APPROVED`
- `ARCHIVE`

No transition rules, "latest Approved wins" resolution, or approval
workflow exist yet — 5.1E owns all of that. A `KnowledgeObject` simply
carries whichever status it currently has; nothing in this package
inspects or enforces a transition between values.

---

## 6. Structural invariants

Only structural validation exists in 5.1A — never business/governance
logic:

- `knowledge_id`, `title`, `source_system`, `source_id`, version `label`,
  `section_id`, section `knowledge_id`, and section `content` must all be
  non-blank.
- `KnowledgeVersion.effective_to` must not be before `effective_from`.
- A version must not list itself in its own `supersedes`/`superseded_by`.
- `KnowledgeSection.sequence` must be non-negative.
- Every `KnowledgeSection` inside a `KnowledgeObject` (or selected into a
  `KnowledgeContextItem`) must have `section.knowledge_id` equal to the
  owning object's `knowledge_id`.
- `section_id` values must be unique within one `KnowledgeObject`.
- `sequence` values must be unique within one `KnowledgeObject` — a
  duplicate sequence is **rejected**, not silently normalized by
  insertion order, so section ordering never depends on an implicit
  construction-order accident.

Explicitly **not** implemented: lifecycle transition legality, "current
version" resolution, applicability matching/scoring, semantic similarity,
or retrieval ranking. See §7.

---

## 7. Non-goals of this phase

5.1A deliberately does not build:

- ingestion (no PDF/DOCX reader, no SharePoint/GCS/Drive/Confluence
  connector, no document parser/chunker) — 5.1C/5.1D
- automatic section segmentation — 5.1D
- storage (no repository implementation, no SQL/vector schema, no cache)
  — 5.1F
- applicability matching/evaluation/ranking outcomes — 5.1B
- version governance (transition rules, "current version" resolution,
  supersession traversal) — 5.1E
- retrieval, ranking, embeddings, or any RAG pipeline — 5.1G
- provenance enrichment, snippet extraction, citation rendering, or
  evidence validation against real retrieved content — 5.1H
- generic agent-facing KM tools — 5.1I
- any agent integration (Incident Manager or otherwise) — 5.1J
- the future Context Engineering Layer, Troubleshooting Manager,
  Troubleshooting State, or next-best-diagnostic-action reasoning — all
  FUTURE, outside Phase 5.1 entirely (see
  `docs/TROUBLESHOOTING_STRATEGY.md` and `docs/AGENT_CONTRACT.md` §2)

---

## 8. Mapping to later 5.1 sub-phases

```text
Knowledge Sources
        ↓
Ingestion / Normalization              5.1C / 5.1D
        ↓
Knowledge Objects                      5.1A (this phase)
        ↓
Metadata / Applicability               5.1A shape / 5.1B evaluation
        ↓
Lifecycle / Version Governance         5.1A values / 5.1E rules
        ↓
Repository                             5.1F
        ↓
Retrieval / Ranking                    5.1G
        ↓
Evidence / Provenance                  5.1A primitives / 5.1H behavior
        ↓
Generic Knowledge Tools                5.1I
        ↓
Any Agent (first: Incident Manager)    5.1J
```

---

## 9. Troubleshooting-strategy alignment

`docs/TROUBLESHOOTING_STRATEGY.md` is a NON-NEGOTIABLE product principle.
5.1A satisfies it by providing the typed primitives the future
troubleshooting loop needs to eventually distinguish:

- which document is being used (`KnowledgeObject.knowledge_id`, `title`)
- what type of knowledge it is (`document_type`)
- which version it is (`version` / `version_label`)
- whether it is Candidate / Approved / Archive (`lifecycle_status`)
- where it came from (`source`)
- what part/section is relevant (`sections`, `KnowledgeSection`)
- what metadata describes it (`metadata`)
- what applicability information is attached to it (`applicability`)
- what evidence/provenance can later be validated
  (`KnowledgeEvidenceReference`)

5.1A does **not** implement the troubleshooting loop itself — no
Troubleshooting Manager, Troubleshooting State, Context Engineering
runtime, or next-best-diagnostic-action reasoning exists in this package.
It only ensures the domain model has stable, typed shapes for those
future capabilities to build against.

---

## 10. Dependency boundary

`backend/knowledge/domain/` must remain independently importable and
testable, with no dependency on any individual agent or on ADK/Gemini —
enforced by `backend/tests/knowledge/test_dependency_boundary.py` (a
static `ast`-based import check plus a fresh-subprocess import check),
not merely a comment. It must never import `google.adk`, `google.genai`,
`backend.agents`, or `backend.tools.teams`.

The governing question for every future extension of this package:

> **Can the Knowledge layer still work without knowing Incident Manager
> exists?**

If the answer becomes no, the architecture is too coupled.

---

## 11. Phase 5.1B — Metadata + Applicability

5.1B answers: **"Given a governed `KnowledgeObject` and some known
operational facts, does this knowledge actually apply?"** —
`backend/knowledge/domain/applicability.py`, plus small additive
hardening of §3's `KnowledgeMetadata`/`Applicability` (§11.10 below).

**Core principle:** SEMANTICALLY RELEVANT does not automatically mean
OPERATIONALLY APPLICABLE. A document can be topically similar to a
problem while still not applying — wrong vendor, release, node type, or
customer. 5.1B exists so that distinction is made deterministically,
never left to the model.

### 11.1 Metadata vs. Applicability

Two different concerns, never mixed:

| | `KnowledgeMetadata` | `Applicability` |
|---|---|---|
| Question it answers | What describes this knowledge? | Where/when is this knowledge valid? |
| Example | `owner="RAN Engineering"`, `tags=["upgrade"]` | `vendor=["Ericsson"]`, `release=["24.Q2"]` |
| Drives applicability evaluation? | **No** | **Yes** |

Applicability dimensions are never promoted into `KnowledgeMetadata.attributes`,
and metadata attributes are never read by `evaluate_applicability`.

### 11.2 Dimension names are open and domain-agnostic

**Applicability dimension names are open and domain-agnostic. The Generic
KM layer performs only deterministic syntactic normalization and
matching. Domain-specific vocabularies/taxonomies, if ever needed, belong
outside the generic applicability evaluator.**

There is no canonical/recommended dimension vocabulary constant anywhere
in `backend/knowledge/domain/` — not even a non-enforced, advisory one.
`evaluate_applicability` and `ApplicabilityContext` know nothing about
what a dimension key semantically represents; they only normalize and
compare whatever keys/values are supplied. `vendor`, `technology`,
`hardware_family`, `customer_segment`, `deployment_type`, or any
dimension invented later all work identically, with zero code change —
the names used throughout this document (`vendor`, `technology`,
`release`, ...) are illustrative examples only, never a runtime
constraint. A hardcoded vocabulary — even an advisory, non-enforced one —
would still bake business/domain assumptions into the generic KM runtime,
which this layer must not do (see §2's "Generic KM purpose" and the
troubleshooting-strategy invariant in §9).

### 11.3 Normalization semantics

- **Keys** (`normalize_dimension_key`, `models.py`): trim, casefold,
  spaces/hyphens → underscore, collapse repeated separators. `"Node
  Type"` / `"node-type"` / `"node_type"` all normalize to `node_type`.
  Two raw keys normalizing to the same canonical key have their value
  lists merged. A key normalizing to `""` is rejected.
- **Values** (`normalize_dimension_value`): trim + casefold, used only
  for comparison — the stored value keeps its original (trimmed) casing.
  `"Ericsson"` and `"ericsson"` compare equal; storage keeps whichever
  was given first.
- No fuzzy/semantic aliasing exists or is planned for this phase (e.g.
  `"tech"` never implicitly means `"technology"`) — normalization is
  syntax only.
- An empty allowed-value list (`{"vendor": []}`) is **rejected**, never
  treated as a wildcard. A blank value is rejected. Duplicate normalized
  values within one dimension are **deduped**, keeping first-seen casing
  and order — this applies to both `Applicability.dimensions` and
  `ApplicabilityContext.dimensions` via the same shared function
  (`normalize_applicability_dimensions`).

### 11.4 Multi-value semantics

- **Within one dimension: OR.** `vendor=["Ericsson", "Nokia"]` matches a
  context with `vendor=["Nokia"]`.
- **Across dimensions: AND.** Knowledge requiring `vendor=Ericsson` AND
  `technology=5G` against a context with `vendor=Ericsson` but
  `technology=4G` is `NOT_APPLICABLE` overall — one satisfied dimension
  never compensates for another that explicitly conflicts.

### 11.5 `ApplicabilityContext`

The generic, agent-independent input: known operational applicability
facts, shaped exactly like `Applicability.dimensions`
(`dict[str, list[str]]`), normalized the same way. It is **not** the
future Context Engineering Layer, Troubleshooting State, Case Context, an
ADK object, or retrieval input — only "a set of known applicability
dimensions."

### 11.6 `ApplicabilityOutcome` values

- **`MATCH`** — every constrained dimension has known context and at
  least one allowed value overlaps.
- **`NOT_APPLICABLE`** — at least one constrained dimension has known
  context that explicitly conflicts with every allowed value. **An
  explicit conflict always dominates** — even alongside matching
  dimensions.
- **`PARTIAL_MATCH`** — at least one constrained dimension matches, and
  every other constrained dimension is `UNKNOWN` (no `NOT_APPLICABLE`
  present). An overall-only outcome — never returned for a single
  dimension.
- **`UNKNOWN`** — the object has applicability constraints, but none of
  them can currently be evaluated because context is absent for all of
  them. Missing context is never guessed into a match.
- **Unconstrained knowledge** (`Applicability.dimensions == {}`)
  evaluates `MATCH` with no per-dimension detail — this means only "no
  applicability rule excludes this knowledge," never semantic relevance
  or lifecycle authority.

### 11.7 Overall evaluation algorithm (`evaluate_applicability`)

```text
1. No applicability constraints           -> MATCH
2. Evaluate every constrained dimension    (MATCH / NOT_APPLICABLE / UNKNOWN each)
3. Any dimension NOT_APPLICABLE            -> overall NOT_APPLICABLE
4. All constrained dimensions MATCH        -> overall MATCH
5. Some MATCH, rest UNKNOWN                -> overall PARTIAL_MATCH
6. No dimension MATCH (all UNKNOWN)        -> overall UNKNOWN
```

No fuzzy score or confidence percentage is computed anywhere — 5.1G may
later use the outcome as one filtering/ranking signal, but this phase
produces only the four discrete outcomes above.

### 11.8 Per-dimension detail

`ApplicabilityEvaluation.dimension_results` (ordered deterministically by
dimension name) holds one `ApplicabilityDimensionEvaluation` per
constrained dimension: `dimension`, `required_values`, `observed_values`,
`matched_values` (the required-side values that overlapped, in the
knowledge object's own original casing/order), and that dimension's own
outcome (`MATCH`/`NOT_APPLICABLE`/`UNKNOWN` only — `PARTIAL_MATCH` is
never a per-dimension value).

### 11.9 Lifecycle and version independence

- **Lifecycle independence:** `LifecycleStatus` plays no part in
  applicability evaluation — a `CANDIDATE`, `APPROVED`, or `ARCHIVE`
  object with identical `Applicability` produces the identical outcome.
  Whether `APPROVED` should be preferred over `CANDIDATE` is a 5.1E
  (lifecycle/version governance) and 5.1G (retrieval/ranking) concern,
  never mixed into this evaluator.
- **Document version vs. operational release:** `KnowledgeVersion.label`
  (the document's own revision, e.g. `"4.2"`) and
  `Applicability.dimensions["release"]` (the target operational software
  release, e.g. `"24.Q2"`, if a caller chooses that dimension name) are
  unrelated concepts. `evaluate_applicability` takes only `Applicability`
  and `ApplicabilityContext` as parameters — there is no code path
  through which a `KnowledgeVersion` could influence its result.

### 11.10 Metadata hardening (additive change to the 5.1A `KnowledgeMetadata` model)

- A blank tag is **rejected** (`ValueError`).
- A duplicate tag (case-insensitive) is **deduped**, keeping first-seen
  casing and order — unlike a blank tag, an accidental repeat is not
  itself invalid input.
- A blank metadata attribute key is **rejected**.
- Mutable defaults (`tags`, `attributes`) remain isolated between
  instances (already true via `Field(default_factory=...)`, unchanged).

### 11.11 Non-goals of this phase

5.1B deliberately does not build: fuzzy/similarity/substring/regex/
wildcard matching; release ranges or version arithmetic; hierarchical
taxonomy matching; model-based ("ask Gemini") applicability judgment;
retrieval, ranking, or embeddings; lifecycle transition workflows or
"current version" resolution (5.1E); storage (5.1F); ingestion (5.1C/
5.1D); generic agent-facing tools (5.1I) or agent integration (5.1J); or
any Context Engineering runtime.

---

## 12. Phase 5.1C — Generic Ingestion Boundary

5.1C answers: **"How can knowledge from ANY future source enter the
Generic KM pipeline through one stable, source-agnostic boundary?"** —
`backend/knowledge/ingestion/`. It does not integrate any real source
(SharePoint/GCS/Drive/Confluence/local files/...); it defines the
contract every future source adapter must satisfy.

```text
External Source
      ↓
Source-specific adapter           (future, outside Generic KM)
      ↓
GENERIC INGESTION BOUNDARY        5.1C (this section)
      ↓
IngestedKnowledgeDocument (normalized, UNSEGMENTED, pre-governance)
      ↓
Structured content processing     5.1D
      ↓
KnowledgeSection[] / KnowledgeObject
      ↓
governance / repository / retrieval later (5.1E / 5.1F / 5.1G)
```

### 12.1 Source-agnostic architecture

Different source systems must all produce the *same* generic ingestion
document contract. There is **no runtime enum, registry, or list of
supported source systems anywhere in this package** —
`KnowledgeSource.source_system` (reused unchanged from §3) remains an
arbitrary non-empty string end to end. `sharepoint`, `gcs`,
`enterprise_repo_x`, and a name invented tomorrow are all handled
identically, with zero code change — see
`backend/tests/knowledge/test_ingestion_source_agnostic.py`, which
statically proves no such vocabulary exists, in addition to sampling
several arbitrary source names at runtime.

### 12.2 `IngestedKnowledgeDocument`

The normalized output every future source adapter must produce, BEFORE
structural segmentation and BEFORE lifecycle governance:

```text
IngestedKnowledgeDocument
├── source: KnowledgeSource                    (reused unchanged, §3)
├── title: str                                  (non-blank)
├── content: str                                 (non-blank, unsegmented, no size limit)
├── knowledge_id_hint: str | None                 (non-blank if present -- a HINT, not the final knowledge_id)
├── document_type_hint: KnowledgeDocumentType | None
├── version_hint: KnowledgeVersion | None          (reused unchanged, §3 -- the DOCUMENT's revision)
├── metadata: KnowledgeMetadata                     (reused unchanged, §3/§11)
├── applicability: Applicability                     (reused unchanged, §3/§11 -- transported, never evaluated)
├── media_type: str | None                            (non-blank if present, free-form)
├── source_revision: str | None                        (non-blank if present, opaque, uninterpreted)
└── observed_at: datetime | None                        (carried through only)
```

Every nested type is reused directly from §3/§11 — no
`SharePointKnowledgeSource`, no duplicate metadata/applicability model,
no ingestion-specific `KnowledgeVersion` fork. `KnowledgeSource` identity
survives ingestion unchanged (never rewritten or manufactured by this
boundary) — see `test_ingestion_boundary_invariants.py`'s
source-identity-preservation tests.

### 12.3 Unsegmented content, deliberately

`content` is the source document's normalized textual payload — **not**
yet `KnowledgeSection[]`, not chunked, not parsed into
Purpose/Preconditions/Steps/Rollback/..., not summarized, not
semantically classified. Automatic segmentation is 5.1D's job entirely;
`IngestedKnowledgeDocument` has no `sections` field at all, and no
`segment()`/`chunk()`/`create_sections()` method or function exists
anywhere in this package (statically verified).

### 12.4 Identity, type, and version hints

- **`knowledge_id_hint`** — a logical id the *source* already knows, if
  any. A hint only: never assumed to equal the eventual governed
  `KnowledgeObject.knowledge_id`, never derived by hashing content, and
  never treated as `source_id`. Final identity governance is later work.
- **`document_type_hint`** — the document type, if the source/adapter
  already knows it. Never inferred here — no classification logic, no
  model call.
- **`version_hint`** — the source's own document revision (e.g.
  `"4.2"`), using the exact same `KnowledgeVersion` shape `KnowledgeObject`
  uses. This is **not** the same concept as an operational software
  release recorded in `applicability`'s own dimensions (e.g.
  `"release": ["24.Q2"]`) — the 5.1B distinction between document version
  and operational release is preserved unchanged at this boundary too.

### 12.5 Metadata and applicability are transported, not evaluated

`metadata`/`applicability` reuse §3/§11's models unchanged. 5.1C never
evaluates applicability, never infers it from document text, and never
calls a model to do either — it only carries through whatever the
source/adapter supplies (or the empty default). 5.1B remains the sole
deterministic applicability evaluator.

### 12.6 INGESTED ≠ APPROVED

`IngestedKnowledgeDocument` has **no `LifecycleStatus` field** —
ingestion is not governance. A document entering the pipeline gains no
operational authority merely by arriving; whether it eventually becomes
`CANDIDATE`/`APPROVED`/`ARCHIVE` is entirely 5.1E's job. This package
does not import `LifecycleStatus` at all (statically verified), so there
is no code path through which ingestion could reference
`LifecycleStatus.APPROVED`.

### 12.7 Ingested content is data, not instruction

External content must not become executable/model instruction merely
because it was ingested. This is an architectural invariant preserved
here in documentation only — formal prompt-injection/untrusted-content
controls are Phase 4H's job, not this phase's; 5.1C builds no security
engine, trust score, or Model Armor integration.

### 12.8 `KnowledgeSourceAdapter`

A minimal structural `typing.Protocol`
(`backend/knowledge/ingestion/adapters.py`), matching the same pattern
already used elsewhere in this backend (e.g.
`backend/api/chat_service.py`'s `_Runner`): any object exposing an async
`fetch_documents()` method yielding `IngestedKnowledgeDocument` instances
satisfies it — no base class, registration, or plugin framework. No
concrete adapter (SharePoint/GCS/Drive/Confluence/local file/PDF/DOCX/...)
exists in this package; only test-only fakes prove the contract (see
`backend/tests/knowledge/test_ingestion_adapter_contract.py`). No
orchestration — scheduler, worker, queue, retry, pagination,
checkpoint/cursor — exists or is implied by this Protocol.

### 12.9 Separation from adjacent phases

- **5.1D (structured content processing)**: consumes
  `IngestedKnowledgeDocument.content` to produce `KnowledgeSection[]`.
  5.1C never produces sections itself.
- **5.1E (lifecycle/version governance)**: decides `LifecycleStatus`
  transitions and "current version" resolution for a governed
  `KnowledgeObject`. 5.1C never assigns a lifecycle status and performs
  no supersession/authority logic.
- **5.1F (repository/storage)**: persists the eventual `KnowledgeObject`.
  5.1C creates no repository, database table, cache, or index.
- **5.1G (retrieval/ranking)**: searches/ranks governed knowledge. 5.1C
  performs no search, ranking, or embedding of any kind.

### 12.10 Non-goals of this phase

5.1C deliberately does not build: any concrete source adapter or
source-specific extractor (PDF/DOCX/HTML/SharePoint REST/GCS download/
Confluence API/OCR/...); ingestion orchestration (scheduler, worker,
queue, retry, pagination, checkpoint/cursor); a source-system
enum/registry of any kind; raw connector credentials anywhere in the
domain contracts; document classification, duplicate detection, or
source-freshness comparison; and, as with every other Generic KM
sub-phase, storage, retrieval/embeddings, agent integration, or any
Context Engineering runtime.

---

## 13. Phase 5.1D — Structured Content Processing / Segmentation

5.1D answers: **"How do we transform one normalized, unsegmented
`IngestedKnowledgeDocument` into deterministic structured knowledge
sections without coupling the Generic KM layer to any document type,
source system, vendor, customer, technology, or operational domain?"** —
`backend/knowledge/processing/`.

```text
ANY SOURCE
    ↓
source-specific adapter                (future, outside Generic KM)
    ↓
IngestedKnowledgeDocument               5.1C
    ↓
STRUCTURED CONTENT PROCESSING           5.1D (this section)
    ↓
StructuredKnowledgeDocument (pre-governance)
    ↓
lifecycle/version governance            5.1E
    ↓
repository                              5.1F
    ↓
retrieval/ranking                       5.1G
```

### 13.1 `IngestedKnowledgeDocument` → `StructuredKnowledgeDocument`

`StructuredKnowledgeDocument` wraps the **exact, unmodified**
`IngestedKnowledgeDocument` it was produced from (`source_document`) plus
an ordered `sections: list[StructuredKnowledgeSection]`. Nothing about
5.1D rewrites, enriches, or drops any field of the original document —
`source`/`title`/`content`/hints/`metadata`/`applicability` all survive
processing byte-for-byte.

### 13.2 `StructuredKnowledgeSection` and local/pre-governance identity

```text
StructuredKnowledgeSection
├── section_key: str          -- LOCAL identity ("section-0000", ...), unique
│                                 within this document only, NOT KnowledgeSection.section_id
├── sequence: int              -- deterministic, non-negative, unique per document
├── heading: str | None          -- structural SYNTAX only, never semantic
├── heading_level: int | None      -- 1-6; set iff heading is set
├── content: str                    -- non-blank, verbatim body text
└── source_locator: str | None        -- "lines:<start>-<end>", 1-based inclusive
```

`section_key` is deliberately **not** `KnowledgeSection.section_id`, and
this section has no `knowledge_id` at all — final governed section
identity (5.1E/5.1F) may not exist yet at this point, since
`IngestedKnowledgeDocument.knowledge_id_hint` is only a hint. The local
key needs to be unique only within the document it was produced from; it
is not required to stay stable across a different revision of the same
source. It is assigned deterministically (`section-0000`, `section-0001`,
...) in document order — never a random UUID, never a timestamp.

### 13.3 Explicit structural segmentation, never arbitrary chunking

The reference processor, `HeadingStructureProcessor`, recognizes explicit
Markdown-style ATX heading **syntax** (`# text` through `###### text`)
already present in normalized text — this is syntax detection, not
semantic interpretation. It never fragments a document by a fixed
character/token count, sliding window, or paragraph-grouping heuristic.
If no trustworthy structural boundary is present anywhere in the
document, the entire content becomes **exactly one section** — verified
even for a multi-thousand-line document with no headings (see
`backend/tests/knowledge/test_processing_reference_processor.py`'s
`test_large_document_with_no_headings_is_never_chunked`).

### 13.4 Heading detection is syntax, never semantics

The processor treats `# Alpha`, `# Root Cause`, `# Bananas`, and
`# Something Invented In 2035` completely identically — it has zero
knowledge of what any heading text means, and there is no
`MOP_SECTIONS`/`RCA_SECTIONS`/`SOP_SECTIONS` dictionary, no fixed
semantic section-type enum, and no `if heading == "Rollback"`-style
branch anywhere in `backend/knowledge/processing/` (statically verified
via `ast`, checking real declared names and comparison operands — not a
raw text scan, which would false-positive on this document's own
illustrative examples). `document_type_hint` has **zero** effect on
segmentation: a MOP, an RCA, `OTHER`, or no hint at all are all processed
by the exact same rules (also statically verified — the processor never
references `document_type_hint` at all).

**Design note on adjacent empty headings**, since it resolves an apparent
tension worth documenting explicitly: a heading immediately followed by
another heading (or by end of document), with no body text between them,
produces **no section** for that heading — `content` must always be
non-blank (the same hard invariant `KnowledgeSection` already enforces in
§6), so a heading with nothing to attach to has nothing valid to emit. A
real nested-heading document, where each level carries at least some body
text, is unaffected and preserves every level. If literally every heading
in a document turns out empty this way (nothing emittable at all), the
whole, unmodified document falls back to one section — the same
"unknown/degenerate structure remains intact" principle applied when no
heading exists at all.

### 13.5 Content before the first heading

Introductory text preceding the first detected heading is preserved as
its own section with `heading=None`/`heading_level=None` — never
dropped, never merged into the first headed section.

### 13.6 Code/literal fence safety

A line whose stripped content starts with three backticks toggles a
fence state (open/close), with or without a following language tag.
While a fence is open, no line — however heading-shaped it looks — is
treated as a structural boundary; it is preserved as ordinary body
content. This uses a small deterministic state machine, not a Markdown
parser dependency (none was added).

### 13.7 Content fidelity

Body text is preserved verbatim: no summarizing, paraphrasing,
spell-correction, command normalization, whitespace rewriting, case
changes, or translation. Only the heading marker line itself (the `#`
run plus its text) is separated out of `content` into `heading`; the
line's original text is otherwise never altered.

### 13.8 Source locators

`source_locator` (`"lines:<start>-<end>"`, 1-based, inclusive) covers
exactly the original document lines assigned to that section's `content`
— never a source-system-specific locator (no SharePoint page id, PDF
coordinate, or Drive block id belongs in this generic contract; a future
source-specific adapter/extractor may preserve those separately, outside
this package). Locators are deterministic and repeat identically across
repeated processing of the same content.

### 13.9 No model, no governance, no fabricated identity

- **No Gemini/LLM of any kind** is used for segmentation, heading
  classification, semantic labeling, or summarization — processing is
  deterministic, local, synchronous Python (verified by the same
  dependency-boundary mechanism as §10, extended to this package).
- **`StructuredKnowledgeDocument` is strictly pre-governance**: no
  `LifecycleStatus` field exists anywhere in this package, no final
  governed `knowledge_id` or `KnowledgeSection.section_id` is fabricated,
  and processing output is never itself treated as a `KnowledgeObject`.
  5.1D never guesses a missing `document_type_hint` into `OTHER`, a
  missing `version_hint` into `"1.0"`, or a missing `knowledge_id_hint`
  into a generated id — those hints are carried through unchanged
  (possibly still absent) inside the preserved `source_document`.
- Metadata/applicability are never evaluated, mutated, or enriched during
  processing — 5.1B remains the sole applicability evaluator.

### 13.10 Non-goals of this phase

5.1D deliberately does not build: any arbitrary chunking by size/tokens;
semantic heading classification or a fixed section-type enum; any
document-type-specific or source-specific processor (`MopProcessor`,
`PdfProcessor`, ...); a Markdown AST/parser dependency; lifecycle
governance (5.1E); storage (5.1F); retrieval/ranking/embeddings (5.1G);
agent integration (5.1I/5.1J); or any Context Engineering runtime.

---

## 14. Phase 5.1E — Versioning + Lifecycle Governance

5.1E answers: **"When does structured knowledge become governed
knowledge, what lifecycle state is it in, and which version is
authoritative/current at a given time?"** — `backend/knowledge/governance/`.
Three explicit, separated responsibilities:

```text
StructuredKnowledgeDocument
        ↓
(A) GOVERNED MATERIALIZATION -- explicit governance assignment
        ↓
KnowledgeObject in CANDIDATE state
        ↓
(B) LIFECYCLE TRANSITIONS -- CANDIDATE -> APPROVED -> ARCHIVE
        ↓
(C) VERSION GOVERNANCE -- given a version family, resolve currentness
        ↓
repository (5.1F) / retrieval (5.1G)
```

### 14.1 (A) Governed materialization — hints are never authority

`materialize_candidate(structured_document, *, knowledge_id, document_type,
version, governed_at=None) -> KnowledgeObject` is the explicit boundary
where a pre-governance document becomes governed knowledge. `knowledge_id`/
`document_type`/`version` are **required, explicit keyword arguments** —
this function never reads `structured_document.source_document.
knowledge_id_hint`/`document_type_hint`/`version_hint` at all (statically
verified — a hint remains preserved in the underlying ingestion history,
but has no code path to become authoritative). There is no fallback that
invents a missing value: no random UUID for a missing id, no `OTHER` for
a missing type, no `"1.0"` for a missing version — a caller must supply
governed identity explicitly or the call fails.

`title`/`source`/`metadata`/`applicability` are preserved from the
structured document's own `source_document` exactly, unmutated and
unevaluated. Each `StructuredKnowledgeSection` becomes one
`KnowledgeSection`, preserving `sequence`/`heading`/`content`/
`source_locator`; `section_type` is left unset (5.1E never fabricates a
semantic type 5.1D never determined).

**Final section identity**: `KnowledgeSection.section_id` is assigned
deterministically by encoding the tuple `(knowledge_id, version.label,
section_key)` with a length-prefixed encoding — each component is
emitted as `"{len(component)}:{component}"`, concatenated with no
separator between components. This is built only from already-governed
identity and the 5.1D local `section_key`, never a random UUID, a
timestamp, or a hash of the section's own body content.

Length-prefixing (rather than joining components with a fixed delimiter
like `"::"`) is what makes the encoding collision-safe, not merely
deterministic: since each component is preceded by its own exact
character count, decoding is an unambiguous left-to-right walk (read
digits up to the next `:`, then read exactly that many characters as the
component, repeat) — so two *different* tuples can never produce the
*same* encoded string, no matter what characters any component contains,
including further `:` characters or digits that could otherwise be
mistaken for a delimiter or a length prefix. For example,
`("a::b", "c", "d")` encodes to `"4:a::b1:c1:d"` while `("a", "b::c",
"d")` encodes to `"1:a4:b::c1:d"` — both would collide under a plain
`"::".join(...)` scheme (both become `"a::b::c::d"`), but remain
distinct here. The scheme guarantees: same tuple → same id; different
tuple → different id; no UUID; no timestamp; no content hashing. Because
`version.label` is one of the three encoded components, two different
governed versions of the same `knowledge_id` never collide on final
section identity even when their local `section_key`s coincide.

### 14.2 Initial lifecycle state

A newly materialized `KnowledgeObject` always enters as `CANDIDATE` —
never `APPROVED`. This preserves INGESTED ≠ STRUCTURED ≠ CANDIDATE ≠
APPROVED ≠ CURRENT as five genuinely distinct concepts throughout the
pipeline. Approval is always a separate, later, explicit transition.

### 14.3 (B) Lifecycle transitions

`transition_lifecycle(knowledge_object, target_status, *,
transitioned_at=None) -> KnowledgeObject` is a pure function: it never
mutates its input, never persists anything, never emits an event, and
never touches any OTHER `KnowledgeObject` (approving one version never
auto-archives a predecessor — see §14.7). The one authoritative
transition table:

```text
CANDIDATE -> APPROVED   (allowed)
APPROVED  -> ARCHIVE    (allowed)
```

Every other transition — `CANDIDATE → ARCHIVE`, `APPROVED → CANDIDATE`,
`ARCHIVE → APPROVED`, `ARCHIVE → CANDIDATE`, and any same-state
"transition" — raises `InvalidLifecycleTransitionError`. `approve_version`/
`archive_version` are thin convenience wrappers around the exact same
table — there is no second, divergent lifecycle implementation anywhere.
`transitioned_at` is always an explicit caller-supplied input; this
package never calls `datetime.now()`/`datetime.utcnow()`/`time.time()`
(statically verified), so identical inputs always produce identical
output. Document type never affects lifecycle semantics — MOP, SOP, RCA,
KB, and OTHER are governed by the exact same table (verified, and
statically confirmed there is no per-type branch or per-type governance
class anywhere in this package).

### 14.4 (C) Version labels are opaque

**VERSION LABEL ≠ VERSION ORDER.** `KnowledgeVersion.label` is never
compared numerically, lexically, or via any parsing/sorting scheme to
determine which version is newer — `"1.9"` vs `"1.10"`, `"Rev-A"` vs
`"Rev-Z"`, or a plainly misleading pair where the alphabetically-later
label is actually the OLDER version, all resolve correctly because
currentness comes entirely from explicit `supersedes`/`superseded_by`
declarations, `effective_from`/`effective_to`, and `LifecycleStatus` —
never the label itself (statically verified: no ordering comparison is
ever applied to a `.label` attribute anywhere in this package).

### 14.5 Explicit supersession graph

`resolve_current_version`/`resolve_supersession_chain` build a
normalized, in-memory, directed relationship graph from
`KnowledgeVersion.supersedes`/`superseded_by` declarations for one
supplied `list[KnowledgeObject]` version family (no repository — the
caller supplies the family; nothing is fetched). A relationship declared
through either field is recognized identically; a version family that is
structurally invalid fails closed by raising, never by guessing:

- more than one `knowledge_id` present → `MixedKnowledgeIdError`
- a duplicate `version.label` → `DuplicateVersionLabelError`
- a `supersedes`/`superseded_by` reference to a label absent from the
  supplied family → `UnknownSupersessionReferenceError`
- a direct or transitive cycle → `SupersessionCycleError`

### 14.6 Effectiveness (`is_effective`)

`is_effective(version, as_of)` is independent of `LifecycleStatus` — a
version can be `APPROVED` but not yet effective, or effective-dated but
still only `CANDIDATE`; neither alone makes it current (**APPROVED ≠
CURRENT**, **CURRENT ≠ APPLICABLE** — applicability, 5.1B, is never
invoked here). Both bounds are inclusive
(`effective_from <= as_of <= effective_to`); an absent bound is
unbounded on that side.

### 14.7 Current-version resolution

`resolve_current_version(versions, as_of) -> CurrentVersionResolution`
returns a typed result (`status`: `RESOLVED` / `NOT_FOUND` / `AMBIGUOUS`,
never a bare `Optional[KnowledgeObject]`, which could not distinguish
"nothing is current" from "governance data does not say which one is").
Only `APPROVED` + effective versions are ever eligible to be returned as
current — `CANDIDATE` and `ARCHIVE` are never current.

**The permanent-supersession rule** (the single rule behind every
scenario below): a successor permanently excludes every version it
(transitively) supersedes the moment it "activates" — genuinely became
approved AND effective, not merely approved. This is deterministic
governance behavior derived only from explicit lifecycle status and
effectiveness timestamps — never inferred from version labels, creation
order, or model reasoning. Once activated, the exclusion holds forever
afterward, regardless of what later happens to the successor itself:

- **Future-effective successor**: a supersedes-declaring successor whose
  `effective_from` has not yet arrived has not activated — the
  predecessor remains current until the successor's own effective date.
- **Candidate successor**: a `CANDIDATE` successor has never been
  approved, so it never activates a permanent transfer — the predecessor
  remains current.
- **Archived successor that became effective before being archived**: a
  successor that genuinely activated (was approved AND its effective
  window had begun) permanently retires its predecessor, even after the
  successor itself later becomes `ARCHIVE` — the predecessor remains
  permanently excluded, the archived successor itself is never returned
  as current, and if no valid replacement exists the result is
  `NOT_FOUND` — never a resurrected predecessor.
- **Archived successor that was archived *before* its effective window
  ever began**: this successor never activated at all, so it must not
  suppress the predecessor — the predecessor may remain current. This
  holds permanently: because `ARCHIVE` cannot transition back to
  `APPROVED`, such a successor can never activate later either, so the
  predecessor remains correctly current even after the calendar
  subsequently passes the abandoned successor's former `effective_from`.
- **When available governance history cannot prove which of the above
  applies** (for example, no transition timestamp was ever recorded for
  the archive transition): resolution fails conservatively — the
  predecessor is treated as excluded rather than risking the silent
  resurrection of potentially obsolete knowledge. The safe result in this
  case is `NOT_FOUND`, not a guess in either direction.

The live `lifecycle_status` of an `ARCHIVE` object alone cannot
distinguish the last two cases from each other, since both are currently
`ARCHIVE`; the distinction is made from the archive transition's own
recorded timestamp compared against the successor's `effective_from` —
no separate lifecycle-history log is introduced to make this
determination.

Approving or archiving one `KnowledgeObject` never automatically mutates
another — there is no auto-archive-the-predecessor behavior anywhere;
currentness is entirely a resolution-time computation over the supplied
family, never a side effect of a lifecycle transition (§14.3).

**Ambiguity**: two eligible, non-excluded versions with no relationship
to each other, or a branching supersession graph (two versions both
superseding the same predecessor, neither superseding the other) both
produce `AMBIGUOUS` — never resolved by label, creation time, list
order, title, or document type.

### 14.8 Non-goals of this phase

5.1E deliberately does not build: persistence/repository access (5.1F —
this package works entirely on caller-supplied, in-memory objects; no
version family is ever fetched); retrieval, ranking, or embeddings
(5.1G); applicability evaluation (5.1B remains sole owner); agent
integration (5.1I/5.1J); any Context Engineering runtime; or any
document-type-specific governance implementation (`MopGovernanceService`
and equivalents do not, and must not, exist).

---

## 15. Phase 5.1F — Knowledge Repository Abstraction

5.1F answers: **"How do governed `KnowledgeObject`s get persisted and
retrieved through one stable repository contract without coupling the
Generic KM domain to a database, vector engine, agent, document type, or
deployment platform?"** — `backend/knowledge/repository/`.

```text
KnowledgeObject (governed, from 5.1E)
        ↓
KNOWLEDGE REPOSITORY                 5.1F (this section)
        ↓
Retrieval / ranking                  5.1G
        ↓
Provenance                           5.1H
        ↓
Agent-facing tools                   5.1I
```

### 15.1 `KnowledgeRepository` — the generic contract

A storage-agnostic `typing.Protocol` (`repository/contracts.py`, the
same structural-Protocol pattern already used for
`KnowledgeSourceAdapter`/`KnowledgeContentProcessor`), exposing exactly
five operations:

```text
add(knowledge_object)          -- persist a NEW governed version
get(knowledge_id, version_label)  -- retrieve the EXACT governed version, or None
replace(knowledge_object)           -- persist updated state for an EXISTING version
list_versions(knowledge_id)           -- the COMPLETE, unfiltered version family
list_all()                              -- the COMPLETE, unfiltered governed corpus
```

`contracts.py` imports nothing beyond `KnowledgeObject` and stdlib
typing — never `sqlite3`, SQLAlchemy, or any storage/cloud technology
(statically verified) — so a future `PostgresKnowledgeRepository`/
`CloudSQLKnowledgeRepository` can exist without ever changing
`KnowledgeRepository` or any of its callers.

**REPOSITORY ≠ RETRIEVAL ENGINE.** This contract answers "give me the
exact governed version I already know the identity of" or "give me
every version of this knowledge_id" — never "find knowledge relevant to
X". No search, ranking, or embedding of any kind exists here; that is
5.1G.

### 15.2 `SQLiteKnowledgeRepository` — the local implementation

The one pragmatic local implementation this phase builds
(`repository/sqlite.py`), backed by async SQLAlchemy — the same pattern
already established for local persistence elsewhere in this backend
(`backend/cases/db.py`) — rather than a new dependency introduced for
this phase. It owns its own engine, session factory, and
`DeclarativeBase`/table set, never shared with Case or ADK session
tables. `database_url` is always an explicit constructor argument — no
environment-specific or developer-machine path is ever hardcoded.

The interface (`KnowledgeRepository`) and this implementation
(`SQLiteKnowledgeRepository`) are deliberately named and kept distinct —
future `PostgresKnowledgeRepository`/`CloudSQLKnowledgeRepository`
implementations are possible without any caller changing.

### 15.3 Logical version identity

The logical identity of one governed version is always the pair
`(knowledge_id, version.label)` — stored as a composite PRIMARY KEY on
`SQLiteKnowledgeRepository`'s single, generic table
(`slopanoc_knowledge_objects`), so uniqueness is enforced by storage
itself, not merely by a Python "check then insert". There is no
document-type-specific table (no `mop_table`/`sop_table`/`rca_table`) —
every `KnowledgeDocumentType` uses the exact same table and code path.

### 15.4 add vs. replace semantics

- **`add`** persists a brand-new `(knowledge_id, version.label)`. If that
  exact identity already exists, it raises
  `KnowledgeVersionAlreadyExistsError` — the underlying storage
  integrity violation (a primary-key conflict) is caught and translated
  into this explicit domain error; `add` never silently overwrites.
- **`replace`** persists updated governed state for an
  ALREADY-EXISTING identity — e.g. the new `KnowledgeObject`
  `governance/service.py`'s `approve_version`/`archive_version` returns
  after a lifecycle transition. If the identity does not already exist,
  it raises `KnowledgeVersionNotFoundError` — `replace` never upserts,
  never creates. Neither operation ever changes the logical identity
  itself (`knowledge_id`/`version.label`); changing identity means
  `add`-ing a different version, never mutating a storage key.

The repository never decides *whether* a transition was valid — it only
persists whatever already-governed object `governance/service.py`
produced. `governance/versioning.py`'s `resolve_current_version`/
`resolve_supersession_chain` are never called from inside this package
either (statically verified) — the intended composition is entirely in
caller code:

```text
versions = repository.list_versions(knowledge_id)
resolution = resolve_current_version(versions, as_of)
```

### 15.5 Complete version-family retrieval

`list_versions(knowledge_id)` returns **every** persisted version —
`CANDIDATE`, `APPROVED`, and `ARCHIVE` alike — with no lifecycle,
effective-date, applicability, or document-type filtering. This is what
lets 5.1E's own resolution operations receive the complete family they
require. Any ordering `SQLiteKnowledgeRepository` applies (alphabetical
by `version_label`, for reproducible output) is **presentation/storage
determinism only** and carries **zero governance meaning** — no caller
may infer currentness or precedence from list position. **VERSION LABEL
≠ VERSION ORDER** remains true inside the repository exactly as it does
in `governance/` (§14.4) — nothing here compares, sorts, or interprets a
version label.

### 15.5a Source-of-truth corpus enumeration (`list_all`)

`list_all()` returns **every** governed `KnowledgeObject` persisted in
the repository — every `knowledge_id`, every version, every
`LifecycleStatus` alike, with no query, `top_k`, document-type filter,
lifecycle filter, or applicability filter accepted. Like `list_versions`,
this is identity enumeration, not search: it performs no ranking, no
filtering, no currentness resolution, and no applicability evaluation.
Each row is reconstructed through the exact same corruption-safe path as
`get`/`list_versions`, so a corrupt row still fails closed with
`KnowledgeRepositoryCorruptionError`. Any ordering
`SQLiteKnowledgeRepository` applies (by `(knowledge_id, version_label)`,
for reproducible output) is presentation/storage determinism only,
carrying zero governance or relevance meaning — identical to
`list_versions`'s own ordering discipline (§15.5).

```text
KnowledgeRepository.list_all()
        ↓
authoritative governed corpus
        ↓
future derived retrieval/search index (5.1G) can be built/rebuilt
```

This exists so a future derived retrieval/search index can be built or
rebuilt directly from the authoritative repository, without importing
`SQLiteKnowledgeRepository` or knowing anything about its table/schema —
5.1F provides only this source-of-truth enumeration; 5.1G decides how
candidate retrieval/indexing actually works on top of it.

### 15.6 Archive ≠ delete

There is no delete/purge operation anywhere in `KnowledgeRepository` or
`SQLiteKnowledgeRepository` (statically verified) — an `ARCHIVE` record
remains fully persisted and retrievable via `get`/`list_versions`,
exactly like any other lifecycle state. History remains available for
supersession resolution, audit, and future provenance work.

### 15.7 What gets persisted, and how

The complete `KnowledgeObject` aggregate — every field from §3/§6/§14,
including nested `KnowledgeVersion`, `KnowledgeSource`,
`KnowledgeMetadata`, `Applicability`, and the full `KnowledgeSection[]`
list — is stored as one JSON payload
(`knowledge_object.model_dump_json()`), reconstructed on read via
`KnowledgeObject.model_validate_json(...)` — the established Pydantic
serialization API, never a hand-built dict, never `pickle`. Round-trip
therefore always re-runs the object's own domain validation, and no
field can silently disappear.

**Corruption fails closed.** If a stored payload is not valid JSON, does
not validate as a `KnowledgeObject`, or validates but its own
`knowledge_id`/`version.label` disagrees with the storage key it was
read under, `get`/`list_versions` raise
`KnowledgeRepositoryCorruptionError` — never a partial object, never a
dropped field, never a fabricated default, never a raw dict.

### 15.8 Source of truth

**The governed `KnowledgeRepository` is the source of truth for
`KnowledgeObject` aggregates.** Any future semantic/vector index (5.1G
or later) is derived infrastructure — optional, rebuildable, and
non-authoritative. **VECTOR INDEX ≠ SOURCE OF TRUTH**: if an index and
this repository ever disagree, the repository wins. **REPOSITORY ≠
RETRIEVAL ENGINE**: this contract never answers "what's relevant", only
"what governed knowledge exists" (`list_all`, §15.5a) or "give me the
exact/complete identity I asked for" (`get`/`list_versions`). No
vector/embedding dependency of any kind exists in this package
(statically verified). `list_all` (§15.5a) is what makes this concrete,
not merely aspirational: it is the one operation a future derived index
needs to enumerate the entire authoritative corpus and rebuild itself
from scratch, without ever depending on `SQLiteKnowledgeRepository` or
its schema.

### 15.9 Non-goals of this phase

5.1F deliberately does not build: search, ranking, or any retrieval
capability (5.1G); a vector/embedding index of any kind; lifecycle
transitions or current-version resolution (both remain exclusively
`governance/`'s job); applicability evaluation (5.1B); a delete/purge
operation; a parallel persistence system for `IngestedKnowledgeDocument`/
`StructuredKnowledgeDocument`; a production cloud database deployment
(Cloud SQL/Postgres/IAM/Secrets Manager); a migration framework (schema
creation is idempotent `create_all`, not a versioned migration tool);
agent integration (5.1I/5.1J); or any Context Engineering runtime.

## 16. Phase 5.1G — Retrieval + Ranking

5.1G answers exactly one question: **"out of all governed knowledge in
the repository, which small, bounded set of sections is actually useful
to consider for this query, while respecting currentness and
applicability?"** It is a **context-reduction boundary**: it exists so
nothing downstream (an agent, a future Context Engineering layer, or a
Gemini call) ever receives the full repository corpus. `backend/knowledge/retrieval/`
composes three already-frozen operations —
`KnowledgeRepository.list_all()` (§15.5a), `governance.versioning.resolve_current_version`
(§14.7), and `domain.applicability.evaluate_applicability` (§11.7) —
unchanged, adding exactly one new capability: deterministic lexical
relevance scoring and ranking.

### 16.1 The six-way separation of concerns

This phase makes explicit a distinction implicit since 5.1E/5.1F:

```text
REPOSITORY    = what governed knowledge exists            (5.1F)
GOVERNANCE    = which version is authoritative/current     (5.1E)
APPLICABILITY = whether knowledge applies to known facts   (5.1B)
RETRIEVAL     = which eligible knowledge is relevant        (5.1G, this phase)
PROVENANCE    = proof of exactly which evidence was used    (5.1H, later)
REASONING     = interpretation of that bounded evidence      (later)
```

Each responsibility lives in exactly one package and is never
re-implemented by another. `backend/knowledge/retrieval/` calls the
first three unchanged; it does not touch provenance or reasoning at all.

### 16.2 `KnowledgeRetrievalQuery` — the input contract

One retrieval request: `query_text` (non-blank), an optional
`applicability_context` (defaults to an empty, unconstrained
`ApplicabilityContext`, §11.5), an explicit `as_of: datetime` (never the
wall clock — the same query always resolves currentness identically),
and a positive `limit`. No agent, ADK, or Gemini type appears anywhere in
this contract.

### 16.3 `KnowledgeRetrievalItem` — one bounded result unit

Section granularity, never document granularity: one item is exactly one
`KnowledgeSection` from the governed-current `KnowledgeObject` of its
family, carried through **unmodified** — never rewritten, summarized, or
re-chunked. Following the same discipline `KnowledgeContextItem`
established in 5.1A, this contract reuses `KnowledgeSection` and
`KnowledgeSource` directly rather than flattening their fields.  Each
item also carries `knowledge_id`, `document_type`, `title`,
`version_label`, `lifecycle_status` (all copied from the owning current
`KnowledgeObject`, for downstream context, never for ranking), an
`applicability_outcome` (`MATCH`/`PARTIAL_MATCH`/`UNKNOWN` — `NOT_APPLICABLE`
sections never reach this contract), and a `relevance_score` in
`[0.0, 1.0]`. This is **not** a provenance/evidence assertion (5.1H owns
that) and **not** the future Knowledge Context contract — it is
retrieval's own, narrower output shape.

### 16.4 `KnowledgeRetrievalResult` and family-level diagnostics

The typed, bounded output: `items` (never exceeding the query's `limit`)
and `excluded_families` — a small, generic, closed-vocabulary list of
`KnowledgeRetrievalDiagnostic` records, one per `knowledge_id` family
whose governance state could not be safely resolved
(`current_version_ambiguous` or `invalid_version_family`, with a short,
safe `detail` string — an exception class name, or the competing version
labels — never raw document content or a stack trace). An empty `items`
list with no diagnostics is a **normal, successful** "nothing matched"
result, not an error — there is no error/success field on this contract
at all.

`NOT_FOUND` (no current version, e.g. a `CANDIDATE`-only family, or a
future-effective `APPROVED` version) and `NOT_APPLICABLE` (an explicit
applicability conflict) are both **silent, expected exclusions** — never
diagnostics. A diagnostic exists only for the narrower case of "a family
existed, but its governed state itself could not be trusted"
(`AMBIGUOUS`, or a structurally invalid family per §14.7/§14.4's
`InvalidVersionFamilyError` subclasses).

### 16.5 Eligibility policy — currentness and applicability, reused unchanged

For each `knowledge_id` family in the repository's full corpus
(`list_all()`, §15.5a), grouped by `knowledge_id` only (never by title,
source, or label similarity):

1. Call `resolve_current_version` (§14.7) unchanged. `NOT_FOUND` → skip
   silently. `AMBIGUOUS` → skip, record a `current_version_ambiguous`
   diagnostic. An `InvalidVersionFamilyError` subclass → skip, record an
   `invalid_version_family` diagnostic (`detail` = the exception class
   name). Only `RESOLVED` continues.
2. Call `evaluate_applicability` (§11.7) unchanged against the resolved
   current object's `Applicability` and the query's
   `applicability_context`. `NOT_APPLICABLE` → skip silently (no
   diagnostic — this is a normal, expected exclusion, not a governance
   failure). `MATCH`, `PARTIAL_MATCH`, and `UNKNOWN` are **all eligible**
   — the outcome is retained verbatim on every resulting item, **never**
   silently upgraded to `MATCH`.

Neither operation is re-implemented, duplicated, or approximated inside
`retrieval/` — both are imported and called exactly as 5.1E/5.1B already
define them.

### 16.6 `KnowledgeRelevanceScorer` and `TokenOverlapRelevanceScorer`

`KnowledgeRelevanceScorer` is a minimal `Protocol`: `score(query_text,
knowledge_object, section) -> float` in `[0.0, 1.0]`, deterministic,
synchronous, local — no network or model call is implied by the shape
itself. This keeps `KnowledgeRetrievalService` independent of the
scoring implementation: a future semantic/embedding-backed scorer could
satisfy the same Protocol without the orchestration changing at all,
provided it preserves the same currentness/applicability semantics
already enforced around it.

The one Phase 5.1G reference implementation, `TokenOverlapRelevanceScorer`,
is deliberately simple: normalized lexical token-overlap coverage —
`|unique query tokens also present in candidate text| / |unique query
tokens|` — using only `str.casefold()` and a Unicode-aware `\w+` word
split (stdlib `re`). No stemming, no synonym table, no fuzzy/edit-distance
matching, no embeddings: "Eric" does not match "Ericsson"; "5G"/"NR" and
"failure"/"fault" are never treated as equivalent. Duplicate query terms
never inflate a score (both sides are tokenized into de-duplicated sets).
The candidate text surface is deliberately narrow and explicit:
`KnowledgeObject.title`, `KnowledgeMetadata.tags`, `KnowledgeSection.heading`,
and `KnowledgeSection.content` — concatenated and tokenized together.
`source_system`, `lifecycle_status`, `version.label`, and `document_type`
never participate in scoring at all.

### 16.7 Deterministic ranking and limit application

Eligible, positively-scored (`relevance > 0.0`) items are ranked by:

1. **Higher `relevance_score` first.**
2. For equal relevance, **stronger applicability certainty first**:
   `MATCH` before `PARTIAL_MATCH` before `UNKNOWN` (a deterministic
   tie-break only — never a business weight; `NOT_APPLICABLE` never
   reaches this stage at all).
3. Remaining ties broken by deterministic identity —
   `knowledge_id`, then `version_label` (carrying **zero** governance or
   precedence meaning here, exactly as in §14.4/§15.5 — used only
   because *some* total order is required once relevance and
   applicability certainty are already tied), then `section.sequence`,
   then `section.section_id` (guaranteed unique).

`limit` is applied **only after** full eligibility filtering, scoring,
and ranking — never earlier, and never as a substitute for sending the
full corpus somewhere else. The result is proven independent of the
repository's own `list_all()` return order (shuffling the input corpus
never changes the ranked output).

### 16.8 Read-only, and independent of any concrete storage or model

`KnowledgeRetrievalService` depends only on the `KnowledgeRepository`
Protocol (§15.1) — never `SQLiteKnowledgeRepository` or any storage
detail — and calls exactly one of its operations, `list_all()`. It never
calls `add`/`replace`, never mutates a `KnowledgeObject` or
`KnowledgeSection`, and never persists retrieval state of any kind.
`backend/knowledge/retrieval/` has no dependency on any individual agent,
ADK, Gemini, concrete cloud/vendor SDK, or concrete embedding/vector
index library (statically verified, exactly like every other KM
package — see §10 and `test_dependency_boundary.py`). No
`datetime.now()`/`datetime.utcnow()`/`time.time()` call exists anywhere
in this package — `as_of` is always the caller's explicit input.

### 16.9 RELEVANCE ≠ AUTHORITY, APPLICABILITY ≠ RELEVANCE

Two new principles this phase adds, alongside the ones already preserved
verbatim from earlier phases (**APPROVED ≠ CURRENT**, **CURRENT ≠
APPLICABLE**, **VERSION LABEL ≠ VERSION ORDER**, **REPOSITORY ≠
RETRIEVAL ENGINE**, **VECTOR INDEX ≠ SOURCE OF TRUTH**):

- **RELEVANCE ≠ AUTHORITY**: a section's lexical relevance to a query has
  no bearing whatsoever on which version is governed-current — that
  decision belongs exclusively to `governance/versioning.py`
  (`resolve_current_version`, §14.7), called here unchanged. A highly
  relevant section from a non-current version is never returned; a
  perfectly current section with zero lexical overlap is never returned
  either — relevance and authority are evaluated independently and both
  must pass.
- **APPLICABILITY ≠ RELEVANCE**: a `MATCH` outcome from
  `evaluate_applicability` means only "not excluded by applicability
  constraints" — it never means "relevant to this query". A `MATCH`
  object with zero lexical overlap with the query is still excluded from
  the retrieval result (its score is `0.0`, so it is filtered before any
  item is ever constructed).

### 16.10 Non-goals of this phase

5.1G deliberately does not build: provenance or citation assembly (a
retrieval item is not yet a proof of evidence used — 5.1H); agent tools
or any ADK/Gemini integration (5.1I); the Context Engineering runtime;
any semantic/embedding/vector-index scorer (only the deterministic
lexical `TokenOverlapRelevanceScorer` reference implementation exists —
a future scorer may implement the same `KnowledgeRelevanceScorer`
Protocol, but does not exist yet); re-chunking, summarizing, or rewriting
section content in any way; document-level (as opposed to section-level)
results; any new lifecycle, versioning, or applicability logic (all
three are reused unchanged from 5.1E/5.1B); a persisted or cached
retrieval index of any kind (every `retrieve()` call re-derives its
result from `list_all()` fresh); and Phase 4H security/authorization
concerns (retrieval assumes it is already operating within an authorized
context).

## 17. Phase 5.1H — Knowledge Provenance

5.1H answers exactly one question: **"how can SLOPANOC prove exactly
which governed knowledge object, version, section, source, and exact
section content supports a retrieved piece of knowledge?"**
`backend/knowledge/provenance/` is the deterministic **validation
boundary** between a 5.1G `KnowledgeRetrievalResult` (a candidate,
not-yet-trusted claim) and a trusted, bounded `KnowledgeEvidenceSet`.

### 17.1 Central trust principle

**THE BACKEND ESTABLISHES EVIDENCE. THE MODEL MAY LATER SELECT FROM IT.
THE MODEL NEVER CREATES IT.** No caller/model can invent `knowledge_id`,
`version_label`, `section_id`, `source_system`, `source_id`,
`source_locator`, section content, title, or document type and have that
invention accepted as provenance — every field on a
`KnowledgeEvidenceItem` is reconstructed from a freshly-fetched, exactly-
identified `KnowledgeRepository.get(...)` result, never copied blindly
from a `KnowledgeRetrievalItem`. **RETRIEVED ≠ VERIFIED EVIDENCE**: a
`KnowledgeRetrievalResult` only tells this package which evidence NEEDS
to be validated — it is never itself trusted.

### 17.2 Provenance is not retrieval

5.1H does not re-run relevance scoring, ranking, applicability
evaluation, or current-version resolution — 5.1G already decided which
candidate sections survived retrieval. The separation is now five deep
(§16.1's six-way separation, provenance's own slice):

```text
Governance    = authority/currentness       (5.1E, reused unchanged)
Applicability = operational applicability   (5.1B, reused unchanged)
Retrieval     = relevance / candidate selection (5.1G, reused unchanged)
Provenance    = evidence authenticity / traceability (5.1H, this package)
Reasoning     = later interpretation         (later)
```

`backend/knowledge/provenance/` never imports
`governance.versioning.resolve_current_version`,
`domain.applicability.evaluate_applicability`,
`retrieval.scoring.KnowledgeRelevanceScorer`, or
`TokenOverlapRelevanceScorer` (statically verified).

### 17.3 `KnowledgeEvidenceReference` reuse

5.1H does not invent a new identity type. It makes the frozen 5.1A
`KnowledgeEvidenceReference` (`knowledge_id`, `version_label`, optional
`section_id`, `source_system`, `source_id`, optional `source_locator`)
**authoritative** by constructing it exclusively from validated
repository objects — never from a retrieval/model claim:

| Reference field  | Constructed from                                        |
|-------------------|---------------------------------------------------------|
| `knowledge_id`    | repository `KnowledgeObject.knowledge_id`                |
| `version_label`   | repository `KnowledgeObject.version.label`               |
| `section_id`      | repository `KnowledgeSection.section_id`                 |
| `source_system`   | repository `KnowledgeSource.source_system`               |
| `source_id`       | repository `KnowledgeSource.source_id`                   |
| `source_locator`  | repository `KnowledgeSection.source_locator`             |

### 17.4 `KnowledgeEvidenceItem` and `KnowledgeEvidenceSet`

`KnowledgeEvidenceItem` represents one actual validated piece of governed
knowledge: an authoritative `reference`, `title`, `document_type`,
`lifecycle_status`, and — following the same reuse discipline as
`KnowledgeContextItem`/`KnowledgeRetrievalItem` — the exact
`KnowledgeSource` and `KnowledgeSection` domain objects directly, rather
than duplicating their fields. A `model_validator` enforces internal
consistency: `reference.knowledge_id`/`section_id` must match
`section.knowledge_id`/`section_id`, and `reference.source_system`/
`source_id` must match `source.source_system`/`source_id` — an
internally contradictory evidence item cannot be constructed at all.

`KnowledgeEvidenceSet` is the bounded aggregate (`items:
list[KnowledgeEvidenceItem]`) for one retrieval turn. It preserves the
5.1G retrieval ranking order, rejects duplicate evidence identities
(`(knowledge_id, version_label, section_id)`), is a plain, deterministic,
serializable Pydantic model with no hidden state, no implicitly-generated
timestamp, no global registry, and is never persisted by this package.

### 17.5 `KnowledgeEvidenceSelectionKey` — the minimal selection identity

A future-safe way for an agent/model to SELECT known evidence without
supplying authoritative provenance fields. It contains **only**:
`knowledge_id`, `version_label`, `section_id`. **`KnowledgeEvidenceSelectionKey`
is a closed identity-only contract. Any field other than `knowledge_id`,
`version_label`, and `section_id` is rejected** (`model_config =
extra="forbid"`) — a selector attempting to smuggle section content,
source identity, title, document type, lifecycle status, a
locator/snippet, or any other field alongside a real identity gets a
deterministic validation failure, never a silently dropped value. No
model integration exists yet — this contract exists purely so
5.1I/5.1J can build on it safely later, and is tested exclusively with
plain Python values in this phase.

### 17.6 `KnowledgeProvenanceService.build_evidence_set`

Depends only on the `KnowledgeRepository` Protocol (5.1F) — never
`SQLiteKnowledgeRepository` (statically verified). For every
`KnowledgeRetrievalItem` in a `KnowledgeRetrievalResult`, in order:

1. **Exact repository lookup.** `repository.get(retrieval_item.knowledge_id,
   retrieval_item.version_label)` — never `list_all`, never a title/source
   search, never a fallback to another version. `None` →
   `KnowledgeEvidenceNotFoundError` (fails closed; no partial evidence
   set is ever returned from a batch containing one bad item).
2. **Exact section lookup.** Find `retrieval_item.section.section_id`
   among the governed object's own `sections` — never matched by
   heading, content similarity, sequence alone, or source locator alone.
   Absent → `KnowledgeEvidenceNotFoundError`.
3. **Exact, unnormalized field comparison** — no hashing, no "close
   enough": section fields (`section_id`, `knowledge_id`, `sequence`,
   `heading`, `section_type`, `content`, `source_locator`), document
   metadata (`title`, `document_type`, `lifecycle_status`), and source
   identity (`source_system`, `source_id`, `source_uri`,
   `display_name`) must all match the freshly-fetched governed state
   exactly. Any disagreement → `KnowledgeEvidenceMismatchError`. This is
   also what makes **stale evidence fail closed**: if the governed
   version was replaced (`repository.replace`) after 5.1G retrieved it
   but before provenance was built, the now-different repository state
   simply fails the same exact-match check — no versioned snapshot or
   locking infrastructure is needed.
4. **Trusted reconstruction.** The `KnowledgeEvidenceItem` appended to
   the result is built entirely from the governed repository object and
   section (§17.3) — never from the `KnowledgeRetrievalItem`'s own
   copies, even when they already matched.

A `KnowledgeRepositoryCorruptionError` raised by `repository.get` is
propagated unchanged — it already fails closed and carries no raw
payload content by its own 5.1F contract, so no additional wrapping is
needed to preserve the "never expose raw payload content" requirement.

### 17.7 `validate_evidence_selection` — bounded selection validation

A pure, synchronous, repository-free function:
`validate_evidence_selection(evidence_set, selections) ->
list[KnowledgeEvidenceItem]`. The trusted evidence universe is exactly
`evidence_set` — this function never queries the repository, so a real
repository section that was simply never part of this turn's bounded
`KnowledgeEvidenceSet` is rejected identically to a purely fabricated
one (**REAL IN REPOSITORY ≠ AVAILABLE EVIDENCE FOR THIS TURN**). If
**any** requested `KnowledgeEvidenceSelectionKey` is absent from the set,
the **entire** selection fails with `KnowledgeEvidenceSelectionError` —
never a partial result silently dropping the invalid entries. Duplicate
requested identities are deduplicated, preserving first-occurrence
order; the returned order follows the **requested** selection order, not
`evidence_set`'s own order (no ordering implies authority in either
direction). An empty `selections` list validly returns `[]`, never an
error.

### 17.8 Preserved principles, and two new ones

Preserved verbatim from earlier phases: **APPROVED ≠ CURRENT**,
**CURRENT ≠ APPLICABLE**, **VERSION LABEL ≠ VERSION ORDER**,
**REPOSITORY ≠ RETRIEVAL ENGINE**, **VECTOR INDEX ≠ SOURCE OF TRUTH**,
**RELEVANCE ≠ AUTHORITY**, **APPLICABILITY ≠ RELEVANCE**. New this phase:

- **RETRIEVED ≠ VERIFIED EVIDENCE**: a `KnowledgeRetrievalResult` is a
  candidate claim about what evidence exists — it becomes trusted only
  after `build_evidence_set` exactly revalidates it against the
  repository.
- **MODEL SELECTION ≠ PROVENANCE**: choosing which validated evidence to
  cite (a future `KnowledgeEvidenceSelectionKey`) is never the same
  operation as establishing what that evidence actually is — Python
  alone determines the latter, always.
- **PROVENANCE MUST COME FROM GOVERNED EVIDENCE**: every trusted field
  traces to exactly one `(knowledge_id, version_label, section_id)`
  repository lookup — never a snippet, summary, or model-generated
  citation.

### 17.9 Non-goals of this phase

5.1H deliberately does not build: agent tools or generic KM tool
exposure (5.1I); any ADK/Gemini/model integration (selection is tested
with plain Python values only); UI (`src/**` is untouched); Teams
provenance coupling (`backend.tools.teams` is never imported — the
philosophy is shared, the implementation is not); cryptographic
signing/hashing/Merkle/PKI infrastructure; provenance persistence (no
table, cache, or run registry — every `KnowledgeEvidenceSet` is a
runtime-only, request-scoped structure); external source re-fetching
(SharePoint/GCS/Drive/Confluence — the governed repository is the
authoritative KM state for this architecture); generated snippets,
summaries, paraphrasing, or content truncation (exact governed section
content is always the authoritative evidence text); a global/run-scoped
evidence registry or `ContextVar` mailbox (5.1I/5.1J decide how evidence
is carried through an agent turn); and any Context Engineering runtime.

## 18. Phase 5.1I — Generic Agent-Facing Knowledge Tools

5.1I answers exactly one question: **"how can any future agent safely
query governed knowledge through one generic tool boundary and receive
only bounded, provenance-validated knowledge without knowing anything
about repository, governance, retrieval, or provenance implementation
details?"** `backend/knowledge/tools/` is the agent-facing boundary that
COMPOSES the frozen 5.1F–5.1H layers — it never reproduces their logic.

### 18.1 One initial capability: `knowledge_search`

This phase implements exactly one agent-facing operation,
`KnowledgeToolService.search`. No `knowledge_get`/`knowledge_get_current`/
`knowledge_get_section`/`knowledge_browse`/`knowledge_latest` exists —
`knowledge_search` already provides the correct bounded path through
currentness (5.1E), applicability (5.1B), relevance (5.1G), and
provenance (5.1H); additional capabilities are introduced later only
when a concrete consumer demonstrably needs them, never speculatively,
to avoid creating alternate paths around the bounded-retrieval
architecture.

### 18.2 `KnowledgeSearchToolRequest` — the ONLY model-controlled input

A CLOSED contract (`extra="forbid"`, following the same discipline the
5.1H `KnowledgeEvidenceSelectionKey` correction established): exactly
`query_text` (non-blank) and `limit` (default 5, technical maximum 10 —
a small cap that exists solely to stop a model from requesting an
unbounded corpus, not business/domain hardcoding). An out-of-range limit
is **rejected**, never silently clamped. A model cannot supply
`source_system`, `document_type`, `version_label`, `lifecycle_status`,
`content`, `evidence`, `provenance`, `as_of`, or `applicability_context`
— those fields simply do not exist on this contract, and any attempt to
supply them fails validation generically (no per-field blacklist).

### 18.3 `KnowledgeToolExecutionContext` — trusted, backend-supplied only

Also a CLOSED contract, containing exactly `as_of: datetime` (always
explicit — this package never calls the wall clock) and
`applicability_context: ApplicabilityContext` (may validly default to
empty — 5.1J's first consumer may have no validated applicability facts
yet; the caller never invents one). This is deliberately **not** part of
`KnowledgeSearchToolRequest`: a future model is never asked what "now"
means, and is never made authoritative for operational applicability —
the trusted caller/orchestrator supplies both, protecting the separation
between model-controlled search expression and trusted operational
execution context. `KnowledgeToolExecutionContext` is explicitly **not**
the future Context Engineering Layer — it carries exactly these two
fields and nothing else.

### 18.4 `KnowledgeToolService` — composition, not reimplementation

Depends only on already-constructed `KnowledgeRetrievalService` (5.1G)
and `KnowledgeProvenanceService` (5.1H) instances — never
`SQLiteKnowledgeRepository`, a database URL, `resolve_current_version`,
`evaluate_applicability`, or a relevance scorer directly (statically
verified). `search` builds a `KnowledgeRetrievalQuery` from
`request.query_text`/`request.limit` and
`context.applicability_context`/`context.as_of`, calls
`KnowledgeRetrievalService.retrieve` unchanged, passes the resulting
`KnowledgeRetrievalResult` to `KnowledgeProvenanceService.build_evidence_set`
unchanged, and only then transforms the two already-verified outputs
into a model-safe view — it never constructs a
`KnowledgeEvidenceReference`/`KnowledgeEvidenceItem`/`KnowledgeEvidenceSet`
itself.

### 18.5 Correlation: identity, never positional zip

`KnowledgeRetrievalResult.items` and `KnowledgeEvidenceSet.items` are
correlated by deterministic `(knowledge_id, version_label, section_id)`
identity — never assumed to "line up" merely because both currently
preserve order. A retrieval identity absent from the evidence set, an
evidence identity absent from retrieval, or a duplicate identity on
either side all raise `KnowledgeToolConsistencyError` (fails closed;
never a partial/best-effort correlation). Order itself IS preserved end
to end (5.1G's ranking order, already preserved once by 5.1H, is
preserved again by this correlation) — but that is a property of the
inputs, not something this function trusts blindly.

### 18.6 `KnowledgeToolEvidenceItem` — the model-safe evidence view

Authoritative fields — `title`, `document_type`, `section_heading`,
`content`, `source_system`, `source_id`, `source_display_name`,
`source_locator` — are populated EXCLUSIVELY from the validated 5.1H
`KnowledgeEvidenceItem`, never from the pre-provenance
`KnowledgeRetrievalItem`. `applicability_outcome` and `relevance_score`
come from the 5.1G retrieval decision instead, since provenance does not
carry either. `selection_key` is the exact `KnowledgeEvidenceSelectionKey`
(5.1H, reused — not a new citation id, not a random UUID) a future model
may use to cite this item later.

**`KnowledgeSource.source_uri` is deliberately excluded** from this
model-facing item — a model does not need raw navigation URLs to reason
over evidence, and a URI may carry environment-specific/internal detail.
This is a presentation/security boundary only: the complete
`KnowledgeSource`, including `source_uri`, remains fully retained in the
trusted `KnowledgeEvidenceSet`.

**RELEVANCE SCORE ≠ CONFIDENCE**: `relevance_score` is only the lexical
relevance score the configured 5.1G scorer produced — never a
probability, never confidence that a procedure or diagnosis is correct,
never a source-authority score, and never renamed `confidence`/
`certainty`/`probability`. **Applicability uncertainty is preserved**:
`MATCH`/`PARTIAL_MATCH`/`UNKNOWN` pass through unchanged from 5.1G —
`NOT_APPLICABLE` never appears here (5.1G already excludes it), and
`PARTIAL_MATCH`/`UNKNOWN` are never silently upgraded to `MATCH`.

### 18.7 `KnowledgeSearchAgentPayload` vs. `KnowledgeSearchExecutionResult` — the trust split

```text
KnowledgeSearchExecutionResult
    agent_payload: KnowledgeSearchAgentPayload   -- may be serialized and shown to a model
    evidence_set:  KnowledgeEvidenceSet           -- TRUSTED backend state, NOT model input/authority
```

`KnowledgeSearchAgentPayload` (`items` + `diagnostics`) is the ONLY
object this package intends to reach a model — it structurally cannot
contain a `KnowledgeEvidenceSet`, a `KnowledgeRepository`, a
`KnowledgeRetrievalResult`, or any service/database state (its declared
field set is exactly `{items, diagnostics}`). `KnowledgeSearchExecutionResult`
is a plain, frozen `dataclass` (not a Pydantic model, deliberately, to
keep "serializable payload" and "internal trusted state" visually
distinct) holding both together — access is via the obvious
`result.agent_payload`/`result.evidence_set` attributes; there is no
`to_dict()`/serialization helper on the whole result that would
encourage sending it anywhere as a unit.

**NO GLOBAL EVIDENCE REGISTRY**: evidence is threaded through this
explicit return value only — no global dict, module singleton,
`ContextVar` mailbox, run-id registry, or session/database evidence
store exists anywhere in this package (statically verified). 5.1J
decides how a concrete agent turn carries `KnowledgeSearchExecutionResult`
forward through one turn.

### 18.8 Evidence selection validation — the model still cannot author authority

`KnowledgeToolService.validate_selection(execution_result, selections)`
is a thin pass-through to 5.1H's own `validate_evidence_selection(execution_result.evidence_set,
selections)` — no validation rule is duplicated. Critically, the
signature accepts only `execution_result` and a list of
`KnowledgeEvidenceSelectionKey` — there is no parameter through which a
caller/model could supply an alternate `EvidenceSet` to validate against.
This means: a fabricated selection key fails
(`KnowledgeEvidenceSelectionError`, unchanged from 5.1H); a real
repository section that was simply never retrieved in this execution
fails identically (the validation universe is `execution_result.evidence_set`,
never the repository); and **MODEL SELECTION ≠ PROVENANCE** — the model
only ever supplies identity, Python alone determines what that identity
actually resolves to.

### 18.9 Preserved principles, and two new ones

Preserved verbatim: **INGESTED ≠ APPROVED**, **APPROVED ≠ CURRENT**,
**CURRENT ≠ APPLICABLE**, **VERSION LABEL ≠ VERSION ORDER**, **REPOSITORY
≠ RETRIEVAL ENGINE**, **VECTOR INDEX ≠ SOURCE OF TRUTH**, **RELEVANCE ≠
AUTHORITY**, **APPLICABILITY ≠ RELEVANCE**, **RETRIEVED ≠ VERIFIED
EVIDENCE**, **MODEL SELECTION ≠ PROVENANCE**, **PROVENANCE MUST COME
FROM GOVERNED EVIDENCE**. New this phase:

- **AGENT PAYLOAD ≠ TRUSTED EVIDENCE STATE**: what a model sees
  (`KnowledgeSearchAgentPayload`) and what the backend retains as
  authoritative (`KnowledgeEvidenceSet`, via `KnowledgeSearchExecutionResult`)
  are structurally different objects, never the same object serialized
  two ways.
- **RELEVANCE SCORE ≠ CONFIDENCE**: a lexical relevance score is never a
  correctness/probability/authority signal — see §18.6.

### 18.10 Non-goals of this phase

5.1I deliberately does not build: any additional get/browse/current tool
(`knowledge_get`/`knowledge_get_current`/`knowledge_get_section`/
`knowledge_browse`/`knowledge_latest` — deferred until a concrete
consumer needs one); any ADK/Gemini integration, `AgentTool`, tool
registration, prompt, or agent instruction change (`backend/agents/**`
is untouched — that is exclusively 5.1J's job); query rewriting,
synonym expansion, vendor inference, or any semantic intelligence in
this adapter layer (`request.query_text` is forwarded to 5.1G verbatim);
content truncation of any kind (5.1G already bounds the NUMBER of
sections; blind character-slicing could alter commands/procedures/
safety-relevant detail — a future token-budget policy is deliberately
deferred, not solved prematurely here); a citation parser, structured-
output schema, retry logic, or citation prompt (5.1J, once there is a
concrete reference consumer); any global/run-scoped evidence registry or
`ContextVar` mailbox; any persistence of `KnowledgeSearchExecutionResult`
or `KnowledgeEvidenceSet`; and any Context Engineering runtime,
Troubleshooting State, or next-best-action reasoning.

## 19. Phase 5.1J — First Reference Consumer (Incident Manager)

5.1J answers exactly one question: **"can the existing Incident Manager
safely consume the Generic Knowledge Management capability during a real
ADK turn, while Python retains authority over the evidence?"** It proves
the generic 5.1I tool boundary has one real consumer, without changing
the frozen agent hierarchy: Team Manager remains the only user-facing
agent, delegating to Incident Manager via `AgentTool` exactly as before;
Incident Manager gains two new tools alongside its existing Teams tools.
Generic KM (`backend/knowledge/**`, 5.1A–5.1I) is completely unmodified
by this phase — everything ADK-specific lives in `backend/tools/knowledge/`
instead, outside the generic KM package.

### 19.1 Two concrete tools, one generic capability

`backend/tools/knowledge/tools.py` exposes exactly two ADK-compatible
functions to Incident Manager:

- **`knowledge_search(query_text, limit)`** — the concrete adapter over
  `KnowledgeToolService.search` (5.1I). Model-visible schema contains
  ONLY `query_text`/`limit` (verified against the ACTUAL ADK-generated
  `FunctionTool` declaration, not merely Python intent) — no
  `tool_context`, `as_of`, `applicability_context`, `run_id`,
  `session_id`, `evidence_set`, or `source_uri` field exists anywhere in
  it.
- **`knowledge_select_evidence(selections)`** — the concrete provenance-
  selection control. `selections: list[KnowledgeEvidenceSelectionKey]`
  (the frozen 5.1H identity-only contract) is used DIRECTLY as the
  parameter type — verified that ADK generates a correctly-shaped,
  closed nested schema from it with no wrapper DTO needed (`knowledge_id`/
  `version_label`/`section_id` only, nothing else, at both the top level
  and inside each array item).

Both remain read-only (no `add`/`replace`/`approve`/`archive`), and no
additional `knowledge_get`/`knowledge_get_current`/`knowledge_get_section`/
`knowledge_browse`/`knowledge_latest` tool exists — 5.1J deliberately
proves the bounded search/provenance/selection path first; further
capabilities are added later only when a concrete need arises.

### 19.2 Trusted execution context: `as_of` and applicability are never model input

`as_of` is captured from an injectable clock (defaulting to
`datetime.now(timezone.utc)`, mirroring `perf_timing.py`'s own
`clock: Clock = time.monotonic` convention) exactly ONCE per run, at the
first `knowledge_search` call, and reused unchanged for every subsequent
search within that same run (`KnowledgeRunEvidenceState.execution_context`).
`ApplicabilityContext` starts empty and is never inferred from user free
text, Teams messages, model reasoning, titles, or fault names — this is
deliberate, matching 5.1B's own frozen semantics (a knowledge item may
legitimately surface as `MATCH`/`PARTIAL_MATCH`/`UNKNOWN`). Neither field
exists on the model-visible `knowledge_search` schema at all — a model
cannot supply or override either, structurally, not merely by
convention.

### 19.3 Why a run-id-keyed dict, not a `ContextVar`

Verified directly against this codebase's own prior investigation
(`backend/agents/team_manager/direct_read_fast_path.py`'s "CORRECTION
PASS"): ADK's `handle_function_call_list_async` runs every tool call via
`asyncio.create_task(...)`, which copies the current `contextvars.Context`
at task-creation time — a `ContextVar.set(...)` performed inside that
child task can never propagate back out to a sibling task or the parent.
`backend/tools/knowledge/runtime.py` therefore keys its trusted evidence
store by a plain, `threading.Lock`-protected, module-level
`dict[run_id, KnowledgeRunEvidenceState]` — the same fix already proven
correct for that module's own `_pending_trusted_result_by_run`. `run_id`
itself comes from `backend.api.turn_context.current_run_id()`, which IS
safely readable from any nested task (it is bound once, at
`chat_service.py`'s own turn start, before any task-splitting occurs —
only WRITES performed inside a child task fail to propagate upward, not
reads of an already-bound value; `teams_get_messages` already relies on
this exact asymmetry).

### 19.4 Available evidence vs. selected evidence

`KnowledgeRunEvidenceState` tracks two distinct collections per run:

- **`available_evidence`** — the union of every successful
  `knowledge_search`'s trusted `evidence_set` within this run, merged by
  `(knowledge_id, version_label, section_id)` identity. An identical
  repeat of an already-available identity is a silent no-op
  (deduplication, first-seen order preserved); a DIFFERENT
  `KnowledgeEvidenceItem` reported for the same identity within the same
  run raises `KnowledgeRuntimeConsistencyError` — fails closed, never
  silently keeps the first or the latest.
- **`selected_evidence`** — populated ONLY by `knowledge_select_evidence`,
  validated via 5.1H's own unmodified `validate_evidence_selection`
  against exactly `available_evidence` (never the repository, never
  another run). Multiple `knowledge_select_evidence` calls union
  deterministically, deduplicating while preserving first-selected order.
  **SEARCH RESULT ≠ EVIDENCE USED**: calling `knowledge_search` never
  implies anything was relied upon; if Incident Manager never calls
  `knowledge_select_evidence`, `selected_evidence` remains empty for the
  entire run — this is normal, not an error, and nothing infers "used
  evidence" from the answer text.

### 19.5 Cleanup, isolation, and the trusted snapshot accessor

`discard_knowledge_run_evidence_state(run_id)` is wired into
`chat_service.py`'s own existing, already-proven turn-end `finally`
block, alongside `pop_message_texts`/`discard_model_call_tracking`/
`discard_pending_trusted_result` — guaranteeing no run-scoped KM evidence
survives past the one turn/run that produced it, on every exit path
(success, a caught exception, or cancellation). Because the store is
keyed by `run_id` (unique per turn) rather than session or user, evidence
is turn-scoped, not session-scoped: a second turn in the same session
gets a fresh, empty evidence universe, and two concurrent runs (any
combination of users/sessions) never observe each other's evidence.
`snapshot_selected_knowledge_evidence(run_id)`/`get_available_knowledge_evidence(run_id)`
are trusted, backend-only accessors — never ADK/model-facing — that let
other trusted backend code inspect what was selected/available before
cleanup; they accept only a trusted runtime run identity and return `[]`/
an empty set for an unknown or already-cleaned-up run, never another
run's evidence.

### 19.6 Model-safe tool results, never trusted state

`knowledge_search` returns exactly `execution.agent_payload.model_dump(mode="json")`
— never `execution.evidence_set`, a repository object, or `source_uri`.
`knowledge_select_evidence` returns only
`{"status": "accepted", "selected": [{"knowledge_id", "version_label", "section_id"}, ...]}`
— reconstructed from the newly-validated trusted items' own reference
fields, never the full `KnowledgeEvidenceSet` and never blindly echoing
the model's request. **MODEL SELECTION ≠ PROVENANCE** holds exactly as in
5.1H: the model supplies identity only; Python alone determines what
that identity resolves to. Neither tool ever writes KM evidence into
ordinary ADK `tool_context.state`/session state — the trusted evidence
never flows through a channel that could reach a prompt or a later turn's
model context.

### 19.7 A verified ADK argument-conversion gap, and its fix

A live smoke test (real Gemini via Vertex AI, an isolated in-memory
repository seeded with one distinctively-named record) surfaced a real,
verified gap in the installed ADK version: `FunctionTool._preprocess_args`
only auto-converts a parameter whose OWN annotation is directly a
Pydantic `BaseModel` — it does not descend into `list[BaseModel]`, so
each entry of a real model-driven `selections` call arrives as a plain
`dict`, even though the generated schema itself is correctly shaped.
`knowledge_select_evidence` therefore explicitly normalizes each entry
through `KnowledgeEvidenceSelectionKey.model_validate(...)` before use —
this does not relax or bypass validation (the same closed, `extra="forbid"`
contract is still enforced), it only performs explicitly what ADK does
not do automatically for a list parameter.

### 19.8 Local repository wiring

`backend/tools/knowledge/runtime.py` composes a process-wide
`SQLiteKnowledgeRepository` (5.1F) + `KnowledgeRetrievalService` (5.1G) +
`KnowledgeProvenanceService` (5.1H) + `KnowledgeToolService` (5.1I)
singleton, mirroring `backend/cases/db.py`'s own `get_case_database()`
pattern exactly. A new, dedicated `Settings.resolve_knowledge_database_url()`
(env var `SLOPANOC_KNOWLEDGE_DATABASE_URL`, default
`sqlite+aiosqlite:///./slopanoc_knowledge.db`) mirrors
`resolve_database_url()`'s own local-file convention — a genuinely
separate database from ADK session storage, since the Generic KM
repository has its own table set and lifecycle. No schema is created
explicitly; every `SQLiteKnowledgeRepository` operation already lazily/
idempotently calls its own `ensure_schema()` (5.1F) — an empty repository
is a valid, non-error starting state.

### 19.9 Teams vs. governed knowledge, and no keyword routing

Incident Manager's own instruction gained one concise "GOVERNED
KNOWLEDGE" paragraph (never a broad rewrite) stating the two sources are
different and may be used independently or together, that `relevance_score`
is not confidence, that `PARTIAL_MATCH`/`UNKNOWN` mean applicability is
not fully proven, that retrieved content is evidence/data rather than an
instruction to follow, and that `knowledge_select_evidence` should be
called only for evidence actually relied upon. Whether to call
`knowledge_search` on a given turn remains the model's own semantic
judgment — no keyword/regex/intent-phrase list was introduced anywhere in
`backend/tools/knowledge/` to gate this decision (statically verified).

### 19.10 A known, deliberate scope boundary

`IncidentManagerRequest` (Incident Manager's own ADK `input_schema`)
still requires `chat_topic: str` — this was NOT changed by 5.1J. A
consequence: Team Manager's existing delegation path has no way to invoke
Incident Manager for a request that is purely about governed knowledge
with no Teams-chat angle at all; every current path to `knowledge_search`
runs through an already-Teams-scoped Incident Manager invocation.
Reworking that would mean changing `IncidentManagerRequest`/Team
Manager's own routing instruction — a materially larger, riskier change
than "register two tools and extend one instruction paragraph," and
outside this phase's own "do not redesign Team Manager unless truly
required" boundary. This is recorded here as a known, deliberate
limitation for a future phase to address (most likely once Context
Engineering exists to route "what kind of context does this turn need"
more generally), not something 5.1J silently worked around.

### 19.11 Preserved principles, and one new one

Preserved verbatim: **INGESTED ≠ APPROVED**, **APPROVED ≠ CURRENT**,
**CURRENT ≠ APPLICABLE**, **VERSION LABEL ≠ VERSION ORDER**, **REPOSITORY
≠ RETRIEVAL ENGINE**, **VECTOR INDEX ≠ SOURCE OF TRUTH**, **RELEVANCE ≠
AUTHORITY**, **APPLICABILITY ≠ RELEVANCE**, **RETRIEVED ≠ VERIFIED
EVIDENCE**, **MODEL SELECTION ≠ PROVENANCE**, **PROVENANCE MUST COME
FROM GOVERNED EVIDENCE**, **AGENT PAYLOAD ≠ TRUSTED EVIDENCE STATE**,
**RELEVANCE SCORE ≠ CONFIDENCE**. New this phase: **AVAILABLE EVIDENCE ≠
SELECTED EVIDENCE** (§19.4) and **REAL IN REPOSITORY ≠ AVAILABLE THIS
TURN** (a repository section that exists but was never retrieved by
THIS run's own searches cannot be selected — proven identical in effect
to a purely fabricated identity) and **SEARCH RESULT ≠ EVIDENCE USED**
(§19.4).

### 19.12 Non-goals of this phase

5.1J deliberately does not build: any additional get/browse/current tool;
Context Engineering, a Troubleshooting Manager, or a Head of Automated
Operations; autonomous execution of any knowledge content (knowledge
remains advisory evidence only); a UI/citation-rendering integration
(`src/**` untouched); a redesign of Team Manager, `TrustedSpecialistResult`,
or Teams provenance (all untouched); Phase 4H security hardening; and a
production (non-SQLite) knowledge database — the local repository remains
the correct choice for this first reference consumer, exactly as 5.1F's
own contract anticipated.
