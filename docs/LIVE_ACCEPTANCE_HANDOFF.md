# SLOPANOC — POST-6A live-acceptance handoff

Executable steps for **this checkout**, in PowerShell, from the repository
root.

Nothing in this document has been run. No migration has been applied, no
Teams message sent, no real token obtained. Every command below is yours
to run deliberately.

**Placeholders only.** `REPLACE-…` values are yours to fill in. Never
paste a real token, gateway URL or connection string into a file in this
repository.

---

## 0. Install the newly declared dependencies

Two backend packages (`PyJWT`, `cryptography`) and one frontend package
(`oidc-client-ts`) are now declared in the manifests. They are already
present in this working environment, but a clean checkout needs them.

```powershell
python -m pip install -r requirements.txt
npm ci
```

Verify the build and the hermetic suites before anything else:

```powershell
npm run build
python -m pytest backend/tests/test_post6a_prompt6.py `
                 backend/tests/test_post6a_identity_and_concurrency.py `
                 backend/tests/test_post6a_durable_outcomes.py -q
npx vitest run src/api/auth.test.ts
```

---

## 1. Pending migrations — MUST be applied before a PostgreSQL deployment

Two revisions are unapplied. **Neither has been run against any
database.**

| Revision | Table | Why it is required |
| --- | --- | --- |
| `f1b6c3d05a27` | `slopanoc_operation_approvals` | Durable human approval records for governed operation descriptors |
| `b7c4e1a95d60` | `slopanoc_operation_claims` | The **fencing generation** that makes multi-worker dispatch safe |

`b7c4e1a95d60` is not optional for a multi-worker PostgreSQL deployment:
without the table, `execution_service` **refuses to dispatch** rather
than sending unfenced. That refusal is deliberate — a deployment that
looks protected and is not is worse than one that fails.

Start the Cloud SQL Auth Proxy, then apply:

```powershell
# Terminal 1 — proxy (see README.md for the instance string)
./cloud-sql-proxy.exe --auto-iam-authn `
  pr-msn-dev-gl-slopai-01:europe-west4:sloc-anoc-sandbox01

# Terminal 2 — inspect FIRST, apply only when the plan looks right
$env:SLOPANOC_DATABASE_URL = "postgresql+asyncpg://REPLACE-IAM-USER@127.0.0.1:5432/slopanoc"
$env:SLOPANOC_KNOWLEDGE_DATABASE_URL = $env:SLOPANOC_DATABASE_URL

python -m alembic current
python -m alembic history --verbose | Select-Object -First 30
python -m alembic upgrade head
python -m alembic current      # expect b7c4e1a95d60
```

Run migrations as `slopanoc_migrator`, not `slopanoc_runtime`.

---

## 2. Identity provider configuration

Register a **SPA** application. The redirect URI is derived by the app as
`<origin>/auth/callback` and is not configurable, so it cannot drift from
the route that handles it. Register exactly:

```
http://localhost:5173/auth/callback      (Vite dev server)
https://REPLACE-YOUR-HOST/auth/callback  (deployed)
```

Post-logout redirect URI is the bare origin.

Expose an API scope on the API app registration (e.g.
`api://REPLACE-API-CLIENT-ID/user_impersonation`) and grant the SPA
access to it. **This is the step that decides whether login works at
all:** without the API scope in the requested scopes, the authorization
server issues only an ID token, which is not a credential this API
accepts.

### Backend

```powershell
$env:SLOPANOC_AUTH_MODE      = "oidc"
$env:SLOPANOC_AUTH_ISSUER    = "https://login.microsoftonline.com/REPLACE-TENANT-ID/v2.0"
$env:SLOPANOC_AUTH_AUDIENCE  = "api://REPLACE-API-CLIENT-ID"
$env:SLOPANOC_AUTH_JWKS_URL  = "https://login.microsoftonline.com/REPLACE-TENANT-ID/discovery/v2.0/keys"
$env:SLOPANOC_AUTH_ALGORITHMS      = "RS256"
$env:SLOPANOC_AUTH_ALLOWED_TENANTS = "REPLACE-TENANT-ID"

# Governance authorization — deny-by-default. Set at least one.
$env:SLOPANOC_KNOWLEDGE_GOVERNOR_ROLES = "SLOPANOC.Governor"
# or the explicit id allowlist (derived form, never an email):
# $env:SLOPANOC_KNOWLEDGE_GOVERNORS = "aad:REPLACE-TENANT-ID:REPLACE-OBJECT-ID"

uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

With `SLOPANOC_AUTH_MODE=oidc` and any of issuer/audience/JWKS missing,
**every request is rejected**. It never falls back to development
identity.

### Frontend

Create `.env.local` (see `.env.example`; never commit it):

```powershell
@"
VITE_SLOPANOC_AUTH_MODE=oidc
VITE_SLOPANOC_AUTH_ISSUER=https://login.microsoftonline.com/REPLACE-TENANT-ID/v2.0
VITE_SLOPANOC_AUTH_CLIENT_ID=REPLACE-SPA-CLIENT-ID
VITE_SLOPANOC_AUTH_SCOPE=openid profile api://REPLACE-API-CLIENT-ID/user_impersonation
"@ | Out-File -FilePath .env.local -Encoding utf8

npm run dev
```

---

## 3. Authoring and approving ONE real descriptor

A governed operation descriptor is what turns an approved procedure into
an executable command. **No tool can infer one** — target scope,
parameters and command templates must be read off the procedure by a
person. The code refuses to guess, deliberately.

Obtain an access token for yourself (interactively — this is the one step
that cannot be scripted from here), then:

```powershell
$env:SLOPANOC_API_BASE_URL = "http://127.0.0.1:8000"
$env:SLOPANOC_API_TOKEN    = "REPLACE-PASTE-YOUR-ACCESS-TOKEN"

# 1. See what sections exist and their current descriptor state.
python -m backend.tools.admin.descriptor_admin list REPLACE-KNOWLEDGE-ID REPLACE-VERSION

# 2. Get a commented template and EDIT IT BY HAND.
python -m backend.tools.admin.descriptor_admin template > draft.json
notepad draft.json
```

In `draft.json`, fill in from the real procedure text:

- `target_scope` — `single_target` / `multi_target` / `target_independent`.
  Never leave `unknown`.
- `parameters` — each `target_identifier` the command needs.
- `command_templates[].template` — use `{parameter_name}` placeholders.
  **Never paste a filled-in example identifier from the document.** An
  example value in a procedure is not a live target, and substituting one
  is exactly the failure the whole mechanism exists to prevent.

```powershell
# 3. Draft it (CANDIDATE — grants nothing).
python -m backend.tools.admin.descriptor_admin draft REPLACE-KNOWLEDGE-ID REPLACE-VERSION REPLACE-SECTION-ID draft.json

# 4. Review, then approve. Identity comes from the token, never a flag.
python -m backend.tools.admin.descriptor_admin show    REPLACE-KNOWLEDGE-ID REPLACE-VERSION REPLACE-SECTION-ID
python -m backend.tools.admin.descriptor_admin approve REPLACE-KNOWLEDGE-ID REPLACE-VERSION REPLACE-SECTION-ID --note "reviewed against the procedure"
```

The `approved by …` line prints the identity **the server verified from
your token**. Confirm it is you. If it is not, stop — that is the
DEF-0046 failure mode and it should be impossible now.

To withdraw: `… revoke REPLACE-KNOWLEDGE-ID REPLACE-VERSION REPLACE-SECTION-ID`.
Editing a descriptor also withdraws its approval automatically.

---

## 4. Manual acceptance scenarios

Sign in at `http://localhost:5173/app`. Each scenario states what you
should see; a deviation is a finding, not a retry.

| # | Do this | Expected |
| --- | --- | --- |
| 1 | **Greeting** — "hello" | A plain conversational reply. No specialist routing, no governed retrieval, no command. |
| 2 | **Missing target** — ask for the operation from §3 without naming a unit | A clarification asking for the missing identifier. **No command text**, not even an example one. |
| 3 | **Confirmation** — reply with just the unit id | The turn resumes the pending operation. The identifier is bound to the same request, and the approved command appears with **your** value, never the procedure's example. |
| 4 | **Correction** — "no, I meant REPLACE-OTHER-UNIT" | The previous target is invalidated and re-confirmed. The old target must not survive anywhere in the reply. |
| 5 | **Unrelated topic** — ask something else that happens to mention the same unit | It must **not** inherit the pending operation's authority. Mentioning a unit is not resuming a request. |
| 6 | **Observation → next step** — paste a command output or screenshot | The output is interpreted **before** any next step is offered. No jumping to step N+1 on unread evidence. |
| 7 | **Reload** — refresh mid-investigation | History shows the identical canonical text, not regenerated prose. Investigation continuity survives. |
| 8 | **Approved Teams write** — propose a message, approve it | Exactly one message. The approval card shows the real destination; the model cannot substitute another. |

### While the gateway returns an empty acknowledgement

Point the gateway at a flow that returns `200` with an empty body, or
`{}`:

```powershell
$env:SLOPANOC_PA_WRITE_CONTRACT = "default"
```

Expected: the write is reported as **not confirmed**, recorded as
`UNKNOWN_OUTCOME`, and **no automatic retry happens**. The durable claim
row stays `unknown_outcome` and becomes unclaimable, so a second attempt
is refused rather than silently re-sending.

This is correct, not a bug. An empty body is not evidence of success, and
the honest resolution is a human checking Teams. `ack_only` /
`identifier_only` select among *supported* contracts for a flow that
genuinely answers differently — **no value makes a response with no
positive evidence count as a successful write.**

---

## 5. What is still required, and what is still unproven

### External dependencies you must supply

- **A real identity provider.** Tenant, SPA + API app registrations, the
  exposed API scope, the redirect URI, and a governor role or allowlist
  entry for at least one human.
- **Power Automate flow, unchanged.** Still the M365 gateway; no Graph
  migration. Application authentication does **not** grant per-user
  Microsoft permissions — a write executes as the flow's identity, not as
  the signed-in user. That limitation is unchanged by this campaign.
- **PostgreSQL / Cloud SQL** for both persistence domains, with both
  migrations applied.

### Verification gaps — stated plainly

- **Distributed correctness is UNVERIFIED.** Isolated PostgreSQL was not
  available for this pass. Every concurrency check ran on SQLite in a
  single process, which exercises the fencing *rules* and nothing about
  two workers. Session advisory locks, `pg_locks` ownership probes, and
  `ON CONFLICT` under real contention have not been run. Do not treat the
  passing tests as evidence of multi-worker safety.
- **No exactly-once delivery, and none is claimed.** Between the
  conditional `DISPATCHED` write and the gateway accepting the request
  there is a real window that only gateway-side deduplication on an
  idempotency key could close. Power Automate offers none.
- **No live acceptance has been performed.** Nothing in this repository
  has been validated against a real IdP, a real database, or a real Teams
  write. Production readiness is **not** established.
- **One redundant model call remains.** A turn already answered
  deterministically (clarification or capability restriction) still runs
  the tool-free presentation Runner and discards its output. Removing it
  requires taking over the user-turn event append that ADK's
  `Runner._append_new_message_to_session` currently owns — without that,
  the turn vanishes from history projection. That is a change to the
  canonical persistence path, and it was deliberately left out of this
  campaign rather than attempted without a full regression run.

### Known pre-existing test failures (unchanged by this campaign)

Present at the audited baseline and still failing, for reasons unrelated
to this work:

```
backend/tests/test_r1_r3_correctness_regression.py  (3)
backend/tests/test_livecorr3_operational_guidance_and_streaming_safety.py  (2)
```

### Untracked artifacts staged for removal

`git rm --cached` has been staged for `logs/` (32 files),
`cloud-sql-proxy.exe`, `slopanoc-current.patch`,
`slopanoc-untracked-tests.zip` and `teams-test-image.png`. **Every file
remains on disk**; only the tracking is removed, and no history was
rewritten. Review and commit at your discretion:

```powershell
git status --short | Select-String '^D '
```

Note that the debug logs under `logs/` were written from real runs and
can contain real session and identity detail — that is the main reason
they should not stay in version control.
