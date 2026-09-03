"""The deterministic Teams write-action approval framework.

Core policy engine only, per instruction: no write execution is wired
here. This package answers exactly one question deterministically -- "is
this exact operation, with this exact payload, currently approved for
one-time execution in this session?" -- and nothing in it is ever
reachable as an LLM-callable tool (see service.py's module docstring).

  - schemas.py   -- the data contracts (`ActionProposal`, `ProposalStatus`,
                     `WriteOperation`, `ApprovalDenialReason`).
  - canonical.py -- deterministic payload canonicalization/hashing.
  - service.py   -- the trusted-boundary proposal lifecycle
                     (create/approve/reject/consume).
  - policy_gate.py -- the deterministic write-authorization check
                     (`authorize_write`).
"""
