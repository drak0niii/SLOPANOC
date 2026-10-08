"""Transactional operational projection; guarded writes never drive business state."""
from datetime import timedelta
from sqlalchemy import select, update, delete, and_, or_, union_all, func
from .models import RunRow, SummaryRow, EventRow
from .projection import RunState, DiagnosticEvent, utcnow, retain_events
from .retention import expiry
from .stages import TERMINAL_STATUSES

TERMINALS = tuple(s.value for s in TERMINAL_STATUSES)
SUMMARY_FIELDS = tuple(c.name for c in SummaryRow.__table__.columns if c.name not in ('expiry_at','session_detached'))
CANONICAL = ('status','terminal_at','started_at','error_code','error_stage','elapsed_ms','current_stage','trace_id','root_span_id','turn_id','turn_id_origin')


class Repository:
    def __init__(self, sessions, config):
        self.sessions, self.config = sessions, config

    def insert(self, session, model):
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        dialect = session.bind.dialect.name
        if dialect not in ('postgresql','sqlite'):
            raise ValueError('Unsupported projection dialect')
        return (pg_insert if dialect == 'postgresql' else sqlite_insert)(model)

    async def persist(self, state):
        # Revalidate at the persistence sink, including frozen nested objects.
        state = RunState.model_validate(state.model_dump(mode='json'))
        values = state.model_dump(mode='python', exclude={'events'})
        # JSON contains only the strict detached DependencyState schema.
        values['dependencies'] = [d.model_dump(mode='json') for d in state.dependencies]
        values['expiry_at'] = state.terminal_at + timedelta(hours=self.config.projection_terminal_hours) if state.terminal_at else expiry(state,self.config)
        async with self.sessions() as session, session.begin():
            saved = (await session.execute(select(SummaryRow).where(SummaryRow.run_id==state.run_id).with_for_update())).scalar_one_or_none()
            if saved is not None:
                if saved.session_detached:
                    values['session_id'] = None
                    values['session_detached'] = True
                if saved.producer_instance_id != state.producer_instance_id:
                    return 'owner_conflict'
                if state.status.value not in TERMINALS or any(getattr(saved,k) != values[k] for k in CANONICAL):
                    return 'terminal_conflict'
                current = (await session.execute(select(RunRow).where(RunRow.run_id==state.run_id))).scalar_one_or_none()
                if current is None:
                    if state.state_version <= saved.state_version:
                        return 'duplicate'
                    old_events = (await session.execute(select(EventRow).where(EventRow.run_id==state.run_id).order_by(EventRow.event_seq))).scalars().all()
                    old_seq = max((e.event_seq for e in old_events),default=0)
                    if any(e.event_seq>old_seq and (e.event_type not in ('cleanup','delivery') or e.status != state.status or e.stage != state.current_stage) for e in state.events):
                        return 'terminal_conflict'
                    old_dtos = tuple(DiagnosticEvent.model_validate({k:getattr(e,k) for k in DiagnosticEvent.model_fields}) for e in old_events)
                    safe_events, _ = retain_events((*old_dtos,*(e for e in state.events if e.event_seq>old_seq)))
                    state = state.model_copy(update={'events':safe_events})
                    changes = {k:values[k] for k in ('cleanup_status','delivery_status','state_version','timeline_truncated_count')}
                    await session.execute(update(SummaryRow).where(SummaryRow.run_id==state.run_id,SummaryRow.state_version<state.state_version).values(**changes))
                    await session.execute(delete(EventRow).where(EventRow.run_id==state.run_id))
                    if state.events:
                        await session.execute(self.insert(session,EventRow),[dict(run_id=state.run_id,**e.model_dump(mode='python')) for e in state.events])
                    return 'persisted'
            ins = await session.execute(self.insert(session, RunRow).values(**values).on_conflict_do_nothing(index_elements=['run_id']))
            accepted = ins.rowcount == 1
            if not accepted:
                row = (await session.execute(select(RunRow).where(RunRow.run_id==state.run_id).with_for_update())).scalar_one()
                if row.session_detached:
                    values['session_id'] = None
                    values['session_detached'] = True
                if row.producer_instance_id != state.producer_instance_id:
                    return 'owner_conflict'
                if row.status in TERMINALS:
                    if state.status.value not in TERMINALS or any(getattr(row,k) != values[k] for k in CANONICAL):
                        return 'terminal_conflict'
                    if state.state_version <= row.state_version:
                        return 'duplicate'
                    # Only owner observations may advance cleanup/delivery, never lifecycle.
                    old_events = (await session.execute(select(EventRow).where(EventRow.run_id==state.run_id).order_by(EventRow.event_seq))).scalars().all()
                    old_seq = max((e.event_seq for e in old_events), default=0)
                    if any(e.event_seq>old_seq and (e.event_type not in ('cleanup','delivery') or e.status != state.status or e.stage != state.current_stage) for e in state.events):
                        return 'terminal_conflict'
                    old_dtos = tuple(DiagnosticEvent.model_validate({k:getattr(e,k) for k in DiagnosticEvent.model_fields}) for e in old_events)
                    safe_events, _ = retain_events((*old_dtos,*(e for e in state.events if e.event_seq>old_seq)))
                    state = state.model_copy(update={'events':safe_events})
                    changes = {k:values[k] for k in ('cleanup_status','delivery_status','state_version','timeline_truncated_count')}
                    await session.execute(update(RunRow).where(RunRow.run_id==state.run_id, RunRow.state_version<state.state_version).values(**changes))
                    await session.execute(update(SummaryRow).where(SummaryRow.run_id==state.run_id).values(**changes))
                    accepted = True
                else:
                    if row.started_at != state.started_at or row.session_id is not None and row.session_id != values['session_id']:
                        return 'identity_conflict'
                    if row.turn_id is not None and (row.turn_id != state.turn_id or row.turn_id_origin != state.turn_id_origin):
                        return 'identity_conflict'
                    result = await session.execute(update(RunRow).where(RunRow.run_id==state.run_id,
                        RunRow.producer_instance_id==state.producer_instance_id,
                        RunRow.state_version<state.state_version, RunRow.status.not_in(TERMINALS)).values(**values))
                    accepted = result.rowcount == 1
            if not accepted:
                return 'stale'
            if state.status in TERMINAL_STATUSES:
                summary = {k:values[k] for k in SUMMARY_FIELDS}
                summary['session_detached'] = values.get('session_detached',False)
                summary['expiry_at'] = expiry(state,self.config)
                await session.execute(self.insert(session, SummaryRow).values(**summary).on_conflict_do_nothing(index_elements=['run_id']))
            # Publisher sends a cumulative bounded significant timeline, never raw traces.
            await session.execute(delete(EventRow).where(EventRow.run_id==state.run_id))
            if state.events:
                await session.execute(self.insert(session, EventRow), [dict(run_id=state.run_id, **e.model_dump(mode='python')) for e in state.events])
            return 'persisted'

    def decode(self, row):
        fields = RunState.model_fields
        data = {k:getattr(row,k) for k in fields if k != 'events' and hasattr(row,k)}
        if isinstance(row, SummaryRow):
            data.update(stage_started_at=row.terminal_at, last_progress_at=row.terminal_at,
                heartbeat_at=row.terminal_at, observed_at=row.terminal_at)
        return RunState.model_validate(data)

    async def get(self, run_id, environments):
        async with self.sessions() as s:
            for model in (RunRow, SummaryRow):
                row = (await s.execute(select(model).where(model.run_id==run_id, model.environment.in_(environments)))).scalar_one_or_none()
                if row:
                    return self.decode(row)
        return None

    async def timeline(self, run_id, after, limit):
        async with self.sessions() as s:
            rows = (await s.execute(select(EventRow).where(EventRow.run_id==run_id, EventRow.event_seq>after)
                .order_by(EventRow.event_seq).limit(limit))).scalars().all()
            return tuple(DiagnosticEvent.model_validate({k:getattr(row,k) for k in DiagnosticEvent.model_fields}) for row in rows)

    async def list_runs(self, environments, *, limit, after=None, as_of=None, active=False,
                        status=None, stage=None, session_id=None, since=None, until=None):
        # Union IDs only, eliminating terminal duplicates across the two retention tiers.
        predicates = []
        for model in (RunRow, SummaryRow):
            cond = [model.environment.in_(environments), model.started_at <= (as_of or utcnow())]
            if active:
                cond.append(model.status.not_in(TERMINALS))
            if status: cond.append(model.status==status)
            if stage: cond.append(model.current_stage==stage)
            if session_id: cond.append(model.session_id==session_id)
            if since: cond.append(model.started_at>=since)
            if until: cond.append(model.started_at<=until)
            if after:
                at, identity = after
                cond.append(or_(model.started_at<at, and_(model.started_at==at, model.run_id<identity)))
            if model is SummaryRow:
                cond.append(~select(RunRow.run_id).where(RunRow.run_id==SummaryRow.run_id).exists())
            predicates.append(select(model.run_id.label('run_id'),model.started_at.label('started_at')).where(*cond))
        query = union_all(*predicates).subquery()
        async with self.sessions() as s:
            ids = (await s.execute(select(query.c.run_id).order_by(query.c.started_at.desc(),query.c.run_id.desc()).limit(limit))).scalars().all()
        return [await self.get(identity,environments) for identity in ids]

    async def cleanup(self, now, batch=500):
        if not 1 <= batch <= 1000:
            raise ValueError('Invalid cleanup batch')
        async with self.sessions() as s, s.begin():
            terminal_ids = (await s.execute(select(RunRow.run_id).where(RunRow.terminal_at.is_not(None),RunRow.expiry_at<=now)
                .order_by(RunRow.expiry_at,RunRow.run_id).limit(batch))).scalars().all()
            summary_ids = (await s.execute(select(SummaryRow.run_id).where(SummaryRow.expiry_at<=now)
                .order_by(SummaryRow.expiry_at,SummaryRow.run_id).limit(batch))).scalars().all()
            stale_ids = (await s.execute(select(RunRow.run_id).where(RunRow.terminal_at.is_(None),RunRow.expiry_at<=now)
                .order_by(RunRow.expiry_at,RunRow.run_id).limit(batch))).scalars().all()
            # Version-independent deletion remains guarded by expiry at mutation time.
            for ids, model in ((terminal_ids+stale_ids,RunRow),(summary_ids,SummaryRow)):
                if ids:
                    await s.execute(delete(model).where(model.run_id.in_(ids),model.expiry_at<=now))
            ids = summary_ids+stale_ids
            if ids:
                await s.execute(delete(EventRow).where(EventRow.run_id.in_(ids),
                    ~select(RunRow.run_id).where(RunRow.run_id==EventRow.run_id).exists(),
                    ~select(SummaryRow.run_id).where(SummaryRow.run_id==EventRow.run_id).exists()))
            return len(terminal_ids)+len(summary_ids)+len(stale_ids)

    async def detach_session(self, session_id):
        async with self.sessions() as s, s.begin():
            for model in (RunRow,SummaryRow):
                await s.execute(update(model).where(model.session_id==session_id).values(session_id=None,session_detached=True))

    async def stale_count(self, environments, now):
        from datetime import timedelta
        threshold = now-timedelta(seconds=self.config.projection_stale_seconds+self.config.projection_clock_grace_seconds)
        deadline = now-timedelta(seconds=self.config.projection_clock_grace_seconds)
        async with self.sessions() as s:
            return (await s.execute(select(func.count()).select_from(RunRow).where(
                RunRow.environment.in_(environments),RunRow.status.not_in(TERMINALS),
                or_(RunRow.heartbeat_at<threshold,RunRow.total_deadline_at<deadline)))).scalar_one()
