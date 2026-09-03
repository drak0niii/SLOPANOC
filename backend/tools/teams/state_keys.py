"""Shared Teams session-state key constants.

Kept separate from `backend/agents/team_manager/state_sync.py` (which
*writes* these keys as part of team_manager's cross-turn context sync) so
that lower-layer tool modules under `backend/tools/teams/` can reference
the same keys without importing `backend.agents.team_manager` --
whose package `__init__.py` eagerly imports its own agent module, which
in turn imports `incident_manager`'s agent module, which imports
`backend.tools.teams.propose_write`. That makes a direct
`propose_write.py -> state_sync.py` import a REAL circular import through
package `__init__.py` (verified: it fails at collection time), not merely
a theoretical layering concern -- hence this small, dependency-free
module both sides can import from instead.
"""
from __future__ import annotations

SELECTED_TEAMS_CHAT_ID_STATE_KEY = "selected_teams_chat_id"
SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY = "selected_teams_chat_topic"
