# Shared application identity: local development and future IAP

ANOC and the separate Observability Read Plane consume the same
`verified_principal_provider` dependency. It delegates to the
`VerifiedIdentityProvider` interface in `backend/authentication.py`. ANOC maps the
validated principal's subject to its existing `UserContext`; session ownership,
approval, command and agent orchestration semantics are unchanged. Observability
continues to require capabilities, role ceilings and authorized environments at
each endpoint. `/access` returns only verified state, capabilities and environments.
There is no second login or browser-maintained Resources identity.

## Explicit local activation

The default `SLOPANOC_AUTH_MODE=disabled` denies human access. A usable local chat
session is not proof of production human authentication. To use the authorized
LOCAL/DEV adapter, supply the same trusted policy and environment to **both**
backend processes. Copy/review `config/auth/development.example.json` as necessary.
The example explicitly grants `api-user` Operator metadata access only; it does
not grant financial, technical or configuration privileges.

For a development shell (repository root), set:

```sh
export SLOPANOC_OTEL_ENVIRONMENT=development
export SLOPANOC_AUTH_MODE=development
export SLOPANOC_DEV_AUTH_POLICY_FILE="$PWD/config/auth/development.example.json"
```

Start ANOC with its existing approved local database/launch configuration. In a
second shell with the same three bindings, start the independent reader:

```sh
.venv/bin/python -m uvicorn backend.api.observability_app:create_app --factory --host 127.0.0.1 --port 8001
```

Start Vite normally with `npm run dev`, leaving the Observability Vite base unset.
The browser's same-origin reads are proxied to port 8001; ANOC and SSE receipts
remain on port 8000. No auth config is exposed through a `VITE_*` variable. No
remote database, cloud resource, identity or permission is provisioned by these
steps. Existing approved database/data-source bindings are still required for
real published telemetry; authentication does not generate data.

Without a dev-user header, both backends use the policy's `default_subject`,
`api-user` in the example (the historical ANOC default). The existing optional
`X-SLOPANOC-DEV-USER` selects only a subject explicitly listed in server policy.
Unknown subjects are denied. Browser roles, capabilities, environment headers,
plain Google email headers and unverified bearer/cookie values grant no authority.
There is no local login UI or new cookie/token mechanism.

This adapter validates a **local development identity against server policy**;
it does not cryptographically authenticate a human. The dev-user source retains
its existing local impersonation semantics for explicitly configured users.
Use loopback-only development services and never deploy this adapter as production
human authentication. It is rejected outside local/development, and rejected on
Cloud Run/App Engine runtime markers even if the environment was mislabelled.

## Trusted grant policy

The policy is bounded to 64 KiB and 64 users. Duplicate JSON keys, malformed grants,
unknown fields/capabilities, unlisted defaults, symlink files and invalid role
ceilings are rejected. Environments are limited to local/development; a subject
must be authorized for the actual process environment. The file is revalidated on
each request; removed/reduced grants are not restored by a stale provider cache.
Manage file access as server configuration, not as a browser-writable resource.

Capabilities must be listed explicitly, within the existing canonical ceiling:

| Role | Permitted ceiling |
|---|---|
| User | own_progress |
| Operator | own_progress, operational_metadata |
| Developer / SRE | operational_metadata, technical_diagnostics |
| FinOps | financial_data |
| Admin | operational_metadata, effective_configuration |
| Auditor | governed_summaries |

For a financial development session, explicitly configure the default user's
role as `FinOps` and capabilities as `["financial_data"]`. For technical diagnostics,
configure `Developer / SRE` with `["operational_metadata", "technical_diagnostics"]`.
Do not add financial permission to a Developer/SRE grant or technical permission
to FinOps. The current model has one role per principal and **no role permits both**.
Multi-role or expanded ceilings require a separately approved architecture change;
this repair does not introduce them. Both roles can use ANOC as the same configured
subject; business ownership remains keyed by that subject.

## Production and future Google Cloud IAP

Production without a real configured verifier denies requests. Development mode
causes startup failure in both backends before workers/warmup/read resources.
`IAPVerifiedIdentityProvider` is an inert deny-only boundary. Selecting `iap` today
still denies every request, including a plausible JWT assertion or email header.
It does not decode a JWT and pretend that decoding is verification.

Future work must implement signature verification of `X-Goog-IAP-JWT-Assertion`,
using the IAP public keys, expected issuer `https://cloud.google.com/iap`, exact
configured audience for the protected resource, and token time checks. Reject
missing, invalid, expired or wrong-audience assertions. Never authenticate using
plain `X-Goog-Authenticated-User-Email` alone. Map verified stable subject to trusted
SLOPANOC role/capability/environment policy; signed identity is not a capability
or approval grant. Preserve Principal validation at both existing dependencies.
See Google's [signed-header validation guidance](https://docs.cloud.google.com/iap/docs/signed-headers-howto).

The future topology must preserve human identity across the protected frontend,
ANOC and reader. Each verifier must validate its own resource audience; an assertion
for one service cannot simply be forwarded and accepted as an assertion for another.
Choose and qualify the actual shared IAP routing/delegation topology before enabling
production transport. Avoid unprotected alternate ingress. Cloud Run service
invocation identity and application human authorization are distinct boundaries.

Production handoff requires the existing ANOC/frontend Cloud Run services and
separate reader service, IAP protection for human access, reviewed human/group IAP
IAM policy, the IAP service agent's permitted service invocation, least-privilege
runtime service identities/read bindings, expected signed-JWT audiences and trusted
subject-to-grant mapping. OAuth/consent configuration depends on the selected IAP
setup; no OAuth client is invented here. See official [Cloud Run IAP setup](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)
and [IAP Cloud Run enablement](https://docs.cloud.google.com/iap/docs/enabling-cloud-run).

No Cloud Run, IAP, IAM, service account, OAuth, secret, routing, database or Terraform
resource is created or changed. All future external mutations require exact-operation
approval under AGENTS.md. M16 acceptance is unchanged; M17 remains unstarted.
