"""Bounded, fenced, environment-scoped local retention. No scheduling at import."""
import asyncio
import calendar
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import select, delete, update, String, or_, and_
from .models import RunRow, SummaryRow, EventRow, Base
from .policy_models import RetentionCheckpoint
from .retention_registry import VERSION, REGISTRY, duration
from .privacy_policy import ENVIRONMENTS

@dataclass(frozen=True)
class Report:
    dataset: str
    cutoff: datetime
    scanned: int
    candidate: int
    eligible: int
    dependency_blocked: int
    unknown: int
    deleted: int
    scan_complete: bool
    dry_run: bool

def calendar_cutoff(now,months):
    index=now.year*12+now.month-1-months;year,month=divmod(index,12);month+=1
    return now.replace(year=year,month=month,day=min(now.day,calendar.monthrange(year,month)[1]))

def financial_tables():
    from .finops.models import FinOpsBase
    from .finops.billing_models import BillingBase
    from .finops.reconciliation_models import ReconciliationBase
    from .finops.unit_economics_models import UnitEconomicsBase
    from .finops.control_models import ControlBase
    return {t.name:t for base in (FinOpsBase,BillingBase,ReconciliationBase,UnitEconomicsBase,ControlBase)
        for t in base.metadata.tables.values()}

class DependencyChecker:
    """Explicit relational and JSON/string lineage adapters across M10–M14.

    A bounded dependency scan with insufficient coverage returns UNKNOWN. Even an
    expired dependent still blocks its source until the dependent is removed. No
    JSON source vector is assumed covered merely because no FK was declared.
    """
    ADAPTERS={
        'corrections':('ai_usage_ledger',),
        'source_generations_pricing':('finops_billing_ingest_state','finops_billing_projection_cache','finops_reconciliation_input','finops_reconciliation_generation'),
        'reconciliation':('finops_reconciliation_state','finops_unit_economics_generation'),
        'denominators':('finops_unit_economics_generation','finops_unit_economics_publication'),
        'forecast_backtest':('finops_control_generation','finops_control_input','finops_forecast_backtest_generation'),
        'anomaly':('finops_control_publication','finops_anomaly_transition'),
    }
    def __init__(self, *, limit=256):
        if not 1<=limit<=1000: raise ValueError('Invalid dependency bound')
        self.limit=limit
        self._rows=None

    def for_batch(self):
        checker=DependencyChecker(limit=self.limit)
        checker._rows={}
        return checker

    async def check(self,session,source_table,identity,environment):
        from sqlalchemy import JSON
        tables=financial_tables()
        unknown=False
        # Cover every durable financial table, including PK/FK identities whose
        # cross-table meaning (inbox event ID, source generation) is a dependency.
        for name in sorted(tables):
            table=tables.get(name)
            if table is None: unknown=True;continue
            # Explicit FK/string/JSON fields include originals, inputs, versions,
            # publications, source vectors, forecast/truth and predecessor links.
            columns=[c for c in table.c if isinstance(c.type,(String,JSON))]
            query=select(*columns)
            if 'environment' in table.c: query=query.where(table.c.environment==environment)
            if self._rows is not None and name in self._rows:rows=self._rows[name]
            else:
                rows=(await session.execute(query.limit(self.limit+1))).all()
                if self._rows is not None:self._rows[name]=rows
            if len(rows)>self.limit:
                unknown=True;rows=rows[:self.limit]
            for row in rows:
                own = getattr(row,'identity',getattr(row,'event_id',getattr(row,'generation',getattr(row,'attempt_id',None))))
                if table.name==source_table and own==identity: continue
                def contains(value,depth=0):
                    nonlocal unknown
                    if depth>16: unknown=True;return False
                    if type(value) is str: return value==identity
                    if type(value) is dict:
                        if len(value)>1024: unknown=True;return False
                        return any(contains(v,depth+1) for v in value.values())
                    if type(value) in (tuple,list):
                        if len(value)>10000: unknown=True;return False
                        return any(contains(v,depth+1) for v in value)
                    return False
                if any(contains(v) for c,v in zip(columns,row) if not (table.name==source_table and c.primary_key)):
                    return 'BLOCKED'
        # Cross-service publication can hold JSON lineage prepared outside this
        # transaction. Shared PostgreSQL deletion remains conservative until an
        # approved publication/deletion protocol has been qualified. Checkpoint
        # fencing alone must never be claimed to fence those publishers.
        unknown |= session.bind.dialect.name != 'sqlite'
        return 'UNKNOWN' if unknown else 'CLEAR'

class RetentionWorker:
    def __init__(self,sessions,environment,*,batch=100,seconds=2,dependencies=None,runtime=None,financial_months=None):
        if environment not in ENVIRONMENTS or not 1<=batch<=1000 or not 0<seconds<=10:
            raise ValueError('Invalid retention scope or bounds')
        self.sessions=sessions;self.environment=environment;self.batch=batch;self.seconds=seconds
        self.dependencies=dependencies or DependencyChecker()
        if financial_months is None:financial_months=REGISTRY['financial_history'].horizon
        if type(financial_months) is not int or not 24<=financial_months<=120:raise ValueError('Invalid financial retention')
        self.financial_months=financial_months;self.runtime=runtime

    def adapter(self,dataset):
        if dataset=='active_terminal': return RunRow.__table__, 'run_id','expiry_at',False
        if dataset in ('ordinary_summary','exceptional_summary'): return SummaryRow.__table__, 'run_id','expiry_at',False
        from .slo_rollups import SREReceiptRow,SREBucketRow
        if dataset=='slo_receipts': return SREReceiptRow.__table__,'run_id','due_at',False
        if dataset=='slo_working_buckets': return SREBucketRow.__table__,'slo_id','at',False
        names={
            'finops_runtime_attempt':('attempt_id','provider_finished_at'),
            'finops_usage_inbox':('event_id','created_at'),
            'ai_usage_ledger':('event_id','observed_at'),
            'finops_usage_bucket':('at','at'),
            'finops_billing_publication':('generation','published_at'),
            'finops_reconciliation_input':('identity','recorded_at'),
            'finops_reconciliation_generation':('identity','published_at'),
            'finops_unit_denominator_snapshot':('identity','captured_at'),
            'finops_control_input':('identity','cutoff'),
            'finops_control_generation':('identity','published_at'),
            'finops_forecast_backtest_generation':('identity','known_at'),
        }
        if dataset not in names: raise ValueError('Dataset has no deletion adapter')
        table=financial_tables()[dataset];key,at=names[dataset]
        if at not in table.c or 'environment' not in table.c:
            raise ValueError('Dataset requires an explicit environment/time adapter')
        return table,key,at,True

    async def run(self,dataset,now,*,dry_run=True):
        if now.tzinfo is None: raise ValueError('Retention clock must be aware')
        try:
            async with asyncio.timeout(self.seconds):
                report=await self._run(dataset,now,dry_run)
        except Exception:
            from .policy_metrics import record,DATASETS
            if dataset in DATASETS:
                try:record(self.runtime,f'slopanoc.retention.{dataset}.failures')
                except Exception:pass
            raise
        from .policy_metrics import record,DATASETS
        if dataset in DATASETS:
            for field,value in (('scanned',report.scanned),('eligible',report.eligible),('blocked',report.dependency_blocked),
                ('unknown',report.unknown),('deleted',report.deleted),('dry_runs',int(dry_run))):
                try:record(self.runtime,f'slopanoc.retention.{dataset}.{field}',value)
                except Exception:pass
        return report

    async def _run(self,dataset,now,dry_run):
        table,key,at,financial=self.adapter(dataset)
        dependencies=self.dependencies.for_batch() if isinstance(self.dependencies,DependencyChecker) else self.dependencies
        cutoff=calendar_cutoff(now,self.financial_months) if financial else (
            now if dataset in ('active_terminal','ordinary_summary','exceptional_summary') else now-duration(dataset))
        async with self.sessions() as session,session.begin():
            checkpoint=None
            if not dry_run:
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                insert=pg_insert if session.bind.dialect.name=='postgresql' else sqlite_insert
                await session.execute(insert(RetentionCheckpoint).values(environment=self.environment,dataset=dataset,
                    policy_version=VERSION,cutoff=cutoff,cursor=None,generation=0,complete=False)
                    .on_conflict_do_nothing(index_elements=['environment','dataset']))
                checkpoint=(await session.execute(select(RetentionCheckpoint).where(
                    RetentionCheckpoint.environment==self.environment,RetentionCheckpoint.dataset==dataset).with_for_update())).scalar_one()
            fence_generation=checkpoint.generation if checkpoint is not None else None
            query=select(table).where(table.c.environment==self.environment,table.c[at]<=cutoff)
            if dataset=='active_terminal': query=query.where(table.c.terminal_at.is_not(None))
            if dataset=='ordinary_summary': query=query.where(table.c.retention_class=='normal')
            if dataset=='exceptional_summary': query=query.where(table.c.retention_class=='exceptional')
            if dataset=='slo_receipts': query=query.where(table.c.settled==True)
            # Composite bucket primary keys use serialized internal cursor below;
            # the dedicated bucket adapter uses at/slo ordering, not row offsets.
            if checkpoint is not None and not checkpoint.complete and checkpoint.cutoff.replace(tzinfo=timezone.utc)==cutoff.astimezone(timezone.utc) and checkpoint.cursor:
                cursor=json.loads(checkpoint.cursor)
                predicates=[]
                for index,column in enumerate(table.primary_key.columns):
                    value=datetime.fromisoformat(cursor[index]) if column.name==at else cursor[index]
                    equals=[previous== (datetime.fromisoformat(cursor[i]) if previous.name==at else cursor[i])
                        for i,previous in enumerate(list(table.primary_key.columns)[:index])]
                    predicates.append(and_(*equals,column>value))
                query=query.where(or_(*predicates))
            rows=(await session.execute(query.order_by(*table.primary_key.columns).limit(self.batch+1).with_for_update())).mappings().all()
            complete=len(rows)<=self.batch;rows=rows[:self.batch]
            blocked=unknown=eligible=deleted=0
            for row in rows:
                dependency=await dependencies.check(session,table.name,row[key],self.environment) if financial else 'CLEAR'
                if dependency=='BLOCKED': blocked+=1;continue
                if dependency!='CLEAR': unknown+=1;continue
                eligible+=1
                if not dry_run:
                    conditions=[table.c[c.name]==row[c.name] for c in table.primary_key.columns]
                    conditions += [table.c.environment==self.environment,table.c[at]<=cutoff]
                    result=await session.execute(delete(table).where(*conditions));deleted+=max(0,result.rowcount)
                    if dataset in ('ordinary_summary','exceptional_summary'):
                        still_active=(await session.execute(select(RunRow.run_id).where(RunRow.run_id==row[key]).limit(1))).first()
                        if not still_active: await session.execute(delete(EventRow).where(EventRow.run_id==row[key]))
            if checkpoint is not None:
                # CAS makes lost fencing fail the whole transaction, including deletes.
                result=await session.execute(update(RetentionCheckpoint).where(
                    RetentionCheckpoint.environment==self.environment,RetentionCheckpoint.dataset==dataset,
                    RetentionCheckpoint.generation==fence_generation).values(cutoff=cutoff,
                    cursor=json.dumps([rows[-1][c.name].isoformat() if isinstance(rows[-1][c.name],datetime) else rows[-1][c.name]
                        for c in table.primary_key.columns],separators=(',',':')) if rows else None,
                    complete=complete,generation=fence_generation+1,policy_version=VERSION))
                if result.rowcount!=1: raise RuntimeError('Retention fence lost')
            return Report(dataset,cutoff,len(rows),len(rows),eligible,blocked,unknown,deleted,complete,dry_run)
