"""Automated Operations Engineer in-process AgentTool definition for Team Manager.
"""
from __future__ import annotations

from google.adk.tools import AgentTool
from backend.agents.automated_operations_engineer.agent import automated_operations_engineer

automated_operations_engineer_tool = AgentTool(agent=automated_operations_engineer)
