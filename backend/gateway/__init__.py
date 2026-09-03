"""The Power Automate execution gateway boundary.

Per docs/AGENT_CONTRACT.md #13 and docs/TEAMS_TOOL_CONTRACT.md #1, this is
the only part of the backend that resolves and uses the Power Automate
gateway secret. No other package imports the gateway URL/config directly.
"""
