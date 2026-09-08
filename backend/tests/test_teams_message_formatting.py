"""POST-B7 UI/UX refinement, Item 1 (CORRECTIVE PASS -- plain text, not
HTML) -- outbound Teams message formatting
(backend/tools/teams/message_formatting.py) and its wiring into
`teams_propose_send_message` (backend/tools/teams/propose_write.py).

A real live test proved the Power Automate/Teams write path does not
render HTML as intended -- this file replaces the prior HTML-oriented
test suite entirely with plain-text-oriented coverage of the same 20
corrective-pass requirements: format scope, semantic-integrity
preservation, no HTML generation, no markup interpretation of literal
`<...>`/script-like text, and the full approval-payload-binding proof
(formatted once, before hashing; never reformatted at execution).
"""
from __future__ import annotations

from backend.tools.teams.message_formatting import format_teams_message


# --- 1-6: format scope -------------------------------------------------


def test_1_single_paragraph_preserved() -> None:
    assert format_teams_message("Hello there.") == "Hello there."


def test_2_multiple_paragraphs_cleanly_separated() -> None:
    result = format_teams_message("First paragraph.\n\nSecond paragraph.")
    assert result == "First paragraph.\n\nSecond paragraph."


def test_3_bullet_list_formatted_as_plain_text() -> None:
    result = format_teams_message("- First item\n- Second item\n- Third item")
    assert result == "• First item\n• Second item\n• Third item"


def test_3b_bullet_list_alternate_markers_normalize_to_one_marker() -> None:
    assert format_teams_message("* One\n* Two") == "• One\n• Two"
    assert format_teams_message("• One\n• Two") == "• One\n• Two"


def test_4_numbered_list_formatted_as_plain_text() -> None:
    result = format_teams_message("1. First step\n2. Second step\n3. Third step")
    assert result == "1. First step\n2. Second step\n3. Third step"


def test_4b_numbered_list_paren_style_normalizes_to_period_style() -> None:
    result = format_teams_message("1) First\n2) Second")
    assert result == "1. First\n2. Second"


def test_5_heading_body_structure_remains_readable() -> None:
    result = format_teams_message("Summary:\n\nThe relay failed at 09:00 UTC.")
    assert result == "Summary:\n\nThe relay failed at 09:00 UTC."


def test_6_mixed_paragraph_and_list_remains_readable() -> None:
    result = format_teams_message(
        "Overview:\n\nThe incident is ongoing.\n\n- Check power\n- Check cabling\n\nEscalate if unresolved."
    )
    assert result == (
        "Overview:\n\n"
        "The incident is ongoing.\n\n"
        "• Check power\n• Check cabling\n\n"
        "Escalate if unresolved."
    )


def test_6b_full_example_structure_from_the_instruction() -> None:
    """Mirrors the instruction's own illustrative presentation shape
    (generic content -- never Aurora/telco-specific in production code,
    this is a fixture)."""
    raw = (
        "Incident Summary\n\n"
        "The service degradation has been confirmed across the affected sites.\n\n"
        "Key Findings\n\n"
        "- Site A: alarm active\n"
        "- Site B: no corresponding alarm\n"
        "- Site C: performance degradation detected\n\n"
        "Recommended Next Steps\n\n"
        "1. Validate current readings.\n"
        "2. Check physical condition.\n"
        "3. Correlate alarms with recent activity.\n\n"
        "Status:\n\n"
        "Further field validation is required."
    )
    result = format_teams_message(raw)
    assert "• Site A: alarm active" in result
    assert "1. Validate current readings." in result
    assert "2. Check physical condition." in result
    assert result.count("<") == 0  # no HTML anywhere


# --- 7/8/9/10/11: normalization + semantic preservation -------------------


def test_7_excessive_blank_lines_normalized() -> None:
    result = format_teams_message("First.\n\n\n\nSecond.")
    assert result == "First.\n\nSecond."


def test_8_unicode_preserved() -> None:
    result = format_teams_message("Status: 🔴 relay down — see café notes (naïve check)")
    assert result == "Status: 🔴 relay down — see café notes (naïve check)"


def test_9_special_characters_preserved() -> None:
    result = format_teams_message('Quotes "like this" & ampersands, 100% preserved.')
    assert result == 'Quotes "like this" & ampersands, 100% preserved.'


def test_10_urls_preserved_exactly() -> None:
    result = format_teams_message("See https://example.com/incident/12345?tab=details for context.")
    assert "https://example.com/incident/12345?tab=details" in result
    assert result == "See https://example.com/incident/12345?tab=details for context."


def test_11_commands_and_code_like_text_preserved() -> None:
    result = format_teams_message("Run: systemctl restart relay-agent --force")
    assert result == "Run: systemctl restart relay-agent --force"


def test_12_semantic_content_unchanged_only_presentation_normalized() -> None:
    raw = "checksum=7319\nstatus=GREEN\nsite-id=NB-4471"
    result = format_teams_message(raw)
    assert "checksum=7319" in result
    assert "status=GREEN" in result
    assert "site-id=NB-4471" in result


# --- 13/14/15: no HTML, no markup interpretation ---------------------------


def test_13_no_html_tags_generated_for_any_recognized_structure() -> None:
    result = format_teams_message("Heading:\n\nParagraph.\n\n- bullet\n\n1. numbered")
    for forbidden in ("<p>", "</p>", "<ul>", "</ul>", "<li>", "</li>", "<ol>", "</ol>", "<strong>", "</strong>", "<br>"):
        assert forbidden not in result


def test_14_literal_angle_bracket_content_is_never_interpreted_as_markup() -> None:
    result = format_teams_message("Use <config-value> as a placeholder, not <script>.")
    assert result == "Use <config-value> as a placeholder, not <script>."


def test_15_script_like_text_remains_harmless_plain_text() -> None:
    raw = '<script>alert(1)</script> and "quotes" & ampersands'
    result = format_teams_message(raw)
    # Passed through byte-for-byte -- plain text is never parsed as markup
    # by anything downstream, so there is nothing to escape.
    assert result == raw


def test_15b_bullet_item_with_angle_brackets_preserved_verbatim() -> None:
    result = format_teams_message("- <img src=x onerror=alert(1)>")
    assert result == "• <img src=x onerror=alert(1)>"


# --- 16/17/18/19: approval payload binding preserved -----------------------


def test_16_formatter_runs_before_proposal_binding() -> None:
    from backend.tools.teams import propose_write

    result = propose_write.teams_propose_send_message("chat-1", "Hello\n\n- One\n- Two")
    assert "error" not in result
    expected = format_teams_message("Hello\n\n- One\n- Two")
    assert result["message"] == expected


def test_17_stored_proposal_contains_canonical_plain_text() -> None:
    from backend.approval.service import load_active_proposal
    from backend.tools.teams import propose_write

    class _Ctx:
        def __init__(self) -> None:
            self.state: dict = {}

    ctx = _Ctx()
    propose_write.teams_propose_send_message("chat-1", "Heading:\n\n- item one\n- item two", tool_context=ctx)
    stored = load_active_proposal(ctx.state)
    assert stored.payload["message"] == format_teams_message("Heading:\n\n- item one\n- item two")
    assert "<" not in stored.payload["message"]


def test_18_approved_execution_sends_the_exact_stored_string() -> None:
    from backend.approval.service import approve_proposal, load_active_proposal
    from backend.tools.teams import execute_write, propose_write

    class _Ctx:
        def __init__(self, state: dict) -> None:
            self.state = state

    state: dict = {}
    propose_write.teams_propose_send_message("chat-1", "Hello\n\n- One\n- Two", tool_context=_Ctx(state))
    proposal = load_active_proposal(state)
    approve_proposal(proposal.proposal_id, state)

    sent: dict = {}

    class _FakeClient:
        def send_message(self, chat_id: str, message: str) -> None:
            sent["chatId"] = chat_id
            sent["message"] = message

    import backend.tools.teams.execute_write as execute_write_module

    original_client = execute_write_module.PowerAutomateClient
    execute_write_module.PowerAutomateClient = lambda: _FakeClient()  # type: ignore[assignment]
    try:
        result = execute_write.teams_send_message(
            "chat-1", format_teams_message("Hello\n\n- One\n- Two"), tool_context=_Ctx(state)
        )
    finally:
        execute_write_module.PowerAutomateClient = original_client

    assert "error" not in result
    assert sent["message"] == format_teams_message("Hello\n\n- One\n- Two")


def test_19_formatter_is_not_invoked_again_during_execute(monkeypatch) -> None:
    """execute_write.py never imports/calls format_teams_message at all --
    proven by asserting the module has no reference to it, structurally
    guaranteeing execution cannot re-format (and therefore cannot risk any
    non-idempotent transform on an already-formatted string)."""
    import backend.tools.teams.execute_write as execute_write_module
    import backend.tools.teams.write_validation as write_validation_module

    assert "format_teams_message" not in dir(execute_write_module)
    assert "format_teams_message" not in dir(write_validation_module)


# --- 20: existing Teams send semantics unchanged ---------------------------


def test_20_chat_id_normalization_and_error_paths_unaffected() -> None:
    from backend.tools.teams import propose_write

    result = propose_write.teams_propose_send_message("  ", "hello")
    assert "error" in result  # unchanged: still validated by normalize_send_message_payload

    result_empty_message = propose_write.teams_propose_send_message("chat-1", "   ")
    assert "error" in result_empty_message  # whitespace-only formats to "" -> still rejected


def test_empty_and_whitespace_only_input_produces_empty_string() -> None:
    assert format_teams_message("") == ""
    assert format_teams_message("   \n\n  \t  ") == ""


def test_heading_requires_short_single_line_ending_in_colon() -> None:
    long_line = ("word " * 30).strip() + ":"
    result = format_teams_message(long_line)
    # A long line is treated as an ordinary paragraph, not a heading --
    # content is identical either way since no markup is added, but the
    # classification still matters for spacing when adjacent to other
    # blocks (a heading never gets a <br>/newline-joined body).
    assert result == long_line


def test_ambiguous_mixed_list_markers_fall_back_to_a_safe_paragraph() -> None:
    result = format_teams_message("- one\nnot a bullet\n- two")
    assert result == "- one\nnot a bullet\n- two"
