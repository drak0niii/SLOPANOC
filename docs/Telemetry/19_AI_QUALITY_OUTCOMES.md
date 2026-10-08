
# 19 — AI Quality and Operational Outcome Telemetry

## Purpose

FinOps unit economics must be joined to quality and operational value.

Cost without quality/outcome context is insufficient.

## Required quality/outcome model

Create a durable outcome schema that can represent:

### Answer quality
- governed_answer,
- source_coverage_status,
- model_reasoning_disclosed,
- operator_acceptance where explicitly available,
- correction/retry requested,
- escalation requested.

### Troubleshooting
- troubleshooting_started,
- troubleshooting_completed,
- incident_resolved,
- escalated,
- diagnostic_steps_count,
- first_time_correct where measurable,
- operator_action_success.

### Teams / Incident Manager
- evidence_found,
- evidence_sufficient,
- chat_resolution_success.

### Automation / AOE
- artifact_generated,
- artifact_accepted,
- artifact_validation_status,
- execution_success where applicable.

## Do not fabricate quality

If the system does not have an authoritative outcome signal:
- store `unknown`,
- do not infer success from absence of complaint.

## First-time-correct

FTC must have an explicit operational definition.

Initial definition:
A turn/case is first-time-correct only when the requested operational objective is completed without:
- correction of the primary answer,
- invalid governed action,
- re-planning caused by incorrect system output,
- operator-declared incorrect result.

If a reliable authoritative FTC signal does not yet exist, expose FTC as unavailable rather than guessed.

## Quality-cost joins

Required analytical joins:
- cost vs completion,
- cost vs governed-answer status,
- cost vs FTC,
- cost vs resolution,
- latency vs outcome,
- model version vs outcome,
- deployment version vs outcome.

## Outcome ownership

Outcome updates come from:
- server-owned workflow state,
- explicit operator feedback,
- trusted downstream action/result state.

Never from hidden model self-evaluation alone.
