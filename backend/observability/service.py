"""Protected query policy: bounded inputs, pagination, freshness and field allowlists."""
import asyncio
import base64
import hashlib
import json
import time
from datetime import datetime, timedelta
from collections import OrderedDict
from uuid import UUID
from pydantic import TypeAdapter
from .schemas import Permission, Identifier
from .authorization import denied
from .api_models import RunView, RunPage, TechnicalView, DependencyView, TimelinePage, HealthView
from .projection import utcnow, utc
from .retention import classification
from .stages import TERMINAL_STATUSES, RunStatus, Stage


def invalid():
    return denied('validation_error','Invalid diagnostic query.')


class Service:
    def __init__(self, repository, config, coordinator=None, runtime=None):
        self.repository,self.config,self.coordinator,self.runtime = repository,config,coordinator,runtime
        self.reads = asyncio.Semaphore(4)
        self.rates = OrderedDict()

    def admit(self, principal):
        now = time.monotonic()
        expired = [k for k,(start,count) in self.rates.items() if now-start>=60]
        for k in expired: self.rates.pop(k,None)
        start,count = self.rates.get(principal.subject,(now,0))
        if count>=60 or principal.subject not in self.rates and len(self.rates)>=2048:
            raise denied('rate_limited','Diagnostic request limit reached.')
        self.rates[principal.subject] = (start,count+1)

    async def query(self, method, *args, **kwargs):
        if self.repository is None:
            raise denied('connector_unavailable','Diagnostics persistence is unavailable.')
        try:
            async with asyncio.timeout(2), self.reads:
                return await getattr(self.repository,method)(*args,**kwargs)
        except Exception as exc:
            from backend.gateway.safe_error import SafeErrorException
            if isinstance(exc,SafeErrorException): raise
            raise denied('connector_unavailable','Diagnostics persistence is unavailable.') from None

    def environment_scope(self,principal,environment=None):
        if environment and environment not in principal.environments:
            raise denied('authorization_error','Diagnostic scope is not permitted.')
        return tuple(sorted(principal.environments)) if environment is None else (environment,)

    def run_id(self,value):
        try:
            if len(value)!=36 or str(UUID(value))!=value: raise ValueError()
        except (ValueError,TypeError): raise invalid() from None
        return value

    def view(self,state,principal,now):
        kind = classification(state,now,self.config)
        technical = None
        if Permission.TECHNICAL_DIAGNOSTICS in principal.capabilities:
            technical = TechnicalView(**{k:getattr(state,k) for k in TechnicalView.model_fields})
        elapsed = state.elapsed_ms if state.status in TERMINAL_STATUSES or kind=='STALE' else max(state.elapsed_ms,(now-state.started_at).total_seconds()*1000)
        return RunView(run_id=state.run_id,environment=state.environment,status=state.status,
            classification=kind,outcome_unknown=kind=='STALE',classification_as_of=now,
            started_at=state.started_at,terminal_at=state.terminal_at,current_stage=state.current_stage,
            current_agent=state.current_agent,current_tool=state.current_tool,
            dependencies=tuple(DependencyView(dependency=d.dependency,operation=d.operation,agent=d.agent,
                tool=d.tool,started_at=d.started_at) for d in state.dependencies),
            elapsed_ms=max(0,elapsed),last_progress_at=state.last_progress_at,heartbeat_at=state.heartbeat_at,
            progress_age_ms=max(0,(now-state.last_progress_at).total_seconds()*1000),
            work_deadline_at=state.work_deadline_at,total_deadline_at=state.total_deadline_at,
            error_code=state.error_code,cleanup_status=state.cleanup_status,delivery_status=state.delivery_status,
            timeline_truncated_count=state.timeline_truncated_count,technical=technical)

    async def run(self,identity,principal):
        identity=self.run_id(identity)
        state=await self.query('get',identity,self.environment_scope(principal))
        if state is None: raise denied('not_found','Run is unavailable.')
        return self.view(state,principal,utcnow())

    def cursor(self,value,scope):
        if not value: return None
        try:
            if len(value)>512: raise ValueError()
            data=json.loads(base64.b64decode(value.encode(),altchars=b'-_',validate=True))
            if not isinstance(data,dict) or data.get('scope')!=scope: raise ValueError()
            return data
        except Exception: raise invalid() from None

    def encode(self,**values):
        return base64.urlsafe_b64encode(json.dumps(values,separators=(',',':')).encode()).decode()

    async def listing(self,principal,*,limit=50,cursor=None,active=False,status=None,stage=None,
                      environment=None,session_id=None,since=None,until=None):
        if not 1<=limit<=100: raise invalid()
        environments=self.environment_scope(principal,environment)
        try:
            status=RunStatus(status).value if status else None
            stage=Stage(stage).value if stage else None
            if session_id is not None: session_id=TypeAdapter(Identifier).validate_python(session_id)
            for value in (since,until):
                if value is not None and value.tzinfo is None: raise ValueError()
            now=utcnow()
            if since and until and (until<since or until-since>timedelta(days=90)): raise ValueError()
        except Exception: raise invalid() from None
        # Fixed filter scope plus principal environment scope, no dynamic query expressions.
        scope=hashlib.sha256(json.dumps([active,status,stage,environments,session_id,
            since.isoformat() if since else None,until.isoformat() if until else None]).encode()).hexdigest()[:16]
        # Defaults move between pages; store them in cursor, then recompute scope below.
        data=self.cursor(cursor,scope) if cursor else None
        after=None
        if data:
            try:
                if set(data)!={'scope','at','id','as_of'}: raise ValueError()
                after=(datetime.fromisoformat(data['at']),self.run_id(data['id']))
                as_of=datetime.fromisoformat(data['as_of'])
                if after[0].tzinfo is None or as_of.tzinfo is None or after[0]>as_of: raise ValueError()
            except Exception: raise invalid() from None
        else: as_of=now
        since=since or as_of-timedelta(days=90)
        until=until or as_of
        if until<since or until-since>timedelta(days=90): raise invalid()
        rows=await self.query('list_runs',environments,limit=limit+1,after=after,as_of=as_of,active=active,
            status=status,stage=stage,session_id=session_id,since=since,until=until)
        rows=[r for r in rows if r is not None]
        next_cursor=None
        if len(rows)>limit:
            last=rows[limit-1]
            next_cursor=self.encode(scope=scope,at=last.started_at.isoformat(),id=last.run_id,as_of=as_of.isoformat())
        return RunPage(items=tuple(self.view(r,principal,now) for r in rows[:limit]),next_cursor=next_cursor,as_of=as_of)

    async def timeline(self,identity,principal,limit=50,cursor=None):
        if not 1<=limit<=100: raise invalid()
        await self.run(identity,principal)  # Permission scope/existence before timeline query.
        scope=hashlib.sha256(('timeline:'+identity+':'+','.join(sorted(principal.environments))).encode()).hexdigest()[:16]
        data=self.cursor(cursor,scope)
        after=0
        if data:
            if set(data)!={'scope','seq'} or type(data.get('seq')) is not int or not 0<=data['seq']<=2**63-1:
                raise invalid()
            after=data['seq']
        events=await self.query('timeline',identity,after,limit+1)
        state=await self.query('get',identity,self.environment_scope(principal))
        return TimelinePage(items=events[:limit],next_cursor=self.encode(scope=scope,seq=events[limit-1].event_seq) if len(events)>limit else None,
            truncated_count=state.timeline_truncated_count if state else 0)

    async def health(self,principal):
        self.environment_scope(principal,self.config.otel_environment)
        now=utcnow();counts={};last=None;pending=admitted=terminal=0
        c=self.coordinator
        if c:
            counts,last=c.health.snapshot()
            with c.lock:
                pending=len(c.slots);admitted=len(c.admitted)
                terminal=sum(s.state.status in TERMINAL_STATUSES for s in c.slots.values())
        persistence='disabled' if not self.config.projection_enabled else 'unavailable'
        stale=None
        if self.repository:
            try:
                stale=await self.query('stale_count',self.environment_scope(principal),now)
                persistence='degraded' if counts.get('failed') or counts.get('rejected') or counts.get('invalid') else 'healthy'
            except Exception:
                persistence='unavailable'
        exporter='disabled' if not self.config.otel_enabled else 'unknown'
        if self.runtime and self.runtime.enabled:
            export_health=self.runtime.health.snapshot()
            exporter='degraded' if any(v['failed'] for v in export_health.values()) else 'healthy' if any(v['exported'] for v in export_health.values()) else 'unknown'
        return HealthView(persistence=persistence,as_of=now,pending=pending,admitted=admitted,terminal_pending=terminal,
            stale_records=stale,exporter_state=exporter,collector_configured=self.config.otel_exporter_otlp_endpoint is not None,
            last_success=last,**counts)
