"""Operational control plane (Troubleshooting Tranche 3).

Deterministic governance AROUND commands the existing Command Authority already authorized.
Separate trust boundaries, evaluated in this order and never merged:

    Command Authority  (agents/technical_authority_engineer/agent_tool.build_server_validated_commands)
      -> Action Policy        (policy.py)       restricts; can never grant command authority
      -> Target Confirmation  (targets.py)      explicit operator decision, bound to one action context
      -> Human Approval       (control_plane.py, via the existing backend/approval proposal lifecycle)
      -> Execution eligibility / controlled read execution (execution.py)

No module here calls an LLM, and none is reachable as a model tool. State-changing actions end at
READY_FOR_EXECUTION: no adapter capable of a state change exists, and none is invoked.
"""
