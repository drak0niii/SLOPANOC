"""Phase 6A.5 (P11-M05): hybrid Knowledge retrieval & evidence selection.

Operates ONLY inside the deterministic `permitted_knowledge_ids` produced
by 6A.4 (`backend/knowledge/narrowing/`) -- never rediscovers
applicability. See `backend/knowledge/hybrid_retrieval/service.py` for
the entry point and `docs/KNOWLEDGE_CONTRACT.md` §27 for the full
contract.
"""
