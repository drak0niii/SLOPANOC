"""Immutable snapshots/results and atomic CAS-fenced publication."""
from datetime import datetime,timezone
from sqlalchemy import select,update
from .repository import insert_for
from .billing_models import PublicationRow,StateRow as BillingState
from .billing_contracts import digest,SourceError
from .reconciliation_models import RegistryRow,InputRow,GenerationRow,StateRow
from .reconciliation_mapping import Registry
from .reconciliation_contracts import Policy,Reason,bounded
from .allocation import validate


def now():return datetime.now(timezone.utc)

class Repository:
    def __init__(self,sessions,policy=None):self.sessions=sessions;self.policy=policy or Policy()
    async def registry(self,registry):
        registry=Registry.model_validate(registry.model_dump());body=registry.model_dump(mode='json')
        async with self.sessions() as s,s.begin():
            await s.execute(insert_for(s,RegistryRow).values(identity=registry.identity,environment=registry.environment,
                version=registry.version,ownership_version=registry.ownership_version,body=body).on_conflict_do_nothing())
            row=(await s.scalars(select(RegistryRow).where(RegistryRow.environment==registry.environment,
                RegistryRow.version==registry.version,RegistryRow.ownership_version==registry.ownership_version))).one()
            if row.identity!=registry.identity or row.body!=body:raise SourceError('MAPPING_INVALID')
        return registry.identity
    async def input(self,body,scope):
        bounded(body,self.policy);identity=digest([scope,body]);environment=body['environment'];period=body['period']
        async with self.sessions() as s,s.begin():
            if body['kind']=='BILLING':
                manifest=await s.get(PublicationRow,body['source_generation'])
                if not manifest or manifest.fingerprint!=body['fingerprint'] or manifest.window_start.date().isoformat()!=period:
                    raise SourceError('INPUT_DETAIL_UNAVAILABLE')
                state=await s.get(BillingState,manifest.state_key)
                if state.environment!=environment or state.scope!=scope:raise SourceError('SCOPE_UNPROVEN')
            await s.execute(insert_for(s,InputRow).values(identity=identity,environment=environment,scope=scope,period=period,
                kind=body['kind'],source_generation=body.get('source_generation'),body=body).on_conflict_do_nothing())
            if (await s.get(InputRow,identity)).body!=body:raise SourceError('FINANCIAL_IMBALANCE')
        return identity
    async def billing(self,environment,scope,period,generation):
        async with self.sessions() as s:
            rows=(await s.scalars(select(InputRow).where(InputRow.environment==environment,InputRow.scope==scope,
                InputRow.period==period,InputRow.kind=='BILLING',InputRow.source_generation==generation).limit(2))).all()
            if len(rows)>1:raise SourceError('FINANCIAL_IMBALANCE')
            return rows[0] if rows else None
    async def reserve(self,environment,scope,period):
        key=digest([environment,scope,period])
        async with self.sessions() as s,s.begin():
            await s.execute(insert_for(s,StateRow).values(key=key,environment=environment,scope=scope,period=period,
                revision=0).on_conflict_do_nothing())
            rev=await s.scalar(update(StateRow).where(StateRow.key==key).values(revision=StateRow.revision+1,
                last_attempt=now()).returning(StateRow.revision))
            return key,rev
    async def fail(self,key,revision,error):
        if error not in {r.value for r in Reason}:error='PUBLICATION_FAILED'
        async with self.sessions() as s,s.begin():
            await s.execute(update(StateRow).where(StateRow.key==key,StateRow.revision==revision).values(error=error))
    async def publish(self,key,revision,registry_id,input_ids,result,vector):
        identity=digest([registry_id,input_ids,result,vector])
        async with self.sessions() as s,s.begin():
            fenced=await s.execute(update(StateRow).where(StateRow.key==key,StateRow.revision==revision).values(revision=revision))
            if fenced.rowcount!=1:return 'SUPERSEDED'
            state=await s.get(StateRow,key)
            registry=await s.get(RegistryRow,registry_id)
            if registry is None:raise SourceError('MAPPING_INVALID')
            Registry.model_validate(registry.body)
            inputs=[await s.get(InputRow,i) for i in input_ids]
            if any(i is None or (i.environment,i.scope,i.period)!=(state.environment,state.scope,state.period) for i in inputs):
                raise SourceError('SCOPE_UNPROVEN')
            if [i.kind for i in inputs]!=['RUNTIME','BILLING','PRICING']:raise SourceError('FINANCIAL_IMBALANCE')
            validate(inputs[1].body['components'],result['classified'])
            # Recompute the deterministic engine result at trust boundary; a valid
            # component set cannot disguise tampered summary/control/status data.
            from .reconciliation import reconcile
            expected=reconcile(inputs[0].body,inputs[1].body,inputs[2].body,Registry.model_validate(registry.body),
                inputs[2].body['quality'],self.policy)
            if expected!=result:raise SourceError('FINANCIAL_IMBALANCE')
            billing_manifest=await s.get(PublicationRow,inputs[1].source_generation)
            if billing_manifest is None or vector.get(billing_manifest.state_key)!=billing_manifest.generation:
                raise SourceError('INPUT_DETAIL_UNAVAILABLE')
            pricing_generations=set(inputs[2].body['generations'])
            if set(vector.values())!={billing_manifest.generation}|pricing_generations:
                raise SourceError('INPUT_DETAIL_UNAVAILABLE')
            for source_key,generation in sorted(vector.items()):
                source=await s.get(BillingState,source_key,with_for_update=True)
                if source is None or source.generation!=generation:return 'SUPERSEDED'
            previous=state.generation
            await s.execute(insert_for(s,GenerationRow).values(identity=identity,registry=registry_id,
                runtime_input=input_ids[0],billing_input=input_ids[1],pricing_input=input_ids[2],
                body=dict(result=result,source_vector=vector,input_ids=input_ids),published_at=now()).on_conflict_do_nothing())
            row=await s.get(GenerationRow,identity)
            if row.body!=dict(result=result,source_vector=vector,input_ids=input_ids):raise SourceError('FINANCIAL_IMBALANCE')
            await s.execute(update(StateRow).where(StateRow.key==key,StateRow.revision==revision).values(generation=identity,error=None,last_success=now()))
            return 'IDENTICAL' if previous==identity else 'PUBLISHED'
    async def current(self,environment,scope,start,end):
        async with self.sessions() as s:
            return list((await s.execute(select(StateRow,GenerationRow).outerjoin(GenerationRow,StateRow.generation==GenerationRow.identity)
                .where(StateRow.environment==environment,StateRow.scope==scope,StateRow.period>=start,StateRow.period<end)
                .order_by(StateRow.period).limit(32))).all())
    async def history(self,key):
        # Historical queries use immutable input links, not mutable current pointers.
        async with self.sessions() as s:
            state=await s.get(StateRow,key)
            return list((await s.scalars(select(GenerationRow).join(InputRow,GenerationRow.billing_input==InputRow.identity)
                .where(InputRow.environment==state.environment,InputRow.scope==state.scope,InputRow.period==state.period)
                .order_by(GenerationRow.published_at).limit(1000))).all())
