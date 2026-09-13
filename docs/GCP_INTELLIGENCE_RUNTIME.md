# GCP Intelligence Runtime — Phase 6A Physical Architecture (P11-M01 / 6A.1)

Status: **P11-M01 / 6A.1 — COMPLETE** (audit + decision + minimum-foundation
milestone; see `CLAUDE.md`'s own 6A.1 closure section for the narrative
record).

**P11-M02 / 6A.2 as-built confirmation (added post-6A.1, no re-audit
performed):** the "TELCO structured context" row of §4's decision matrix
(EXTEND — a new Alembic-managed table set in the SAME Cloud SQL instance/
database, isolated from `slopanoc_knowledge_objects`/Case/ADK session
tables) was implemented exactly as decided —
`backend/context/sqlalchemy/{models,db,service}.py`,
`alembic/versions/9b6df6490c0e_*.py`. It resolves via `Settings
.resolve_database_url()` — the SAME session/Case database domain, never
`resolve_knowledge_database_url()` (TELCO Context is operational/
Case-like state, not governed knowledge content; see CLAUDE.md's 6A.2
closure section for the full ownership-model rationale). No new GCP
service, bucket, or extension was introduced by 6A.2 — this remains an
EXTEND of the existing Cloud SQL instance only, exactly as this document
already decided.

## 1. Scope

This document is authoritative **only** for Phase 6A's **physical
GCP/runtime/tooling architecture and technology decisions** — which GCP
services and libraries Phase 6A capabilities run on, why, at what
evidence-backed scale, and under what future migration trigger. It does
**not** override:

```text
docs/MASTER_ROADMAP.md          — roadmap STATUS (authoritative)
docs/BUILD_SEQUENCE.md          — strategic BUILD ORDER (authoritative)
docs/AGENT_CONTRACT.md          — agent topology/trust boundaries
docs/KNOWLEDGE_CONTRACT.md      — Generic KM domain contract
docs/TEAMS_TOOL_CONTRACT.md     — Teams tool contract
docs/INTELLIGENCE_ARCHITECTURE.md — Phase 6A logical architecture/contracts
docs/TROUBLESHOOTING_STRATEGY.md — non-negotiable product strategy
```

Where this document discusses a *logical* concept already defined
elsewhere (TELCO Context, hybrid retrieval boundary, Context vs.
Evidence, Skill/Experience Memory boundaries), it links to
`docs/INTELLIGENCE_ARCHITECTURE.md` rather than restating the definition
— this document only adds the **physical** answer ("on what GCP
service/library does this run") to questions that document already
scoped logically. This document is added to the Authority Map in
`docs/MASTER_ROADMAP.md` §8.

**This milestone implements no Phase 6A intelligence capability.** It
produces an architecture decision record and, where explicitly noted in
§13, a small number of reversible, non-destructive, zero-behavior-change
foundation edits.

---

## 2. Current as-built GCP architecture (evidence-backed)

Classification key used throughout this document:

```text
VERIFIED LIVE     — confirmed against the real, running GCP project this session
VERIFIED IN CODE  — confirmed by direct source/config/dependency inspection
DOCUMENTED        — stated in existing canonical docs, not independently
                     re-verified live in this session
UNKNOWN           — could not be established this session (reason given)
```

### 2.1 Live GCP audit performed this session

```bash
gcloud auth list --filter=status:ACTIVE
gcloud config get-value project
```

Result: **VERIFIED LIVE** — active account `costin.ionita@ericsson.com`,
active project `pr-msn-dev-gl-slopai-01` (matches every existing
canonical document's reference to this project). `gcloud config
get-value region` returned no `core/region` property set (expected —
this project's resources are region-scoped individually per resource,
e.g. `europe-west4` for Cloud SQL/GCS, not via a global gcloud config
default).

Further live inspection (`gcloud services list --enabled`, `gcloud sql
instances list`, `gcloud storage buckets list`, `gcloud run services
list`, `gcloud sql databases list`) all failed identically:

```text
ERROR: ... Reauthentication failed. cannot prompt during
non-interactive execution.
```

This is an **expired Application Default Credentials session requiring
interactive re-authentication**, which this non-interactive agent session
cannot perform (per this milestone's own §4 instruction: "if CLI access
is unavailable, document that limitation and continue from code/
configuration/deployment manifests/existing documentation... do not
fabricate live GCP state"). No further live GCP resource enumeration was
attempted after the second identical failure, to avoid repeatedly
re-triggering the same auth error. **If a human operator wants this
audit's remaining live-resource rows independently re-verified, run `!
gcloud auth login` in this session first** (interactive login is outside
this agent's own tool surface), then re-run the four commands above.

### 2.2 Resource inventory

| Component | Classification | Evidence |
|---|---|---|
| GCP project | VERIFIED LIVE | `pr-msn-dev-gl-slopai-01` (§2.1) |
| Gemini models | VERIFIED IN CODE | `google-adk==1.33.0`, `google-genai==1.75.0` (`requirements.txt`); default model `gemini-2.5-flash` (`backend/config/settings.py::_DEFAULT_MODEL`, matches `LlmAgent.DEFAULT_MODEL`) |
| Vertex AI (model inference mode) | VERIFIED IN CODE + DOCUMENTED | `GOOGLE_GENAI_USE_VERTEXAI`/`GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION` env vars, read directly by the `google-genai` client (not SLOPANOC's own code) — `README.md` §"Google authentication". **No `google-cloud-aiplatform` (Vertex AI SDK) direct dependency exists** — access is entirely through `google-adk`/`google-genai`'s own Vertex-mode client. |
| Gemini ADK | VERIFIED IN CODE | `google-adk==1.33.0`; `Agent`/`AgentTool`/`Runner`/`DatabaseSessionService` used throughout `backend/agents/`, `backend/api/chat_service.py` |
| Application/runtime hosting | UNKNOWN (no Cloud Run/GKE manifest in repo; live `gcloud run services list` blocked, §2.1) | No `Dockerfile`, no Cloud Build config, no Cloud Run/GKE YAML found anywhere in the repository (`Glob` confirmed). `README.md`'s own "CURRENT LIMITATIONS" already documents "production deployment identity/configuration not yet exercised." Local dev runs `uvicorn backend.api.app:app` directly. |
| Cloud SQL PostgreSQL | DOCUMENTED (not re-verified live this session, §2.1) | PostgreSQL 18 instance `sloc-anoc-sandbox01`, project `pr-msn-dev-gl-slopai-01`, region `europe-west4`, connection name `pr-msn-dev-gl-slopai-01:europe-west4:sloc-anoc-sandbox01` (`README.md` §"Local Cloud SQL PostgreSQL development`). Two logical domains — session/Case (`SLOPANOC_DATABASE_URL`) and Governed Knowledge (`SLOPANOC_KNOWLEDGE_DATABASE_URL`) — both fail-closed to the `postgresql` dialect at startup (`backend/api/runtime_database_policy.py`, VERIFIED IN CODE). |
| GCS — chat attachments | DOCUMENTED (not re-verified live this session) | `slopanoc-chat-attachments-sandbox01`, `europe-west4`, uniform bucket-level access, public access prevention, no versioning (`README.md`). |
| GCS — knowledge artifacts | CONFIGURED BUT NOT ACTIVE | `Settings.knowledge_artifacts_bucket`/`SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET` exists in code (`backend/config/settings.py`) and `backend/knowledge_ingestion/artifact_storage.py` is fully implemented and was live-validated against the *existing* chat-attachments bucket's project with synthetic bytes only (per `CLAUDE.md`'s own A5 record) — **no dedicated `slopanoc-knowledge-artifacts-*` bucket has been provisioned yet.** |
| Secret Manager | VERIFIED IN CODE, CONFIGURED BUT NOT ACTIVE for Cloud SQL | `google-cloud-secret-manager==2.28.0`; `resolve_database_url`/`resolve_knowledge_database_url`/`resolve_power_automate_gateway_url` all support a `*_SECRET_RESOURCE` fallback (`backend/config/settings.py`, `_fetch_secret_from_secret_manager`). Local Cloud SQL development so far uses IAM DB auth + Auth Proxy, never this fallback (`README.md` limitations). |
| IAM / service accounts | DOCUMENTED | Local Cloud SQL dev uses the developer's own IAM identity, a member of both `slopanoc_migrator` and `slopanoc_runtime` Postgres roles; a dedicated least-privilege runtime service account for production does not exist yet (`README.md`/`CLAUDE.md` limitations). |
| Artifact Registry / CI/CD / deployment | NOT PRESENT | No `Dockerfile`, `cloudbuild.yaml`, CI workflow, or deploy script found anywhere in the repository (`Glob` confirmed). |
| Logging / monitoring / tracing (Cloud-native) | NOT PRESENT | No `google-cloud-logging`/`google-cloud-monitoring`/`opentelemetry-*` **direct** dependency in `requirements.txt`. `google-adk` transitively bundles `opentelemetry-api` for its own internal `tracer.start_as_current_span` instrumentation (confirmed by the D2 defect record in `docs/DEFECT_REGISTER.md`) — this is ADK's own internal tracing, never wired to Cloud Trace/Cloud Logging/BigQuery by SLOPANOC. |
| Existing developer observability | VERIFIED IN CODE | `backend/api/perf_timing.py` (before/after-model-call timing, structured log lines only), `backend/api/run_trace.py` (mid-turn activity indicator) — developer-diagnostic only, not a production observability/alerting stack (matches README's own "CURRENT LIMITATIONS"). |
| Session persistence | VERIFIED IN CODE | ADK `DatabaseSessionService` → Cloud SQL PostgreSQL via `asyncpg` (`SLOPANOC_DATABASE_URL`). |
| Case persistence | VERIFIED IN CODE | `backend/cases/db.py`, async SQLAlchemy, same Cloud SQL database, Alembic-managed, isolated table/metadata set from ADK's own session tables. |
| Knowledge persistence | VERIFIED IN CODE | `backend/knowledge/repository/sqlalchemy.py` — ONE generic table, `slopanoc_knowledge_objects`, composite PK `(knowledge_id, version_label)`, entire `KnowledgeObject` (including `artifacts`) as one `Text`/JSON payload column. Dialect-neutral (proven against SQLite and Cloud SQL PostgreSQL). **No vector/embedding column exists today.** |
| Attachment/media persistence | VERIFIED IN CODE | `backend/attachments/{models,repository,service,storage}.py` — Cloud SQL metadata (`slopanoc_chat_attachments`) + private GCS binary, `Part.from_uri` only (never bytes/base64 in the DB). |
| Knowledge ingestion processing (A5) | VERIFIED IN CODE — see §5 | `backend/knowledge/ingestion/extractors/{docx,xlsx,pdf,txt,ole}.py` — deterministic, no Document AI, no OCR. |
| Image interpretation | VERIFIED IN CODE | `backend/knowledge_ingestion/gemini_image_interpreter.py` — reuses the SAME shared Gemini client (`get_shared_llm`) via a bounded, tool-less, one-shot `Agent`+`Runner`+`InMemorySessionService` pattern already used elsewhere in this codebase. |
| Teams / Power Automate integration | VERIFIED IN CODE — NOT a GCP service | `backend/gateway/power_automate_client.py`, plain HTTP (`requests`) to an external Power Automate flow URL. Out of scope for this document. |

### 2.3 Explicitly NOT present (confirmed, not merely assumed)

Confirmed absent from `requirements.txt` and from every `Glob`/`Grep`
sweep of the repository: `google-cloud-aiplatform` (Vertex AI SDK),
`pgvector` (Python client), `redis`/`pymemcache`, `elasticsearch`/
`opensearch-py`, `google-cloud-bigquery`, `google-cloud-documentai`,
any embedding-model client, any vector-index client, any
`opentelemetry-exporter-*` package, any Dockerfile/CI/CD manifest.

**Enablement status of these APIs on the live GCP project itself is
UNKNOWN** (blocked by §2.1's auth limitation) — "not present in code"
is a different, narrower claim than "not enabled on the project," and
this document does not conflate the two.

---

## 3. Phase 6A capability requirements (recap, full definitions in `docs/INTELLIGENCE_ARCHITECTURE.md`)

TELCO Context persistence (§6/§7.1 below), Knowledge metadata/
applicability (§7.2), raw multimodal artifacts (§7.3), document parsing
(§7.4), embeddings (§7.5), vector retrieval (§7.6), exact/lexical
retrieval (§7.7), hybrid ranking (§7.8), Experience Memory persistence
(§7.9), Skills registry (§7.10), Context Engineering runtime placement
(§7.11), caching (§7.12), observability (§7.13), security/IAM
implications (§7.14).

---

## 4. Decision matrix

| Capability | Existing implementation | Decision | Physical target | Why | Added cost/complexity | Future migration trigger |
|---|---|---|---|---|---|---|
| TELCO structured context (6A.2) | None yet | **EXTEND** | New Alembic-managed table set in the SAME Cloud SQL instance/database, isolated from `slopanoc_knowledge_objects`/Case/ADK session tables (same domain-isolation discipline already used 3x in this codebase) | Reuses proven dialect-neutral async-SQLAlchemy pattern; no new service; structured facts (customer/vendor/technology/...) benefit from real columns + indexes, not a JSON blob | Low — one migration, no new dependency | Row count / query latency measured at 6A.2 implementation time |
| Knowledge metadata / applicability (6A.2/6A.4) | `KnowledgeObject.metadata`/`.applicability`, part of the existing JSON payload | **REUSE**, **EXTEND** only if narrowing-query performance requires it | Same `slopanoc_knowledge_objects` table; if 6A.4 needs indexed lookups, use Postgres `JSONB` + `GIN` index on specific payload keys (native Postgres capability, no new service) | One shared Governed Knowledge architecture invariant (§7 of `docs/INTELLIGENCE_ARCHITECTURE.md`) — never a second metadata authority | Low — an index, not a schema fork | Measured 6A.4 query latency against real corpus scale |
| Raw multimodal artifacts (6A.3) | `backend/knowledge_ingestion/artifact_storage.py` (fully implemented, content-hash-addressed GCS keys) + Cloud SQL JSON metadata | **EXTEND** (provision the already-coded, not-yet-created dedicated bucket) | `SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET` → a real, dedicated `slopanoc-knowledge-artifacts-*` GCS bucket, mirroring the existing chat-attachments bucket's private/uniform-access/no-versioning configuration | The code and setting already exist; only physical provisioning is missing | Low, one-time, reversible (a bucket can be deleted) | N/A — this is the existing target, not a scale decision |
| Document parsing / structure extraction (6A.3) | A5 — DOCX/XLSX/PDF/OLE/TXT extractors, compound artifact tree, hierarchical provenance, real TELCO/RAN MOP-validated (§5) | **REUSE / EXTEND** | Same `backend/knowledge/ingestion/extractors/` package; extend only for a genuinely new container format if one is found, never replace | A5 already satisfies every provenance/structure requirement Phase 6A names (document→section→procedure step→table→row/cell→image, parent-child lineage) | None (no new dependency) | A real document format A5 cannot parse at all is found |
| — Document AI (considered, not selected) | N/A | **REJECT** (for now) | — | Fails the §9 test: no unmet capability demonstrated; A5 already extracts structured content deterministically with full provenance at zero marginal API cost | Would add per-page cost, a new IAM surface, a new network hop, and non-determinism (a hosted OCR/layout model) for a requirement already met | Only if a real document format/quality A5 cannot handle is found in the actual TELCO/RAN corpus |
| Embeddings (6A.5) | None | **ADD** (deferred implementation to 6A.5; no corpus embedding now) | A Vertex AI-native text embedding model, reached through the SAME project/credentials/`google-genai`/Vertex-mode client family already used for Gemini (exact model selection is 6A.5's own implementation decision, not made here) | Staying inside the already-authorized, already-billed Vertex AI surface avoids a second AI vendor/credential/IAM boundary | Usage-based, incremental (small per-embedding-call cost) | N/A until 6A.5 begins |
| Exact / lexical retrieval (6A.5) | None (5.1G's `TokenOverlapRelevanceScorer` is an in-process linear scan, not an indexed search) | **EXTEND** | Native PostgreSQL: exact match via indexed equality on structured identifier columns (from 6A.2's TELCO Context/6A.4's narrowing columns); lexical via `tsvector`/`tsquery` + `GIN` index | Zero new service; PostgreSQL full-text search is mature, proven, and already the persistence layer | Low — indexes only | Corpus size or latency exceeding what a single Postgres instance's FTS can serve interactively |
| Vector / semantic retrieval (6A.5) | None | **EXTEND** (Cloud SQL + `pgvector`), **DEFER** Vertex AI Vector Search | Cloud SQL PostgreSQL + the `pgvector` extension, in the SAME instance/database already used for everything else | See §7 below — simplest architecture that plausibly meets Phase 6A's actual (MVP-tier) scale; no new network hop, no new IAM surface, no new managed service, no second data-consistency boundary vs. the metadata that already lives in the same row | Low–moderate: one extension (subject to live confirmation, §8), new vector column(s)/index, index-build cost as corpus grows | Candidate-set / vector-count / p95-latency threshold defined in §7.3 exceeded, OR `pgvector` unavailable/unsupported on this Cloud SQL instance (§8) |
| Hybrid ranking (6A.5) | `backend/knowledge/retrieval/scoring.py`'s `KnowledgeRelevanceScorer` Protocol + `TokenOverlapRelevanceScorer` reference implementation | **EXTEND** | Same `backend/knowledge/retrieval/` package: a new scorer/ranking function combining exact + lexical (Postgres FTS) + semantic (`pgvector` distance) signals, composed the same way `service.py` already composes currentness/applicability/relevance today | Preserves the existing Protocol-based extensibility this package was already designed for (its own docstring: "a future semantic/embedding-backed scorer could satisfy this same Protocol without service.py changing") | None beyond 6A.5's own implementation | N/A |
| Experience Memory persistence (6A.8) | None | **EXTEND** | New Alembic-managed table set in the same Cloud SQL instance, isolated from `slopanoc_knowledge_objects` (mirrors the existing Case-vs-Knowledge isolation pattern) — logically distinct from Approved Knowledge even on shared physical infrastructure | Same "one relational engine, many isolated domains" pattern already proven 3x | Low | Row count / query pattern measured at 6A.8 implementation time |
| Skills registry (6A.7) | None | **ADD** | Version-controlled definition files (in-repo, reviewed via normal PR/code review), with an optional lightweight Cloud SQL lookup/index table added later ONLY if runtime lookup performance requires it | Skills are behavioral definitions, not high-volume transactional data — versioning/review/auditability favor source control over a database-first registry | Low (no new service; possibly a small future table) | Runtime lookup latency/volume measured at 6A.7 implementation time |
| Context Engineering runtime placement (6A.6) | None | **EXTEND** | A new in-process Python package inside the existing FastAPI backend (e.g. `backend/context_engineering/`), mirroring `backend/knowledge/`'s own dependency-boundary discipline | No new reasoning boundary, no new agent, no new microservice — Context Engineering is a deterministic platform layer, never a service of its own (`docs/INTELLIGENCE_ARCHITECTURE.md` §4) | None | N/A |
| Caching | None | **DEFER** | — | No measured requirement exists yet; embedding/retrieval/context-assembly latency is still unmeasured (§10) | N/A | A real measured latency/cost problem, found only after 6A.5/6A.6 are implemented and measured |
| Observability (6A.11) | `perf_timing.py`/`run_trace.py` (structured log lines) | **EXTEND** | Same structured-log-line pattern, extended with new fields (retrieval latency, candidate counts, context size, token consumption); durable metric storage (if 6A.11 needs it) prefers a small Cloud SQL table over BigQuery at current scale | Reuses proven, zero-new-dependency instrumentation; BigQuery/Cloud Monitoring export remains a Phase 4H+/production-observability-hardening concern, not 6A's | Low | Metric volume/query pattern that a Cloud SQL table cannot serve |
| Security / IAM | Existing `slopanoc_migrator`/`slopanoc_runtime` role split, private GCS, Secret Manager | **EXTEND** (apply the same pattern to every new table/bucket) | New tables use `slopanoc_runtime` DML-only access exactly like existing tables; the new GCS bucket mirrors the existing private/uniform-access config; no new trust boundary invented | Preserves already-proven least-privilege pattern | None new | Phase 4H's own hardening pass |

---

## 5. A5 capability audit (multimodal / compound knowledge)

**What already works, deterministically, with no Gemini call:**
DOCX (paragraph/heading/table/hyperlink extraction in true document
order, embedded-media discovery via the real OOXML relationship graph),
XLSX (workbook→sheet→row/cell structure, formulas read as literal
strings, never evaluated), PDF (one artifact per page, 1-based page
locator, encrypted-PDF detection), TXT (verbatim line structure), and
OLE2 Compound-File-Binary embedded objects (e.g. a Word "Insert Object"
embed, parsed via `olefile` — no macro/VBA/COM execution capability of
any kind). A recursive dispatcher (`extractors/dispatch.py`) walks
nested containers to a defensive depth/count/size limit, with partial-
failure isolation (one bad embedded object never aborts its parent).

**What uses Gemini:** only image interpretation
(`gemini_image_interpreter.py`) — a bounded, tool-less, one-shot call
through the same shared model client every other agent call uses; EMF/
WMF vector images are rasterized to PNG first (via Pillow's bundled GDI
path) before interpretation. Derived (model-generated) text is always
kept distinct from source (structurally-extracted) text (`derived: bool`
on `KnowledgeArtifact`), and retrieval ranking already prefers native
source content over a derived description at comparable relevance
(§4's Hybrid ranking row).

**What metadata/provenance/relationships are already retained:**
`KnowledgeArtifact.artifact_id`/`parent_artifact_id` (full hierarchical
lineage, including nested embeds — e.g. an image inside an embedded DOCX
inside the root document), `content_hash` (SHA-256, enabling structural,
key-based deduplication of byte-identical embedded objects across
different parent documents — proven against the real corpus, where two
real MOPs share byte-identical boilerplate attachments), `extraction_
status` (COMPLETE/PARTIAL/FAILED/SKIPPED), `derived`/`kind` (open
string), and a `storage_ref` (`gs://...`) back to the durable, content-
addressed GCS binary. This is exactly the document→section→procedure
step→table→row/cell→image/parent-relationship hierarchy Phase 6A's own
`docs/INTELLIGENCE_ARCHITECTURE.md` §8 requires — **A5 already satisfies
it**, live-validated against the real TELCO/RAN corpus (2 real Rogers
MOPs at 19 artifacts each, including 2-level nesting).

**What 6A.3 still needs (genuine gaps, not present today):** an
`embedding` vector (and its generating model/version) associated with a
section/artifact identity for 6A.5's own retrieval; the still-
unprovisioned dedicated `knowledge-artifacts` GCS bucket (§4); nothing
else — the extraction/provenance/dedup/lineage foundation itself is
already complete and does not need replacing.

**Conclusion: 6A.3 must EXTEND A5, never replace it.** No evidence
anywhere in the codebase or corpus supports introducing Document AI or
any other document-processing service — see §4's explicit REJECT
rationale.

---

## 6. Multimodal architecture decision

PDF/DOCX/XLSX/tables/images/diagrams/embedded artifacts continue through
A5's existing extractors (§5), producing `KnowledgeArtifact` nodes with
full parent-chain lineage back to the (virtual, unrepresented) root
document. Provenance survives because identity is structural, not
positional: `(knowledge_id, version_label, artifact_id)` (or,
non-artifact text, `(knowledge_id, version_label, section_id)`) is the
same identity already used end-to-end in the existing evidence/
provenance pipeline (`docs/KNOWLEDGE_CONTRACT.md` §17/§19). 6A.3's own
job is to attach an embedding to this SAME identity, never to invent a
second identity scheme for "searchable" content.

---

## 7. Retrieval architecture decision

### 7.1 Applicability-before-retrieval (unchanged from `docs/INTELLIGENCE_ARCHITECTURE.md` §9)

Physical mapping: deterministic applicability filtering stays exactly
where it is today — Python, in-process, over `KnowledgeObject.
applicability`/6A.2's new TELCO Context columns — narrowing the
candidate set BEFORE any Postgres query touches exact/lexical/vector
indexes. No change to this boundary.

### 7.2 Exact + lexical

Native PostgreSQL: exact match via ordinary indexed equality (structured
identifier columns from 6A.2/6A.4); lexical via `tsvector`/`tsquery` +
`GIN` index over section/artifact text. No new service.

### 7.3 Vector / semantic

**Decision: Cloud SQL PostgreSQL + `pgvector` is the preferred Phase 6A
hybrid-retrieval foundation, subject to the live confirmation in §8.**
Evaluated against the alternatives:

| Requirement | Cloud SQL + `pgvector` | Vertex AI Vector Search |
|---|---|---|
| TELCO corpus size (current, evidence-backed — §5's real validation corpus: 4 governed objects, tens of artifacts) | Comfortably sufficient | Massive over-provisioning |
| Expected evidence-unit growth (MVP → growth tier, §9) | `pgvector`'s IVFFlat/HNSW indexes handle up to low-millions of vectors well within interactive latency | Designed for that scale and far beyond; no advantage until this scale is reached |
| Metadata-filtering requirement (§6/§7.1's applicability-first narrowing) | Native — same SQL query, same transaction, same row | Requires a separate filtered-search API and keeping two systems' metadata in sync |
| Hybrid retrieval (exact + lexical + semantic, §7.2) | One database, one query surface, one transaction boundary | Exact/lexical would still need Postgres; vector would live in a second system — two round trips, no shared transaction |
| Latency | One in-VPC/same-region DB round trip | An additional network hop to a separate managed service |
| Cost | No new managed service; storage/compute already paid for | New managed service with its own base cost |
| Operational complexity | One persistence technology to operate, already proven in production use here | A second data plane to provision, secure, and keep synchronized with Postgres |
| Governance / provenance | Trivial — the vector lives in the same row/table as the already-governed `KnowledgeObject`/artifact identity | Requires a second identity-consistency mechanism between the vector index and the KM repository |
| Codebase familiarity | This backend already has three Cloud-SQL-backed persistence layers | None — a first-time integration |
| Future growth | Documented migration trigger below | N/A until that trigger is hit |

**Future migration trigger (explicit, not arbitrary):** reconsider
Vertex AI Vector Search (or another dedicated vector service) if, once
6A.5 is actually implemented and measured against real corpus growth,
any of: candidate vector count exceeds roughly **1–5 million** rows,
measured p95 vector-query latency exceeds the troubleshooting-loop's own
latency budget (§10) with a properly tuned `pgvector` index, or Cloud SQL
CPU/memory dedicated to index maintenance materially degrades the
session/Case/Knowledge workloads already sharing that instance. None of
these conditions are met today — A5's real validation corpus is four
governed objects.

### 7.4 Hybrid ranking placement

See §4's "Hybrid ranking" row — `backend/knowledge/retrieval/`, extending
the existing `KnowledgeRelevanceScorer` Protocol. Vector similarity is
one input signal, never the final authority — the same
applicability-then-relevance-then-tie-break discipline
`docs/KNOWLEDGE_CONTRACT.md`'s 5.1G/A5-corrective-pass ranking already
enforces stays in force, extended with an exact/lexical/semantic input
instead of lexical-only.

---

## 8. Database / `pgvector` feasibility check — LIVE-VERIFIED DURING 6A.5

**UPDATE (6A.5 / P11-M05):** this check WAS performed live, with valid
Application Default Credentials and a real Cloud SQL Auth Proxy
connection (`127.0.0.1:5433`, IAM DB auth, `costin.ionita@ericsson.com`).
Real, observed result:

```sql
SELECT name, default_version, installed_version
FROM pg_available_extensions WHERE name = 'vector';
-- name='vector', default_version='0.8.5', installed_version=NULL
```

The extension IS available (version 0.8.5) but is NOT installed.
Attempting `CREATE EXTENSION IF NOT EXISTS vector` (both a direct,
rolled-back probe AND the real 6A.5 Alembic migration itself) was
DENIED:

```
asyncpg.exceptions.InsufficientPrivilegeError: permission denied to
create extension "vector"
HINT: Must be superuser to create this extension.
```

The connected IAM role is a member of `cloudsqliamuser`/
`slopanoc_migrator`/`slopanoc_runtime` — none of which include Cloud
SQL's own `cloudsqlsuperuser` pseudo-role, which Cloud SQL PostgreSQL
requires specifically for `CREATE EXTENSION`. This is a REAL,
CONFIRMED, currently-unresolved blocker (not self-granted, not worked
around) — see `docs/KNOWLEDGE_CONTRACT.md` §27.7/§27.9 and the 6A.5
closure report's own Evidence Pack for the full record. The conclusion
remains: **Cloud SQL PostgreSQL + `pgvector` is still the correct,
preferred architecture** (nothing about this blocker invalidates that
decision — it is a privilege/administration gap, not an architecture
gap) — installing the extension requires a `cloudsqlsuperuser`-
privileged administrative action on the real instance, outside any
application code's or migration's own authority.

---

## 9. Scale assumptions (honest, tiered — not fabricated production volume)

```text
CURRENT / MVP    — tens of governed knowledge objects, low hundreds of
                    artifacts/sections/evidence units (A5's real
                    validation corpus: 4 objects, ~40 artifacts total)
GROWTH           — hundreds to low thousands of governed documents,
                    tens of thousands of evidence units
LARGE SCALE      — hundreds of thousands of documents, millions of
                    evidence units — the tier at which §7.3's migration
                    trigger becomes relevant
```

No exact future production volume is claimed or assumed beyond this.

---

## 10. Latency budget (preliminary, honest)

```text
MEASURED CURRENT:
  model warm-up cold-start: ~5-22s observed (P4COLD investigation,
    CLAUDE.md) — mitigated by process-lifetime warm-up, unrelated to
    retrieval
  per-model-call timing: instrumented (perf_timing.py) but no retrieval-
    path timings exist yet, because no 6A retrieval path exists yet

ESTIMATED FUTURE (design intent, not measured):
  applicability filter: sub-millisecond, in-process Python over an
    already-narrowed candidate set (unchanged from today's 5.1G behavior)
  exact/lexical/vector query: one Postgres round trip, same
    instance/region as the rest of the runtime — target low tens of
    milliseconds, not measured

UNKNOWN / MUST MEASURE (explicitly, not guessed):
  actual pgvector query latency at any real corpus size (blocked on §8)
  embedding-generation latency per document/section at ingestion time
  end-to-end context-assembly latency once 6A.6 exists
```

Design principle carried forward unchanged: deterministic filtering
(applicability, exact/lexical narrowing) happens BEFORE the more
expensive vector/semantic step and BEFORE the specialist model call, so
model reasoning operates over a small, already-validated candidate set —
never the reverse.

---

## 11. Cost model (qualitative; no fabricated cloud prices)

| Component | Classification |
|---|---|
| Gemini/Vertex AI model calls | Already paid/existing |
| Cloud SQL PostgreSQL (existing instance) | Already paid/existing |
| GCS (existing + one new bucket, §4) | Already paid/existing, negligible incremental (bucket creation itself is free; storage is usage-based and small at current corpus size) |
| `pgvector` extension | Negligible incremental (no separate billing; index storage/compute is part of the existing Cloud SQL instance) |
| Embedding API calls (6A.5) | Usage-based incremental — proportional to corpus size and re-embedding frequency, no bulk embedding performed by this milestone |
| Vertex AI Vector Search (deferred) | Would be a material new fixed/operational cost — exactly why §7.3 defers it |
| Document AI (rejected) | Would be a material new usage-based cost for a capability A5 already provides free (no per-document API charge) |
| Memorystore/Redis (deferred) | Would be a material new fixed cost with no demonstrated need |

---

## 12. Security implications (Phase 4H remains the hardening owner)

New trust/IAM boundaries this architecture will eventually introduce
(not implemented now):

- New Cloud SQL tables (TELCO Context, Experience Memory) must use the
  existing `slopanoc_runtime` (DML-only) / `slopanoc_migrator` (schema)
  role split — no new role, no elevated privilege.
- The new `knowledge-artifacts` GCS bucket must mirror the existing
  chat-attachments bucket's private/uniform-bucket-level-access/no-
  public-access configuration exactly.
- A `pgvector` column/index carries no new secret material — vectors are
  derived numeric data, not credentials — but the ARTIFACT/SECTION
  identity a vector points back to must remain governed by the SAME
  CANDIDATE/APPROVED/ARCHIVE lifecycle as its source `KnowledgeObject`:
  a retrieval query must never surface a vector match whose owning
  object is not currently APPROVED-and-effective, exactly like today's
  lexical retrieval already respects lifecycle (`docs/KNOWLEDGE_
  CONTRACT.md` §14/§16).
- Customer/account isolation (§19 of the 6A.1 instruction, `docs/
  INTELLIGENCE_ARCHITECTURE.md` §6's TELCO Context dimensions already
  include `Customer`/`Account`): the physical design must make a future
  `customer`/`account` filter enforceable as an ordinary indexed WHERE
  clause (or row-level security policy) on every new table this document
  introduces — no schema decision here makes cross-account isolation
  structurally difficult later. The actual enforcement mechanism
  (application-level filter vs. Postgres row-level security) is a 6A.2/
  6A.4 decision, not made here. **6A.4 decision (COMPLETE):**
  application-level deterministic filtering (`backend/knowledge/
  narrowing/`, in-process, no new table, no JSONB/`GIN` index, no
  Postgres row-level security) — justified by this milestone's own
  measured real-corpus scale (3 real governed objects); no query-
  performance need was found or is expected until corpus scale grows by
  orders of magnitude, per `docs/KNOWLEDGE_CONTRACT.md` §26.9.
- No secret value was read, printed, or logged during this audit.

This document implements none of Phase 4H's actual hardening — it only
avoids foreclosing it.

---

## 13. Implementation scope of this milestone (minimum foundation only)

**No infrastructure was created, deleted, or modified.** No `CREATE
EXTENSION`, no bucket creation, no IAM change, no database migration, no
production index. This milestone's only output is this document plus the
status/cross-reference edits listed in the closure report — a
documentation/decision-record milestone, exactly as instructed.

---

## 14. Explicitly deferred decisions

- Exact embedding model/version for 6A.5 (only the *provider family* —
  Vertex AI, same client as Gemini — is decided here).
- `pgvector` live-availability confirmation (§8) — a precondition for
  6A.5, not resolved here.
- Exact TELCO Context table schema (6A.2's own job).
- Exact customer/account isolation enforcement mechanism (6A.2/6A.4).
- Skills file format and directory layout (6A.7's own job).
- Whether 6A.11 needs durable metric storage at all, and if so its exact
  schema.
- Dedicated production runtime service account creation (pre-existing
  limitation, unrelated to and not newly introduced by Phase 6A).

## 15. Non-regression constraints

See the closure report's §O — restated in full there per this
milestone's own required structure; unchanged from `docs/INTELLIGENCE_
ARCHITECTURE.md` §17.
