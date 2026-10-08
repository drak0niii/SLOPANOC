# SLOPANOC Execution Plan Standard

For substantial implementation work, use a persistent execution plan.

For the Production Observability + SRE + FinOps program, the active execution plan is:

`.agent/OBSERVABILITY_EXECUTION.md`

## Purpose

The execution plan is the persistent engineering record that allows work to continue safely across:

- different Codex conversations;
- different days;
- different milestones;
- context resets.

The plan must describe what actually exists in the repository, not what was merely intended.

## Before Each Milestone

Codex must:

1. read the root `AGENTS.md`;
2. read `.agent/OBSERVABILITY_EXECUTION.md`;
3. read `docs/Telemetry/README.md`;
4. read the current milestone in `docs/Telemetry/12_IMPLEMENTATION_ROADMAP.md`;
5. read the design documents relevant to that milestone;
6. inspect the actual current source code;
7. identify existing components to reuse;
8. identify architecture conflicts;
9. define the files expected to change;
10. define validation commands;
11. define milestone exit criteria.

Only then may implementation begin.

## During Implementation

The execution plan must remain current.

Record:

- significant implementation decisions;
- deviations from the original plan;
- discovered architecture facts;
- failed validation;
- repairs made;
- intentionally deferred work.

## Completion

A milestone is complete only after:

- implementation;
- validation;
- regression testing where applicable;
- exit-criteria review;
- working-tree review.

The plan must explicitly state:

`Ready for Next Milestone: YES`

before the next milestone is permitted.

If the result is NO, continue repairing the current milestone.