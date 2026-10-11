# Production human reader identity — deployment contract

**NOT IMPLEMENTED / NOT ENABLED.** Existing `IAPVerifiedIdentityProvider` denies every
request. Local development identity is prohibited on Cloud Run (`K_SERVICE`) and must
never be a fallback. The contract below is reviewable; it grants no IAM or deployment
permission. Human identity is separate from Pub/Sub machine push verification.

## Selected target and audience

Proposed target: IAP directly on Cloud Run service
`slopanoc-development-observability-reader`, project number `995771529960`, region
`europe-west4`. Exact expected IAP assertion audience:
`/projects/995771529960/locations/europe-west4/services/slopanoc-development-observability-reader`.
If an approved existing HTTPS load balancer is instead selected, replace this single
binding with `/projects/995771529960/global/backendServices/<verified numeric ID>`;
never accept both or infer the value from a request. Direct IAP avoids inventing a
mandatory new load balancer. Eligibility/OAuth/IAP configuration and ingress/invoker
bindings remain deployment approval items.

Google specifies the direct Cloud Run audience and signature requirements in
[Securing apps with signed headers](https://docs.cloud.google.com/iap/docs/signed-headers-howto).
Direct IAP setup is described in [Enable IAP for Cloud Run](https://docs.cloud.google.com/iap/docs/enabling-cloud-run).

## Required verifier

Read only `X-Goog-IAP-JWT-Assertion`, with bounded header size (16 KiB), one assertion,
and no duplicate ambiguous identity headers. Verify ES256 signature against Google's
IAP keys, known key ID, issuer `https://cloud.google.com/iap`, exact configured audience,
expiry and issued-at, 30-second skew and maximum lifetime 660 seconds. Require stable
nonempty `sub` compatible with existing Principal identifier contract; use issuer+sub
as the policy identity. Signed email may help display/admin enrollment but is not a
role or group assertion. Unsigned email/user headers, client roles, JWT decoded without
verification, Pub/Sub service identities and development headers confer no access.

A bounded rotating public-key cache must use trusted HTTPS endpoints, timeout 2s,
bounded verification concurrency and fail closed on unknown keys/cache expiry/fetch
failure; a valid cached key can be used until its approved cache expiry. Never log
assertion/token/email or raw verification exceptions. Missing/invalid assertion returns
a safe authentication error; valid identity without server grants returns authorization
error. No database reader acquisition on authentication failure.

## Application authorization

IAP admission is an outer perimeter. Versioned server-owned policy separately maps
verified issuer+subject or trusted administratively synchronized Google group membership
to SLOPANOC role, capabilities and allowed environments. No group lookup in arbitrary
request headers; no inferred email-domain admin; no Microsoft/Graph changes. Define
policy freshness/revocation bound at 5 minutes, fail closed if group snapshot expires.
Policy entries must pass `Principal.valid()` and remain subsets of existing
`ROLE_PERMISSIONS`; endpoint-specific capability and environment checks remain required.

| Role | Permitted policy intent |
|---|---|
| User | Existing safe own-run access only where current endpoint contract supports it |
| Operator | Explicit operational capability; no automatic content access |
| Developer / SRE | Explicit diagnostic/operational grants; restricted content separately authorized |
| FinOps | Financial usage/allocation/budgets/forecast/unit-economics grants only; no automatic conversation, Teams or knowledge content |
| Admin | Reviewed explicit grants within current role ceiling; no reader mutation APIs |
| Auditor | Reviewed read capabilities/environment scope; no write authority |

Role alone is not a grant. Concrete subject/group assignments, policy revision and
exact source are unbound and block deployment. Provision no OAuth, groups or IAM here.

## Origin and trust chain

Keep the existing UI and separate GET-only reader. Use explicit first-party HTTPS UI
origin -> IAP-protected reader via `VITE_OBSERVABILITY_API_BASE_URL`, with exact
`SLOPANOC_OBSERVABILITY_ALLOWED_ORIGINS`, credentials and no wildcard. The UI origin
must be supplied/verified; it is not derived from the projector URL. A same-origin proxy
is optional only after its identity forwarding and audience contract are explicitly
reviewed; forwarding a machine identity must not silently become a human identity.
Reader origin is the approved deployed service HTTPS URL, not yet available. Configure
`SLOPANOC_AUTH_MODE=iap` only once a real verifier and trusted policy are implemented.
Proposed future verifier audience/policy configuration names must be added to code and
tested before activation; current code does not consume invented identity variables.

Required predeployment tests: signed valid token, wrong issuer/audience/key/algorithm,
expired/future/overlong lifetime, missing or duplicate header, spoofed email/dev user,
expired group snapshot, revoked/unknown identity, role ceiling, cross-environment request,
FinOps content denial, direct bypass and key-service outage. Current deny-only tests
prove denial, not live authentication. No IAP configuration changed.
