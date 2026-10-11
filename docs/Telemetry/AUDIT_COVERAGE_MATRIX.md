# Final local audit coverage — 2026-10-11

Full Auditability: **NO**. CAPTURED denotes implemented boundaries with flags enabled, not exhaustive or deployed coverage. Class C is not crash-durable execution capture. DERIVABLE requires a durable deterministic source; this table does not promote spans or mutable snapshots into lifecycle history. Existing historical gaps remain UNKNOWN. See [architecture](27_EXECUTION_AUDIT_JOURNAL_AND_CONTROLLED_EGRESS.md).

| Domain | Status | Business Authority | Audit Authority | Events |
|---|---|---|---|---|
| Session | CAPTURED, PARTIAL C | ApiSessionService / ADK | Execution journal | session.created, session.accessed; no fake closed/resumed |
| Run | CAPTURED, PARTIAL | M7 durable status | Execution journal | run.accepted, run.terminal; coalesced first persistence caveat |
| Turn | CAPTURED, PARTIAL | M7 binding / TurnPlan owner | Execution journal | turn.bound; not all TurnPlan transitions |
| Team Manager | CAPTURED, PARTIAL C | Team Manager / ADK orchestration | Execution journal | agent.execution.started/completed/failed |
| Specialist agent | CAPTURED, PARTIAL C | Specialist / ADK orchestration | Execution journal | same lifecycle, actual execution/parent IDs |
| Model attempt | CAPTURED | M10 attempt owner | Execution journal | model.attempt.started/completed; early run ID can be absent |
| Model usage | CAPTURED | Unsampled M10 ledger | Journal reference, not financial truth | model.usage.captured; agent execution reference where known |
| Retrieval | CAPTURED, PARTIAL C | RetrievalService | Execution journal | retrieval.started/completed/failed |
| Evidence | CAPTURED, scoped A/B | Case CAS / ADK committed provenance | Execution journal + durable ADK handoff | evidence.selection.recorded; safe revision/count only |
| Tool execution | CAPTURED, PARTIAL C | ADK actual dispatcher / direct Teams tool | Journal, derived ToolExecutionRecord | tool.execution.started/completed/failed |
| Approval | CAPTURED, scoped B | Pending proposal policy / ADK | Journal + ADK committed handoff | requested/approved/rejected/expired/consumed actual deltas |
| Governed action | CAPTURED, scoped A/D | Case progression / existing Teams authorization | Journal + action intent | governance.progression.recorded, action.authorized |
| Command authorization | CAPTURED, scoped D | Existing authorize_write | Journal decision receipt | authorization.decided; no authority transferred |
| Command dispatch | CAPTURED, scoped D | Teams gateway / execution service | Journal + action intent record | dispatch_attempted/acknowledged/completed/failed/outcome_unknown |
| Accounting debt | CAPTURED | M10 attempt/inbox owner | Journal reference | accounting.debt.created/resolved |
| Terminal result | CAPTURED, PARTIAL | M7 durable terminal | Execution journal | run.terminal |
| Failure / timeout / retry | CAPTURED, PARTIAL | Existing execution and retry policies | Journal for observed facts | finite terminal facts; no audit-driven retry |
| Crash/outage-lost C lifecycle | NOT_CAPTURED | Actual execution may have occurred | No durable source available | Cannot deterministically reconstruct lost events |
| Unread-session B autonomous recovery | UNWIRED | Durable ADK metadata | Recoverable source exists; no autonomous drainer | Reconcile currently requires authorized session read |

## Drill-down and limits

Run audit GET returns safe typed occurrence-ordered events, explicit processed-at/partial status and bounded current action summaries. Actual session/run/turn/agent/model/retrieval/tool/proposal/action references form the graph only where present. Actor/target references resolve through authorized owner data; missing IDs and targets are not guessed. An action without run ID is not invented into a run timeline. No raw payload rendering or UI redesign.

No separate session closed/resumed lifecycle exists in the inspected owner and neither is synthesized. No evidence is selected from telemetry. Unknown dispatch cannot prove success or failure; DISPATCHING read outcome is UNKNOWN even if post-egress storage is unavailable. Production historical completeness, automatic handoff recovery, real actor identity, recovery configuration and C41/C42 remain outstanding.
