"""Explicitly approved LOCAL disposable schema rehearsal. Never accepts a database URL.

Creates one loopback-only, randomly named Docker container, applies the real migration
history, exercises the proposed roles with fabricated reference facts, records metadata
only, and removes its own container/anonymous volume even on failure. No cloud clients.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
IMAGE = 'pgvector/pgvector@sha256:2358fcba361ed2233a5ed81b5fe4ca779ccb304120ce531a3bf51c0ed7e2bc11'
RECEIPT = ROOT / 'docs/Telemetry/validation/EXECUTION_JOURNAL_POSTGRES_REHEARSAL.json'


def docker(*args, check=True):
    return subprocess.run(['docker', *args], check=check, capture_output=True, text=True)


def migrate(url, revision):
    # Patch ONLY the public URL resolver inside this subprocess. It cannot resolve
    # inherited Secret Manager settings or a developer's shared database URL.
    code = """import sys
from backend.config import settings
class LocalOnly:
    def resolve_database_url(self): return sys.argv[1]
settings.get_settings=lambda: LocalOnly()
from alembic.config import Config
from alembic import command
command.upgrade(Config('alembic.ini'),sys.argv[2])
"""
    result = subprocess.run([sys.executable, '-c', code, url, revision], cwd=ROOT,
                            capture_output=True, text=True)
    if result.returncode:
        # URL/password are never included in receipts or errors.
        raise RuntimeError('Local migration failed: ' + result.stderr.replace(url, '[LOCAL_URL]')[-6000:])
    return result.stderr


async def verify(port, password, receipt):
    import asyncpg
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import select
    from backend.observability.hybrid.capture import Capture, make_event
    from backend.observability.hybrid.config import HybridConfig
    from backend.observability.hybrid.models import AuditRow, OutboxRow, HybridBase
    from backend.observability.hybrid.store import OutboxStore, ProjectorStore
    from backend.observability.projection import utcnow

    async def connect(user='postgres', pw=password):
        return await asyncpg.connect(host='127.0.0.1', port=port, database='slopanoc', user=user, password=pw)
    owner = await connect()
    try:
        receipt['postgresql_version']=await owner.fetchval('SHOW server_version')
        assert receipt['postgresql_version'].startswith('18.')
        receipt['vector_version']=await owner.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")
        async def schema():
            rows = await owner.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            indexes = await owner.fetch("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY indexname")
            constraints = await owner.fetch("SELECT c.conname,c.contype,r.relname table_name,pg_get_constraintdef(c.oid) definition FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace JOIN pg_class r ON r.oid=c.conrelid WHERE n.nspname='public' ORDER BY c.conname,c.oid")
            return {'tables':[r['tablename'] for r in rows], 'indexes':[dict(r) for r in indexes], 'constraints':[dict(r) for r in constraints]}
        before = await schema()
        expected = sorted({t.name for t in HybridBase.metadata.tables.values()})
        assert set(expected) <= set(before['tables'])
        expected_indexes={index.name for table in HybridBase.metadata.tables.values() for index in table.indexes}
        assert expected_indexes <= {r['indexname'] for r in before['indexes']}
        from sqlalchemy import CheckConstraint, ForeignKeyConstraint
        for table in HybridBase.metadata.tables.values():
            observed=[r for r in before['constraints'] if r['table_name']==table.name]
            for cls,code in ((CheckConstraint,b'c'),(ForeignKeyConstraint,b'f')):
                expected_count=sum(isinstance(c,cls) for c in table.constraints)
                assert sum(r['contype']==code for r in observed)==expected_count,(table.name,cls)
        receipt['hybrid_index_fk_check_comparison']='PASS'
        from alembic.script import ScriptDirectory
        from alembic.config import Config
        graph = ScriptDirectory.from_config(Config(str(ROOT/'alembic.ini')))
        assert graph.get_heads() == ['e27b2c8d0f51']
        assert await owner.fetchval('SELECT version_num FROM alembic_version') == 'e27b2c8d0f51'
        migrations = len(list(graph.walk_revisions()))
        assert migrations == 19
        # Require EVERY application metadata table, including M7-M15.
        from backend.observability.finops.models import FinOpsBase
        from backend.observability.finops.billing_models import BillingBase
        from backend.observability.finops.reconciliation_models import ReconciliationBase
        from backend.observability.finops.unit_economics_models import UnitEconomicsBase
        from backend.observability.finops.control_models import ControlBase
        from backend.observability.models import Base
        from backend.observability import slo_rollups, policy_models
        all_expected = {t.name for b in (Base,FinOpsBase,BillingBase,ReconciliationBase,UnitEconomicsBase,ControlBase,HybridBase) for t in b.metadata.tables.values()}
        assert all_expected <= set(before['tables']), sorted(all_expected-set(before['tables']))
        url = f'postgresql+asyncpg://postgres:{password}@127.0.0.1:{port}/slopanoc'
        repeat = await asyncio.to_thread(migrate, url, 'head')
        assert 'Running upgrade' not in repeat
        assert await schema() == before
        receipt.update(head='e27b2c8d0f51', revision_count=migrations, schema=before,
                       second_upgrade='NO_OP', application_observability_tables=sorted(all_expected))
        grants = (ROOT/'docs/Telemetry/validation/HYBRID_PHASE1_DATABASE_ROLES.sql').read_text()
        await owner.execute(grants)
        role_attrs=await owner.fetch("SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname LIKE 'slopanoc_hybrid_%' ORDER BY rolname")
        assert len(role_attrs)==4 and all(not any(row[key] for key in ('rolcanlogin','rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')) for row in role_attrs)
        receipt['runtime_role_attributes']=[dict(r) for r in role_attrs]
        await owner.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        users={}
        for role in ('anoc','publisher','projector','reader'):
            name='rehearsal_'+role; pw=secrets.token_hex(16)
            await owner.execute(f"CREATE ROLE {name} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
            await owner.execute(f'GRANT slopanoc_hybrid_{role} TO {name}')
            users[role]=(name,pw)
        role_checks=[]
        async def denied(role, sql, code='42501'):
            c=await connect(*users[role])
            try:
                try: await c.execute(sql)
                except asyncpg.PostgresError as exc:
                    assert exc.sqlstate == code, (role,exc.sqlstate)
                    role_checks.append({'role':role,'operation':sql.split()[0], 'sqlstate':code})
                else: raise AssertionError('Unexpected permission: '+role+' '+sql.split()[0])
            finally: await c.close()
        engines={}
        factories={}
        for role,(name,pw) in users.items():
            engine=create_async_engine(f'postgresql+asyncpg://{name}:{pw}@127.0.0.1:{port}/slopanoc')
            engines[role]=engine; factories[role]=async_sessionmaker(engine,expire_on_commit=False)
        try:
            cfg=HybridConfig(environment='development')
            now=utcnow(); run=str(uuid4())
            event=make_event('run.accepted','development','run',run,1,now,status='RUNNING',run_id=run,
                             session_id=str(uuid4()),turn_id='rehearsal-turn',trace_id='a'*32)
            async with factories['anoc']() as s,s.begin():
                assert await Capture().append(s,event)
                assert not await Capture().append(s,event)
            rollback_run=str(uuid4())
            rollback=make_event('run.accepted','development','run',rollback_run,1,now,status='RUNNING',run_id=rollback_run)
            # A real rollback proves paired inserts cannot escape their caller's transaction.
            async with factories['anoc']() as s:
                await s.begin(); await Capture().append(s,rollback); await s.rollback()
            assert await owner.fetchval('SELECT count(*) FROM observability_critical_audit') == 1
            assert await owner.fetchval('SELECT count(*) FROM observability_audit_outbox') == 1
            outbox=OutboxStore(factories['publisher'],cfg)
            claims=await outbox.claim(); assert len(claims)==1
            captured,token,_=claims[0]
            assert await outbox.settle(captured.event_id,token,success=True)
            assert not await outbox.settle(captured.event_id,token,success=True)
            projector=ProjectorStore(factories['projector'],cfg)
            assert await projector.apply(captured)=='PROCESSED'
            assert await projector.apply(captured)=='DUPLICATE'
            assert await projector.failure('INVALID')=='DEAD'
            async with factories['reader']() as s:
                assert (await s.scalar(select(AuditRow.event_id))) == str(event.event_id)
                assert (await s.scalar(select(OutboxRow.publication_state))) == 'PUBLISHED'
            reader=await connect(*users['reader'])
            try:
                for table in all_expected:
                    # Review grants intentionally exclude M15 policy, which reader does not expose.
                    if table.startswith(('finops_','ai_usage_')):
                        await reader.fetch(f'SELECT * FROM public.{table} LIMIT 0')
            finally:await reader.close()
            from backend.execution_audit.journal import Journal,event as journal_event
            from backend.execution_audit.read import AuditReadRepository
            tool_execution_id=str(uuid4())
            journal_fact=journal_event('tool.execution.started','development',tool_execution_id,
                consistency_class='C',outcome='STARTED',tool_execution_id=tool_execution_id,
                tool_name='teams_get_messages',run_id=run)
            assert await Journal(factories['anoc'],'development').append(journal_fact)
            assert await projector.apply(journal_fact)=='PROCESSED'
            page=await AuditReadRepository(factories['reader']).timeline('development',run)
            assert len(page.events)==2
            # Action intent insert under ANOC role and role-isolated read.
            anoc=await connect(*users['anoc'])
            try:
                await anoc.execute("INSERT INTO execution_action_record (execution_id,environment,authorization_id,action_type,state,attempt,started_at,updated_at) VALUES ($1,'development',$2,'teams.sendMessage','AUTHORIZED',1,now(),now())",str(uuid4()),str(uuid4()))
            finally:await anoc.close()
            await denied('publisher','SELECT * FROM execution_action_record')
            await denied('projector',"UPDATE execution_action_record SET state='COMPLETED'")
            await denied('reader',"UPDATE execution_action_record SET state='COMPLETED'")
            try:await owner.execute("UPDATE observability_critical_audit SET producer='anoc'")
            except asyncpg.PostgresError as exc:assert exc.sqlstate=='P0001'
            else:raise AssertionError('Journal immutability trigger missing')
            receipt['journal_timeline_action_role_and_immutability']='PASS'
            for role in users:
                await denied(role,'CREATE TABLE public.rehearsal_forbidden (id integer)')
                await denied(role,'DELETE FROM observability_critical_audit')
                await denied(role,'SELECT * FROM slopanoc_cases')
            await denied('anoc',"UPDATE observability_critical_audit SET producer='forbidden'")
            await denied('publisher',"UPDATE observability_audit_outbox SET payload_hash='forbidden'")
            await denied('projector',"UPDATE observability_audit_outbox SET attempt_count=0")
            await denied('reader',"UPDATE observability_run_projection SET status='FAILED'")
            await denied('reader',"INSERT INTO observability_projection_failure SELECT * FROM observability_projection_failure")
            # Constraint checks run as owner to distinguish constraints from permissions.
            for sql,code in [("UPDATE observability_audit_outbox SET publication_state='INVALID'",'23514'),
                             ("UPDATE observability_projection_receipt SET duplicate_count=-1",'23514'),
                             ("UPDATE observability_audit_outbox SET event_id='00000000-0000-0000-0000-000000000000' WHERE event_id=(SELECT event_id FROM observability_audit_outbox ORDER BY event_id LIMIT 1)",'23503')]:
                try: await owner.execute(sql)
                except asyncpg.PostgresError as exc: assert exc.sqlstate==code
                else: raise AssertionError('Constraint accepted invalid value')
            receipt.update(roles='PASS', role_denials=role_checks,
                           real_runtime_capture_delivery_projection='PASS', rollback='PASS',
                           duplicate_capture_and_projection='PASS', constraints='PASS',
                           role_sql_sha256=hashlib.sha256(grants.encode()).hexdigest())
        finally:
            for engine in engines.values(): await engine.dispose()
    finally: await owner.close()


def main():
    if '--approved-local-disposable' not in sys.argv:
        raise SystemExit('Requires explicit local disposable database authorization.')
    name='slopanoc-audit-rehearsal-'+secrets.token_hex(6)
    password=secrets.token_hex(24)
    receipt={'scope':'LOCAL DISPOSABLE SCHEMA ONLY; fabricated test references; no customer data',
             'image':IMAGE,'started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'status':'FAIL'}
    created=False
    try:
        docker('run','--detach','--name',name,'--label','slopanoc.purpose=audit-schema-rehearsal',
               '--publish','127.0.0.1::5432','--env','POSTGRES_DB=slopanoc',
               '--env','POSTGRES_PASSWORD='+password,IMAGE)
        created=True
        for _ in range(120):
            if docker('exec',name,'pg_isready','-U','postgres','-d','slopanoc',check=False).returncode==0: break
            time.sleep(.25)
        else: raise RuntimeError('Local PostgreSQL did not become ready')
        binding=json.loads(docker('inspect','--format','{{json .NetworkSettings.Ports}}',name).stdout)['5432/tcp'][0]
        assert binding['HostIp']=='127.0.0.1';port=int(binding['HostPort'])
        url=f'postgresql+asyncpg://postgres:{password}@127.0.0.1:{port}/slopanoc'
        migrate(url,'d3f8b1c6a942')
        receipt['historical_reconstruction']='d3f8b1c6a942'
        migrate(url,'head')
        asyncio.run(verify(port,password,receipt))
        receipt['status']='PASS'
    except Exception as exc:
        receipt['failure']=str(exc).replace(password,'[LOCAL_PASSWORD]')[:6000]
        raise
    finally:
        if created:
            docker('rm','--force','--volumes',name)
            receipt['owned_container_and_volume_removed']=docker('inspect',name,check=False).returncode!=0
        receipt['finished_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        RECEIPT.write_text(json.dumps(receipt,indent=2,default=lambda value: value.decode() if isinstance(value,bytes) else str(value))+'\n')
        print(json.dumps({k:receipt.get(k) for k in ('status','head','revision_count','second_upgrade','roles','owned_container_and_volume_removed')}))

if __name__=='__main__':main()
