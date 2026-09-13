"""TELCO Context domain (Phase 6A.2 / P11-M02).

See docs/INTELLIGENCE_ARCHITECTURE.md #6 for the logical TELCO Context
model this package implements, and CLAUDE.md's own 6A.2 closure section
for the full design rationale.

This package answers exactly one question: "what do we deterministically
know about the current TELCO operational situation, what do we not know,
where did each fact come from, and how can that state be compared later?"
It does NOT implement Knowledge applicability matching (see
backend/knowledge/domain/telco_applicability.py for the separate,
Knowledge-side applicability bridge), retrieval, embeddings, Context
Engineering assembly, Skills, Experience Memory, or the Troubleshooting
Manager -- all of those remain later Phase 6A milestones.

DEPENDENCY BOUNDARY (mirrors backend/knowledge/'s own discipline,
enforced by backend/tests/context/test_dependency_boundary.py):
`backend/context/domain/` never imports `google.adk`, `google.genai`,
`backend.agents`, `backend.tools.teams`, or `backend.knowledge` -- TELCO
Context is a peer context domain, never coupled to Knowledge Context or
to any individual agent. The governing question, exactly like Knowledge's
own: "can the Context layer still work without knowing Incident Manager
-- or Knowledge -- exists?" If the answer becomes no, the architecture is
too coupled.
"""
