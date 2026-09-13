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
Phase 5.1J (Incident Manager, the first reference consumer); §22 covers
the Knowledge Context / RAG / Memory target mental model; §23 covers the
Phase 6A.2 Knowledge Applicability bridge (`ApplicabilityScopeKind`,
additive, CURRENT); §24 covers Phase 6A.3's multimodal-ingestion
industrialization (Layer H production wiring, XLSX range/table
provenance, and a real, confirmed defect fix — DEF-0017 — additive,
CURRENT — see docs/INTELLIGENCE_ARCHITECTURE.md for the full Phase 6A
context and docs/DEFECT_REGISTER.md for DEF-0017's full record); §25
covers the 6A.3 corrective addendum's canonical Knowledge Asset Metadata
standard (additive, CURRENT); §26 covers Phase 6A.4's deterministic
TELCO applicability & knowledge narrowing (`backend/knowledge/
narrowing/`, additive, CURRENT); §27 covers Phase 6A.5's hybrid Knowledge
retrieval & evidence selection (`backend/knowledge/hybrid_retrieval/`,
additive, **PARTIAL** — real exact/lexical retrieval and real embedding
generation validated against the real DEV database; real pgvector
similarity search itself blocked by a confirmed `CREATE EXTENSION`
privilege denial — see §27.9). See
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

## 20. A5 — Compound-artifact extension (additive only)

A5 (Knowledge Island Ingestion Foundation + Real TELCO/RAN Compound
Knowledge Validation) proves the 5.1A–5.1J contract above can represent
real COMPOUND operational knowledge — DOCX/XLSX/PDF/TXT with embedded/
nested artifacts (images, spreadsheets, other documents) — without
changing any of it. Every A5 addition is a new, optional field or a new
sibling module; nothing described in §1–§19 was rewritten, and every
plain-text document/section built before A5 remains byte-for-byte valid
and behaviorally identical.

### 20.1 `KnowledgeArtifact` (new domain type)

`backend/knowledge/domain/artifacts.py`. One generic node type
representing any embedded object discovered inside a compound source
document — never a per-format subclass (`EmbeddedDocx`/`EmbeddedXlsx`/
...). Key fields: `artifact_id` (deterministic, content-derived —
never a random UUID, so re-ingesting identical content reproduces the
identical id), `parent_artifact_id` (`None` for an artifact embedded
directly in the root document), `kind` (open string, e.g.
`"embedded_docx"`, `"image"`, `"xlsx_sheet"`, `"pdf_page"`, `"txt_log"`,
`"ole_package"` (a legacy OLE2 Compound-File-Binary "Insert Object >
Create from File" embed — its own extracted payload becomes a CHILD
artifact, dispatched through this same generic pipeline; corrective
pass, `backend/knowledge/ingestion/extractors/ole.py`),
`"unsupported_artifact"` — never a closed enum, exactly like
`KnowledgeSection.section_type`), `depth`, `content_hash` (SHA-256,
deduplication basis), `extracted_text` (`None` until/unless extraction
or model interpretation produced one), `derived` (SOURCE vs DERIVED —
`False` for a structural extraction of real content, `True` for a model
interpretation such as an image description; source truth is never
overwritten by derived text), `locator_detail` (opaque, kind-specific
addressing, e.g. `"sheet=Q3"`/`"page=4"` — never parsed generically,
mirroring why `source_locator` elsewhere in this contract stays a plain
line-range string), `storage_ref` (a durable `gs://` reference, set
only once the durable-artifact-storage boundary has run),
`extraction_status`/`extraction_error` (a genuinely bounded state
machine — COMPLETE/PARTIAL/FAILED/SKIPPED — unlike `kind`, which is an
open content-taxonomy string). `validate_artifact_lineage` enforces a
closed graph: unique `artifact_id`, every `parent_artifact_id` resolves
within the same list.

### 20.2 Additive fields on existing contracts

- `IngestedKnowledgeDocument.artifacts: list[KnowledgeArtifact] = []` —
  the compound tree a source adapter's own extraction discovered.
  Default empty; `content` remains the root document's own unsegmented
  text exactly as §12.3 already described.
- `KnowledgeSection.artifact_id` / `StructuredKnowledgeSection
  .artifact_id: Optional[str] = None` — which artifact (if any) this
  section's content was derived from. `KnowledgeObject`/
  `StructuredKnowledgeDocument` cross-check that a non-`None` value
  resolves to a real artifact in the same object's own `artifacts`.
- `KnowledgeObject.artifacts: list[KnowledgeArtifact] = []` — carried
  through unchanged by `governance/service.py`'s `materialize_candidate`
  (§14 is otherwise completely unmodified: knowledge_id/document_type/
  version remain required, explicit, never-inferred kwargs;
  `section_type` remains unset UNLESS the caller explicitly supplies the
  new, optional `section_roles: dict[section_key, section_type]`
  kwarg — a trusted-caller-only assertion, mirroring the
  knowledge_id/document_type/version discipline exactly; 5.1E still
  never fabricates a semantic type on its own initiative).
- `KnowledgeEvidenceReference.artifact_id` / `KnowledgeEvidenceItem
  .artifact: Optional[KnowledgeArtifact] = None` — §16's provenance
  revalidation (`build_evidence_set`) now also resolves the matching
  artifact from the SAME freshly-refetched governed object the section
  itself is checked against — never from the retrieval result, never
  from a caller/model claim. Completes hierarchical provenance
  (section → artifact → parent artifact → ... → root) through the
  EXISTING evidence architecture (§16.1's "REPOSITORY REVALIDATION,
  NOT TRUST" principle applies identically to the artifact reference).

### 20.3 No schema/migration change

`slopanoc_knowledge_objects`' existing `payload` `Text` column (the
whole `KnowledgeObject` serialized as one JSON document, §15) absorbs
every field above transparently — empirically proven: an artifact-
bearing `KnowledgeObject`, including a populated `storage_ref`, round-
trips through `SqlAlchemyKnowledgeRepository` unchanged, for both
SQLite and (by the same dialect-neutral construction) Cloud SQL
PostgreSQL. No Alembic migration exists or was needed for A5.

### 20.4 Extraction, durable storage, and image interpretation (new sibling modules, not part of the §1–§19 contract itself)

- `backend/knowledge/ingestion/{extraction.py,extractors/}` — real
  DOCX/XLSX/PDF/TXT extraction and recursive embedded-artifact
  discovery, still inside the existing `backend/knowledge/` dependency
  boundary (§3's "no ADK/Gemini/cloud-vendor-SDK import" rule; document-
  format parsers are not cloud SDKs).
- `backend/knowledge_ingestion/{artifact_storage.py,gemini_image_interpreter.py,local_file_adapter.py}`
  — concrete, cloud-SDK-dependent modules living OUTSIDE
  `backend/knowledge/` entirely, mirroring the existing `backend/knowledge/tools/`
  (generic) vs `backend/tools/knowledge/` (concrete, ADK-facing) split
  this contract already established for the agent-facing tool boundary
  (§18). `backend/knowledge/ingestion/image_interpretation.py` itself
  stays generic (an `ImageInterpreter` Protocol, no ADK/Gemini import) —
  only the concrete Gemini implementation lives in the sibling package.

### 20.5 Preserved invariant

The same question §1's own governing invariant asks — "can the
Knowledge layer still work without knowing Incident Manager exists?" —
holds identically for A5's compound-artifact support: nothing in
§20.1–20.4 references Incident Manager, Teams, or any specific
document/vendor vocabulary. MOP/SOP/RCA/KB remain document TYPES, never
a separate architecture; DOCX/XLSX/PDF/TXT remain FORMATS an adapter's
own extraction handles, never a reason to fork the Generic KM contract
itself.

## 21. A5 final corrective pass — native-vs-derived retrieval tie-break (additive only)

A real live-validation defect (a query for native XLSX report fields
sometimes citing an image-derived screenshot description instead of the
native XLSX header text) surfaced a generic retrieval-ranking gap, not
an Incident-Manager-specific one — the fix belongs in Generic KM's own
retrieval contract, unchanged by which agent later consumes it.

- `KnowledgeRetrievalItem` (`backend/knowledge/retrieval/contracts.py`)
  gained one new field: `is_derived: bool` (default `False`), resolved
  from the retrieved section's owning `KnowledgeArtifact.derived` flag
  (§20's own `derived: bool` — no new concept, just surfaced onto the
  retrieval result). A plain-text section with no owning artifact is
  always `is_derived=False`.
- `KnowledgeRetrievalService`'s ranking (`retrieval/service.py`) buckets
  `relevance_score` first (`_relevance_bucket`, bucket width `0.15` —
  a fixed, generic constant, never tuned to any one query/document
  vocabulary), THEN tie-breaks within a bucket by `is_derived` (native,
  `False`, sorts first), THEN falls through to the existing exact-
  relevance/applicability/identity tie-breakers unchanged. A candidate
  outside another candidate's relevance bucket is never reordered by
  this tie-break — a genuinely more relevant derived item still outranks
  a genuinely less relevant native one.
- Preserved invariant: this tie-break is a pure ranking-layer addition
  over the SAME retrieval/applicability/lifecycle pipeline §1–§19
  already define — it does not change what counts as MATCH/UNKNOWN/
  NOT_APPLICABLE, does not change provenance validation, and carries no
  document-type/vendor-specific logic (no "XLSX" or "screenshot" string
  anywhere in the ranking code).

## 22. Knowledge Context vs. RAG vs. Memory (target mental model)

Documentation-only clarification (no contract/behavior change) of terms
this document and CLAUDE.md use elsewhere, written down once here as the
canonical definitions so they are not redefined inconsistently later.

### 22.1 Knowledge Context vs. RAG

**Knowledge Context** is the architectural context domain: "what does our
governed organisational/technical knowledge say?" — one of the (future)
Context Engineering Layer's three context domains alongside Operational
Context and Case Context (CLAUDE.md's "TARGET FUTURE ARCHITECTURE").

**RAG** (retrieval-augmented generation) is the *mechanism* this
document's §16/§18 already implement to obtain Knowledge Context for a
turn: `list_all` → `resolve_current_version` → `evaluate_applicability`
→ lexical ranking → provenance revalidation → a trusted
`KnowledgeEvidenceSet`. RAG is not a second repository or a competing
source of truth — it is how the ONE `KnowledgeRepository` (§15) gets
queried. **Do not describe RAG as a repository/database in its own
right**, and do not build a second retrieval path that bypasses §16–§18's
existing lifecycle/applicability/provenance boundary.

### 22.2 Knowledge Islands

"Knowledge Islands" is descriptive shorthand for the set of external
sources §12's generic ingestion boundary may draw from — MOP, SOP, RCA,
KB articles, vendor documentation, troubleshooting guides, engineering
standards, SharePoint-originated governed content, and other approved
technical repositories. It names a category of SOURCE, not a new
architectural layer: every Knowledge Island still flows through the SAME
generic path this document defines — source adapter/ingestion (§12) →
`IngestedKnowledgeDocument` → processing (§13) → governance (§14) →
`KnowledgeRepository` (§15) → retrieval/RAG (§16, §22.1) → Knowledge
Context. A5 (§20) is the first real-corpus proof of this for compound
DOCX/XLSX/PDF/TXT content; it added no per-source-type architecture.

### 22.3 Knowledge vs. Memory

**Knowledge** (this document, §1–§21) is governed, versioned, lifecycle-
managed, approved-or-not organisational content — a `KnowledgeObject`
with a real `LifecycleStatus`.

**Memory** is a distinct, NOT-YET-BUILT concept documented in CLAUDE.md's
architecture-invariants section and in `docs/BUILD_SEQUENCE.md`'s Phase
6/7 target architecture — session/conversation memory (CURRENT, ADK
session state, not organisational knowledge), Case/Fault Context
(CURRENT, `backend/cases/`, durable structured operational state — not
Approved Knowledge), and Experience Memory (FUTURE — prior operational
experience/pattern information). None of the three is a
`KnowledgeObject`, and none of the three carries `LifecycleStatus`.

Worked example: an RCA document describing a past outage, once ingested
and Approved, is **Knowledge** (queryable via RAG, §22.1). The system
independently noticing that three similar incidents previously ended in
the same physical fault is **Experience Memory** — pattern information
about what was *observed*, not a governed instruction. It does not
become Knowledge merely by being observed or repeated.

### 22.4 Experience → Candidate Knowledge → Approved Knowledge

Target governance flow (FUTURE — this specific Experience-to-Knowledge
PROMOTION flow does not exist yet; an Experience Memory FOUNDATION now
does exist as of 6A.8, see §30, but deliberately implements no
promotion path of any kind — recorded here so a future implementation
lands on this document's existing lifecycle model rather than inventing
a parallel one):

```text
operational experience
        ↓
Experience Memory (FUTURE)
        ↓
repeated / valuable learning
        ↓
CANDIDATE knowledge  ──┐
        ↓              │  same three lifecycle states
trusted / human review │  §5/§14 already define —
        ↓              │  no new lifecycle state is
APPROVED knowledge   ──┘  introduced for this flow
        ↓
RAG / Knowledge Context (§22.1)
```

This is the SAME `CANDIDATE → APPROVED → ARCHIVE` lifecycle §5/§14
already govern, not a second governance model. A future Experience
Memory implementation would materialize a `CANDIDATE` `KnowledgeObject`
exactly the way `governance/service.py`'s `materialize_candidate` does
today for an ingested document (§14) — never a shortcut that mints an
`APPROVED` object directly from repeated observation.

**NON-NEGOTIABLE, restated from CLAUDE.md's architecture invariants:**
Memory must never silently become Approved Knowledge. An agent (or a
future Experience Memory mechanism) observing something repeatedly does
not make it organisational truth — only the existing human-gated
`CANDIDATE → APPROVED` transition (§14) does.

### 22.5 MOP/SOP/RCA/KB remain Knowledge, not Skills

`KnowledgeDocumentType` (§3) — MOP, SOP, RCA, KB Article, Troubleshooting
Guide, Operational Procedure, Technical Instruction — are content TYPES
inside Generic KM, describing *what a document is*. A (FUTURE, not built)
**Skill** answers a different question — *how a specialist should
conduct a recurring kind of work* — and is behavioral orchestration, not
a document and not a `KnowledgeDocumentType`. A Skill may RETRIEVE
Knowledge (e.g. an applicable MOP) as part of executing itself, but a
Skill never IS a MOP/SOP/RCA/KB, and ingesting a document never creates
a Skill. See `docs/AGENT_CONTRACT.md` §3a and `docs/BUILD_SEQUENCE.md`'s
Phase 6/7 target architecture for the Skill definition and how it is
expected to consume Knowledge Context (this document), Experience
Memory, Case Context, and Operational Context without owning or
duplicating any of them. `docs/INTELLIGENCE_ARCHITECTURE.md` is the
canonical Phase 6A (`P11`) document for the Skill and Experience Memory
boundaries as they apply inside Phase 6A, the TELCO Context/
applicability-narrowing model, and the hybrid (exact + lexical +
semantic) retrieval boundary that extends — never replaces — this
document's own deterministic 5.1G/5.1H retrieval and provenance rules;
`P11-M00` (6A.0, an architecture/contract freeze) and `P11-M01` (6A.1, a
GCP physical-architecture decision record) are both COMPLETE and
introduced no change to any rule in this document. `P11-M02` (6A.2) and
`P11-M03` (6A.3) each added a small, additive extension — see §23/§24
below.

---

## 23. Phase 6A.2 — Knowledge Applicability bridge (additive, CURRENT)

`backend/knowledge/domain/telco_applicability.py` (new file, P11-M02 /
6A.2) adds `ApplicabilityScopeKind` (`CONSTRAINED`/`EXPLICIT_ANY`/
`UNSPECIFIED`) and `KnowledgeApplicabilityProfile` — a bridge that closes
a real gap §11's own `Applicability` model could not express on its own:
a dimension ABSENT from `Applicability.dimensions` has always meant
"unconstrained" to `evaluate_applicability` (§11.7), which is correct
and UNCHANGED for that function's own narrow question. Phase 6A's
TELCO/customer-isolation requirement needs a stricter, ADDITIONAL
distinction: "this knowledge intentionally applies regardless of vendor"
(`EXPLICIT_ANY`, a real governance decision) must never be confused with
"vendor applicability was simply never reviewed for this document"
(`UNSPECIFIED`, the safe default) — the latter must fail closed for a
future 6A.4 narrowing engine, never silently broaden applicability.

**Nothing in §3–§22 of this document changed.** `Applicability`,
`ApplicabilityContext`, `evaluate_applicability`, and every existing
5.1B/5.1E/5.1G/5.1H rule are byte-for-byte unmodified —
`telco_applicability.py` only reads an existing, already-governed
`KnowledgeObject`'s `applicability`/identity fields (via the explicit
keyword-argument bridge constructor `from_knowledge_object`, never a
live `KnowledgeObject` import) and computes a new, separate,
purely-additive signal alongside them. `evaluate_applicability` itself
is not called, wrapped, or modified — `dimension_scope` is not consulted
by `knowledge_search`/`knowledge_select_evidence` or any other live tool
path today; it exists so a future 6A.4 matching engine has this
fail-closed signal ready when it needs it (SEARCH RESULT != EVIDENCE
USED continues to hold unchanged — see §18/§19).

**Backward compatible with every real, already-ingested A5 document by
construction, not merely by claim:** `explicit_any_dimensions` defaults
to an empty set, so bridging ANY existing `KnowledgeObject.applicability`
— constrained or unconstrained — never raises and never requires
re-ingestion or a migration; verified directly against the real A5
corpus shape (`backend/tests/knowledge/test_telco_applicability.py`).
`CONSTRAINED` and `EXPLICIT_ANY` are structurally mutually exclusive per
dimension (a `ValidationError` if a caller tries to declare both), and
dimension keys are normalized via the SAME `normalize_dimension_key`
§11.3 already defines, so a key compares identically here and in
`Applicability.dimensions`.

---

## 24. Phase 6A.3 — Multimodal Ingestion Industrialization (additive, CURRENT)

P11-M03 (6A.3) audited the real, already-implemented A5 multimodal
ingestion pipeline against a stricter bar: not "does the code exist and
pass its own unit tests" but "does a real file, ingested through the
real production entry point, actually produce retrieval-ready evidence
units." The audit found A5's individual layers (DOCX/XLSX/PDF/OLE
extraction, deduplication, lineage, image interpretation, processing-
status modeling) were already production-ready, evidenced directly
against the real TELCO/RAN validation corpus — but found two real,
concrete gaps, both closed in this pass, plus one genuine defect found
and fixed.

### 24.1 Layer H production wiring (the real integration gap)

`backend/knowledge/processing/compound.py` ("A5 Layer H",
`process_compound_document`) already existed and was already unit-
tested against synthetic fixtures — it bridges an `IngestedKnowledgeDocument`'s
root text AND every artifact's own `extracted_text` into one
`StructuredKnowledgeDocument`, tagging each resulting section with its
owning `artifact_id`. The audit found, by direct `grep`, that NOTHING in
`backend/` (excluding tests) ever called it, and that `materialize_
candidate(` had ZERO production call sites anywhere either — the real
local-file ingestion entry point
(`backend/knowledge_ingestion/local_file_adapter.py`'s `ingest_local_file`)
stopped at producing an `IngestedKnowledgeDocument` and never structured
or governed it. The practical, verified consequence: for a PDF or XLSX
ROOT document specifically, `extract_root_document`'s own returned "root
text" is a short structural summary ("PDF document with N page(s).")
never the real page/sheet content — real content exists only inside each
`KnowledgeArtifact.extracted_text`. Without Layer H wired in, that real
content would never reach a `KnowledgeSection` and would therefore never
be retrievable via `knowledge_search`, even though it was already
correctly extracted and stored.

Closed with `ingest_and_structure_local_file(s)` (new functions,
`local_file_adapter.py`) — a pure composition of the two existing,
UNMODIFIED functions (`ingest_local_file` then `process_compound_document`),
still stopping short of governance (the returned `StructuredKnowledgeDocument`
is ready for an explicit, separate `materialize_candidate` call, exactly
preserving the existing "governance is a deliberate, separate, trusted
step" boundary — A5 instruction section 38/40, unchanged).

Proven end-to-end against the real validation corpus (not merely
synthetic fixtures): `backend/tests/test_knowledge_real_corpus_full_pipeline.py`
extracts, structures, and governs (CANDIDATE → APPROVED) all three real
DOCX files plus a real standalone XLSX workbook, persists them into an
isolated in-memory repository, and runs the real, UNMODIFIED
`KnowledgeRetrievalService`/`TokenOverlapRelevanceScorer` against them —
proving a real query retrieves the Document1 VSWR "no restart" rule from
an actual section (not merely `document.content`), and that a real
embedded XLSX sheet's own content (a query token read at runtime from
the artifact's own extracted text, never hardcoded) is independently
retrievable via its own artifact-tagged section.

### 24.2 XLSX range/table provenance hardening

Audited gap: `extract_xlsx` (`backend/knowledge/ingestion/extractors/xlsx.py`)
produced exactly one artifact per SHEET, with `locator_detail="sheet=<name>"`
only — no range or table-level provenance, despite `KnowledgeArtifact
.locator_detail`'s own docstring already documenting `"sheet=Q3;range=
B2:D10"` as an intended shape. Closed additively, without removing the
existing whole-sheet artifact: `locator_detail` now includes the sheet's
real used range (`Worksheet.dimensions`, e.g. `"sheet=VSWR;range=A1:F32"`),
and every NATIVE Excel Table the workbook itself already declares
(`Worksheet.tables` — an author-defined named range with real headers,
never a heuristically-guessed block of cells) is extracted as its own
`kind="xlsx_table"` child artifact, nested under its owning sheet
artifact, with `locator_detail="sheet=<name>;table=<table name>;range=<ref>"`.
A sheet with no native Table declared produces no `xlsx_table` artifacts
— unchanged behavior for the common case. Required switching
`openpyxl.load_workbook`'s `read_only` flag from `True` to `False`
(empirically verified first, not assumed: `read_only=True`'s
`ReadOnlyWorksheet` does not expose `.tables`/`.dimensions` at all) —
still bounded by the same pre-existing `ExtractionLimits.max_artifact_bytes`
ceiling every XLSX artifact was already subject to.

Proven against real content: the real standalone XLSX validation file
(`Rogers_Core_Outage_Impact_Agent_Surgical_Checklist.xlsx`) contains 8
real sheets and 7 real native Excel Tables — all 7 were correctly
extracted as `xlsx_table` artifacts with real range provenance, live-
verified in this milestone's own validation pass (structural counts
only; no real cell value is asserted or reproduced in any committed test
or document).

### 24.3 DEF-0017 — a real, confirmed defect found and fixed

Writing the Layer H integration tests above found a real, previously-
undiscovered, HIGH-severity defect: `extract_xlsx`/`extract_pdf`, when
invoked as the ROOT document (`dispatch.py`'s `extract_root_document`),
received the literal sentinel STRING `"root"` as `container_artifact_id`
— unlike `extract_docx`'s own already-correct `None` — producing sheet/
page artifacts whose `parent_artifact_id="root"` pointed at an
artifact_id that was never actually present in the resulting document's
own `artifacts` list. `IngestedKnowledgeDocument`'s own lineage validator
correctly rejected this as a dangling parent reference, meaning **any
attempt to ingest a standalone/root-level XLSX or PDF file through the
real pipeline crashed unconditionally** before a governed `KnowledgeObject`
could ever be built. Full record: `docs/DEFECT_REGISTER.md` DEF-0017.
Fixed by widening `container_artifact_id`'s type to `Optional[str]` in
both extractors and passing `None` from `dispatch.py`'s root-level call
sites — proven to change NO artifact_id/content_hash/storage key that
may already exist from a prior real ingestion run (`deterministic_
artifact_id`'s own basis string already treats a missing parent as the
literal word `'root'` internally).

### 24.4 Applicability inheritance — audited, unchanged

6A.3 instruction section 19 requires artifact/evidence-derived content
to inherit document-level applicability, never silently broaden it.
Audited and found ALREADY correct by construction, requiring no code
change: `Applicability` lives only on `KnowledgeObject` (§3/§11) —
`KnowledgeSection`/`KnowledgeArtifact` carry no independent applicability
field of their own, so every section (root-derived or artifact-derived
alike) necessarily shares the exact same, single `Applicability` as its
owning `KnowledgeObject`. There is no mechanism today for a section or
artifact to narrow OR broaden applicability independently — full
inheritance is the only behavior that exists, which trivially satisfies
"never silently broaden."

### 24.5 Known, deliberately-unaddressed limitations (honest, not defects)

- **PDF section/heading detection beyond page number**: `pypdf`'s raw
  page-text extraction carries no reliable font/style metadata, unlike
  DOCX's paragraph styles — building heading detection here would require
  heuristics/guessing, which this codebase's own deterministic-extraction
  discipline forbids. Page-level provenance (`locator_detail="page=<n>"`)
  remains the deterministic ceiling; not changed in 6A.3.
- **Document-level (cross-`knowledge_id`) deduplication**: `materialize_
  candidate` still requires an EXPLICIT `knowledge_id` from its caller —
  nothing prevents a caller from governing the same file's content under
  two different `knowledge_id`s. This is an existing, deliberate 5.1E
  design choice (identity is never invented/inferred), not a 6A.3 gap —
  only artifact-level (nested attachment) deduplication is automatic.
- **Image-interpretation bounded context (`context_by_artifact_id`)**:
  `apply_image_interpretation`'s own `context_by_artifact_id` parameter
  (image + nearby section text -> better interpretation) exists but is
  not populated by `local_file_adapter.py`'s real call site — deriving
  genuinely reliable "nearby text" would require tracking each artifact's
  structural proximity through extraction, a nontrivial, distinct
  enhancement deliberately out of this milestone's own bounded scope.

---

## 25. Knowledge Asset Metadata Standard (6A.3 corrective addendum, additive, CURRENT)

A bounded corrective/additive pass attached to 6A.3 — NOT a reopening or
redesign of 6A.3's own multimodal ingestion architecture (§24, entirely
unmodified by this addendum). Introduces the canonical **Knowledge Asset
Metadata** standard every governed `KnowledgeObject` can use, and that
6A.4 (deterministic TELCO applicability/knowledge narrowing) can rely on.

### 25.1 Three distinct concepts, never collapsed

This addendum is explicit that a `KnowledgeObject` now carries THREE
structurally distinct kinds of information, linked through stable
identities but never merged into one generic dictionary:

1. **Knowledge Asset Metadata** (this section) — what is this asset, who
   owns it, what is its lifecycle, where does it apply, is it approved
   for AI use.
2. **Structural provenance** (§20/A5, `KnowledgeArtifact` lineage,
   unmodified) — exactly where inside the source this evidence
   originated (document → artifact → section → step/table/cell/image).
3. **Derivation provenance** (§20/A5, `KnowledgeArtifact.derived`,
   unmodified) — is this content original source content or derived
   through extraction/normalization/interpretation.

### 25.2 The canonical model

`backend/knowledge/domain/asset_metadata.py` (new file) defines
`KnowledgeAssetMetadata`, one structured container reached via
`KnowledgeObject.metadata.asset_metadata` — a single new, purely
additive field on the existing, otherwise-unmodified `KnowledgeMetadata`
model (`models.py`). Nine category groupings mirror §4's own canonical
table: Identity, Classification, Governance, Ownership, Lifecycle,
Process/Roles, Applicability Scope, Technical Scope, Audit.

Every leaf attribute is one of four typed wrappers, never a bare string
or an untyped dict:

- `TextMetadataField` / `DateMetadataField` / `BooleanMetadataField` —
  single-valued (`raw_value`, `normalized_value`, `source`).
- `MultiValueMetadataField` — genuinely multi-valued attributes (Vendor/
  OEM, Technology/Domain, Related Systems/Tools, Linked Policies/
  Standards, Equipment/Asset, Customer/Operator, Territory/Country) —
  a typed collection (`raw_values`, `normalized_values`, `source`),
  never a comma-joined scalar.
- `ReviewerMetadataField` (a list of `ReviewerEntry`) — Reviewer(s),
  keeping person and organization structurally separate, never collapsed
  into one string (matching `roles.approved_by`/`roles.approver_
  organization`'s own person/organization split).

`source: MetadataSource` records the provenance CHANNEL (document
header/body, file properties, filename, repository/SharePoint metadata,
ingestion configuration, trusted user input, existing Knowledge record,
unknown) — deliberately has NO model-inference member; no field in this
standard is ever populated from LLM speculation (§8's "no metadata
guessing" is a hard architectural property here, not merely a
convention).

### 25.3 Deliberate non-duplication of existing identity fields

`KnowledgeAssetIdentityMetadata` does NOT include `knowledge_object_id`/
`document_title`/`revision`/`document_type` — each already has a single,
authoritative home (`KnowledgeObject.knowledge_id`/`.title`/`.version
.revision`/`.document_type`). Duplicating them would create two sources
of truth for the same fact; the gap matrix (§25.7) documents each as
SUPPORTED via its existing field instead. `KnowledgeDocumentType`
(enums.py) gained five new members for TELCO/operations coverage —
`RUNBOOK`, `HLD`, `ASSESSMENT_REPORT`, `ACTION_PLAN`, `CHANGE_REQUEST`
— a purely additive enum extension (PROCESS/PROCEDURE already map to
the existing `OPERATIONAL_PROCEDURE`; KB already maps to `KB_ARTICLE`).
The pre-existing, dormant `KnowledgeMetadata.owner`/`.classification`
fields (confirmed unused anywhere in this codebase by grep) are left
completely untouched — new canonical fields (`ownership.business_owner`,
`classification.confidentiality_class`, etc.) are added alongside them,
never repurposing or migrating them.

### 25.4 Raw + normalized values, never guessed

`normalize_date_value` (asset_metadata.py) performs ONLY two
deterministic conversions: ISO-8601 parsing, and a bounded Excel/
spreadsheet serial-number interpretation (the well-defined 1899-12-30
epoch every mainstream spreadsheet tool, including `openpyxl`, already
uses) — verified against this addendum's own worked example (serial
`46248` → `2026-08-14`). A deliberate 10,000 floor on the plausible
serial range excludes a bare short number (e.g. a 4-digit year) from
ever being misread as a date serial. Anything else (free text, an
ambiguous format) leaves `normalized_value=None` — the raw string is
always preserved regardless. `normalize_source_lifecycle_stage`
similarly only matches the five canonical stage labels case-
insensitively; an unrecognized value stays unnormalized, never
best-guessed. Boolean flags and language-code normalization are NOT
attempted automatically anywhere in this module — a trusted caller must
supply `normalized_value` explicitly (e.g. there is no hardcoded
`"Uen"` → `"en"` mapping, since no authoritative code table was
available to this addendum — the raw value is preserved either way).

### 25.5 Applicability bridge (feeds 6A.2, never duplicates it)

`backend/knowledge/domain/asset_metadata_applicability_bridge.py` (new
file) adds ONE pure function, `derive_applicability_dimensions_from_
asset_metadata`, mapping six asset-metadata fields (Customer/Operator,
Territory/Country, Equipment/Asset, Vendor/OEM, Technology/Domain,
Related Systems/Tools) into an `Applicability.dimensions`-shaped dict.
It does NOT modify, wrap, or duplicate `KnowledgeApplicabilityProfile`/
`ApplicabilityScopeKind`/`dimension_scope` (§23, byte-for-byte
unmodified). **The critical invariant, preserved unchanged from §23 and
proven by test**: a dimension with zero populated asset-metadata values
is simply ABSENT from the returned dict — never an empty-list key, never
inferred as `EXPLICIT_ANY`. 6A.2's own `dimension_scope`, given that
absence, already correctly resolves it to `ApplicabilityScopeKind
.UNSPECIFIED`, the safe fail-closed default. `EXPLICIT_ANY` remains
exactly what 6A.2 already defined it as — an explicit, separate,
trusted-caller-supplied declaration (`explicit_any_dimensions`) — this
bridge never infers it from a value's text (e.g. a raw value literally
reading `"Any"` becomes an ordinary CONSTRAINED value, not a wildcard;
see the dedicated test proving this).

### 25.6 Governance/lifecycle independence and the two-lifecycle distinction

`KnowledgeAssetGovernanceMetadata` (Source-of-Truth Flag, AI-Approved
Flag, Approval Constraints, Linked Policies/Standards) and
`KnowledgeAssetLifecycleMetadata` (source lifecycle stage, review/expiry
dates) perform NO cross-field inference — `ai_approved_flag=true` never
implies `source_of_truth_flag=true`; an expired `expiry_date` never
auto-flips `ai_approved_flag`. Both facts remain independently
representable and independently readable; deciding what to DO with the
combination is explicitly 6A.4's future eligibility-engine concern, not
built here.

`KnowledgeAssetLifecycleMetadata.lifecycle_stage` (Draft/Under Review/
Active/Deprecated/Archived — the SOURCE document's own self-reported
state) is a GENUINELY DIFFERENT dimension from SLOPANOC's own governance
`LifecycleStatus` (CANDIDATE/APPROVED/ARCHIVE, §14/enums.py) — the two
are never blindly mapped to one another (e.g. `lifecycle_stage="Active"`
does NOT imply `lifecycle_status=APPROVED`); `lifecycle_status` continues
to change ONLY through `transition_lifecycle`/`approve_version`/
`archive_version` (§14, unmodified), never derived from `lifecycle_
stage`. A document can legitimately be source-`Active` while still
SLOPANOC-`CANDIDATE` (not yet reviewed) — proven by test.

### 25.7 Gap matrix (existing support before this addendum)

| Attribute | Existing support | Source of truth after this addendum | Status |
| --- | --- | --- | --- |
| Knowledge Object ID | SUPPORTED | `KnowledgeObject.knowledge_id` (unchanged, not duplicated) | SUPPORTED |
| Document Number | MISSING | `asset_metadata.identity.document_number` | SUPPORTED (structure only — no extractor populates it yet; requires header/body text parsing) |
| Revision | PARTIAL | `KnowledgeVersion.revision`/`.label` (unchanged, not duplicated) | SUPPORTED |
| Document Title | SUPPORTED | `KnowledgeObject.title` (unchanged, not duplicated) | SUPPORTED |
| Document Type | SUPPORTED | `KnowledgeObject.document_type` (extended: +5 members) | SUPPORTED |
| Date (identity, doc's own claimed update date) | MISSING | `asset_metadata.identity.date` | SUPPORTED (structure only — NOT_AVAILABLE_FROM_SOURCE via file properties; would require header/body parsing) |
| File Format | MISSING | `asset_metadata.identity.file_format` | SUPPORTED (extractor: filename) |
| Language Code | MISSING | `asset_metadata.identity.language_code` | SUPPORTED (structure); PARTIAL via extractor — real corpus mostly does not set this OOXML property |
| Confidentiality Class | MISSING | `asset_metadata.classification.confidentiality_class` | SUPPORTED (structure only — NOT_AVAILABLE_FROM_SOURCE via file properties; would require banner-text heuristics, deliberately not attempted) |
| External Confidentiality Label | MISSING | `asset_metadata.classification.external_confidentiality_label` | SUPPORTED (structure only, same caveat) |
| Source-of-Truth Flag | MISSING | `asset_metadata.governance.source_of_truth_flag` | SUPPORTED (structure only — no source deterministically encodes this) |
| AI-Approved Flag | MISSING | `asset_metadata.governance.ai_approved_flag` | SUPPORTED (structure only) |
| Approval Constraints | MISSING | `asset_metadata.governance.approval_constraints` | SUPPORTED (structure only) |
| Linked Policies / Standards | MISSING | `asset_metadata.governance.linked_policies_standards` | SUPPORTED (structure only) |
| Business Owner | PARTIAL (dormant `metadata.owner`) | `asset_metadata.ownership.business_owner` (new, structured; `owner` untouched) | SUPPORTED (structure only) |
| SME Team | MISSING | `asset_metadata.ownership.sme_team` | SUPPORTED (structure only) |
| Domain / Repository of Origin | MISSING | `asset_metadata.ownership.domain_repository_of_origin` | SUPPORTED (structure only) |
| Business Domain | MISSING | `asset_metadata.ownership.business_domain` | SUPPORTED (structure only) |
| Lifecycle Stage | MISSING | `asset_metadata.lifecycle.lifecycle_stage` | SUPPORTED (structure + deterministic 5-value normalization) |
| Next Review Date | MISSING | `asset_metadata.lifecycle.next_review_date` | SUPPORTED (structure + date normalization) |
| Expiry Date | MISSING | `asset_metadata.lifecycle.expiry_date` | SUPPORTED (structure + date normalization) |
| Prepared By | MISSING | `asset_metadata.roles.prepared_by` | SUPPORTED (extractor: DOCX `author`/XLSX `creator`) |
| Creator Organization | MISSING | `asset_metadata.roles.creator_organization` | SUPPORTED (structure only — NOT_AVAILABLE_FROM_SOURCE via file properties) |
| Checked By | MISSING | `asset_metadata.roles.checked_by` | SUPPORTED (structure only) |
| Checker Organization | MISSING | `asset_metadata.roles.checker_organization` | SUPPORTED (structure only) |
| Approved By | MISSING | `asset_metadata.roles.approved_by` | SUPPORTED (structure only) |
| Approver Organization | MISSING | `asset_metadata.roles.approver_organization` | SUPPORTED (structure only) |
| Reviewer(s) | MISSING | `asset_metadata.roles.reviewers` (person+org typed list) | SUPPORTED (structure only) |
| Customer / Operator | MISSING | `asset_metadata.applicability_scope.customer_operator` | SUPPORTED (structure + applicability bridge) |
| Territory / Country | MISSING | `asset_metadata.applicability_scope.territory_country` | SUPPORTED (structure + applicability bridge) |
| Equipment / Asset | MISSING | `asset_metadata.technical_scope.equipment_asset` | SUPPORTED (structure + applicability bridge) |
| Vendor / OEM | MISSING | `asset_metadata.technical_scope.vendor_oem` | SUPPORTED (structure + applicability bridge) |
| Technology / Domain | MISSING | `asset_metadata.technical_scope.technology_domain` | SUPPORTED (structure + applicability bridge) |
| Related Systems / Tools | MISSING | `asset_metadata.technical_scope.related_systems_tools` | SUPPORTED (structure + applicability bridge) |
| Last Modified By | MISSING | `asset_metadata.audit.last_modified_by` | SUPPORTED (extractor: DOCX/XLSX `last_modified_by`) |
| Last Modified Date | MISSING | `asset_metadata.audit.last_modified_date` | SUPPORTED (extractor + deterministic date normalization) |

"SUPPORTED (structure only)" means the typed field/validation/
normalization/persistence path exists and is tested, but no automatic
extractor populates it from the real corpus yet — a future, separate
ingestion enhancement (header/body text parsing, a repository/SharePoint
metadata adapter, or trusted operator input) would populate it without
any further schema change. No row was omitted from §4's canonical
catalogue.

### 25.8 Optional file-properties extractor (NOT wired into 6A.3's ingestion path)

`backend/knowledge/ingestion/asset_metadata_from_file_properties.py`
(new file) provides two read-only functions
(`extract_asset_metadata_from_docx_properties`/`_xlsx_properties`)
populating `file_format`/`language_code`/`roles.prepared_by`/`audit
.last_modified_by`/`audit.last_modified_date` from a real DOCX/XLSX
file's own OOXML core properties — deterministic, no LLM, never writes
to the source file (proven by a before/after SHA-256 hash test).
Deliberately does NOT read OOXML's numeric `revision` property (a
save-count integer, not a document revision label — mapping it would
misrepresent it; proven absent by an AST-based test, not a docstring
claim). **Deliberately NOT wired into `local_file_adapter.py`'s default
`ingest_local_file`/`ingest_and_structure_local_file(s)` pipeline** — per
this addendum's own explicit instruction not to reopen/redesign 6A.3's
ingestion architecture; both functions remain independently callable by
a future, separate integration decision.

### 25.9 Persistence and backward compatibility

No schema/migration change. `slopanoc_knowledge_objects`' existing JSON
`payload` column (§15, dialect-neutral, unchanged since A5) absorbs
`KnowledgeAssetMetadata` transparently, exactly like every other
optional addition to `KnowledgeObject` before it — proven by round-trip
test (`KnowledgeObject.model_dump_json()` →
`KnowledgeObject.model_validate_json()`). Every field in this standard
has a safe empty default, so a REAL pre-addendum persisted payload
(missing the `asset_metadata` key entirely inside `metadata`)
deserializes successfully with every new field reading as
`None`/empty — proven directly (`test_historical_payload_without_
asset_metadata_key_deserializes_successfully`, constructed from a real
`KnowledgeObject` with the key deleted from its own JSON, not a
hand-written fixture guessing at the shape).

### 25.10 Explicitly out of scope

No MOP creation/authoring workflow, no MOP templification, no Knowledge
admission/quality-gate, no minimum-requirement validation engine, no SME
approval workflow, no source-document modification, no 6A.4 deterministic
narrowing/eligibility engine, no embeddings/vector retrieval, no new
agent. A future Knowledge creation/admission capability MAY use this
metadata model to enforce minimum requirements on newly-created MOPs and
other operational Knowledge before they are ingested — that capability
remains intentionally outside this addendum.

---

## 26. Phase 6A.4 — Deterministic TELCO Applicability & Knowledge Narrowing (additive, CURRENT)

`backend/knowledge/narrowing/` (new package: `contracts.py`,
`eligibility.py`, `context_adapter.py`, `applicability_gate.py`,
`service.py`) implements the deterministic filtering layer that produces
a small, permitted, applicable Knowledge candidate set from the full
governed corpus, BEFORE any semantic/vector retrieval (P11-M05, not yet
built) or LLM reasoning ever sees it. Governing principle: **the LLM
must never decide which documents to inspect from the full corpus —
deterministic rules eliminate what is unauthorized, stale, superseded,
customer/vendor/technology-incompatible, or otherwise not applicable,
first.**

### 26.1 Two gates, never one mixed function

```text
ALL GOVERNED KNOWLEDGE
        |
GATE 1 -- AUTHORITY / ELIGIBILITY      (eligibility.py)
        |
ELIGIBLE KNOWLEDGE
        |
GATE 2 -- TELCO APPLICABILITY          (applicability_gate.py)
        |
PERMITTED / EXCLUDED / INDETERMINATE   (all three explicit, never merged)
```

**Gate 1** reuses, never duplicates, the existing frozen 5.1E version/
supersession authority (`governance/versioning.py`'s `resolve_current_
version`) — it adds only the per-OBJECT reason-code mapping that
authority does not itself provide. Governance/asset-metadata policy
signals (AI-Approved, Expiry, Review-Due, Source-of-Truth) are ALWAYS
computed and reported; only AI-Approved and Expiry can ever cause
exclusion, and only when `NarrowingPolicy.enforce_ai_approved`/
`enforce_not_expired` is explicitly set `True` (default `False` —
OBSERVE mode). This is the OBSERVE/ENFORCE boundary §9 of the 6A.4
instruction required: missing newly-added governance metadata never
silently broadens access (a missing flag is never treated as `True`),
and never silently destroys existing retrieval behavior for historical
Knowledge (default policy never excludes on a missing signal) — proven
by `test_narrowing_eligibility.py`'s dedicated historical-object test
and `test_narrowing_service.py`'s policy on/off pair.

**Gate 2** reuses, never wraps or duplicates, 6A.2's own `dimension_
scope`/`ApplicabilityScopeKind` (CONSTRAINED/EXPLICIT_ANY/UNSPECIFIED,
`telco_applicability.py`, byte-for-byte unmodified) and 5.1B's own exact,
normalized dimension-value comparison (`normalize_dimension_value`, also
unmodified — §26 of the 6A.4 instruction's release-matching requirement
is satisfied by this SAME generic exact-match rule, no dimension-
specific ordering/range logic anywhere). What is genuinely new: Gate 2
compares a knowledge object's CONSTRAINED dimension against a FULL,
STATE-AWARE `ContextValue` (KNOWN/UNKNOWN/CONFLICTING/NOT_APPLICABLE,
6A.2's `backend/context/domain/`) rather than a flat "known or nothing"
`ApplicabilityContext` — 5.1G's own `evaluate_applicability` has no way
to express CONFLICTING or NOT_APPLICABLE context at all, so it could not
be reused as-is for this specific comparison (it remains completely
unmodified and is not called by this package).

### 26.2 Canonical-source reconciliation (§4 of the 6A.4 instruction)

A knowledge object can now have up to THREE sources bearing on TELCO
applicability: its own frozen `Applicability.dimensions` (5.1B/A5), the
6A.3-addendum `asset_metadata`-derived dimensions (`derive_
applicability_dimensions_from_asset_metadata`, unmodified), and (new,
6A.4) `asset_metadata.applicability_scope.explicit_any_dimensions`.
`applicability_gate.py`'s `resolve_canonical_dimensions` merges the first
two deterministically: ADDITIVE where only one source declares a
dimension key; FAIL-CLOSED (`METADATA_SOURCE_CONFLICT`, never last-
write-wins) where both declare the SAME key with a DIFFERENT normalized
value set — that dimension is removed from consideration and reported as
`indeterminate`, never silently resolved either direction. The SAME
fail-closed treatment applies if a dimension is claimed as EXPLICIT_ANY
by asset metadata while ALSO CONSTRAINED by the base `Applicability` (an
authoring contradiction that would otherwise crash 6A.2's own
`KnowledgeApplicabilityProfile` construction).

`KnowledgeMetadata.attributes` (the pre-6A.3, untyped, open bag) is
handled completely separately — `LEGACY_ATTRIBUTE_COMPATIBILITY_MAP` is
a deterministic, currently EMPTY allow-list (no real ingested object
populates `.attributes`, confirmed by grep during this milestone's own
audit) — no arbitrary legacy key is ever silently trusted as narrowing
input; adding a real mapping later is a deliberate, reviewable code
change, never an implicit runtime behavior.

### 26.3 The persisted EXPLICIT_ANY gap, closed

6A.2's `KnowledgeApplicabilityProfile.explicit_any_dimensions` existed
only as a live constructor argument with no persisted field anywhere on
`KnowledgeObject` — audited during 6A.4's own AUDIT phase: no real,
already-governed object could ever actually BE `EXPLICIT_ANY` for a
dimension. Closed via ONE new, additive field: `KnowledgeAssetMetadata
.applicability_scope.explicit_any_dimensions: list[str]` (§25's own
model, extended per that section's own explicit permission for a future
milestone to do so) — a list of dimension key NAMES a governance decision
has explicitly declared wildcard, never auto-populated from a value's
own text (a raw value literally reading `"Any"` does NOT become
EXPLICIT_ANY automatically — proven by test).

### 26.4 Result contract and explainability

`KnowledgeNarrowingResult` carries three EXPLICIT, never-merged ID
lists — `permitted_knowledge_ids` (the actual candidate set a future
6A.5 should consume), `excluded_knowledge_ids`, `indeterminate_
knowledge_ids` (Gate-1-eligible objects whose Gate 2 outcome could not be
determined from current context — never silently included as applicable,
never silently excluded either) — plus a full per-object `items` list
(`KnowledgeNarrowingItem`, both gates' own decisions, per-dimension
detail) and `exclusion_reason_counts` for observability. `NarrowingReason
Code` is a small, stable, machine-readable CORE vocabulary (`MATCH`,
`APPLICABILITY_UNSPECIFIED`, `DIMENSION_MISMATCH`, `CONTEXT_UNKNOWN`,
`CONTEXT_CONFLICTING`, `METADATA_SOURCE_CONFLICT`, `SUPERSEDED`,
`ARCHIVED`, `CANDIDATE_NOT_APPROVED`, `NOT_EFFECTIVE`, `AMBIGUOUS_
VERSION_RESOLUTION`, `UNAUTHORIZED` (reserved, unreachable — see §26.5),
`AI_APPROVAL_MISSING`, `AI_APPROVAL_FALSE`, `EXPIRED`, `REVIEW_DUE`) —
WHICH dimension mismatched/was indeterminate lives in each item's own
`applicability.dimension_results`, keeping the core reason vocabulary
small while dimension names stay open (matching `applicability.py`'s own
"dimension-agnostic by design" invariant, preserved unchanged).

### 26.5 Authorization (§22 of the 6A.4 instruction)

Audited before writing Gate 1: no authorization/confidentiality
ENFORCEMENT mechanism exists anywhere in the current Governed Knowledge
architecture (Phase 4H, which would build one, remains future/
unimplemented). There was therefore nothing to "preserve" — `NarrowingReasonCode
.UNAUTHORIZED` exists as a reserved, currently-unreachable code for a
future Phase 4H gate to wire into, never a fabricated check invented
here.

### 26.6 Document Revision vs. Network/Software Release (§6, mandatory)

Kept structurally separate, proven by test (`test_document_revision_and_
network_release_are_independent`, plus a dedicated AST-based source
proof that `applicability_gate.py` never references `.revision`
anywhere): `KnowledgeVersion.revision`/`.label` (the DOCUMENT's own
revision identifier, e.g. `"PA1"`) belongs to Knowledge lifecycle/
versioning (§14, unmodified) and is NEVER read by anything in `backend/
knowledge/narrowing/`. A network/software release value (e.g.
`"24.Q2"`) is an ordinary, open `Applicability.dimensions["release"]`
entry, compared via the same generic exact-match rule as every other
dimension — no special release-ordering/range logic exists anywhere.

### 26.7 Not wired into the live `knowledge_search` tool (§47, deliberate)

Mirrors 6A.2's own `dimension_scope` (not consulted by the live tool-
calling path) and 6A.3's own `ingest_and_structure_local_file` (not
wired into any production ingestion trigger): `backend/knowledge/
narrowing/` is a new, additive, internal service/repository-level API.
`KnowledgeRetrievalService.retrieve()` (5.1G) is BYTE-FOR-BYTE
UNMODIFIED by 6A.4 — verified by an empty scoped `git diff`. A future
milestone may choose to consume `narrow_knowledge`'s output to bound
`retrieve()`'s own corpus; that wiring decision is explicitly deferred.

### 26.8 Artifact-level narrowing (§32) — not needed, audited

`KnowledgeArtifact` carries no independent applicability field (§25's
own audit, unchanged) — every artifact/section trivially inherits its
owning `KnowledgeObject`'s single `Applicability`, already the only
behavior that exists. 6A.4 therefore narrows at the `knowledge_id`
level only; no artifact-level narrowing logic was added or is needed.

### 26.9 Performance

No model call anywhere in `backend/knowledge/narrowing/` or anything it
calls (verified structurally by this milestone's own dependency-boundary
extension — no `google.adk`/`google.genai` import reachable). `narrow_
corpus` is one deterministic Python pass over the corpus (`N` objects ×
one grouping/comparison pass, no `N × M` anything) — at this milestone's
own measured real-corpus scale (3 real governed objects), narrowing
completes as part of the same test process in well under a second; no
new database index or JSONB filter was added or found necessary at this
scale (§35), and none was added.

### 26.10 Corrective pass — unspecified applicability must fail closed

A bounded corrective pass, attached to 6A.4 (not a new sub-milestone),
fixed a real defect found and reproduced before any code change: a
Knowledge object with ZERO declared TELCO applicability intent (no
CONSTRAINED dimension, no EXPLICIT_ANY, no conflict —
`dimensions_of_interest` empty in `applicability_gate.py`'s
`evaluate_telco_applicability`) previously returned `outcome="match"`
merely because nothing was ever checked, allowing it into
`permitted_knowledge_ids`. **Governing rule, now enforced: absence of
applicability information is not evidence of applicability.**

**Fix, minimal and surgical**: the one `if not dimensions_of_interest:`
branch now returns `outcome="indeterminate"` (reusing the existing
`NarrowingReasonCode.APPLICABILITY_UNSPECIFIED`, never a new reason
code) instead of `"match"`. `service.py`'s existing bucket-assignment
logic (`mismatch` → excluded, `indeterminate` → indeterminate, else →
permitted) required NO change — it already routes any `indeterminate`
outcome to `indeterminate_knowledge_ids` correctly. No other function in
`backend/knowledge/narrowing/` was touched — `eligibility.py`
(Gate 1), `context_adapter.py`, `service.py`, and `contracts.py` are
byte-for-byte unmodified by this pass.

**What did NOT need to change, confirmed by audit before any fix**:
partially-constrained profiles (e.g. Vendor=Ericsson, Technology=LTE,
everything else unspecified) and explicitly-generic profiles (any
dimension declared EXPLICIT_ANY) already produced a non-empty
`dimensions_of_interest` under the pre-existing implementation — both
already correctly resolved via the normal per-dimension comparison path,
untouched by this fix. The only genuinely broken case was the "zero
dimensions of interest at all" branch.

**EXPLICIT_ANY remains strictly distinct from UNSPECIFIED**, proven by a
dedicated regression test (`test_unspecified_still_differs_from_
explicit_any`): a wholly EXPLICIT_ANY profile (every dimension
explicitly declared generic) still correctly resolves `match`
unconditionally — only a wholly *undeclared* profile becomes
`indeterminate`. Generic Knowledge must therefore declare its
genericness explicitly via EXPLICIT_ANY; leaving every dimension
unspecified is never an implicit substitute.

**Real-corpus impact, honestly reported**: the same real TELCO/RAN
corpus validated in 6A.4's own closure (3 real governed objects, none of
which declare any TELCO applicability dimension) now correctly resolves
`permitted=0, excluded=0, indeterminate=3` — a change from the
pre-correction `permitted=3, excluded=0, indeterminate=0` the 6A.4
closure report itself recorded. This is the corrected, intended
behavior, not a regression: those three real objects genuinely have no
declared applicability metadata yet, so they correctly cannot be
asserted as customer/vendor/technology-applicable by 6A.5 or any future
consumer until a real governance step populates that metadata.

---

## 27. Phase 6A.5 — Hybrid Knowledge Retrieval & Evidence Selection (additive, COMPLETE — see §27.9/§27.10)

`backend/knowledge/hybrid_retrieval/` (new package: `contracts.py`,
`indexable_text.py`, `embedding.py`, `repository.py`, `fusion.py`,
`reranking.py`, `evidence_selection.py`, `indexing.py`, `service.py`)
implements the retrieval layer that operates ONLY inside 6A.4's own
deterministic `permitted_knowledge_ids` (`backend/knowledge/narrowing/`,
byte-for-byte unmodified) — EXACT + LEXICAL + SEMANTIC search, fused
deterministically (RRF), reranked deterministically, then evidence-
selected, distinct from retrieval. The concrete embedding provider
(`VertexTextEmbeddingProvider`, real Vertex AI `text-embedding-005`)
lives at `backend/knowledge_hybrid_retrieval/` — OUTSIDE `backend/
knowledge/` entirely, mirroring A5's own `gemini_image_interpreter.py`
precedent for the identical dependency-boundary reason.

### 27.1 Candidate-boundary enforcement (mandatory, query-level)

Every one of the three channel methods on `EvidenceIndexRepository`
(`exact_match`/`lexical_search`/`semantic_search`) takes `permitted_
knowledge_ids` as a REQUIRED parameter and constrains the SQL query
itself (`WHERE knowledge_id = ANY(:ids)`) — never a Python-side filter
applied after an unconstrained query. An empty `permitted_knowledge_ids`
short-circuits `hybrid_retrieve` before any channel is ever called, and
before any database connection is used for search at all. Proven, not
merely claimed: `test_hybrid_retrieval_repository_postgres.py`'s own
`test_real_postgres_exact_match_respects_permitted_ids`/`test_real_
postgres_lexical_search_respects_permitted_ids` insert an "EXCLUDED"
row into the REAL database and assert it is never returned when queried
with a different permitted set; `test_hybrid_retrieval_narrowing_
integration.py` chains a REAL, unmodified `narrow_corpus` (6A.4) result
directly into `hybrid_retrieve`, proving an excluded (vendor/technology
mismatch) and an indeterminate (unspecified applicability) document
never appear even as an intermediate hit from any channel.

### 27.2 Retrieval granularity and provenance (§7/§8)

SECTION granularity (never whole-document), mirroring 5.1G's own
`KnowledgeRetrievalItem` — `EvidenceIndexRecord` carries `knowledge_id`/
`version_label`/`section_id`/`artifact_id`, a durable route back to the
governed source. `resolve_is_derived`/`build_indexable_text`
(`indexable_text.py`) deliberately duplicate (never import) 5.1G's own
`_resolve_is_derived` logic and A5's XLSX-table locator convention, per
the same architectural-layer independence discipline `narrowing/
eligibility.py` already established.

### 27.3 Fusion (RRF) and deterministic reranking

`fusion.py` implements Reciprocal Rank Fusion — chosen specifically
because exact/lexical/semantic raw scores live on incomparable scales,
and RRF needs no cross-channel normalization (operates on rank position
only). `reranking.py` combines the RRF fusion score with an exact-match
boost, a multi-channel-agreement bonus, and a source-over-derived tie-
break, with a final deterministic `evidence_id` tie-breaker — proven,
by direct source-scan test, to contain NO `google.adk`/`google.genai`
import anywhere.

### 27.4 Evidence selection, distinct from retrieval

`evidence_selection.py`'s `select_evidence` is a bounded top-K budget
filter over an ALREADY-reranked candidate list — retrieval candidates
are never automatically evidence (the same `SEARCH RESULT != EVIDENCE
USED` invariant §17/§19 already established for Generic KM, now
extended to hybrid retrieval).

### 27.5 Index synchronization (§24)

`indexing.py`'s `index_knowledge_object` batches an entire object's
sections into ONE embedding call, skips already-current content via a
`content_hash` comparison performed BEFORE any embedding call (proven
by test: the fake embedding provider is never invoked for unchanged
content), and never lets an embedding-provider failure block exact/
lexical indexing for the same evidence unit (the row is still upserted,
`embedding=None`).

### 27.6 Real embedding model decision (§12/§46)

`text-embedding-005` (Vertex AI), 768 dimensions — verified by a REAL,
live `embed_content` call during this milestone's own AUDIT phase
(project `pr-msn-dev-gl-slopai-01`, location `europe-west3`) before being
committed to, never assumed from remembered documentation. A real
3-sentence similarity check produced the expected ordering (a
semantically related sentence scored higher than an unrelated one),
confirming genuine usability for TELCO-shaped operational text.
Formalized as `test_hybrid_retrieval_embedding_real_api.py`, itself
gated on real API reachability (skips, never fails, if unreachable).

### 27.7 RESOLVED BLOCKER: pgvector extension installation

EARLIER IN THIS MILESTONE, the real DEV Cloud SQL PostgreSQL instance's
own IAM database role (`costin.ionita@ericsson.com`, at that time a
member of `cloudsqliamuser`/`slopanoc_migrator`/`slopanoc_runtime`) was
NOT a member of `cloudsqlsuperuser` — `CREATE EXTENSION vector` was
probed directly (a rolled-back transaction) AND attempted through the
real 6A.5 Alembic migration (`a1f3c9e07b21`) itself; BOTH failed
identically:

```
asyncpg.exceptions.InsufficientPrivilegeError: permission denied to
create extension "vector"
HINT: Must be superuser to create this extension.
```

Per this milestone's own explicit instruction, this was NOT worked
around by self-granting a role, switching credentials, or bypassing
Alembic with ad-hoc DDL — the finding was reported, and the migration
file was left as the correct, intended production schema, pending a
`cloudsqlsuperuser`-privileged administrative action.

**LATER IN THE SAME MILESTONE, that administrative action was performed
externally** (confirmed via a fresh, independent `pg_roles` query
showing `cloudsqlsuperuser` newly present in the role's memberships —
not initiated, requested, or worked toward by this codebase or this
session in any way): `CREATE EXTENSION vector` was re-probed and
succeeded; `pg_extension` confirmed `extversion 0.8.5` genuinely
installed. The real Alembic migration (`a1f3c9e07b21`) was then applied
for real (`alembic upgrade head`, `9b6df6490c0e -> a1f3c9e07b21`),
creating `slopanoc_knowledge_evidence_index` with a genuine `vector(768)`
column (confirmed via `information_schema.columns`: `udt_name = vector`,
not `text`). **This extension installation is a permanent, persistent
database-level fact** — confirmed to survive independently of which role
is later connecting (see §27.10 for the subsequent role-membership
reversion, which does NOT re-remove the extension). The pgvector-
specific blocker this section originally recorded is CLOSED.

### 27.8 Degraded-mode fallback (§42/§43) — a real, tested capability

`EvidenceIndexRepository.ensure_schema()` does NOT raise when `CREATE
EXTENSION vector` fails — it creates a DEGRADED table (identical exact/
lexical columns; `embedding` stored as a plain, never-searched `TEXT`
column) and sets `vector_available = False`. `semantic_search()` then
returns `[]` WITHOUT executing any query, and `hybrid_retrieve`'s own
`RetrievalTelemetry.semantic_channel_mode` reports `"degraded_no_vector_
extension"` — EXPLICITLY, never indistinguishable from "genuinely zero
semantic matches." This let EXACT and LEXICAL channels be validated for
real against the actual DEV database (12 real integration tests, all
passing) despite the confirmed extension blocker — a real capability
this milestone's own real-stack failure forced into existence and then
tested, not merely a theoretical allowance.

### 27.9 Real end-to-end pgvector similarity search: VALIDATED

Once the extension was installed (§27.7) and the real migration applied,
a real, live, end-to-end semantic-search proof was performed against the
real DEV database: two real `KnowledgeObject`s (one about an antenna-
feeder VSWR fault, one about an unrelated billing-portal password reset)
were indexed via `index_knowledge_object` using the REAL
`VertexTextEmbeddingProvider` (real Vertex `text-embedding-005` calls,
confirmed via a real `embedding_model="text-embedding-005"` value
recorded on the stored row); `hybrid_retrieve` was then called with a
query worded so differently from the indexed text that the exact and
lexical channels contributed ZERO hits (`exact_hit_count=0`,
`lexical_hit_count=0`), isolating the semantic channel as the ONLY
possible source of a correct ranking. Real, captured output:

```
vector_available after ensure_schema: True
semantic_channel_mode: executed
exact_hit_count: 0
lexical_hit_count: 0
semantic_hit_count: 2
candidates (knowledge_id, fusion_score, rerank_score):
  PGVEC-VALIDATION-RELEVANT   0.01639  0.01639
  PGVEC-VALIDATION-UNRELATED  0.01613  0.01613
top result: PGVEC-VALIDATION-RELEVANT
REAL PGVECTOR SEMANTIC SEARCH VALIDATION: PASSED
```

This is the real, live proof that a genuine `<=>` cosine-distance query
against real stored `vector(768)` embeddings correctly ranks semantically
related content above unrelated content, using real Vertex embeddings,
against the real DEV Cloud SQL instance — the exact capability §27.9's
own prior PARTIAL status was blocked on. The validation rows were
deleted afterward (self-cleaning, no residue left in the shared schema).
`test_hybrid_retrieval_service.py::TestRealEndToEnd::test_real_semantic_
channel_ranks_semantically_related_content_first` (new) codifies this
exact proof as a permanent, repeatable test.

### 27.10 Milestone status: COMPLETE — with an honest, current
operational note on role-membership volatility

**6A.5 is COMPLETE.** The pgvector privilege blocker this milestone's
own exit criteria were conditioned on — "if any required real-stack
component cannot be validated, report PARTIAL/BLOCKED" — is resolved:
the extension is genuinely, persistently installed, the real migration
was genuinely applied, and real pgvector similarity search was genuinely
validated end-to-end (§27.9), all without this codebase or session ever
requesting, granting, or working around the privilege itself.

HONEST OPERATIONAL NOTE, discovered later in the SAME milestone (not a
reopening of the blocker, a distinct, narrower, ordinary fact): shortly
after the validation in §27.9, this session observed that the current
developer IAM identity's role memberships had been externally reverted
to a bare `cloudsqliamuser` (no `slopanoc_migrator`, no `slopanoc_
runtime`, no `cloudsqlsuperuser`) — confirmed via a fresh `pg_roles`
query showing only `cloudsqliamuser` and the identity itself. A
concurrently-running background regression pass's own (pre-fix) test
teardown also dropped the just-migrated `slopanoc_knowledge_evidence_
index` table before this reversion was noticed. As a direct, live-
observed consequence:

- The pgvector EXTENSION remains installed (confirmed again after the
  reversion: `pg_extension.extversion = 0.8.5`) — this is a permanent
  database-level fact, independent of which role is connected.
- The evidence-index TABLE no longer exists on the shared DEV database
  right now, and re-creating it (re-running `alembic upgrade head`)
  requires `slopanoc_migrator` membership to be restored to the
  developer identity first — an ORDINARY migration precondition
  identical to every prior milestone's migration work (e.g. 6A.2), NOT
  a reappearance of the pgvector-specific blocker.
- `test_hybrid_retrieval_repository_postgres.py` and `test_hybrid_
  retrieval_service.py::TestRealEndToEnd` were made deliberately
  environment-adaptive as a direct result of observing this fluctuation
  within a single session: they SKIP cleanly (never fail, never
  fabricate) whenever schema-level DDL is currently denied, and they
  assert full real-mode behavior whenever it is available — proven to
  do both correctly within this same session (13 passed / 14 skipped
  when run against the reverted state; all real-mode assertions
  previously observed passing against the elevated state, per §27.9).

CARRY-FORWARD (6A.6 precondition, not a 6A.5 blocker): before any future
milestone performs LIVE indexing/retrieval work against the real DEV
database, restore `slopanoc_migrator` (schema) and `slopanoc_runtime`
(DML) membership to the developer identity, then re-run `alembic upgrade
head` once to re-create `slopanoc_knowledge_evidence_index` — a routine
operation, already proven to work, not a design gap.

**UPDATE — this carry-forward item is now CLOSED, see §27.11**: the
user independently restored `slopanoc_migrator`/`slopanoc_runtime`
membership, and a dedicated corrective pass repaired the resulting
Alembic/schema drift and re-created the table for real. §27.10's own
"table no longer exists" statement above is a HISTORICAL record of the
state observed at THIS point in the milestone — it is superseded, not
retroactively edited, by §27.11.

### 27.11 CORRECTIVE PASS — Schema Drift Repair, Destructive-Test-
Teardown Defect (DEF-0018), and Final Live DEV Proof

A bounded corrective pass attached to 6A.5 (not 6A.6, not a redesign of
hybrid retrieval), triggered by the user's own independent live-DEV
query proving genuine Alembic/physical-schema drift: `alembic_version`
recorded `a1f3c9e07b21` as applied, but `to_regclass('public.slopanoc_
knowledge_evidence_index')` returned `NULL` — the real table §27.10
documented as migrated no longer existed, while Alembic's own bookkeeping
still claimed it did.

ROOT CAUSE, confirmed by direct inspection, not speculation: `test_
hybrid_retrieval_repository_postgres.py`'s `repository` pytest fixture
tore down with `DROP TABLE IF EXISTS slopanoc_knowledge_evidence_index`
after EVERY test — harmless when written (the table was not yet a real
migrated object), but destructive from the moment the real
`a1f3c9e07b21` migration was successfully applied earlier in this same
milestone. This is now registered as **DEF-0018** (`docs/DEFECT_
REGISTER.md`).

REPAIR SEQUENCE, each step verified live before proceeding to the next:
1. Audited `a1f3c9e07b21`'s complete owned-object set (one table, one
   primary key, three named indexes, one idempotent `CREATE EXTENSION
   IF NOT EXISTS vector`) and confirmed it is a direct, single-head
   child of `9b6df6490c0e` with no unrelated newer migration.
2. Confirmed every migration-owned object was genuinely absent (table,
   all three named indexes) — consistent with a physical state matching
   `9b6df6490c0e`, never partially/ambiguously migrated.
3. `alembic stamp 9b6df6490c0e` — bookkeeping-only, no DDL — verified
   immediately (`alembic_version` -> `9b6df6490c0e`, table still absent,
   pgvector still `0.8.5`, untouched).
4. `alembic upgrade head` — the REAL migration re-ran normally, no
   ad-hoc DDL, no manual index recreation. Verified: `alembic_version`
   -> `a1f3c9e07b21`; `embedding` column genuinely `udt_name=vector`
   (not `text`); `text_search_vector` genuinely `tsvector`; all three
   named indexes plus the primary key present; all ten `NOT NULL`
   constraints present; pgvector still `0.8.5`, untouched throughout.
5. Fixed DEF-0018: the fixture never issues table-level DDL again.
   Every evidence_id this file inserts now goes through a new `_eid()`
   helper applying a module-level `_TEST_EVIDENCE_ID_PREFIX` namespace;
   teardown is now `DELETE ... WHERE evidence_id LIKE '<prefix>%'` —
   test-owned rows only, never the table. A new dedicated regression
   test, `test_repository_fixture_teardown_never_drops_table_or_
   unrelated_rows`, inserts one row OUTSIDE the prefix (simulating real,
   permanent, unrelated data already in the shared table) and one row
   inside it, invokes the EXACT teardown statement directly, and asserts
   the foreign row survives, the prefixed row is gone, and the table
   itself still exists.
6. Real retrieval re-proven against the repaired, real table: exact
   match, lexical/FTS, and — critically — real pgvector semantic search
   (`test_real_postgres_semantic_search_orders_by_real_cosine_distance`,
   hand-crafted vectors; `TestRealEndToEnd::test_real_semantic_channel_
   ranks_semantically_related_content_first`, real Vertex embeddings) —
   both passed against the newly-real, migrated table. The full 6A.4→6A.5
   candidate-boundary chain (`test_hybrid_retrieval_narrowing_
   integration.py`) and the empty-permitted-set short-circuit proof were
   both re-run and passed unchanged.
7. Full backend regression re-run against the repaired live database:
   **3452 passed, 1 skipped, 2 failed** — both failures
   (`test_p4b3_source_provenance.py::test_direct_unique_source_present_
   and_matches_teams_contract`, `test_r1_r3_correctness_regression.py::
   test_full_ambiguous_to_resolved_flow_call_graph`) reconfirmed, by
   direct standalone re-run, as the SAME pre-existing, order-dependent
   flakiness already documented across the D2/6A.4/original-6A.5
   closures (both pass cleanly alone); a scoped diff proved this
   corrective pass touched none of their implementation paths
   (`backend/tools/teams/`, `backend/agents/team_manager/`, `backend/
   api/selection_service.py`, `backend/api/read_continuation_execution
   .py` all empty-diff).
8. MANDATORY post-test live-DEV re-verification, performed AFTER the
   full regression suite completed (this is the check that would have
   caught the original DEF-0018 symptom): `alembic_version =
   a1f3c9e07b21`; table PRESENT; row count `0` (no test residue); all
   four indexes (three named + primary key) PRESENT; pgvector `0.8.5`;
   role memberships unchanged (`cloudsqliamuser`/`slopanoc_migrator`/
   `slopanoc_runtime`, no `cloudsqlsuperuser`, no IAM action taken by
   this pass at any point).

NOT DONE, per explicit instruction: no `GRANT`/`ALTER ROLE`/IAM
modification of any kind; no retrieval-architecture change; no touch to
Team Manager/Incident Manager/Teams/frontend/approval-write/6A.2/6A.3/
6A.4 semantics; 6A.6 not started.

**STATUS: 6A.5 / P11-M05 remains COMPLETE**, now with the schema drift
repaired, DEF-0018 fixed and regression-tested, and a final, live,
post-test-verified proof that the real migrated schema survives a full
backend regression run intact. **P11-M06 (6A.6 — Context Engineering &
Evidence Package) is NEXT — not started by this corrective pass.**

## 28. Phase 6A.6 — Context Engineering & Evidence Package (additive, COMPLETE)

`backend/context_engineering/` (new package: `contracts.py`,
`assembly.py`, `fingerprint.py`, `rendering.py`) implements the
deterministic PLATFORM CAPABILITY that converts already-trusted,
already-computed context/evidence into one versioned, auditable
`ContextPackage` for a FUTURE specialist reasoning layer to consume.
**Never an agent, never an LLM call, never a second Knowledge/Case/
TELCO-Context authority** — the package performs no reasoning of its
own (docs/INTELLIGENCE_ARCHITECTURE.md §4).

### 28.1 Pure assembly, zero I/O (§25/§56/§57)

`assemble_context_package(input_: ContextPackageInput) -> ContextPackage`
is a synchronous, side-effect-free, single-parameter function. Every
input is data the CALLER already fetched from the real, authoritative
source — `telco_context_state` is exactly `TelcoContextService.get_
context_state(...)`'s own return value (6A.2); `evidence_selection` is
exactly `evidence_selection.select_evidence(...)`'s own return value
(6A.5); `case_context` is an already-built `CaseContextSnapshot`
(existing Case contract). The function has NO code path to fetch any of
these itself — no database access, no `repository.list_all()`
equivalent, no `narrow_corpus`/`hybrid_retrieve`/`select_evidence` call
of its own. Enforced by two independent test files: `backend/tests/
context_engineering/test_dependency_boundary.py` (AST import-prefix
check + real fresh-subprocess standalone-import probe, mirroring 6A.2/
6A.4/6A.5's own established pattern — forbidding `google.adk`/
`google.genai`/`backend.agents`/`backend.tools`/`backend.knowledge.
narrowing`/`backend.knowledge.hybrid_retrieval.{repository,service,
indexing,embedding}`/any SQL driver, while positively proving the ONLY
`backend.knowledge.hybrid_retrieval` import anywhere in the package is
`.contracts`, never the retrieval service itself) and `test_no_
knowledge_bypass.py` (a narrower, call-level AST scan proving `assembly
.py` never CALLS `narrow_corpus`/`hybrid_retrieve`/`select_evidence`/
`list_all`/`evaluate_applicability`, even though `.contracts` types are
importable).

### 28.2 The `ContextPackage` contract (§7/§8/§9)

Versioned via `context_schema_version` (currently `"1.0"`) — a NEW
optional field may be added without bumping this; a field removal or
meaning change would require a bump. Identity is expressed through
already-legitimate fields (`owner_kind`/`owner_id`/`session_id`/
`case_id`/`request`) — never a fabricated `package_id`; the practical,
deterministic identity for auditability is `content_fingerprint` (§28.6).
Fields: `telco_context: list[TelcoDimensionView]`, `case_context:
Optional[CaseContextSnapshot]`, `operational_observations: list[
OperationalObservation]`, `evidence: EvidencePackage`, `provenance_
manifest: list[ProvenanceManifestEntry]`, `assembly_trace:
AssemblyTrace`, `content_fingerprint: str`, `created_at: datetime`
(deliberately excluded from the fingerprint — see §28.6).

### 28.3 TELCO Context integration — never resolved, never guessed (§11/§12/§13)

`TelcoDimensionView`/`TelcoAssertionView` are package-facing projections
of 6A.2's own `ContextValue`/`ContextAssertion` — same `state`/
`raw_value`/`canonical_value`/`origin`/`source_reference`/`asserted_at`
fields, never fewer. KNOWN, UNKNOWN, CONFLICTING, and NOT_APPLICABLE all
survive verbatim: a dimension with zero assertions is simply absent
(never fabricated as UNKNOWN); a CONFLICTING dimension keeps every
disputed assertion, never collapsing to one value; a MULTI-cardinality
dimension (e.g. `ALARM`) keeps every distinct concurrent value.
Dimensions are rendered in a stable, sorted order (by `dimension.value`)
so repeated assembly is never at the mercy of `compute_context_state`'s
own dict-iteration order.

### 28.4 Case Context integration (§14)

Accepts the ALREADY-EXISTING, already-budgeted `backend.cases.schemas
.CaseContextSnapshot` verbatim — no parallel Case model, no re-derived
truncation logic (`context_truncated`/`total_item_count`/`included_item_
count` all pass through exactly as `backend.cases.snapshot.build_case_
context_snapshot` computed them). `backend.cases.service`/`.db`/
`.snapshot` are all on the forbidden-import list — 6A.6 never fetches or
builds a snapshot itself.

### 28.5 Evidence Package integration — SEARCH RESULT != EVIDENCE USED (§18/§19/§20/§55)

`EvidencePackage`/`EvidenceItemView` are built EXCLUSIVELY from
`ContextPackageInput.evidence_selection.selected` (6A.5's own
`EvidenceSelectionResult`) — never the raw candidate set, never a
re-run of retrieval. `selected_rank` preserves 6A.5's own deterministic
reranked order (never re-sorted); `channel_scores` is a deterministically
channel-name-sorted `{channel: raw_score}` map — a retrieval/ranking
SIGNAL only, never a correctness probability. `source_evidence_count`/
`derived_evidence_count` are computed from each item's own `is_derived`
flag (never re-derived independently). No embedding vector is ever
exposed (proven structurally: `EvidenceItemView` has no field capable of
carrying one). Proven end to end with a real chained 6A.2→6A.4→6A.5→6A.6
pipeline (§28.9): if 5 candidates were retrieved and 2 selected, the
package contains exactly 2 evidence items, never 5, and never a
candidate from an excluded/indeterminate Knowledge object.

### 28.6 Deterministic assembly + content fingerprint (§26/§27/§53)

`fingerprint.compute_content_fingerprint` computes a SHA-256 hex digest
over the package's canonical JSON dump (`sort_keys=True`, compact
separators) — excluding ONLY `created_at` and `content_fingerprint`
itself (`_EXCLUDED_FIELDS`, proven exact by a dedicated test). Given
identical logical `ContextPackageInput`, repeated assembly always
produces the identical fingerprint (proven across 10 repeated calls,
and across two inputs constructed with assertions in reversed order —
`compute_context_state`'s own dict-iteration nondeterminism is fully
absorbed by `assembly.py`'s own stable dimension sort, see §28.3).

### 28.7 Owner isolation (§10/§50)

`assemble_context_package` accepts exactly ONE `(owner_kind, owner_id)`
pair, ONE optional case, ONE optional session per call — structurally,
there is no parameter shape that could combine two owners' data. Proven
behaviorally: two independently-constructed packages for two different
sessions (or two different cases) never share a TELCO value, a case
item, or an evidence item; a CASE-owned package never silently inherits
an unrelated session_id.

### 28.8 Rendering — faithful, deterministic, never reasoning (§28/§29/§34)

`rendering.render_context_package_as_text` is a SEPARATE adapter (never
the canonical model) producing labelled REQUEST/TELCO CONTEXT/CASE
CONTEXT/OPERATIONAL CONTEXT/KNOWLEDGE EVIDENCE/UNCERTAINTIES/SOURCE
MANIFEST sections. Proven, by AST source-scan, to define no function
whose name suggests answering/inferring/recommending/resolving, and to
import nothing agent/LLM-shaped. Per-evidence-item truncation (a
deliberately generic, non-query-tuned per-item character budget) is
NEVER hidden — a truncated item's own identity is always fully shown,
and the rendered text states both the rendered and the real original
character count.

### 28.9 Controlled end-to-end 6A.2→6A.6 proof

`backend/tests/context_engineering/test_end_to_end_6a2_to_6a6.py`
chains the REAL, UNMODIFIED `compute_context_state` (6A.2) →
`narrow_corpus` (6A.4) → `hybrid_retrieve` (6A.5) → `select_evidence`
(6A.5) → `assemble_context_package` (6A.6), using the exact controlled
scenario this milestone's own instruction specified: Case
`CASE-6A6-001`, Customer Vodafone, Domain RAN, Vendor Ericsson,
Technology LTE, Alarm VSWR, one intentionally UNKNOWN dimension
(Release), one operational observation, one case fact, one SOURCE
evidence item (a real, structurally-extracted section) and one DERIVED
evidence item (a real section linked to a `KnowledgeArtifact
(derived=True)`, exercising the REAL `resolve_is_derived` resolution,
not a hand-set flag). Plus the two other mandatory controlled proofs:
a CONFLICTING-vendor scenario (never resolved to one vendor) and a
no-evidence scenario (a valid, empty-evidence package, no fallback to
excluded Knowledge). Uses the SAME `FakeIndexRepository`/
`FakeEmbeddingProvider` pattern `test_hybrid_retrieval_narrowing_
integration.py` (6A.5) already established — proves the 6A.4→6A.5→6A.6
CHAINING logic; the real-Postgres/pgvector proof itself remains 6A.5's
own closure (§27.9), not re-proven here.

### 28.10 Not implemented, by design, per explicit instruction

No new Cloud SQL table/persistence (`assemble_context_package` is pure,
in-process, ephemeral — every input is already persisted elsewhere);
no Skills (6A.7); no Experience Memory (6A.8); no Troubleshooting
Manager (6A.9); no dual-specialist orchestration (6A.10); no Phase 7
troubleshooting loop; no invented confidence scores; no operational-
observation COLLECTION mechanism (the `OperationalObservation` contract
is a generic, forward-compatible SLOT with no producer wired anywhere
in this codebase — ITSM/Alarm/Topology/KPI/Change/Handover connectors,
P13/5.2–5.7, remain the future sources); no live wiring into Incident
Manager, Team Manager, or the `knowledge_search`/`knowledge_select_
evidence` tools (that live-wiring is explicitly future specialist-
integration scope, 6A.9/6A.10, not 6A.6's own contract-and-assembly
scope).

### 28.11 Regression and non-regression

47 new tests across 9 new test files in `backend/tests/context_
engineering/` (dependency boundary, TELCO integration, Case integration,
evidence integration, owner isolation, determinism/fingerprint, empty
states, rendering, no-Knowledge-bypass, end-to-end 6A.2→6A.6) — all
passing. Full backend regression, `npm run build`, and `git diff
--check` results are recorded in this milestone's own closure report.
Confirmed via empty scoped `git diff`: `backend/agents/`, `backend/
tools/`, `backend/api/`, `src/` (frontend), and every 6A.2/6A.3/6A.4/
6A.5 file (`backend/context/domain/`, `backend/context/sqlalchemy/`,
`backend/knowledge/narrowing/`, `backend/knowledge/hybrid_retrieval/`)
all remain byte-for-byte untouched by this milestone.

**STATUS: 6A.6 / P11-M06 is COMPLETE.** See CLAUDE.md's own 6A.6 closure
section for the full narrative.

## 29. Phase 6A.7 — Skills Framework (additive, COMPLETE)

`backend/skills/` (new package: `contracts.py`, `versioning.py`,
`fingerprint.py`, `loader.py`, `registry.py`, `readiness.py`,
`applicability.py`, `rendering.py`) implements the canonical, typed,
deterministic **Skill** contract: a reusable operational METHODOLOGY
("how do I perform this operational method?") — **never an agent, never
Knowledge, never a Tool, never memory, never a free-form prompt, never a
workflow engine, never an autonomous executor** (restating, never
contradicting, `docs/AGENT_CONTRACT.md` §3a and `docs/INTELLIGENCE_
ARCHITECTURE.md` §11).

### 29.1 Pure, declarative, zero I/O (enforced dependency boundary)

`backend/skills/` imports ONLY `backend.context.domain.enums`
(`ContextDimension` — a type) and `backend.context_engineering.
contracts` (`ContextPackage` — a type, for readiness/applicability
function signatures only) — never a service/repository/database module
of any domain, never `google.adk`/`google.genai`, never `backend.
agents`/`backend.tools`, never `backend.knowledge.*`, never `backend.
cases.*`. Enforced by `backend/tests/skills/test_dependency_boundary.py`
(AST import-prefix check + a POSITIVE proof that the only permitted
`backend.context_engineering` import is `.contracts`, never `.assembly`
+ a real fresh-subprocess standalone-import probe) and `test_no_
execution_no_bypass.py` (a narrower call-level AST scan proving no
module in the package calls a Knowledge/retrieval/LLM/agent-shaped
function, writes a file, or makes a network call).

### 29.2 The canonical `SkillDefinition` contract

`skill_schema_version` (the CONTRACT's own structure version, currently
`"1.0"`) is deliberately SEPARATE from `version` (the METHODOLOGY
revision, a semantic-version string, e.g. `"1.0.0"`) — conflating the
two was an explicit, audited risk this milestone avoided. Fields:
`skill_id`, `version`, `lifecycle` (`ACTIVE`/`DEPRECATED` — deliberately
NOT Knowledge's `CANDIDATE`/`APPROVED`/`ARCHIVE`, a different governed-
artifact type with no approval workflow implemented here), `name`,
`description`, `objective`, `applicability` (`SkillApplicability`),
`context_requirements` (typed 6A.2 `ContextDimension` references —
invalid values fail validation structurally, via the enum type itself),
`requires_case_context`, `evidence_requirement` (`minimum_selected_
items`/`requires_source_evidence`), `capability_requirements` (minimal
symbolic identifiers, e.g. `alarms.read`, format-validated only — no
capability registry exists or is built), `methodology` (ordered,
non-executable `MethodologyStep`s), `expected_output`, `guardrails`,
`metadata`. `model_config = {"extra": "forbid"}` — an unknown field
fails closed at the pydantic layer itself.

### 29.3 Semantic versioning (§18/§19/§20)

Uses the `packaging` library's `Version` (promoted from an already-
installed transitive dependency to an explicit pinned direct one — see
`requirements.txt`) for correct ordering (`1.9.0 < 1.10.0`, never naive
lexical string comparison, which gets this backwards). `SkillDefinition
.version` is validated at construction time — an invalid semantic
version fails closed immediately, never silently coerced or compared
lexically downstream.

### 29.4 Strict, fail-closed declarative loading (§13/§14/§48)

Production Skill definitions are YAML, parsed via `yaml.safe_load`
EXCLUSIVELY (`backend/skills/loader.py`) — never `yaml.load`, never a
custom constructor capable of arbitrary object instantiation (proven by
a dedicated AST source-scan test asserting the module calls only
`yaml.safe_load`, and by a live test feeding a real `!!python/object/
apply:...` payload and confirming it fails closed rather than
executing). `load_skill_definitions_from_directory` loads in a
DETERMINISTIC filename-sorted order (never filesystem/glob iteration
order) and raises immediately on the first malformed file, naming its
exact path — it never silently skips a bad file and continues.

### 29.5 Deterministic registry (§45-§49)

`SkillRegistry` is immutable once constructed (no `add`/`remove`
method) — duplicate `(skill_id, version)` identity is rejected AT
CONSTRUCTION TIME, regardless of whether the two definitions happen to
be content-identical (never "last-loaded wins"/"first-loaded wins").
`list_skills()` returns a stable order (sorted by `skill_id`, then
PARSED semantic version — never lexical/insertion/filesystem order).
`resolve_exact(skill_id, version)` always resolves an explicit historical
reference to exactly that version if present — never silently
substituted with a newer one; raises `SkillNotFoundError` (never returns
`None`) on a clean lookup failure. `resolve_latest_active(skill_id)`
selects the highest valid version among `ACTIVE` lifecycle only —
`DEPRECATED` versions remain exact-resolvable but are excluded from this
discovery helper, with NO automatic fallback to a `DEPRECATED` version
merely because no `ACTIVE` one exists.

### 29.6 Deterministic content fingerprint (§21/§63)

Unlike 6A.6's `ContextPackage` (which has a real, volatile `created_at`
to exclude), `SkillDefinition` is a purely declarative, static artifact
with no timestamp field at all — the ENTIRE canonical model is
fingerprinted. Several list fields (`context_requirements`,
`capability_requirements`, `guardrails`, `applicability.conditions`) are
semantically UNORDERED sets — `fingerprint.py` explicitly re-sorts them
before serialization so declaration-order differences never change the
fingerprint; `methodology` is re-sorted by each step's own `sequence`
(never by list-declaration order) for the same reason, without
discarding the one list whose order IS semantically meaningful. Proven:
reordered-but-equivalent input produces an identical fingerprint; a
material methodology, version, or lifecycle change produces a different
one. For auditability/change-detection only — never authorization.

### 29.7 Readiness — `ContextPackage`-aware, never guessing (§50/§53/§65)

`evaluate_skill_readiness(skill, context_package, available_
capabilities=None)` is pure and single-`ContextPackage`-scoped — no
code path retrieves another case/session/owner to fill in missing
context (§53's owner-isolation invariant, proven both behaviorally and
by a source-scan test asserting no `TelcoContextService`/`CaseService`
reference exists in `readiness.py`). Each required `ContextDimension`
resolves to `SATISFIED` (state `KNOWN`) or `MISSING` with an explicit,
distinct reason string (`"required context is UNKNOWN"`/`"...
CONFLICTING"`/`"...NOT_APPLICABLE"`/`"...absent"`) — never guessed,
never resolved by picking one assertion or the latest one. Evidence
requirements check `ContextPackage.evidence`'s own already-computed
`selected_evidence_count`/`source_evidence_count` — never a fresh
retrieval call.

**Capability readiness (§35/§69)**: `available_capabilities` is an
OPTIONAL, caller-supplied `set[str] | None` — if `None`, every
capability requirement resolves to `NOT_EVALUATED`, never silently
`SATISFIED` and never silently `MISSING`. The OVERALL `SkillReadinessResult
.status` is a deliberate THIRD state, `INDETERMINATE` (distinct from
`READY`/`NOT_READY`), reserved for exactly the case where every context/
evidence requirement is satisfied but a capability's availability was
never supplied — an honest design choice avoiding a false `READY` or a
false `NOT_READY` (documented explicitly, since the milestone's own
instruction did not mandate one specific shape here).

### 29.8 Applicability — typed-only, never free text (§23-§26/§66)

`evaluate_skill_applicability(skill, context_package)` answers "could
this methodology apply to this explicitly known context?" — a SMALL,
Skill-scoped, typed dimension-VALUE match, structurally distinct from
6A.4's own Knowledge-narrowing applicability engine (never imported,
never duplicated — proven by the dependency-boundary test). A Skill
declaring NO conditions is trivially `APPLICABLE`. Per-condition
outcomes: `APPLICABLE` (a KNOWN dimension's accepted canonical value
intersects the declared `allowed_values`), `NOT_APPLICABLE` (KNOWN but
no intersection, or the dimension is explicitly `NOT_APPLICABLE`), or
`INDETERMINATE` (`UNKNOWN`/`CONFLICTING`/absent — never guessed).
Overall outcome: an explicit `NOT_APPLICABLE` on any condition wins over
an `INDETERMINATE` one (a known incompatibility is more informative than
an unknown); `INDETERMINATE` wins over `APPLICABLE` (never claim
applicability while a required condition is unresolved). Proven, by a
dedicated signature-introspection test, that the function accepts
exactly two parameters (`skill`, `context_package`) — no free-text input
channel exists.

### 29.9 No Skill selection, no execution, no runtime capability discovery

Readiness and applicability each evaluate exactly ONE, explicitly-
referenced Skill — there is no function anywhere in `backend/skills/`
that accepts a list of Skills to rank/recommend/select among (proven by
a source-scan test for `rank`/`select`/`recommend`/`route`-shaped
function names). No function executes a Skill, a methodology step, or a
Tool (proven by a source-scan test for `execute_*`/`run_skill`/
`invoke_*`-shaped names); no workflow/state-machine/DAG-executor/
scheduler class exists. Capability requirements are purely declarative
— `available_capabilities` is caller-supplied data, never discovered by
importing Tools, querying Team Manager/Incident Manager/MCP/Graph/BMC/
Power Automate, or inspecting a live network.

### 29.10 Production Skill decision

**Production Skill count: 0.** No production `*.yaml` Skill definition
file was created anywhere in this repository — per this milestone's own
explicit instruction, a production Skill may be added only if canonical
architecture already establishes it or a clearly justified, immediately-
needed reusable methodology exists; neither condition was met. The one
example Skill (`telco.incident_evidence_review`, §67 of this milestone's
own instruction) exists ONLY as a test fixture (`backend/tests/skills/
test_controlled_examples.py`), explicitly documented as such — zero
production count is a valid, deliberate outcome, not a gap.

### 29.11 Regression and non-regression

94 new tests across 10 new test files in `backend/tests/skills/`
(dependency boundary, schema validation, versioning, fingerprint,
registry, readiness, capability readiness, applicability, rendering,
no-execution/no-bypass, controlled examples) — all passing. Full backend
regression, `npm run build`, and `git diff --check` results are recorded
in this milestone's own closure report. Confirmed via a real, empty
`grep` across every agent/tool/API file: zero references to `backend.
skills` anywhere outside `backend/skills/`/`backend/tests/skills/`
themselves. Confirmed via empty scoped `git diff`: `backend/agents/`,
`backend/tools/`, `backend/api/`, `src/` (frontend), and every 6A.2-6A.6
file all remain byte-for-byte untouched by this milestone. Live DEV
Cloud SQL state (`alembic_version`/`pgvector`/evidence-index table and
indexes) confirmed unchanged before and after — expected, since
`backend/skills/` contains no database code of any kind.

**STATUS: 6A.7 / P11-M07 is COMPLETE.** See CLAUDE.md's own 6A.7 closure
section for the full narrative. (HISTORICAL, as of this section's own
closure: Phase 6A was still IN PROGRESS with P11-M08 NEXT. **Phase 6A
[P11] has SINCE reached COMPLETE AND FROZEN status — P11-M00 through
P11-M11 [6A.0 through 6A.11] are all COMPLETE — see `docs/INTELLIGENCE_
ARCHITECTURE.md` §19/§20 for 6A.9/6A.10's own canonical documentation,
CLAUDE.md's own 6A.11 closure section for the formal freeze record, and
§30 immediately below for 6A.8's own closure.**)

## 30. Phase 6A.8 — Experience Memory Foundation (additive, COMPLETE)

`backend/experience_memory/` (new peer package: `domain/{enums,models,
admission,fingerprint}.py` — pure, zero-I/O; `sqlalchemy/{models,db,
service}.py` — the one Cloud SQL persistence boundary) implements the
canonical **Experience Memory** contract: durable, historical,
provenance-rich, deterministically-admitted, owner/customer-isolated
record of "what happened in relevant previous operational cases" —
**never Knowledge, never current Context, never a Skill, never an
agent, never a recommendation engine, never a learning loop**.

### 30.1 Trust hierarchy (never collapsed)

```
CURRENT OPERATIONAL CONTEXT (6A.2/6A.6) = current observed/asserted truth
GOVERNED KNOWLEDGE (5.1/6A.4/6A.5)      = authoritative procedural/factual source
EXPERIENCE MEMORY (6A.8)                = historical supporting signal only
```

Every `ExperienceRecord` carries `source_class: Literal["EXPERIENCE"] =
"EXPERIENCE"` — a fixed literal present on every instance, so a
downstream consumer can distinguish Experience from Governed Knowledge
or a current-observation representation purely by inspecting this one
field, even out of context. No code path in this package writes to
`TelcoContextProfile`/`ContextAssertion` (6A.2), `CaseContextItemDTO`
(Case), `KnowledgeObject` (5.1/6A.3), or `SkillDefinition` (6A.7) — a
historical Experience never overwrites or resolves current truth.

### 30.2 Pure domain, zero I/O (enforced dependency boundary)

`backend/experience_memory/domain/` imports ONLY `pydantic` and its own
sibling `domain` modules — never a SQL driver, never `google.adk`/
`google.genai`, never `backend.agents`/`backend.tools`/`backend.skills`/
`backend.knowledge.*`/`backend.cases.*`/`backend.context_engineering.*`.
`backend/experience_memory/sqlalchemy/` is the ONLY module permitted to
import SQLAlchemy/asyncpg/aiosqlite, and even there the same
agent/LLM/Knowledge/Skill-execution import prohibition holds. Enforced
by `backend/tests/experience_memory/test_dependency_boundary.py` (AST
import-prefix checks for both layers + a real fresh-subprocess
standalone-import probe + a source-text scan proving no vector/
embedding reference of any kind anywhere in the package) and `test_no_
execution_no_bypass.py` (call-level AST proof: no Knowledge search, no
Skill resolution/execution, no Tool invocation, no unscoped `list_all`-
style method).

### 30.3 Deterministic admission (§20-22 of the milestone instruction)

`domain/admission.py::evaluate_admission` is a pure function switching
on the closed `ExperienceSourceOrigin` enum: 5 ALLOWED origins (observed
case outcome, explicit case resolution, executed action result,
validated operator feedback, trusted external system state) may
`ACCEPT`; 9 DENIED origins (LLM speculation, generated recommendation,
unexecuted action, unverified RCA, assistant answer, user free-form
statement, retrieval ranking, semantic similarity, model confidence)
always `REJECT`; the reserved `UNSPECIFIED` origin always resolves to
`INDETERMINATE` — never silently promoted to `ACCEPT`. No LLM call
exists anywhere in the admission path. Only `ACCEPT`ed candidates are
ever persisted — there is no separate "candidate" table for `REJECT`/
`INDETERMINATE` outcomes (§63: "do not create a candidate table unless
necessary" — none was).

### 30.4 Deterministic identity and idempotency (§16/§17, identity basis
corrected TWICE — see the 6A.8 final SOURCE-NAMESPACE corrective pass,
which supersedes the immediately prior corrective pass's own
`source_origin`-as-namespace basis)

**`source_origin` vs. `source_namespace` — two separate contracts, never
overloaded into one field (the central correction of this final pass):**

```text
source_origin     = trust/admission classification (ACCEPT/REJECT/INDETERMINATE input)
source_namespace  = producer/system event-ID namespace (identity/dedup/replay/provenance input)
```

`domain/fingerprint.py::compute_experience_id(owner_id, experience_type,
source_namespace, source_event_id)` is a deterministic SHA-256 hex
digest — the SAME four-input basis always produces the SAME
`experience_id`, used directly as `slopanoc_experience_records
.experience_id`'s PRIMARY KEY. **`source_origin` is deliberately EXCLUDED
from this basis.**

**Second corrective-pass finding:** the immediately prior pass's basis
(`owner_id`+`experience_type`+`source_origin`+`source_event_id`) was
itself re-audited and found to conflate trust classification with
producer identity — `source_origin` is a small, closed, trust-tier enum
(5 allowed + 9 denied + 1 unspecified member), never designed to
distinguish, say, "BMC" from "Alarm Platform" (two different real
producer systems that could both legitimately be classified
`TRUSTED_EXTERNAL_SYSTEM_STATE`, yet must never collide merely because
they share that classification and happen to reuse the same literal
`source_event_id`). A new field, `source_namespace: str` (§5: validated
against `^[a-z][a-z0-9_.-]*$`, required, non-blank, e.g. `"bmc"`,
`"teams"`, `"onefm"`, `"enm"` — illustrative only, never a hardcoded
product taxonomy, never silently derived from `source_origin`), was
added to `ExperienceCandidate`/`ExperienceRecord` and to the persisted
schema (§30.10.1 below) as the genuine namespace component. Case's own
`SourceType` was re-audited and confirmed NOT reusable (a closed,
small, Case-specific enum describing who/what authored a Case context
item — a different concern — and importing it would violate Experience
Memory's own established dependency boundary, which forbids
`backend.cases` imports). Source timestamps remain, as always, NEVER
part of this basis (not collision-safe).

**Identity deliberately tracks the producer EVENT, not the admission
classification of any one submission:** two candidates sharing
`owner_id`+`experience_type`+`source_namespace`+`source_event_id` but
asserting a DIFFERENT `source_origin` resolve to the SAME
`experience_id` — audited, no proven reason found to make trust
classification part of identity (a later, differently-classified
resubmission of the same real-world event is treated as the same
event, not a second historical record).

`ExperienceMemoryService.record_experience` checks for an existing row
with that identity BEFORE inserting; a repeated write of the exact same
source identity (owner + type + namespace + event id) returns the
existing record (`deduplicated=True`) rather than creating a duplicate
— no separate UNIQUE constraint is needed. Proven both at the pure-
function level, via the real service (SQLite), and end to end against
the live Cloud SQL DEV table:
- Cross-namespace collision impossible: `test_corrective_pass_cross_
  namespace_same_event_id_never_collides` / `test_corrective_pass_
  cross_namespace_same_event_id_persists_two_records` / `test_real_
  postgres_corrective_pass_cross_namespace_same_event_id_two_distinct_
  records`.
- Same-namespace idempotency: `test_corrective_pass_same_full_source_
  identity_is_idempotent` / `test_corrective_pass_same_full_source_
  identity_written_twice_is_idempotent` / `test_real_postgres_
  corrective_pass_same_full_source_identity_idempotent`.
- `source_origin` independence (same origin, different namespace, same
  event id → still distinct): `test_corrective_pass_source_origin_
  independence` / `test_corrective_pass_source_origin_independence_via_
  service` / `test_real_postgres_corrective_pass_source_origin_
  independence`.
- Same namespace, different origin → same producer event (same
  `experience_id`, second write deduplicated): `test_corrective_pass_
  same_namespace_different_origin_is_same_identity` / `test_real_
  postgres_corrective_pass_same_namespace_different_origin_is_same_
  event`.
- A denied origin remains denied regardless of namespace: `test_
  corrective_pass_denied_origin_remains_denied_regardless_of_namespace`.

No existing Experience rows were affected by either identity correction
— the live DEV table held 0 rows before this pass, immediately before
the schema change, and after (reconfirmed by direct query each time),
so no data migration/backfill was ever needed. Because a NEW persisted
column (`source_namespace`) was genuinely required this time (unlike
the prior corrective pass, which changed only the application-side hash
inputs), ONE small, direct-child Alembic migration
(`d3f8b1c6a942`, revising `c7e2a4f9b83d`) was authored and applied for
real against the live Cloud SQL DEV instance — see §30.10.1. A
SEPARATE, broader `compute_experience_content_fingerprint` hashes the
full candidate's canonical JSON (now automatically including
`source_namespace`, a normal candidate field) for audit/dedup-support/
change-detection only (§43) — never used as identity or authority.

### 30.5 Owner/customer isolation — query-time, never post-filtered
(§32-34/§45; terminology corrected by the 6A.8 final corrective pass §4)

**Terminology correction (§4):** `owner_id` is a LOGICAL Experience
ownership/scope key — a deterministic string the trusted calling code
supplies to partition Experience records (in practice, expected to be a
canonical TELCO customer/tenant value such as "Vodafone"). This
milestone's own repository audit found **no canonical, authenticated
tenant identity/authorization model anywhere in this codebase yet**
(`backend/api/identity.py::UserContext` is a development-only, single-
header, per-request `user_id` — not a customer/tenant authorization
boundary). 6A.8 therefore does NOT itself establish authenticated
customer/tenant authorization — it establishes the SQL-level, query-time
ISOLATION mechanism that a future, upstream authorization layer would
need to bind a caller's authenticated identity to a permitted `owner_id`
scope before that scope ever reaches this service. These are two
DISTINCT concerns, never conflated:

```text
6A.8 (implemented):        logical owner-scope isolation at the SQL query level
future/upstream (NOT built): authenticated authorization binding
                              caller identity -> permitted owner_id scope(s)
```

This correction does NOT weaken the SQL filtering in any way — it only
corrects what the filtering is claimed to guarantee. `ExperienceQuery
.owner_id` is a structurally REQUIRED field — there is
no way to construct a valid, unscoped query. Every method on
`ExperienceMemoryService` (`get_by_id`, `query`, `invalidate`) adds
`ExperienceRecordTable.owner_id == owner_id` to the SQL `WHERE` clause
itself, before the database ever executes it — never a Python
post-filter over an unscoped result set. There is no `list_all()`
method (§44/§80). A record belonging to a different owner is
indistinguishable from "does not exist" (`get_by_id`/`invalidate` both
return `None`) — anti-enumeration, matching this codebase's existing
attachment/session lookup convention. Proven, at the real PostgreSQL
level, by `backend/tests/experience_memory/test_experience_memory_
postgres.py::test_real_postgres_cross_owner_exclusion_at_sql_level`,
which issues a raw `SELECT owner_id FROM slopanoc_experience_records
WHERE owner_id = :owner` and proves the result set for one owner never
contains a row belonging to another, even as an intermediate result.

### 30.6 Provenance without duplication (§25-29; §12 of the final
source-namespace corrective pass)

`ExperienceCandidate`/`ExperienceRecord` carry stable REFERENCES only —
`source_namespace` (the producer/system namespace, persisted as real,
independently inspectable provenance — never only inside the
`experience_id` hash where it could not later be read back; proven by
`test_real_postgres_source_namespace_survives_round_trip`), `case_id`
(the canonical Case identifier, never a Case snapshot copy),
`context_fingerprint` (a plain `str` pointer to a 6A.6 `ContextPackage
.content_fingerprint`, never the full `ContextPackage`), `skill_id`/
`skill_version`/`skill_fingerprint` (the EXACT Skill identity — a
`model_validator` fails closed if `skill_id` is set without
`skill_version`, so "latest Skill" can never be silently implied, per
§28), and `evidence_references: list[ExperienceEvidenceReference]`
(each carrying `evidence_id`/`knowledge_id`/`version_label`/
`section_id`/`artifact_id` — plain strings, never a copy of governed
Knowledge content; at least one identifier is required per reference).

### 30.7 Minimal lifecycle (§40/§41)

`ExperienceLifecycle`: `ACTIVE` | `INVALIDATED` — deliberately NOT
Knowledge's `CANDIDATE`/`APPROVED`/`ARCHIVE` (a different governed-
artifact concern). `ExperienceMemoryService.invalidate` sets
`lifecycle=INVALIDATED`, `invalidated_at`, `invalidation_reason` — it
never mutates any other field, and never physically deletes the row
(history is not rewritten). Default `query()` retrieval excludes
`INVALIDATED` records unless `include_invalidated=True` is explicitly
set.

### 30.8 Structured, bounded, deterministic retrieval (§36/§45/§46/§53)

`ExperienceMemoryService.query` supports only justified filters (case
ID, experience type, Skill ID/version, source-event time range) layered
on top of the mandatory owner scope; ordering is always `recorded_at
DESC, experience_id ASC` (`recorded_at` is never `NULL`, unlike the
optional `source_event_at`, making it the only field that can give a
fully deterministic, always-available primary sort); `limit` is bounded
(`ge=1, le=200`, default 50). An empty result (`records=[]`) is a
completely valid, ordinary outcome — never an error, never a trigger to
broaden scope, search another owner, or fall back to Knowledge.

### 30.9 Semantic/vector retrieval — explicitly deferred (§37/§75)

Even though `pgvector` (v0.8.5) is already installed and used by 6A.5's
own `slopanoc_knowledge_evidence_index` table, `slopanoc_experience_
records` has NO vector column, NO `pgvector` operator, NO HNSW/IVFFlat
index, and NO Vertex embedding call anywhere — proven by a dedicated
source-text scan (`test_no_vector_or_embedding_reference_anywhere`).
This is a deliberate, documented deferral: semantic similar-case
retrieval may be evaluated later, once real Experience volume and a
real 6A.9 use case justify it — foundation first (trust, provenance,
admission, isolation, structured retrieval), never infrastructure
merely because it happens to already exist.

### 30.10 Database schema and migration

One primary table, `slopanoc_experience_records` (Alembic revision
`c7e2a4f9b83d`, revising `a1f3c9e07b21` — the correct, single, current
head at the time this migration was authored), plus 5 indexes each
justified against an actual retrieval pattern this milestone implements
(`ix_experience_owner`; `ix_experience_owner_case`; `ix_experience_
owner_type`; `ix_experience_owner_skill`; `ix_experience_owner_
recorded_at`). No supporting/candidate table was added (§10's own
"prefer the smallest schema" — the audit found none justified: rejected/
indeterminate candidates are never persisted at all). Bounded, per-
record structures (evidence references, observed facts, metadata) are
stored as JSON text columns on the same row, consistent with this
codebase's existing convention of a JSON payload column for bounded
per-record structured data. `alembic/env.py`'s `target_metadata` was
extended with `ExperienceMemoryBase.metadata` (mirroring the identical
fix 6A.2 already made for its own new tables) — the same edit also
corrected two pre-existing, stale docstring omissions (6A.2's and
6A.5's own `Base.metadata` additions had never been documented in that
docstring's own module-level comment list, despite already being wired
into `target_metadata` since their own milestones).

### 30.10.1 Corrective migration — `source_namespace` (final source-
namespace corrective pass)

`alembic/versions/d3f8b1c6a942_experience_records_source_namespace.py`
— a single, small, direct-child migration (`down_revision =
'c7e2a4f9b83d'`) adding exactly one column: `source_namespace VARCHAR
NOT NULL`, no default, no new index (identity computation reads this
column application-side, not via a database query, and no retrieval
requirement currently justifies an index on it — §27 of that pass's
own instruction). Safe without a backfill because the live DEV table
was verified, by direct query, to hold **0 rows** immediately before
this migration was authored and again immediately before it was
applied — reconfirmed a third time immediately after, still 0.
`c7e2a4f9b83d` itself was NOT edited (§14/§16 of that pass's own
instruction: never silently edit an already-applied migration and
pretend the live schema changed with it) — the live schema now sits at
`d3f8b1c6a942`, one clean step ahead, with a single linear Alembic
chain (`alembic heads` confirmed exactly one head both before and
after). Applied via the corrected in-process-environment-variable
strategy (§30.11 below) directly against the live Cloud SQL DEV
instance — never via SQLite. Downgrade (`DROP COLUMN source_namespace`)
was verified by direct code review of the migration's own symmetric
`upgrade()`/`downgrade()` pair, never exercised destructively against
shared DEV.

### 30.11 Migration-test target-isolation finding (OPS-0001)

An attempt to validate this migration's upgrade/downgrade behavior
against an isolated SQLite scratch file, via `alembic.config.Config
.set_main_option("sqlalchemy.url", ...)`, was found NOT to work:
`alembic/env.py`'s `_resolved_database_url()` unconditionally calls
`get_settings().resolve_database_url()`, by deliberate, pre-existing
design — it never reads the `Config` object's own URL at all. This
caused a migration-chain test to run, briefly and harmlessly, against
the real local development file `./slopanoc_sessions.db` (see `docs/
DEFECT_REGISTER.md` OPS-0001 for the full incident/recovery record — a
byte-for-byte backup was taken and verified before a bounded,
authorized cleanup fully restored the file to its exact prior state;
ADK's own session data was never at risk, since Alembic never manages
those tables). The CORRECTED strategy — used for this migration's real
validation — sets `SLOPANOC_DATABASE_URL` in the test PROCESS's own
environment (the one input `resolve_database_url()` actually honors),
pointed at the real Cloud SQL DEV instance via the already-running
Cloud SQL Auth Proxy: `alembic upgrade head` was applied for real
(never a destructive `downgrade` against shared state), the resulting
schema was introspected column-by-column against the migration's own
DDL, and a dedicated real-PostgreSQL integration test suite (`backend/
tests/experience_memory/test_experience_memory_postgres.py`, 9 tests)
validated the full write/read/idempotency/isolation/provenance/
retrieval contract against that real table — cleaning up only its own
namespaced synthetic rows (`DELETE ... WHERE owner_id LIKE
'slopanoc-test-experience-memory-%'`), never a table-level DDL
statement, applying the DEF-0018 lesson from the very first version of
this file rather than rediscovering it.

### 30.12 Production writer/consumer decision

**Production Experience writers = 0.** No current SLOPANOC event
(Case resolution, executed write action, operator feedback) is
automatically wired to call `record_experience` — per the milestone's
own explicit instruction, a zero-producer foundation is a valid,
deliberate outcome; inventing a producer merely to demonstrate
functionality was avoided. **Production specialist consumers = 0.** No
route from Team Manager, Incident Manager, or Context Engineering into
`backend/experience_memory/` exists — confirmed by a real, empty `grep`
for `backend.experience_memory`/`backend/experience_memory` anywhere
outside the package's own two directories. 6A.9 owns wiring either a
producer or a consumer; this milestone establishes only the reusable
capability both would depend on.

### 30.13 Regression and non-regression

**Fresh, exact count after the final source-namespace corrective pass:
115 tests across 7 test files** in `backend/tests/experience_memory/`
(contracts 38, admission 21, fingerprint 16, dependency boundary 6,
no-execution/no-bypass 5, service-SQLite 15 — 101 fast/isolated tests —
plus a real PostgreSQL integration file, 14 tests) — all passing, the
real-PostgreSQL subset run against the live Cloud SQL DEV instance
after the corrective migration was applied. Full backend regression,
`npm run build`, and `git diff --check` results are recorded in this
milestone's own final closure report. Confirmed via empty scoped `git
diff`: `backend/agents/`, `backend/tools/`, `backend/api/`, `src/`
(frontend), and every 6A.2–6A.7 file all remain byte-for-byte untouched
by every one of this milestone's three passes. Live DEV Cloud SQL
state: `alembic_version` now at `d3f8b1c6a942` (the corrective
migration's own revision, a direct child of `c7e2a4f9b83d`);
`pgvector`/6A.5 evidence table and its 4 indexes unchanged throughout.

**STATUS: 6A.8 / P11-M08 is COMPLETE.** See CLAUDE.md's own 6A.8
closure sections for the full narrative, including the migration-test
incident/recovery record and both identity corrective passes.
(HISTORICAL, as of this section's own closure: Phase 6A was still IN
PROGRESS with P11-M09 NEXT. **Phase 6A [P11] has SINCE reached COMPLETE
AND FROZEN status — P11-M00 through P11-M11 [6A.0 through 6A.11] are
all COMPLETE, including 6A.9 [Troubleshooting Manager & Intelligence
Assembly] and 6A.10 [Dual-Specialist Orchestration] — see
`docs/INTELLIGENCE_ARCHITECTURE.md` §19/§20 for their own canonical
documentation, and CLAUDE.md's own 6A.11 closure section for the formal
freeze record.**)
