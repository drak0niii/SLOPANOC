"""Case target facts: managed-object targets PROVEN by this fault's trusted evidence.

A state-changing governed action may only be offered for a target this investigation has itself
observed. The only sources are TRUSTED, VALIDATED step results of the SAME fault thread:

    operator output that validated as a governed step's result   (ResultSource.OPERATOR_MESSAGE)
    controlled read execution output                              (ResultSource.EXECUTION_ADAPTER)
    an approved live operational / context source                 (ResultSource.CONTEXT_SOURCE)

Never model or assistant prose, example commands, unselected knowledge, a caller summary, a failed
command's output, another fault's results, or results made stale by a reopen.

A fact records exactly what the evidence states, with its provenance:
    typed     `key=value` as written (e.g. an identifier pair inside a managed-object path)
    literal   an identifier-shaped token written on its own (letters AND digits, e.g. a unit name);
              key "" -- usable only by a governed slot that itself declares no key
No equivalence is inferred between different keys or spellings: two targets are the same only when
key AND value are identical (a literal only when the value is identical). Pure and
derived on demand from the authoritative progression, so it is always consistent with the trusted
results (and needs no migration). No vendor, technology, unit or fault vocabulary.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from backend.cases.troubleshooting_progression import ResultSource, StepStatus, TroubleshootingProgression

TRUSTED_TARGET_SOURCES = frozenset({ResultSource.OPERATOR_MESSAGE, ResultSource.EXECUTION_ADAPTER, ResultSource.CONTEXT_SOURCE})
_RESULT_STATUSES = frozenset({StepStatus.OBSERVED, StepStatus.VERIFIED, StepStatus.COMPLETED})
_PAIR = r"[A-Za-z][A-Za-z0-9_]*=[A-Za-z0-9][A-Za-z0-9_\-\.:/]*"
_PATH = re.compile(rf"(?<![A-Za-z0-9_\-=]){_PAIR}(?:,{_PAIR})*")
_SPLIT_PAIR = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=([A-Za-z0-9][A-Za-z0-9_\-\.:/]*)")
_LITERAL = re.compile(
    r"(?<![A-Za-z0-9_\-\.:/=])(?=[A-Za-z0-9_\-\.:/]*[A-Za-z])(?=[A-Za-z0-9_\-\.:/]*[0-9])"
    r"[A-Za-z0-9][A-Za-z0-9_\-\.:/]*[A-Za-z0-9](?![A-Za-z0-9_\-\.:/=])"
)
_MAX_FACTS = 200


class CaseTargetFact(BaseModel):
    """One `key=value` target identity observed in one trusted result of one fault."""

    fact_id: str
    fault_id: str
    target_key: str
    raw_identifier: str
    mo_path: str
    """The full comma-separated identifier path the pair was observed in (context, not a mapping)."""
    step_id: str
    result_id: str
    result_source: str
    observed_at: Optional[datetime] = None

    @property
    def identity(self) -> str:
        return f"{self.target_key}={self.raw_identifier}" if self.target_key else self.raw_identifier

    def view(self) -> dict[str, str]:
        return {
            "fact_id": self.fact_id, "identity": self.identity, "mo_path": self.mo_path, "fault_id": self.fault_id,
            "step_id": self.step_id, "result_id": self.result_id, "source": self.result_source,
        }


def _fact_id(fault_id: str, key: str, value: str, result_id: str) -> str:
    return "tgt-" + hashlib.sha256("\x1f".join((fault_id, key, value, result_id)).encode("utf-8")).hexdigest()[:16]


def extract_identifier_pairs(text: str) -> list[tuple[str, str, str]]:
    """(key, value, path) for every `key=value` identifier pair in `text`, as written."""
    out: list[tuple[str, str, str]] = []
    for path in _PATH.finditer(text or ""):
        for key, value in _SPLIT_PAIR.findall(path.group(0)):
            value = value.rstrip(".:/")
            if value and (key, value, path.group(0)) not in out:
                out.append((key, value, path.group(0)))
    return out


def extract_literal_identifiers(text: str) -> list[str]:
    """Identifier-shaped tokens written on their own (not inside a `key=value` pair)."""
    out: list[str] = []
    without_pairs = _PATH.sub(" ", text or "")
    for match in _LITERAL.finditer(without_pairs):
        if match.group(0) not in out:
            out.append(match.group(0))
    return out


def _trusted_steps(progression: Optional[TroubleshootingProgression], fault_id: Optional[str]) -> list[Any]:
    """Steps of `fault_id` whose result is trusted, validated and not made stale by a reopen."""
    if progression is None or not fault_id or fault_id not in progression.faults:
        return []
    stale_up_to = progression.faults[fault_id].reopened_at_sequence
    steps = []
    for step in progression.steps_for(fault_id):
        result = step.result
        if result is None or step.status not in _RESULT_STATUSES or result.source not in TRUSTED_TARGET_SOURCES:
            continue
        if str((result.validation or {}).get("status", "validated")) != "validated":
            continue  # a failed command's output proves nothing about the node
        if stale_up_to is not None and step.sequence <= stale_up_to:
            continue
        steps.append(step)
    return steps


def trusted_case_results(progression: Optional[TroubleshootingProgression], fault_id: Optional[str]) -> list[dict[str, str]]:
    """The trusted result texts of `fault_id` (same trust rules as target facts), with identities:
    the only evidence a governed condition scope may be established from."""
    return [
        {"step_id": step.step_id, "result_id": step.result.result_id, "text": step.result.text}
        for step in _trusted_steps(progression, fault_id)
    ]


def case_target_facts(progression: Optional[TroubleshootingProgression], fault_id: Optional[str]) -> list[CaseTargetFact]:
    """Every target fact of `fault_id`, derived from its trusted, validated, non-stale results."""
    if progression is None or not fault_id or fault_id not in progression.faults:
        return []
    facts: list[CaseTargetFact] = []
    for step in _trusted_steps(progression, fault_id):
        result = step.result
        observed = [(key, value, path) for key, value, path in extract_identifier_pairs(result.text)]
        observed += [("", value, value) for value in extract_literal_identifiers(result.text)]
        for key, value, path in observed:
            facts.append(CaseTargetFact(
                fact_id=_fact_id(fault_id, key, value, result.result_id), fault_id=fault_id, target_key=key,
                raw_identifier=value, mo_path=path, step_id=step.step_id, result_id=result.result_id,
                result_source=result.source.value, observed_at=result.recorded_at,
            ))
            if len(facts) >= _MAX_FACTS:
                return facts
    return facts
