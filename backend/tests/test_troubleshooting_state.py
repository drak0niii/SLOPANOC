"""Tests for backend/cases/troubleshooting_state.py -- Persistent Troubleshooting State.
"""
from backend.cases.troubleshooting_state import (
    DiagnosticCheckRecord,
    TroubleshootingState,
    TroubleshootingStatus,
)


def test_troubleshooting_state_lifecycle() -> None:
    state = TroubleshootingState(
        fault_id="FAULT-RAN-9901",
        status=TroubleshootingStatus.INVESTIGATING,
        symptom_summary="Cell 42 reporting VSWR > 1.5",
        working_hypothesis="Faulty RF jumper or connector",
        competing_hypotheses=["Transceiver degradation", "Antenna mechanical tilt issue"],
        verified_evidence_ids=["EVID-TEAMS-1789", "EVID-KM-MOP-01"],
    )

    assert state.fault_id == "FAULT-RAN-9901"
    assert state.status == TroubleshootingStatus.INVESTIGATING
    assert len(state.competing_hypotheses) == 2
    assert len(state.verified_evidence_ids) == 2
    assert len(state.diagnostic_history) == 0

    # Add diagnostic check record
    check = DiagnosticCheckRecord(
        check_id="chk-01",
        action="Measure return loss on port A",
        rationale="Isolate jumper vs antenna feeder issue",
        grounded_command="measure feeder cell 42 port A",
        command_source_id="MOP-ERICSSON-4G-VSWR",
        expected_observation="Return loss > 15 dB",
        observed_result="Return loss measured at 11 dB (defective)",
    )
    state.diagnostic_history.append(check)
    state.status = TroubleshootingStatus.TESTING_NEXT_STEP

    assert len(state.diagnostic_history) == 1
    assert state.diagnostic_history[0].grounded_command == "measure feeder cell 42 port A"
    assert state.status == TroubleshootingStatus.TESTING_NEXT_STEP

    # Transition to mitigation recommended
    state.status = TroubleshootingStatus.MITIGATION_RECOMMENDED
    state.working_hypothesis = "Confirmed defective RF jumper on port A"
    state.competing_hypotheses.clear()

    assert state.status == TroubleshootingStatus.MITIGATION_RECOMMENDED
    assert len(state.competing_hypotheses) == 0
