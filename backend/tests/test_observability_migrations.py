import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import inspect,text,CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable,CreateIndex
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.observability.database import Database
from backend.observability.models import Base


def migration():
    p=Path('alembic/versions/9f71c2a64e08_observability_storage.py')
    spec=importlib.util.spec_from_file_location('m7_migration',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_isolated_migration_upgrade_constraints_indexes_downgrade(tmp_path):
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'migration.sqlite'),isolated_test=True)
    module=migration();assert module.down_revision=='7c4e9a2d1f05'
    try:
        async with db.engine.begin() as conn:
            await conn.execute(text('CREATE TABLE sessions (id INTEGER PRIMARY KEY)'))
            await conn.execute(text('INSERT INTO sessions VALUES (1)'))
            def run(c):
                context=MigrationContext.configure(c)
                with Operations.context(context):module.upgrade()
                inspector=inspect(c)
                assert {name for name in Base.metadata.tables if not name.startswith('observability_sre_')}<=set(inspector.get_table_names())
                for table in (t for t in Base.metadata.sorted_tables if not t.name.startswith('observability_sre_')):
                    assert {col['name'] for col in inspector.get_columns(table.name)}==set(table.columns.keys())
                    assert {idx['name'] for idx in inspector.get_indexes(table.name)}=={idx.name for idx in table.indexes}
                    assert len(inspector.get_check_constraints(table.name))==len([t for t in table.constraints if isinstance(t,CheckConstraint)])
                with Operations.context(context):module.downgrade()
                assert inspect(c).get_table_names()==['sessions']
                with Operations.context(context):module.upgrade()
            await conn.run_sync(run)
            assert (await conn.execute(text('SELECT id FROM sessions'))).scalar_one()==1
    finally:await db.close()


def test_postgres_ddl_is_offline_and_application_owned():
    module=migration();assert module.revision=='9f71c2a64e08'
    ddl='\n'.join(str(CreateTable(t).compile(dialect=postgresql.dialect())) for t in Base.metadata.sorted_tables)
    ddl+='\n'.join(str(CreateIndex(i).compile(dialect=postgresql.dialect())) for t in Base.metadata.sorted_tables for i in t.indexes)
    assert 'TIMESTAMP WITH TIME ZONE' in ddl
    assert 'observability_run_status' in ddl and 'observability_turn' in ddl and 'observability_run_event' in ddl
    assert 'prompt' not in ddl and 'estimated_cost' not in ddl and 'user_states' not in ddl
    assert 'state_version >= 0' in ddl


def test_database_production_constructor_rejects_sqlite():
    with pytest.raises(ValueError):Database('sqlite+aiosqlite:///:memory:')


@pytest.mark.asyncio
async def test_actual_alembic_environment_isolated_upgrade_downgrade(tmp_path,monkeypatch):
    import asyncio
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from backend.config.settings import Settings
    target='sqlite+aiosqlite:///'+str(tmp_path/'alembic.sqlite')
    monkeypatch.setattr('backend.config.settings.get_settings',lambda:Settings({'SLOPANOC_DATABASE_URL':target}))
    cfg=Config();cfg.set_main_option('script_location','alembic')
    assert ScriptDirectory.from_config(cfg).get_current_head()=='b37e90a14c62'
    db=Database(target,isolated_test=True)
    try:
        async with db.engine.begin() as c:
            await c.execute(text('CREATE TABLE unrelated (id INTEGER PRIMARY KEY)'))
            await c.execute(text('INSERT INTO unrelated VALUES (17)'))
        # Existing parent uses Postgres-only timestamp ALTER; this isolated fixture
        # stamps the verified parent then applies only the new additive revision.
        await asyncio.to_thread(command.stamp,cfg,'7c4e9a2d1f05')
        await asyncio.to_thread(command.upgrade,cfg,'head')
        async with db.engine.connect() as c:
            tables=await c.run_sync(lambda s:inspect(s).get_table_names())
            assert set(Base.metadata.tables)<=set(tables)
            assert (await c.execute(text('SELECT id FROM unrelated'))).scalar_one()==17
        await asyncio.to_thread(command.downgrade,cfg,'7c4e9a2d1f05')
        async with db.engine.connect() as c:
            assert not set(Base.metadata.tables)&set(await c.run_sync(lambda s:inspect(s).get_table_names()))
            assert (await c.execute(text('SELECT id FROM unrelated'))).scalar_one()==17
        await asyncio.to_thread(command.upgrade,cfg,'head')
    finally:await db.close()


def test_actual_postgres_migration_chain_offline(monkeypatch):
    import io
    from alembic import command
    from alembic.config import Config
    from backend.config.settings import Settings
    # Explicit fake offline target, no Secret Manager/DSN resolution or connection.
    monkeypatch.setattr('backend.config.settings.get_settings',lambda:Settings({'SLOPANOC_DATABASE_URL':'postgresql+asyncpg://unused:unused@127.0.0.1/unused'}))
    buffer=io.StringIO();cfg=Config(output_buffer=buffer);cfg.set_main_option('script_location','alembic')
    command.upgrade(cfg,'head',sql=True)
    ddl=buffer.getvalue();assert 'CREATE TABLE observability_run_status' in ddl
    assert 'TIMESTAMP WITH TIME ZONE' in ddl
    assert 'DROP TABLE sessions' not in ddl and 'unused:unused' not in ddl
    buffer.seek(0);buffer.truncate()
    command.downgrade(cfg,'9f71c2a64e08:7c4e9a2d1f05',sql=True)
    assert 'DROP TABLE observability_run_status' in buffer.getvalue()
    assert 'DROP TABLE slopanoc_cases' not in buffer.getvalue()
