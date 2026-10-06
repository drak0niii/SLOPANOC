"""Startup/import-graph regression (post-Tranche 3).

Uvicorn failed with `ImportError: cannot import name 'plan_operational_control' from partially
initialized module 'backend.operations.control_plane'` because of the cycle

    app.py -> approval_service -> operations.control_plane -> technical_authority_engineer.schemas
    -> technical_authority_engineer/__init__ -> agent -> agent_tool -> operations.control_plane

The in-process test suite never saw it: by the time any test imported `control_plane`, the TAE
package was already fully initialized. Each check here therefore runs in a FRESH interpreter so
the real first-import order is exercised, with nothing mocked.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _fresh_import(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=120)


def test_backend_app_imports_in_a_fresh_interpreter() -> None:
    result = _fresh_import("from backend.api.app import app; print('APP_IMPORT_OK')")
    assert result.returncode == 0, result.stderr[-4000:]
    assert "APP_IMPORT_OK" in result.stdout


@pytest.mark.parametrize(
    "first_import",
    [
        "backend.api.approval_service",
        "backend.api.execution_service",
        "backend.api.operational_execution_service",
        "backend.api.pending_action",
        "backend.operations.control_plane",
        "backend.operations.policy",
        "backend.operations.execution",
        "backend.operations.targets",
        "backend.agents.technical_authority_engineer.schemas",
        "backend.agents.technical_authority_engineer.agent_tool",
        "backend.agents.technical_authority_engineer",
        "backend.api.chat_service",
    ],
)
def test_control_plane_and_tae_tool_coexist_whatever_loads_first(first_import: str) -> None:
    code = (
        f"import {first_import}\n"
        "from backend.operations.control_plane import plan_operational_control\n"
        "from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool\n"
        "print('IMPORT_GRAPH_OK')\n"
    )
    result = _fresh_import(code)
    assert result.returncode == 0, result.stderr[-4000:]
    assert "IMPORT_GRAPH_OK" in result.stdout
