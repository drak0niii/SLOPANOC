# Cost-ledger completeness boundary

M9 defines the 100% architectural target but never evaluates it. Source M10_COST_LEDGER
is DATA_SOURCE_AVAILABLE_IN_M10. There is no finite burn-rate/error-budget alert,
model-usage substitute, pricing or accounting implementation here. M10 must supply
its unsampled durable accounting-grade data and its own acceptance evidence before
this objective can be evaluated. No remote remediation or deployment is authorized.

## M10 local runtime accounting source

M10 supplies durable STARTED attempts and immutable BASE capture to the M9 evaluator.
ADMITTED/NOT_STARTED are excluded. Unknown quantities can satisfy capture while lowering
separate quantity coverage. Pending inbox/started debt is uncaptured until materialized;
conflict prevents healthy classification. Source freshness comes from successful
durable checkpointing, never API polling. The24h operational threshold is strictly
below99.99%; the architectural100% objective has no finite burn/error-budget division.
The source-controlled policy remains disabled/undeployed, with no notification wiring.

Inspect protected ledger-health and completeness views; distinguish pending committed
payload (recoverable) from STARTED/OUTCOME_UNKNOWN debt without committed payload
(quantities cannot be recreated). Recovery scans100records/pass with short transactional
row locks and bounded retries. Exhausted/conflicting records remain durable; inspect
through trusted engineering processes. Never rerun Gemini, embeddings, agents, tools,
commands or responses to repair accounting. No automatic external remediation exists.

Check deployed migration/config/identity and DB reachability with separately approved
operations. Production deployment and live PostgreSQL/provider crash validation remain
pending. Enabling a flag is not a migration and no schema is created automatically.
Disabling accounting is not a recovery of historical gaps; preserve original events,
pending rows and referenced corrections. Any shared migration/deployment/SQL mutation
requires exact-operation approval under AGENTS.md. Keep24month retention target and
future reconciliation holds; M10 schedules no destructive pruning.
