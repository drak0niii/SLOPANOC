"""Bounded inbox-only replay. No imports from agents/tools/model providers."""
async def recover(ledger):
    repo=ledger.repository
    events=await repo.pending(ledger.config.finops_recovery_batch)
    for event in events:
        try:
            result=await ledger.bounded(repo.materialize,event)
            ledger.signal({'PERSISTED':'replayed','DUPLICATE':'duplicates','BUSY':'pending','CONFLICT':'conflicts'}[result])
        except Exception:
            ledger.signal('persistence_failures')
            await ledger.bounded(repo.retry_failed,event,ledger.config.finops_recovery_attempts)
    return len(events)
