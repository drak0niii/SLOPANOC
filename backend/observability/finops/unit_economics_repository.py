"""M13-only writes; frozen analytical history and atomic source-fenced publication."""
from datetime import datetime, timezone
from sqlalchemy import select, update
from .repository import insert_for
from .billing_contracts import digest, SourceError
from .unit_economics_models import DefinitionRow, DriverRow, SnapshotRow, GenerationRow, PublicationRow
from .unit_economics_contracts import Snapshot, Policy, bounded
from .unit_registry import Registry as Units
from .unit_drivers import Registry as Drivers
from .reconciliation_models import StateRow as FinancialState, GenerationRow as FinancialGeneration, InputRow, RegistryRow
from .reconciliation_mapping import Registry as FinancialRegistry
from .billing_models import StateRow as SourceState

def now():
    return datetime.now(timezone.utc)

def key_for(environment,scope,period,kind):
    return digest([environment,scope,period,kind])

def turn_scope(environment):
    return digest(['M9',environment])

class Repository:
    def __init__(self,sessions,policy=None):
        self.sessions=sessions;self.policy=policy or Policy()
    async def reserve(self,environment,scope,period,kind='ECONOMICS'):
        key=key_for(environment,scope,period,kind)
        async with self.sessions() as s,s.begin():
            await s.execute(insert_for(s,PublicationRow).values(key=key,environment=environment,scope=scope,
                period=period,kind=kind,revision=0).on_conflict_do_nothing())
            rev=await s.scalar(update(PublicationRow).where(PublicationRow.key==key).values(
                revision=PublicationRow.revision+1,last_attempt=now()).returning(PublicationRow.revision))
            return key,rev
    async def fail(self,key,revision,error):
        # Safe finite codes only, never exception/provider text.
        codes={'PUBLICATION_FAILED','FINANCIAL_IMBALANCE','MAPPING_INVALID','SCOPE_UNPROVEN','BATCH_LIMIT','SOURCE_UNAVAILABLE'}
        async with self.sessions() as s,s.begin():
            await s.execute(update(PublicationRow).where(PublicationRow.key==key,PublicationRow.revision==revision)
                .values(error=error if error in codes else 'PUBLICATION_FAILED'))
    async def save_snapshot(self,key,revision,snapshot):
        snap=Snapshot.model_validate(snapshot.model_dump());body=snap.model_dump(mode='json');bounded(body,self.policy)
        async with self.sessions() as s,s.begin():
            fence=await s.execute(update(PublicationRow).where(PublicationRow.key==key,PublicationRow.revision==revision).values(revision=revision))
            if fence.rowcount!=1:return 'SUPERSEDED'
            state=await s.get(PublicationRow,key)
            if (state.environment,state.period,state.kind)!=(snap.environment,snap.period,snap.source):
                raise SourceError('SCOPE_UNPROVEN')
            await s.execute(insert_for(s,SnapshotRow).values(identity=snap.identity,environment=snap.environment,period=snap.period,
                source=snap.source,body=body,captured_at=snap.captured_at).on_conflict_do_nothing())
            saved=Snapshot.model_validate((await s.get(SnapshotRow,snap.identity)).body)
            if saved.identity!=snap.identity:
                raise SourceError('FINANCIAL_IMBALANCE')
            identical=state.snapshot==snap.identity
            await s.execute(update(PublicationRow).where(PublicationRow.key==key,PublicationRow.revision==revision)
                .values(snapshot=snap.identity,last_success=now(),error=None))
            return 'IDENTICAL' if identical else 'PUBLISHED'
    async def snapshot(self,environment,scope,period,source):
        async with self.sessions() as s:
            state=await s.get(PublicationRow,key_for(environment,scope,period,source))
            row=await s.get(SnapshotRow,state.snapshot) if state and state.snapshot else None
            return (state,Snapshot.model_validate(row.body)) if row else (state,None)
    async def registries(self,units,drivers,scope):
        units=Units.model_validate(units.model_dump());drivers=Drivers.model_validate(drivers.model_dump())
        async with self.sessions() as s,s.begin():
            await s.execute(insert_for(s,DefinitionRow).values(identity=units.identity,version=units.version,body=units.model_dump(mode='json')).on_conflict_do_nothing())
            await s.execute(insert_for(s,DriverRow).values(identity=drivers.identity,environment=drivers.environment,scope=scope,
                version=drivers.version,body=drivers.model_dump(mode='json')).on_conflict_do_nothing())
            definition=(await s.scalars(select(DefinitionRow).where(DefinitionRow.version==units.version))).one()
            driver=(await s.scalars(select(DriverRow).where(DriverRow.environment==drivers.environment,
                DriverRow.scope==scope,DriverRow.version==drivers.version))).one()
            if definition.identity!=units.identity or driver.identity!=drivers.identity:
                raise SourceError('MAPPING_INVALID')
        return units.identity,drivers.identity
    async def financial(self,environment,scope,period):
        async with self.sessions() as s:
            state=(await s.scalars(select(FinancialState).where(FinancialState.environment==environment,
                FinancialState.scope==scope,FinancialState.period==period))).one_or_none()
            g=await s.get(FinancialGeneration,state.generation) if state and state.generation else None
            if not g:return None
            return dict(state_key=state.key,generation=g.identity,body=g.body,
                billing=(await s.get(InputRow,g.billing_input)).body,runtime=(await s.get(InputRow,g.runtime_input)).body,
                registry=FinancialRegistry.model_validate((await s.get(RegistryRow,g.registry)).body))
    async def publish(self,key,revision,financial_id,snapshot_ids,registry_ids,result):
        from .unit_economics import calculate
        identity=digest([financial_id,snapshot_ids,registry_ids,result])
        bounded(result,self.policy)
        async with self.sessions() as s,s.begin():
            fence=await s.execute(update(PublicationRow).where(PublicationRow.key==key,PublicationRow.revision==revision).values(revision=revision))
            if fence.rowcount!=1:return 'SUPERSEDED'
            state=await s.get(PublicationRow,key)
            f=await s.get(FinancialGeneration,financial_id)
            if f is None:raise SourceError('SOURCE_UNAVAILABLE')
            fs=(await s.scalars(select(FinancialState).where(FinancialState.environment==state.environment,
                FinancialState.scope==state.scope,FinancialState.period==state.period).with_for_update())).one_or_none()
            if not fs or fs.generation!=financial_id:return 'SUPERSEDED'
            # M11 movement invalidates old M12 financial evidence even before M12 reruns.
            for source_key,gid in sorted(f.body['source_vector'].items()):
                row=await s.get(SourceState,source_key,with_for_update=True)
                if row is None or row.generation!=gid:return 'SUPERSEDED'
            snaps=[]
            for source,sid in sorted(snapshot_ids.items()):
                row=await s.get(SnapshotRow,sid)
                if row is None or (row.environment,row.period,row.source)!=(state.environment,state.period,source):
                    raise SourceError('SCOPE_UNPROVEN')
                scope=turn_scope(state.environment) if source=='M9' else state.scope
                head=await s.get(PublicationRow,key_for(state.environment,scope,state.period,source),with_for_update=True)
                if not head or head.snapshot!=sid:return 'SUPERSEDED'
                snaps.append(Snapshot.model_validate(row.body))
            units=Units.model_validate((await s.get(DefinitionRow,registry_ids[0])).body)
            drivers=Drivers.model_validate((await s.get(DriverRow,registry_ids[1])).body)
            billing=(await s.get(InputRow,f.billing_input)).body;runtime=(await s.get(InputRow,f.runtime_input)).body
            financial_registry=FinancialRegistry.model_validate((await s.get(RegistryRow,f.registry)).body)
            expected=calculate(f.body['result'],billing,runtime,financial_registry,snaps,drivers,units,self.policy)
            if expected!=result:raise SourceError('FINANCIAL_IMBALANCE')
            previous=state.generation
            body=dict(result=result,snapshot_ids=snapshot_ids)
            await s.execute(insert_for(s,GenerationRow).values(identity=identity,definitions=registry_ids[0],drivers=registry_ids[1],
                reconciliation=financial_id,turn_snapshot=snapshot_ids.get('M9'),model_snapshot=snapshot_ids.get('M10'),body=body,published_at=now()).on_conflict_do_nothing())
            if (await s.get(GenerationRow,identity)).body!=body:raise SourceError('FINANCIAL_IMBALANCE')
            await s.execute(update(PublicationRow).where(PublicationRow.key==key,PublicationRow.revision==revision)
                .values(generation=identity,last_success=now(),error=None))
            return 'IDENTICAL' if previous==identity else 'PUBLISHED'
    async def current(self,environment,scope,start,end):
        async with self.sessions() as s:
            return list((await s.execute(select(PublicationRow,GenerationRow).outerjoin(GenerationRow,PublicationRow.generation==GenerationRow.identity)
                .where(PublicationRow.environment==environment,PublicationRow.scope==scope,PublicationRow.kind=='ECONOMICS',
                    PublicationRow.period>=start,PublicationRow.period<end).order_by(PublicationRow.period).limit(32))).all())
