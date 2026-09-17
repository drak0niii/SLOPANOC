"""POST-6A -- Admin CLI for governed operation descriptors.

There is no frontend governance surface, so this is the usable interface
for a human to author, review, approve and revoke a governed operation
descriptor.

THE NORMAL PATH IS THE AUTHENTICATED HTTP API.

    SLOPANOC_API_BASE_URL   where the running API is
    SLOPANOC_API_TOKEN      the governor's own bearer token

The CLI sends that token and NOTHING ELSE about identity. It does not
take an `--actor`, because it has no way to prove one: the server derives
the actor from the verified token and writes the approval record against
that verified identity. Whatever this tool believes about who is running
it is irrelevant, and that is the point.

WHY THE PREVIOUS DESIGN WAS A BYPASS. It accepted `--actor alice` and
authorized the act if `SLOPANOC_ADMIN_ACTOR_ID` matched and was on the
governor allowlist. But `SLOPANOC_ADMIN_ACTOR_ID` is CONFIGURATION, not
authentication: it proves only that someone could set an environment
variable on the host. Anyone with shell access to the server -- a
deployment account, a CI runner, a compromised process -- could approve
a state-changing Teams operation and have the approval record name a
human who never reviewed it. An audit record that names an unverified
identity is worse than no record, because it is trusted.

THE LOCAL PATH IS DEVELOPMENT-ONLY (`--local`). It talks to the database
directly through `KnowledgeGovernanceService`, and it refuses unless
`SLOPANOC_GOVERNANCE_DEV_MODE` is explicitly enabled -- the same flag that
makes the server itself accept unverified identity. It exists so local
work against a local database does not require standing up an IdP. It is
not a fallback for production and never silently becomes one: if the
remote path is unusable, this tool reports that rather than quietly
doing the privileged thing locally.

USAGE (from the repository root):

    # Authenticated (normal):
    $env:SLOPANOC_API_BASE_URL = "https://your-host"
    $env:SLOPANOC_API_TOKEN    = "<paste your access token>"
    python -m backend.tools.admin.descriptor_admin list      K1 2.0
    python -m backend.tools.admin.descriptor_admin show      K1 2.0 s1
    python -m backend.tools.admin.descriptor_admin template  > draft.json
    python -m backend.tools.admin.descriptor_admin draft     K1 2.0 s1 draft.json
    python -m backend.tools.admin.descriptor_admin approve   K1 2.0 s1 --note "reviewed"
    python -m backend.tools.admin.descriptor_admin revoke    K1 2.0 s1

    # Development-only, direct to the local database:
    python -m backend.tools.admin.descriptor_admin approve K1 2.0 s1 --local

`template` prints a complete, commented example descriptor covering every
field a human must fill in: target scope, parameters, approved command
templates, effect, prerequisites and prohibitions. `draft` validates that
JSON against `GovernedOperationDescriptor` before sending -- a malformed
descriptor is rejected here, never partially stored.

WHAT REMAINS HUMAN WORK: this tool cannot infer a descriptor. Target
scope, parameters and command templates must be read off the approved
procedure by a person -- see `draft_descriptor_from_section`'s own
docstring for why guessing any of them is exactly the fabrication the
whole mechanism exists to prevent.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Optional

from backend.config.settings import get_settings
from backend.knowledge.domain.operation_descriptor import GovernedOperationDescriptor

_API_BASE_URL_ENV_VAR = "SLOPANOC_API_BASE_URL"
_API_TOKEN_ENV_VAR = "SLOPANOC_API_TOKEN"

_TEMPLATE = {
    "operation_id": "restart-rru",
    "target_scope": "single_target",
    "_target_scope_options": ["unknown", "target_independent", "single_target", "multi_target"],
    "effect": "state_change",
    "_effect_options": ["read_only", "state_change", "unknown"],
    "parameters": [
        {
            "name": "unit_id",
            "kind": "target_identifier",
            "_kind_options": ["target_identifier", "target_type", "value"],
            "required": True,
            "identifier_class": "RRU",
            "allowed_values": [],
        }
    ],
    "command_templates": [
        {
            "template_id": "restart",
            "template": "accn FieldReplaceableUnit={unit_id} restartunit 1 1 1",
            "_template_note": "Use {parameter_name} placeholders. NEVER paste a filled example identifier.",
            "effect": "state_change",
            "prerequisites": [],
            "prohibitions": [],
        }
    ],
    "prerequisites": [],
    "prohibitions": [],
}


# ---------------------------------------------------------------------
# The authenticated path
# ---------------------------------------------------------------------


class _RemoteError(RuntimeError):
    """A failure reported by the API, already in its safe form."""


def _api_base_url() -> str:
    raw = (os.environ.get(_API_BASE_URL_ENV_VAR) or "").strip().rstrip("/")
    if not raw:
        raise _RemoteError(
            f"{_API_BASE_URL_ENV_VAR} is not set. Point it at the running SLOPANOC API, "
            f"or pass --local for the development-only direct path."
        )
    return raw


def _api_token() -> str:
    """The governor's own bearer token, read from the environment ONLY.

    Never accepted as a command-line argument: an argument is visible in
    the process list and in shell history, and a token is a credential.
    Never printed, never logged, never included in an error message --
    every failure below reports a status and the API's own safe message.
    """
    raw = (os.environ.get(_API_TOKEN_ENV_VAR) or "").strip()
    if not raw:
        raise _RemoteError(
            f"{_API_TOKEN_ENV_VAR} is not set. Governance actions are performed as the identity the "
            f"API verifies from this token; there is no way to assert an actor without one."
        )
    return raw


def _request(method: str, path: str, body: Optional[dict] = None) -> Any:
    """One small HTTP call against the governance routes.

    Uses `requests`, which this backend already depends on for the Power
    Automate gateway -- no new dependency for an operator tool.
    """
    import requests

    url = f"{_api_base_url()}{path}"
    headers = {"Authorization": f"Bearer {_api_token()}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    response = requests.request(method, url, headers=headers, json=body, timeout=30)

    if response.status_code == 401:
        raise _RemoteError("the API rejected this token. Obtain a current token and try again.")
    if response.status_code == 403:
        raise _RemoteError(
            "this identity is not permitted to govern operation descriptors. "
            "A governor role or allowlist entry is required."
        )
    if not response.ok:
        # Report the API's own already-safe message when it sent one;
        # never echo a raw body, which could carry unexpected detail.
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("userMessage") or payload.get("detail") or "")
        except ValueError:
            detail = ""
        raise _RemoteError(f"the API returned {response.status_code}" + (f": {detail}" if detail else ""))
    return response.json()


def _operations_path(knowledge_id: str, version_label: str, section_id: Optional[str] = None) -> str:
    from urllib.parse import quote

    base = (
        f"/api/knowledge/{quote(knowledge_id, safe='')}"
        f"/versions/{quote(version_label, safe='')}/operations"
    )
    return base if section_id is None else f"{base}/{quote(section_id, safe='')}"


# ---------------------------------------------------------------------
# The development-only local path
# ---------------------------------------------------------------------


def _require_local_development() -> str:
    """POST-6A -- `--local` IS DEVELOPMENT-ONLY, ENFORCED.

    Permitted only when `SLOPANOC_GOVERNANCE_DEV_MODE` is explicitly
    enabled: the same flag that makes the server itself accept unverified
    identity, so this cannot be more permissive than the deployment it
    runs against.

    `SLOPANOC_ADMIN_ACTOR_ID` names the identity the resulting record
    carries. It is configuration, NOT proof of identity, and it authorizes
    nothing on its own -- the dev-mode flag above is what permits the act.
    Outside development the authenticated API is the only route, and this
    function refuses rather than pretending.
    """
    settings = get_settings()
    if not settings.governance_dev_mode:
        raise PermissionError(
            "--local is development-only and requires SLOPANOC_GOVERNANCE_DEV_MODE. "
            "In any other deployment, set SLOPANOC_API_BASE_URL and SLOPANOC_API_TOKEN and use the "
            "authenticated API, so the approval record names the identity the server verified."
        )
    actor = (settings.admin_actor_id or "").strip()
    if not actor:
        raise PermissionError(
            "--local requires SLOPANOC_ADMIN_ACTOR_ID so the resulting record names a local development "
            "actor rather than an empty identity."
        )
    return actor


def _local_service():
    from backend.api.knowledge_governance_service import get_knowledge_governance_service

    return get_knowledge_governance_service()


# ---------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------


def _print_sections(rows: list[dict]) -> None:
    for row in rows:
        print(
            f"{row['section_id']:<28} authority={row.get('authority') or '-':<10} "
            f"scope={row.get('target_scope') or '-':<18} approved_by={row.get('approved_by') or '-'}"
        )


def _load_descriptor(path: Optional[str]) -> Optional[GovernedOperationDescriptor]:
    """Validate the operator's JSON HERE, before anything is sent or
    stored. `None` means "draft the skeleton from the section itself",
    which the server does on its own."""
    if path is None:
        return None
    raw = json.loads(open(path, encoding="utf-8").read())
    # Strip the `_*` explanatory keys the template carries so an operator
    # can edit the template in place without stripping them by hand.
    cleaned = {k: v for k, v in raw.items() if not k.startswith("_")}
    for key in ("parameters", "command_templates"):
        cleaned[key] = [{k: v for k, v in entry.items() if not k.startswith("_")} for entry in cleaned.get(key, [])]
    return GovernedOperationDescriptor.model_validate(cleaned)


def _run_remote(args: argparse.Namespace) -> int:
    if args.command == "list":
        payload = _request("GET", _operations_path(args.knowledge_id, args.version_label))
        _print_sections(payload.get("sections", []))
        if not payload.get("may_approve"):
            print("\nnote: this identity may not approve descriptors.", file=sys.stderr)
        return 0

    if args.command == "show":
        payload = _request("GET", _operations_path(args.knowledge_id, args.version_label))
        for row in payload.get("sections", []):
            if row["section_id"] == args.section_id:
                print(json.dumps(row, indent=2))
                return 0
        print(f"no section {args.section_id!r}", file=sys.stderr)
        return 1

    if args.command == "draft":
        descriptor = _load_descriptor(args.path)
        body = {"descriptor": descriptor.model_dump(mode="json") if descriptor else None}
        result = _request(
            "POST", f"{_operations_path(args.knowledge_id, args.version_label, args.section_id)}/draft", body
        )
        print(f"drafted {result['operation_id']} as {result['authority']}")
        return 0

    if args.command == "approve":
        result = _request(
            "POST",
            f"{_operations_path(args.knowledge_id, args.version_label, args.section_id)}/approve",
            {"note": args.note},
        )
        # `approved_by` is the server's verified identity, not anything
        # this process claimed. Printing it is how the operator confirms
        # the record names who they expect.
        print(
            f"approved by {result['approved_by']} "
            f"fingerprint={str(result['descriptor_fingerprint'])[:16]}..."
        )
        return 0

    if args.command == "revoke":
        result = _request(
            "POST", f"{_operations_path(args.knowledge_id, args.version_label, args.section_id)}/revoke", {}
        )
        print("revoked" if result.get("revoked") else "nothing to revoke")
        return 0

    return 2


async def _run_local(args: argparse.Namespace) -> int:
    service = _local_service()

    if args.command == "list":
        _print_sections(await service.list_sections(args.knowledge_id, args.version_label))
        return 0

    if args.command == "show":
        for row in await service.list_sections(args.knowledge_id, args.version_label):
            if row["section_id"] == args.section_id:
                print(json.dumps(row, indent=2))
                return 0
        print(f"no section {args.section_id!r}", file=sys.stderr)
        return 1

    # Everything below MUTATES governed content.
    actor = _require_local_development()

    if args.command == "draft":
        descriptor = _load_descriptor(args.path)
        if descriptor is None:
            from backend.api.knowledge_governance_service import draft_descriptor_from_section

            obj = await service._require_object(args.knowledge_id, args.version_label)
            descriptor = draft_descriptor_from_section(obj, args.section_id)
        authored = await service.author_operation(
            args.knowledge_id, args.version_label, args.section_id, descriptor, actor_user_id=actor
        )
        print(f"drafted {authored.operation_id} as {authored.authority.value}")
        return 0

    if args.command == "approve":
        record = await service.approve_operation(
            args.knowledge_id, args.version_label, args.section_id, actor_user_id=actor, note=args.note
        )
        print(f"approved by {record.approved_by} fingerprint={record.descriptor_fingerprint[:16]}...")
        return 0

    if args.command == "revoke":
        revoked = await service.revoke_operation(
            args.knowledge_id, args.version_label, args.section_id, actor_user_id=actor
        )
        print("revoked" if revoked else "nothing to revoke")
        return 0

    return 2


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="descriptor_admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(p, section: bool = True):
        p.add_argument("knowledge_id")
        p.add_argument("version_label")
        if section:
            p.add_argument("section_id")
        p.add_argument(
            "--local",
            action="store_true",
            help="DEVELOPMENT ONLY: act directly on the local database instead of the authenticated API. "
            "Requires SLOPANOC_GOVERNANCE_DEV_MODE.",
        )

    _common(sub.add_parser("list", help="list sections and descriptor state"), section=False)
    _common(sub.add_parser("show", help="show one section's descriptor state"))
    sub.add_parser("template", help="print an example descriptor to edit")
    draft = sub.add_parser("draft", help="draft/replace a CANDIDATE descriptor (governor only)")
    _common(draft)
    draft.add_argument("path", nargs="?", help="JSON file; omitted drafts a skeleton from the section")
    approve = sub.add_parser("approve", help="approve a descriptor (governor only)")
    _common(approve)
    approve.add_argument("--note")
    revoke = sub.add_parser("revoke", help="revoke an approval (governor only)")
    _common(revoke)

    args = parser.parse_args(argv)
    if args.command == "template":
        print(json.dumps(_TEMPLATE, indent=2))
        return 0

    try:
        return asyncio.run(_run_local(args)) if args.local else _run_remote(args)
    except Exception as exc:  # noqa: BLE001 -- an operator tool reports, never tracebacks
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
