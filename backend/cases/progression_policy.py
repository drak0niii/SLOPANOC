"""Troubleshooting progression policy: the small, server-owned set of knobs that progression
decisions apply deterministically (no magic numbers in the controller).

Every decision taken under this policy records `{policy_id, version, rule}` in the progression's
audit (escalation records, re-check / reopen events), so an auditor can see WHICH policy caused
WHICH transition. There is no customer-specific policy yet: SYSTEM_DEFAULT_POLICY is the
documented default (docs/TROUBLESHOOTING_STRATEGY.md section 14.7).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class TroubleshootingProgressionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = "slopanoc-system-default"
    version: str = "2"
    max_failed_verifications: Optional[int] = Field(
        default=2, ge=1, description="Failed post-action verifications on one fault before the policy escalates it (None: never on its own)."
    )
    allow_operator_requested_recheck: bool = Field(
        default=True, description="An operator's explicit re-check request that resolves to ONE prior step may repeat it."
    )
    allow_reopen_after_resolution: bool = Field(
        default=True, description="The operator may reopen a RESOLVED / ESCALATED fault (recurrence or re-check request)."
    )
    result_staleness: Literal["state_change_or_reopen", "never"] = Field(
        default="state_change_or_reopen",
        description="Earlier results become stale after a later completed state change or an operator reopen.",
    )
    allow_escalation_proposals: bool = Field(
        default=True, description="An agent's escalation proposal may be accepted (the agent decides whether it is technically appropriate)."
    )
    escalation_requires_recorded_evidence: bool = Field(
        default=True,
        description="An accepted escalation must rest on a validated observation, a failed verification or an explicit knowledge gap.",
    )

    def ref(self, rule: str) -> dict[str, Any]:
        return {"policy_id": self.policy_id, "version": self.version, "rule": rule}


SYSTEM_DEFAULT_POLICY = TroubleshootingProgressionPolicy()


def get_progression_policy() -> TroubleshootingProgressionPolicy:
    return SYSTEM_DEFAULT_POLICY
