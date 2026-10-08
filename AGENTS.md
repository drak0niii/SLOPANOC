# SLOPANOC Codex Engineering Contract
## Authoritative Observability Architecture
For all Observability, SRE and FinOps development, the authoritative specifications are under:
`docs/Telemetry/`
Start with:
1. `docs/Telemetry/README.md`
2. `docs/Telemetry/00_MASTER_BUILD_CONTRACT.md`
3. `docs/Telemetry/12_IMPLEMENTATION_ROADMAP.md`
4. `docs/Telemetry/14_CODEX_EXECUTION_GUIDE.md`
5. `docs/Telemetry/15_DEFINITION_OF_DONE.md`
Then read the specialist design documents referenced by the current milestone.
The current local working tree is authoritative.
Do not assume remote GitHub is newer than local code.
Never reset, checkout, revert, discard, overwrite, or otherwise destroy unrelated existing local work.
---
## Observability Execution Model
The Production Observability + SRE + FinOps platform must be implemented strictly sequentially:
M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9 → M10 → M11 → M12 → M13 → M14 → M15 → M16 → M17 → M18 → M19.
ONLY ONE milestone may be implemented at a time.
Never begin milestone N+1 until milestone N satisfies every applicable exit criterion.
If any current milestone exit criterion fails:
- stop progression;
- repair the current milestone;
- run validation again;
- do not implement the next milestone;
- do not silently waive or reinterpret an exit criterion.
A milestone is complete only when its implementation and validation gates both pass.
---
## Execution Plans
For this program use:
`.agent/OBSERVABILITY_EXECUTION.md`
as the persistent execution and status record.
Before implementation of every milestone, update that file with:
- current milestone;
- objective;
- authoritative specification files;
- existing architecture inspected;
- components to reuse;
- files expected to change;
- implementation plan;
- architecture and safety invariants;
- test and validation commands;
- exit criteria.
After implementation update it with:
- implementation performed;
- actual files changed;
- validation performed;
- validation results;
- failures encountered;
- repairs performed;
- exit criteria PASS/FAIL;
- known limitations;
- whether the next milestone is permitted.
This file is persistent engineering memory for the whole M0-M19 implementation.
---
## Scope Discipline
Do not implement later milestones early.
For example:
When implementing M2, implement the M2 root-turn tracing requirements.
Do not also implement M10 FinOps accounting simply because related interfaces are visible.
Small enabling interfaces for a future milestone are allowed only where necessary to satisfy the current milestone architecture and must not implement future business functionality.
Do not broaden the task beyond the current milestone.
---
## Existing SLOPANOC Architecture Must Be Preserved
Observability must integrate with the current SLOPANOC architecture.
Do not redesign or weaken:
- Team Manager conversational ownership;
- TurnPlan;
- conversation thread registry;
- pending interactions;
- Context Engineering Broker;
- source requirements;
- Technical Authority Engineer;
- Incident Manager;
- Problem Manager;
- Automated Operations;
- primary/supporting specialist ownership;
- TurnAuthority;
- claim provenance;
- governed knowledge;
- Teams resource continuity;
- troubleshooting progression;
- ProcedureAction;
- Command Authority;
- approvals;
- command egress;
- case/fault state;
- session persistence;
- SSE behavior;
- rewind behavior.
Observability is NEVER evidence or authority.
Telemetry may explain what happened.
Telemetry must never:
- select governed evidence;
- grant authority;
- authorize a command;
- bypass approval;
- change provenance;
- become conversation intent.
---
## Production Architecture Decisions
The following architecture is already approved.
Do not redesign it.
### Telemetry
SLOPANOC  
→ OTLP  
→ OpenTelemetry Collector  
→ Google Cloud Trace  
→ Google Cloud Monitoring  
→ Google Cloud Logging
### Active Run State
Production cross-instance active-run status is stored durably in Cloud SQL.
Process-local memory may be used only as a fast execution-local cache.
### Logging
Production application logging is structured JSON.
### FinOps
Primary accounting sources:
- GCP Detailed Billing Export;
- GCP Pricing Export.
FOCUS is supplementary.
Primary reporting currency:
EUR.
The runtime usage/cost ledger is:
- unsampled;
- idempotent;
- append-oriented;
- durable;
- accounting-grade.
### Infrastructure
Observability covers:
- Cloud Run;
- Cloud SQL;
- Vertex AI / Gemini;
- Microsoft Graph;
- BigQuery;
- Google Cloud Storage;
- knowledge retrieval;
- network/external dependencies;
- OpenTelemetry Collector;
- frontend/SSE path.
### Infrastructure as Code
Terraform is the standard for observability infrastructure where supported.
### Browser Tracing
Use W3C Trace Context.
### Continuous Profiling
CPU and memory profiling are part of the full production build.
## Cloud / Infrastructure Safety Gate
This is a HARD SAFETY RULE.
Codex must NEVER create, modify, enable, disable, deploy, destroy, migrate, import, reconfigure, or otherwise mutate any GCP, cloud, database, billing, IAM, Terraform-managed, Microsoft Graph, or other external-service resource without explicit user approval for that exact operation.
Planning a milestone, implementing local code, writing Terraform, writing migrations, or marking a milestone ready for validation does NOT grant permission to perform cloud or external-service mutations.
This rule applies to all milestones, including M0-M19.
Prohibited Without Explicit User Approval
Codex must NOT execute any command or action that can mutate a remote/shared environment, including but not limited to:
- gcloud services enable;
- gcloud services disable;
- gcloud run deploy;
- gcloud run services update;
- gcloud sql create/update/delete operations;
- Cloud SQL instance/database/user changes;
- database migrations against any shared, remote, staging, or production database;
- alembic upgrade against any shared or remote database;
- BigQuery dataset/table/view creation, modification, or deletion;
- GCP Billing Export configuration;
- Cloud Monitoring dashboard creation/modification;
- Cloud Monitoring alert-policy creation/modification;
- Cloud Logging sink/exclusion creation/modification;
- IAM role binding changes;
- service-account creation/modification/deletion;
- service-account key creation;
- Secret Manager changes;
- GCS bucket creation/modification/deletion;
- OpenTelemetry Collector deployment;
- Cloud Run environment-variable or secret changes;
- Terraform apply;
- Terraform destroy;
- Terraform import;
- Terraform state mutation;
- deployment scripts that modify cloud resources;
- Microsoft Graph / Entra / Teams configuration changes;
- external webhook/service configuration changes;
- any command with equivalent remote side effects.
Codex must treat an authenticated gcloud, Terraform, database, Microsoft, or external-service session as potentially production-impacting.
The existence of credentials or permissions does NOT imply authorization to use them for mutation.
Allowed Without Additional Approval
Codex MAY perform non-mutating local development activities required by the active milestone, including:
- inspect local source code;
- read local configuration;
- inspect Git history and diffs;
- create or modify local source files;
- create or modify local documentation;
- write Terraform files;
- write OpenTelemetry Collector configuration;
- write deployment manifests;
- write SQL/Alembic migration files without applying them remotely;
- write BigQuery SQL/view definitions without creating them remotely;
- write dashboard/alert definitions;
- write CI/CD configuration;
- run local unit tests;
- run local integration tests that use local/test doubles only;
- run static analysis;
- run linting;
- run type checking;
- run terraform fmt;
- run terraform validate;
- run a non-mutating terraform plan only when its backend/provider configuration cannot create, modify, import, lock, or otherwise mutate remote infrastructure or remote state;
- inspect existing GCP configuration with read-only commands where the command is known to have no mutation side effect.
If there is uncertainty whether a command is read-only, Codex must treat it as a mutation and STOP for approval.
Mandatory Approval Gate
Before ANY remote/cloud/external mutation, Codex must STOP and present:
1. Exact command or action proposed
2. Exact project / account / tenant / environment affected
3. Exact resources that will be created, modified, enabled, disabled, migrated, imported, or deleted
4. Why the mutation is required for the current milestone
5. Expected operational impact
6. Expected security / IAM impact
7. Expected FinOps / cost impact
8. Data-loss or service-interruption risk
9. Rollback / recovery method
10. Whether the action is DEV, STAGING, or PRODUCTION
Then Codex must wait for explicit user approval.
Approval must be specific to the proposed operation.
Approval for one operation does NOT authorize later operations.
Statements such as:
- "continue";
- "implement M1";
- "finish the milestone";
- "run the tests";
- "make it production ready";
do NOT constitute cloud-mutation approval.
Terraform Rule
Codex may author Terraform locally.
Codex may run:
- terraform fmt;
- terraform validate;
and may run terraform plan only when it has first verified that the plan operation is non-mutating and does not require unsafe remote state changes.
Codex must NEVER run:
- terraform apply;
- terraform destroy;
- terraform import;
- force-unlock/state mutation commands;
without explicit approval for that exact action.
Terraform-managed production configuration must remain source-controlled.
Database Rule
Codex may create migration files locally.
Codex must NEVER apply migrations to a shared, staging, or production database without explicit approval.
Before proposing a remote database migration it must report:
- target database/environment;
- migration revision;
- schema changes;
- table/index impact;
- lock/downtime risk;
- data migration risk;
- rollback strategy.
Local disposable databases may be used without separate approval only when they are clearly isolated from all shared/cloud environments.
Billing / FinOps Rule
Codex must NEVER enable or modify:
- Cloud Billing exports;
- billing account links;
- budget policies;
- pricing-export configuration;
- BigQuery billing datasets;
- cost-allocation infrastructure
without explicit approval.
Codex may prepare all code, SQL, Terraform, schemas, dashboards, and validation locally first.
IAM / Security Rule
Codex must NEVER change:
- IAM bindings;
- roles;
- service accounts;
- service-account keys;
- workload identity;
- OAuth configuration;
- secrets;
- API permissions
without explicit approval.
No milestone exit criterion may be silently interpreted as permission to perform these actions.
Milestone Completion and Cloud Deployment
A milestone may be considered:
IMPLEMENTED AND LOCALLY VALIDATED
even when a required cloud deployment has not yet been approved.
In that situation .agent/OBSERVABILITY_EXECUTION.md must explicitly record:
- local implementation status;
- local validation status;
- cloud deployment status = NOT EXECUTED — USER APPROVAL REQUIRED;
- exact pending cloud operations.
Codex must not falsely mark a cloud-dependent acceptance criterion as runtime-validated if the required cloud operation has not occurred.
Where the milestone's formal exit criterion requires live cloud validation, Ready for Next Milestone must remain NO until the approved live validation is complete, unless the authoritative roadmap explicitly separates local implementation from deployment validation.
Absolute Rule
No cloud mutation by implication. No deployment by assumption. No remote migration by convenience.
When in doubt:
STOP, show the exact proposed operation, and request explicit approval.
---
## Settings / UI Contract
The observability platform must be integrated into the existing SLOPANOC UI.
It must NOT become a separate application.
The target information architecture is:
Settings  
└── Observability & FinOps  
    ├── Overview  
    ├── Tracing  
    ├── Reliability  
    ├── SLOs  
    ├── FinOps  
    ├── Data & Retention  
    ├── Integrations  
    ├── Access  
    └── Diagnostics
The UI must use the existing SLOPANOC:
- layout;
- typography;
- visual language;
- component patterns;
- navigation;
- spacing;
- interaction model.
Do not introduce an unrelated design system.
### Normal Chat UI
Normal users see safe progress such as:
- Understanding request…
- Selecting context…
- Reading Teams conversation…
- Searching governed knowledge…
- Analyzing evidence…
- Preparing response…
A run ID may be available under a Details control.
Normal users must not see internal reasoning or unrestricted diagnostic data.
### Diagnostics
Authorized users must be able to locate a run and see:
- run ID;
- status;
- current stage;
- current agent;
- current tool;
- current dependency;
- elapsed time;
- last progress;
- timeline;
- errors;
- cost summary where permitted.
### Configuration Ownership
Configuration managed through:
- Terraform;
- production environment policy;
- secure backend configuration
must be presented in the production Settings UI as effective read-only configuration unless the architecture explicitly permits runtime mutation.
The Settings UI must never bypass IaC.
---
## RBAC
The observability UI and APIs use these roles:
- User
- Operator
- Developer / SRE
- FinOps
- Admin
- Auditor
FinOps may access:
- cost;
- usage;
- allocation;
- budgets;
- forecasts;
- unit economics.
FinOps must not automatically receive access to:
- raw conversation content;
- Teams message bodies;
- knowledge document contents;
- sensitive case information.
RBAC must be server-enforced.
---
## Security
Never log, export, or persist by default:
- hidden chain-of-thought;
- passwords;
- API keys;
- OAuth tokens;
- bearer tokens;
- authorization headers;
- full Teams message bodies;
- full prompts;
- full model responses;
- attachment contents;
- full governed knowledge bodies.
Follow:
`docs/Telemetry/09_SECURITY_PRIVACY_RETENTION_SAMPLING.md`
and:
`docs/Telemetry/18_STRUCTURED_LOGGING_STANDARD.md`
---
## Metric Cardinality
Never use high-cardinality identifiers as metric labels, including:
- run_id;
- turn_id;
- session_id;
- user ID;
- Teams chat ID;
- case ID;
- fault ID.
These belong in traces, durable records, or accounting ledgers.
---
## Testing and Validation
Every milestone must run the validations defined by:
- its roadmap requirements;
- `docs/Telemetry/13_TEST_VALIDATION_ACCEPTANCE.md`;
- its milestone-specific execution plan.
A milestone may be marked COMPLETE only when:
1. implementation exists;
2. code imports/builds;
3. milestone-specific tests pass;
4. applicable existing regression tests pass;
5. every exit criterion has been individually evaluated;
6. every blocking defect is fixed;
7. no required architecture item remains unimplemented.
Always distinguish:
IMPLEMENTED BUT UNTESTED
from:
VALIDATED COMPLETE.
Never mark IMPLEMENTED BUT UNTESTED as complete.
---
## Milestone Completion Report
At the end of every milestone return exactly:
### Milestone
### Implemented
### Files Changed
### Tests / Validation
### Exit Criteria
### Known Limitations
### UI / Settings Impact
### Ready for Next Milestone
`Ready for Next Milestone` must be:
YES
or:
NO
If NO, stop.
Do not begin the next milestone.
---
## UI Impact Rule
Every milestone must explicitly evaluate:
1. whether it creates information required by Settings → Observability & FinOps;
2. whether API/data contracts need to support that UI;
3. whether UI work belongs to the current milestone;
4. whether RBAC applies;
5. whether configuration is view-only or editable.
Record this in:
`.agent/OBSERVABILITY_EXECUTION.md`
Do not prematurely build UI belonging to a later milestone.
---
## FinOps Rule
Every billable model invocation must eventually have exactly one accounting path into the unsampled usage ledger.
Trace sampling must never become accounting sampling.
Estimated cost and provider-billed cost are separate values.
Do not overwrite financial history to apply corrections.
Use correction/reversal semantics from:
`docs/Telemetry/20_FINOPS_ACCOUNTING_DURABILITY.md`
---
## Repository Hygiene
Keep diffs scoped to the active milestone.
Do not modify unrelated code.
Do not perform opportunistic refactors unless required by the current milestone.
Do not commit generated secrets, credentials, telemetry payloads, local database contents, or production exports.
Before declaring a milestone complete inspect:
- `git status`;
- `git diff`;
- untracked files;
- test results.
---
## Final Rule
The files in `docs/Telemetry/` define WHAT must be built.
`.agent/OBSERVABILITY_EXECUTION.md` defines WHERE the implementation currently is.
The current source code defines WHAT actually exists.
Never claim completion from documentation alone.