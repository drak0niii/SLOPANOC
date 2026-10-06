SLOPANOC / DEMO-NEW — POC hardening tranche 1
Source commit: 443b26fab42666a94743419965eab6c91943bd8e

STATUS
- Created patch applicator; NOT applied to GitHub. GitHub branch creation returned HTTP 403.
- Validated applicator syntax and exact-anchor transformation against a synthetic fixture.
- Did NOT execute the project's actual backend/frontend tests or live integrations.
- This is partial delivery, NOT a claim that all seven corrective items are complete.

APPLIED BY THIS SCRIPT
1. Removes inference of executed checks from arbitrary troubleshooting conversation.
   Adds an owner-scoped POST /api/sessions/{session_id}/checks/{check_id}/confirm
   that requires the explicit check ID and a nonblank user-reported result.
3. Only explicitly selected KM sections enter TAE's trusted evidence; arbitrary
   model-supplied case/user/metric evidence IDs are rejected. Existing verified
   Cases/metrics need a later server-side resolution adapter to be re-enabled.
6. Writes a durable per-session execution-attempt marker before an external Teams
   call, blocking blind same-session retries after ambiguous outcomes. This is
   containment, NOT exactly-once delivery: the Power Automate flow must dedupe
   a stable idempotency key across sessions and deployments, and a reconciliation
   endpoint is still required.
7. Adds targeted regression tests and a CI workflow that runs the full backend
   tests, frontend tests and frontend build.
Extra: removes exception details from the TAE's model-facing error responses.

NOT COMPLETE; MUST REMAIN OUT OF SHARED/LIVE DEPLOYMENT
2. Typed command authorization: requires a trusted registry of procedure ID,
   version, step ID, operation class, equipment target, parameters, approvals,
   prerequisites, expiry and restrictions. String-matching a MOP is insufficient.
4. Final-response operational safety: structured renderer and deterministic
   policy recheck are not implemented by this patch. Do not use command advice
   to operate live network devices until this is implemented and tested.
5. Production authentication: requires Entra issuer, API audience, JWKS/token
   validation, frontend OIDC/PKCE, authorization roles and tenant configuration.
   The current development identity header is NOT authentication.
6. External idempotency: see note above; this patch only provides a local fence.
7. End-to-end LIVE evaluation requires authorized Vertex AI, Cloud SQL, Teams,
   GCS and operator-reviewed scenarios; CI/mock tests alone do not establish it.

INSTALL (PowerShell, from repo root)
  git fetch origin DEMO-NEW
  git switch DEMO-NEW
  git pull --ff-only origin DEMO-NEW
  git switch -c poc-hardening-20260921
  Copy-Item "<download-folder>\apply_poc_hardening.py" .
  python .\apply_poc_hardening.py --repo .
  python .\apply_poc_hardening.py --repo . --apply
  python -m pytest backend/tests/test_poc_hardening_tranche1.py -q
  python -m pytest backend/tests -q
  npm test
  npm run build
  git diff --check
  git status --short

INSTALL (macOS/Linux, from repo root)
  git fetch origin DEMO-NEW
  git switch DEMO-NEW
  git pull --ff-only origin DEMO-NEW
  git switch -c poc-hardening-20260921
  cp /path/to/download/apply_poc_hardening.py .
  python3 apply_poc_hardening.py --repo .
  python3 apply_poc_hardening.py --repo . --apply
  python3 -m pytest backend/tests/test_poc_hardening_tranche1.py -q
  python3 -m pytest backend/tests -q
  npm test && npm run build
  git diff --check
  git status --short

The script refuses any branch other than poc-hardening-20260921, refuses a
nonmatching HEAD, refuses dirty tracked files, and validates every patch anchor
and all generated Python syntax BEFORE writing ANY file. It neither commits
nor pushes. Review/test changes before git add/commit/push.

FOLLOW-UP ENGINEERING GATES
- Verify prior regression suites after the evidence-selection change; this
  deliberately revokes the old 'retrieved evidence is trusted evidence' path.
- Implement typed command policy and a final allowlisted rendering boundary.
- Complete Entra authentication before any network-accessible demonstration.
- Add provider-side idempotency and reconciliation before enabling Teams writes
  for users who may retry, open multiple sessions, or use multiple instances.
