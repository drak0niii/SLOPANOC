"""Deterministic interactive-disambiguation framework (interaction-
capability extension).

Answers exactly one question deterministically -- "the user asked for a
named resource that could not be resolved exactly; here are a small,
authoritative set of real candidates, and here is which one (if any) the
user picked" -- generic enough to represent any future deterministic
ambiguity (not just Teams chat names), without building a general
workflow engine.

  - schemas.py -- `PendingSelection`, `SelectionOption`, `SelectionStatus`,
                   `SelectionKind`.
  - service.py -- the trusted-boundary selection lifecycle
                   (create/choose/skip/supersede), mirroring
                   backend/approval/service.py's shape.

Like the approval framework, nothing here is ever reachable as an
LLM-callable tool for CHOOSING/SKIPPING a selection -- that decision
belongs to the user, resolved only through the API endpoints in
backend/api/selection_service.py, never through model judgment.
"""
