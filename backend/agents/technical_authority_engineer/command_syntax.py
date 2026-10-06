"""Single-invocation command syntax boundary (Command Authority defense in depth).

One operational command = ONE invocation. A candidate that composes several invocations, or lets
a shell/CLI evaluate embedded text, is never eligible for classification as read-only and never
eligible for authorization -- even when that exact composed string appears in APPROVED, SELECTED
governed knowledge:

    multiple_lines          newline / carriage return
    command_substitution    backtick, `$(...)`, `${...}`
    and_list / or_list      `&&` / `||`
    command_separator       `;`
    pipeline                `|`
    background_operator     `&`
    redirection             `<` / `>` outside a `<placeholder>` span

Purely syntactic: no command, vendor, object or document names. Markdown emphasis and ONE pair of
wrapping backticks (model formatting) are removed first; a backtick inside the command is
substitution. Classification, grounding, placeholders, target confirmation, policy and approval
all still apply to every single-invocation command.
"""
from __future__ import annotations

import re
from typing import Optional

_PLACEHOLDER_SPAN = re.compile(r"<[A-Za-z_][\w.\-]*>")
_CHECKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("multiple_lines", re.compile(r"[\r\n]")),
    ("command_substitution", re.compile(r"`|\$\(|\$\{")),
    ("and_list", re.compile(r"&&")),
    ("or_list", re.compile(r"\|\|")),
    ("command_separator", re.compile(r";")),
    ("pipeline", re.compile(r"\|")),
    ("background_operator", re.compile(r"&")),
)


def _unwrap(command: str) -> str:
    text = re.sub(r"\*\*", "", command or "").strip()
    if len(text) >= 2 and text.startswith("`") and text.endswith("`") and "`" not in text[1:-1]:
        text = text[1:-1].strip()
    return text


def single_invocation_violation(command: Optional[str]) -> Optional[str]:
    """The composition found in `command` (a reason code), or None for a single invocation."""
    text = _unwrap(command or "")
    for reason, pattern in _CHECKS:
        if pattern.search(text):
            return reason
    if re.search(r"[<>]", _PLACEHOLDER_SPAN.sub("X", text)):
        return "redirection"
    return None


def is_single_invocation(command: Optional[str]) -> bool:
    return single_invocation_violation(command) is None
