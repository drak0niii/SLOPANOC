"""Problem Manager in-process AgentTool definition for Team Manager.
"""
from __future__ import annotations

from google.adk.tools import AgentTool
from backend.agents.problem_manager.agent import problem_manager

problem_manager_tool = AgentTool(agent=problem_manager)
