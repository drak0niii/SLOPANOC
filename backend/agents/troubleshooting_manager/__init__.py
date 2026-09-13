"""Phase 6A.9: Troubleshooting Manager specialist reasoning boundary.

A SECOND specialist, alongside `incident_manager`, answering "given
trusted current operational context, governed evidence, applicable
methodology, available capabilities, and relevant historical
experience, what is the next troubleshooting assessment or information
requirement?" -- never "what happened?" (`incident_manager`'s own
question, unchanged and untouched by this package).

NOT WIRED INTO `team_manager` IN THIS MILESTONE (that is 6A.10's own
scope). This package is exercised only via its own direct/internal
invocation surface (`runtime.run_troubleshooting_assessment`), never
through a live user-facing conversation yet.

NO TOOL EXECUTION: the `troubleshooting_manager` ADK agent itself has
`tools=[]` -- it cannot call Teams, Knowledge, Experience Memory, or any
other capability. Every input it reasons over was already deterministically
resolved and validated by the impure coordinator layer in this package
(`skill_resolution.py`/`experience_support.py`/`runtime.py`) BEFORE the
model is ever invoked.
"""
