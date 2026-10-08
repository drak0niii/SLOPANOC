"""M10 durable population adapter. Formula/evaluation belongs exclusively to M9."""
from datetime import timedelta
from ..slo_evaluator import Snapshot, Bucket, Counts, minute
from ..slo_contract import WINDOW_SECONDS

class AccountingSource:
    def __init__(self, repository):self.repository=repository
    async def snapshot(self,environment,slo_id,now):
        health=await self.repository.health(environment,now)
        rows=await self.repository.buckets(environment,minute(now)-timedelta(seconds=WINDOW_SECONDS),minute(now))
        return Snapshot(environment,buckets=tuple(Bucket(at,Counts(good=good,bad=bad)) for at,good,bad in rows),
            updated_at=health['updated_at'],coverage_start=health['coverage_start'],
            capture_complete=not health['conflicts'],available=health['updated_at'] is not None,
            source='DURABLE_ACCOUNTING',watermark=minute(now))

class CompositeSource:
    def __init__(self,operational,accounting):self.operational,self.accounting=operational,accounting
    async def snapshot(self,environment,slo_id,now):
        if slo_id=='cost_ledger_completeness' and self.accounting:
            return await self.accounting.snapshot(environment,slo_id,now)
        if self.operational:return await self.operational.snapshot(environment,slo_id,now)
        return Snapshot(environment,available=False,source='RUNTIME_ROLLUPS')
