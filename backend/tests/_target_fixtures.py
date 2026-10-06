"""Test-only helpers: trusted current-case target facts and the target gate's authority view.

Production derives facts from a fault's trusted, validated step results (backend.cases.target_facts)
and only server code passes a target validation to Command Authority. Unit tests that exercise a
later stage (grounding, attestation, policy) in isolation use these to represent "the target gate
already passed for exactly this target".
"""
from __future__ import annotations

from typing import Any

from backend.cases.target_facts import CaseTargetFact

FAULT = "FAULT-TARGET-FIXTURE"


def fact(key: str, value: str, *, fault_id: str = FAULT, step_id: str = "step-fixture", result_id: str = "obs-fixture") -> CaseTargetFact:
    return CaseTargetFact(
        fact_id=f"tgt-{fault_id}-{key}-{value}", fault_id=fault_id, target_key=key, raw_identifier=value, mo_path=f"{key}={value}",
        step_id=step_id, result_id=result_id, result_source="operator_message",
    )


def target_validation(*pairs: tuple[str, str], fault_id: str = FAULT) -> dict[str, Any]:
    return {
        "passed": True, "fault_id": fault_id,
        "targets": [{"key": k, "value": v, "kind": "parameterized", "fact_ids": [fact(k, v, fault_id=fault_id).fact_id]} for k, v in pairs],
    }


def confirmed_and_validated(*pairs: tuple[str, str]) -> dict[str, Any]:
    """trusted_context for a state change whose target was validated AND confirmed."""
    return {"target_confirmed": True, "target_validation": target_validation(*pairs)}
