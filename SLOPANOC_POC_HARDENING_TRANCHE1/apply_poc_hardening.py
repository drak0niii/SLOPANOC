#!/usr/bin/env python3
"""Apply the first SLOPANOC POC hardening tranche against DEMO-NEW.

Fixes:
  1. No inference of diagnostic execution from arbitrary conversation text.
  3. Only explicitly selected knowledge can enter TAE's trusted evidence;
     model-supplied case/metric IDs cannot be promoted to verified evidence.
  6. Persist an execution-attempt fence before Teams writes, preventing blind
     same-session retries after ambiguous gateway failures.
  7. Add targeted regression tests for (1), (3), and the retry fence.

IMPORTANT: This is a targeted safety tranche, not completion of all seven
roadmap items. Authentication, structured command authorization and comprehensive
output safety require separate implementation and live verification.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from textwrap import dedent

SOURCE_COMMIT = "443b26fab42666a94743419965eab6c91943bd8e"


def replace_once(text: str, before: str, after: str, path: str) -> str:
    count = text.count(before)
    if count != 1:
        raise RuntimeError(f"ABORT {path}: expected 1 anchor, found {count}. No files changed.")
    return text.replace(before, after, 1)


def content(source: str) -> str:
    return dedent(source).lstrip("\n")


def patches(root: Path) -> dict[Path, str]:
    modifications: dict[Path, str] = {}
    tae_path = root / "backend/agents/technical_authority_engineer/agent_tool.py"
    tae = tae_path.read_text(encoding="utf-8")

    old_execution = '''        # 3. Detect user execution of previously recommended checks
        symptoms_str = " ".join(args.get("verified_symptoms") or [])
        problem_str = args.get("problem_statement", "")
        combined_text = f"{problem_str} {symptoms_str}".strip()

        for rec in reversed(troubleshooting_state.diagnostic_history):
            if rec.status == CheckLifecycleStatus.RECOMMENDED:
                cmd = (rec.grounded_command or "").strip().lower()
                obs = None
                if cmd and cmd in combined_text.lower():
                    obs = f"Output observed for `{rec.grounded_command}`: {combined_text[:200]}"
                elif combined_text:
                    obs = combined_text[:200]
                troubleshooting_state.record_user_execution(
                    check_id=rec.check_id,
                    command=rec.grounded_command,
                    observed_result=obs,
                )
                break

'''
    new_execution = '''        # 3. Execution status must never be inferred from model-controlled text.
        # A confirmed check ID + user-submitted result is handled exclusively by
        # the trusted /checks/{check_id}/confirm API (or a future verified tool
        # result). Questions, restatements and follow-ups leave it RECOMMENDED.

'''
    tae = replace_once(tae, old_execution, new_execution, str(tae_path))
    tae = replace_once(tae, '    CheckLifecycleStatus,\n', '', str(tae_path))

    old_evidence = '''        km_selected = snapshot_selected_knowledge_evidence(run_id)
        if not km_selected:
            km_available = get_available_knowledge_evidence(run_id)
            km_items = km_available.items
        else:
            km_items = km_selected

        for item in km_items:
'''
    new_evidence = '''        # Retrieved-but-unselected evidence is NOT authoritative evidence.
        # In particular, explicit empty selection must remain empty.
        km_items = snapshot_selected_knowledge_evidence(run_id)

        for item in km_items:
'''
    tae = replace_once(tae, old_evidence, new_evidence, str(tae_path))

    old_caller = '''        # If caller provides case context or user-provided verified observation with non-forgeable type:
        c_type = caller_item.get("source_type")
        if c_type in ("case_context", "user_evidence", "observed_metric"):
            seen_source_ids.add(c_src)
            evidence_items.append(
                EvidenceReference(
                    source_id=c_src,
                    source_type=c_type,
                    title=caller_item.get("title"),
                    content_snippet=caller_item.get("content_snippet"),
                    metadata=caller_item.get("metadata", {}),
                )
            )
        else:
            logger.warning("Rejecting ungrounded caller evidence item: %r", c_src)
'''
    new_caller = '''        # A source_type string is model-controlled and is not proof of origin.
        # Case/user/metric evidence must be resolved from server-side records
        # before it can be called verified. Do not trust arbitrary caller IDs.
        logger.warning("Rejecting unverified caller evidence ID: %r", c_src)
'''
    tae = replace_once(tae, old_caller, new_caller, str(tae_path))
    # Specialist exceptions must not disclose raw upstream messages in a
    # model-facing tool result or logs (DB URLs and gateway URLs are secrets).
    tae = replace_once(
        tae,
        'logger.error("Technical Authority Engineer runner failed: %s", e, exc_info=True)',
        'logger.error("Technical Authority Engineer runner failed: %s", type(e).__name__)',
        str(tae_path),
    )
    tae = replace_once(
        tae,
        'detail=f"Safe error boundary intercepted specialist runner exception: {e}",',
        'detail="Specialist runner failed; no operational recommendation was authorized.",',
        str(tae_path),
    )
    tae = replace_once(
        tae,
        'logger.error("Technical Authority Engineer output schema validation failed: %s", e)',
        'logger.error("Technical Authority Engineer output schema validation failed: %s", type(e).__name__)',
        str(tae_path),
    )
    tae = replace_once(
        tae,
        'detail=f"Safe error boundary intercepted schema validation error: {e}",',
        'detail="Specialist output failed schema validation.",',
        str(tae_path),
    )
    modifications[tae_path] = tae

    execution_path = root / "backend/api/execution_service.py"
    execution = execution_path.read_text(encoding="utf-8")
    execution = replace_once(
        execution,
        'from dataclasses import dataclass\n',
        'from dataclasses import dataclass\nfrom datetime import datetime, timezone\n',
        str(execution_path),
    )
    execution = replace_once(
        execution,
        '_DENIAL_MESSAGES: dict[ApprovalDenialReason, str] = {',
        '''# Durable same-session retry fence. This is NOT provider-side idempotency:
# a new session or a separately created proposal could still duplicate the
# operation. The Power Automate flow needs a durable external idempotency key
# for exactly-once semantics across sessions/processes.
_EXECUTION_ATTEMPT_KEY = "teams_execution_attempt"

_DENIAL_MESSAGES: dict[ApprovalDenialReason, str] = {''',
        str(execution_path),
    )
    old_approved = '''        # status == ProposalStatus.APPROVED from here on -- the only
        # remaining possibility given ProposalStatus's 5 closed values.

        tool_context = _ExecutionToolContext(state=session.state)
'''
    new_approved = '''        # status == ProposalStatus.APPROVED from here on.
        # A gateway timeout cannot establish whether the Teams write landed.
        # Persist a fence BEFORE calling the external service. Once attempted,
        # never automatically retry this session's write without reconciliation.
        prior_attempt = session.state.get(_EXECUTION_ATTEMPT_KEY)
        if isinstance(prior_attempt, dict) and prior_attempt.get("status") == "unconfirmed":
            raise SafeErrorException(SafeError(
                error_code="action_failure",
                user_message=(
                    "A prior Teams write has an unconfirmed outcome. Reconcile it in Teams "
                    "before initiating another write; automatic retry is blocked."
                ),
            ))

        attempt = {
            "proposal_id": proposal.proposal_id,
            "operation": proposal.operation.value,
            "status": "unconfirmed",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        await session_service.persist_state_delta(
            session, {_EXECUTION_ATTEMPT_KEY: attempt}
        )
        # append_event advances the stored session revision: reload before
        # the tool consumes the proposal and before its subsequent write.
        session = await session_service.get_session(session_id, user_id)
        tool_context = _ExecutionToolContext(state=session.state)
'''
    execution = replace_once(execution, old_approved, new_approved, str(execution_path))
    old_success = '''        # Success: consume_proposal already ran INSIDE teams_create_chat/
        # teams_send_message (the fixed step-5 of execute_write.py's own
        # order), mutating session.state in place. Persist it exactly the
        # way approve()/reject() persist their own transition.
        await session_service.persist_state_delta(
            session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
        )
'''
    new_success = '''        # Successful external write: consume_proposal ran inside the tool.
        # Persist the consumed proposal AND the attempt result together.
        # An error after the remote write but before this commit leaves the
        # previously persisted unconfirmed fence in place, blocking retries.
        attempt["status"] = "confirmed"
        session.state[_EXECUTION_ATTEMPT_KEY] = attempt
        await session_service.persist_state_delta(
            session, {
                PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY],
                _EXECUTION_ATTEMPT_KEY: attempt,
            },
        )
'''
    execution = replace_once(execution, old_success, new_success, str(execution_path))
    modifications[execution_path] = execution

    app_path = root / "backend/api/app.py"
    app = app_path.read_text(encoding="utf-8")
    app = replace_once(
        app,
        'from backend.api import approval_service\n',
        'from backend.api import approval_service\nfrom backend.api import check_confirmation_service\n',
        str(app_path),
    )
    app = replace_once(
        app,
        'from backend.api.schemas import (\n',
        'from backend.api.schemas import (\n    CheckConfirmationRequest,\n',
        str(app_path),
    )
    endpoint = '''    @app.post("/api/sessions/{session_id}/checks/{check_id}/confirm")
    async def confirm_diagnostic_check(
        session_id: str,
        check_id: str,
        body: CheckConfirmationRequest,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
    ) -> dict[str, str]:
        """Explicit user-reported execution; never inferred from chat text."""
        return await check_confirmation_service.confirm_check(
            session_service, session_id, check_id, body.observed_result, user.user_id
        )

'''
    app = replace_once(
        app,
        '    @app.post("/api/sessions/{session_id}/messages", response_model=ChatResponse)\n',
        endpoint + '    @app.post("/api/sessions/{session_id}/messages", response_model=ChatResponse)\n',
        str(app_path),
    )
    modifications[app_path] = app

    schemas_path = root / "backend/api/schemas.py"
    schemas = schemas_path.read_text(encoding="utf-8")
    # Do not insert into a possibly decorated/derived existing schema class.
    schema_decl = '''\n\nclass CheckConfirmationRequest(BaseModel):
    """Explicit confirmation that a named check was executed.

    The result is a user report, not automatically verified telemetry.
    """
    observed_result: str = Field(min_length=1, max_length=10000)

'''
    marker = 'class SendMessageRequest(BaseModel):'
    if schemas.count(marker) != 1:
        raise RuntimeError(f"ABORT {schemas_path}: missing unique SendMessageRequest anchor")
    schemas = schemas.replace(marker, schema_decl + marker, 1)
    modifications[schemas_path] = schemas

    confirmation_path = root / "backend/api/check_confirmation_service.py"
    if confirmation_path.exists():
        raise RuntimeError(f"ABORT: {confirmation_path} already exists")
    modifications[confirmation_path] = content('''
        """Owner-scoped, explicit diagnostic-check confirmation boundary."""
        from __future__ import annotations

        from backend.api.session_service import ApiSessionService
        from backend.cases.troubleshooting_state import CheckLifecycleStatus, TroubleshootingState
        from backend.gateway.safe_error import validation_error


        async def confirm_check(
            session_service: ApiSessionService,
            session_id: str,
            check_id: str,
            observed_result: str,
            user_id: str,
        ) -> dict[str, str]:
            # Ownership before and after taking the same lock as a chat turn.
            await session_service.get_session(session_id, user_id)
            if not check_id.strip() or not observed_result.strip():
                raise validation_error("Check ID and observed result are required.")
            async with session_service.lock_for(session_id, user_id):
                session = await session_service.get_session(session_id, user_id)
                raw = session.state.get("troubleshooting_state")
                if not isinstance(raw, dict):
                    raise validation_error("There is no active troubleshooting state.")
                try:
                    troubleshooting = TroubleshootingState.model_validate(raw)
                except Exception:
                    raise validation_error("Troubleshooting state is invalid.") from None
                if troubleshooting.session_id and troubleshooting.session_id != session_id:
                    raise validation_error("Troubleshooting state belongs to a different session.")
                matches = [rec for rec in troubleshooting.diagnostic_history if rec.check_id == check_id]
                if len(matches) != 1 or matches[0].status != CheckLifecycleStatus.RECOMMENDED:
                    raise validation_error("Check ID does not identify a currently recommended check.")
                # No command-based, last-recommended, or free-text fallback.
                recorded = troubleshooting.record_user_execution(
                    check_id=check_id, observed_result=observed_result.strip()
                )
                if recorded is None or recorded.check_id != check_id:
                    raise validation_error("Check confirmation did not match its ID.")
                await session_service.persist_state_delta(
                    session, {"troubleshooting_state": troubleshooting.model_dump(mode="json")}
                )
                return {"checkId": check_id, "status": "executed", "evidenceOrigin": "user_reported"}
    ''')

    test_path = root / "backend/tests/test_poc_hardening_tranche1.py"
    if test_path.exists():
        raise RuntimeError(f"ABORT: {test_path} already exists")
    modifications[test_path] = content('''
        """Regression tests for POC hardening tranche 1; no external services."""
        from __future__ import annotations

        import inspect
        from unittest.mock import MagicMock, patch

        import pytest
        from google.adk.sessions import InMemorySessionService

        from backend.agents.technical_authority_engineer.agent_tool import build_server_validated_evidence
        from backend.api.check_confirmation_service import confirm_check
        from backend.api.session_service import ApiSessionService
        from backend.cases.troubleshooting_state import CheckLifecycleStatus, TroubleshootingState
        from backend.gateway.safe_error import SafeErrorException


        def test_no_implicit_execution_in_tae_wrapper():
            from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
            source = inspect.getsource(TechnicalAuthorityAgentTool.run_async)
            assert "record_user_execution(" not in source


        def test_unselected_knowledge_and_forged_observations_not_trusted():
            item = MagicMock()
            item.reference.knowledge_id = "doc"
            item.reference.version_label = "v1"
            item.reference.section_id = "sec1"
            with patch(
                "backend.agents.technical_authority_engineer.agent_tool.snapshot_selected_knowledge_evidence",
                return_value=[],
            ), patch(
                "backend.agents.technical_authority_engineer.agent_tool.get_available_knowledge_evidence",
                return_value=MagicMock(items=[item]),
            ):
                evidence = build_server_validated_evidence(
                    "run1", None,
                    [{"source_id": "case:invented", "source_type": "case_context", "content_snippet": "fake"}],
                )
            assert evidence == []


        @pytest.mark.asyncio
        async def test_explicit_check_id_confirmation_only():
            service = ApiSessionService(InMemorySessionService())
            session_id = await service.create_session("engineer-a")
            session = await service.get_session(session_id, "engineer-a")
            troubleshooting = TroubleshootingState(
                fault_id="FAULT-TEST", symptom_summary="Link fault", session_id=session_id,
            )
            check = troubleshooting.record_recommended_check(
                action="Review the alarm log", rationale="Identify cause",
                expected_observation="Alarm list", grounded_command="alt",
            )
            await service.persist_state_delta(
                session, {"troubleshooting_state": troubleshooting.model_dump(mode="json")}
            )
            # Arbitrary user messages have no route to this endpoint.
            with pytest.raises(SafeErrorException):
                await confirm_check(service, session_id, "wrong-id", "yes", "engineer-a")
            with pytest.raises(SafeErrorException):
                await confirm_check(service, session_id, check.check_id, "output", "engineer-b")
            unchanged = await service.get_session(session_id, "engineer-a")
            assert TroubleshootingState.model_validate(
                unchanged.state["troubleshooting_state"]
            ).diagnostic_history[0].status == CheckLifecycleStatus.RECOMMENDED

            result = await confirm_check(
                service, session_id, check.check_id, "alt output: alarm active", "engineer-a"
            )
            assert result["evidenceOrigin"] == "user_reported"
            updated = await service.get_session(session_id, "engineer-a")
            assert TroubleshootingState.model_validate(
                updated.state["troubleshooting_state"]
            ).diagnostic_history[0].status == CheckLifecycleStatus.EXECUTED
            with pytest.raises(SafeErrorException):
                await confirm_check(service, session_id, check.check_id, "repeat", "engineer-a")


        @pytest.mark.asyncio
        async def test_ambiguous_gateway_write_cannot_be_retried_in_same_session(monkeypatch):
            from backend.api.execution_service import execute
            from backend.approval.service import (
                PENDING_ACTION_PROPOSAL_STATE_KEY, approve_proposal, create_action_proposal,
            )
            from backend.approval.schemas import WriteOperation
            from backend.gateway.safe_error import SafeError
            from backend.api import execution_service

            service = ApiSessionService(InMemorySessionService())
            session_id = await service.create_session("engineer-a")
            session = await service.get_session(session_id, "engineer-a")
            proposal = create_action_proposal(
                WriteOperation.TEAMS_SEND_MESSAGE.value,
                {"chatId": "chat-1", "message": "hello"},
                session.state,
            )
            assert approve_proposal(proposal.proposal_id, session.state).success
            await service.persist_state_delta(
                session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
            )
            attempts = []

            async def ambiguous_gateway(*args, **kwargs):
                attempts.append(1)
                return {"error": SafeError(error_code="run_failure", user_message="Outcome unknown").to_dict()}

            monkeypatch.setattr(execution_service, "run_in_threadpool", ambiguous_gateway)
            with pytest.raises(SafeErrorException):
                await execute(service, session_id, proposal.proposal_id, "engineer-a")
            assert len(attempts) == 1
            fenced = await service.get_session(session_id, "engineer-a")
            assert fenced.state["teams_execution_attempt"]["status"] == "unconfirmed"
            with pytest.raises(SafeErrorException):
                await execute(service, session_id, proposal.proposal_id, "engineer-a")
            assert len(attempts) == 1


        def test_execution_fence_is_persisted_before_external_write():
            from backend.api import execution_service
            source = inspect.getsource(execution_service.execute)
            fence = source.index("_EXECUTION_ATTEMPT_KEY: attempt")
            write = source.index("teams_send_message,", fence)
            assert fence < write
            assert 'prior_attempt.get("status") == "unconfirmed"' in source
    ''')

    ci_path = root / ".github/workflows/poc-hardening.yml"
    if ci_path.exists():
        raise RuntimeError(f"ABORT: {ci_path} already exists")
    modifications[ci_path] = content('''
        name: POC hardening verification
        on:
          pull_request:
          push:
            branches: [poc-hardening-20260921]
        jobs:
          backend:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-python@v5
                with:
                  python-version: '3.11'
                  cache: pip
              - run: python -m pip install -r requirements-dev.txt
              - run: python -m pytest backend/tests -q
          frontend:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-node@v4
                with:
                  node-version: '20'
                  cache: npm
              - run: npm ci
              - run: npm test
              - run: npm run build
    ''')
    return modifications


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Local SLOPANOC repo directory")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: only verify patch anchors)")
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    if not (root / ".git").exists():
        raise SystemExit(f"Not a Git checkout: {root}")
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Cannot determine checkout revision: {exc}") from exc
    if head != SOURCE_COMMIT:
        raise SystemExit(f"Expected untouched DEMO-NEW commit {SOURCE_COMMIT}; found {head}. Aborting.")
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=root, text=True
    ).strip()
    if branch != "poc-hardening-20260921":
        raise SystemExit(
            "Create/switch to the isolated poc-hardening-20260921 branch first; "
            f"current branch is {branch!r}. No changes made."
        )
    pending = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root, text=True,
    ).strip()
    if pending:
        raise SystemExit("Tracked files have uncommitted changes. Commit/stash them first. No changes made.")
    changed = patches(root)  # Validate EVERY anchor/new path before writing ANY file.
    import ast
    for path, body in changed.items():
        if path.suffix == ".py":
            ast.parse(body, filename=str(path))
    print(f"Preflight OK: {len(changed)} files staged for {'write' if args.apply else 'dry-run'}")
    for path in changed:
        print(" ", path.relative_to(root))
    if not args.apply:
        print("No files changed. Re-run with --apply to write to your CURRENT branch.")
        return
    for path, body in changed.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    print("Patch written. Run targeted and full tests before committing; no Git commit or push was performed.")


if __name__ == "__main__":
    main()
