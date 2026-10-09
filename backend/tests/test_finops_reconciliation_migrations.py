import importlib.util
import pytest
from sqlalchemy import inspect,CheckConstraint,text
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects.postgresql import dialect
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic.script import ScriptDirectory
from backend.observability.database import Database
from backend.observability.finops.reconciliation_models import ReconciliationBase

@pytest.mark.asyncio
async def test_additive_frozen_migration_isolated_constraints_and_postgres_ddl(tmp_path):
    spec=importlib.util.spec_from_file_location('m12','alembic/versions/e12c6d8a4b50_finops_reconciliation.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.down_revision=='d11b4a8c9e32'
    config=Config();config.set_main_option('script_location','alembic');assert ScriptDirectory.from_config(config).get_heads()==['b15e3f7a9c81']
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'migration.db'),isolated_test=True)
    try:
        async with db.engine.begin() as c:
            await c.execute(text('CREATE TABLE source_sentinel (id INTEGER PRIMARY KEY)'))
            await c.execute(text('INSERT INTO source_sentinel VALUES (42)'))
            def run(conn):
                with Operations.context(MigrationContext.configure(conn)):m.upgrade()
                i=inspect(conn)
                for t in ReconciliationBase.metadata.sorted_tables:
                    assert set(t.columns.keys())=={x['name'] for x in i.get_columns(t.name)}
                    assert {x.name for x in t.indexes}=={x['name'] for x in i.get_indexes(t.name)}
                    assert len(t.foreign_key_constraints)==len(i.get_foreign_keys(t.name))
                    assert len([x for x in t.constraints if isinstance(x,CheckConstraint)])==len(i.get_check_constraints(t.name))
                with Operations.context(MigrationContext.configure(conn)):m.downgrade()
                assert i.get_table_names() # new inspector below avoids cached schema
                assert inspect(conn).get_table_names()==['source_sentinel']
                assert conn.execute(text('SELECT id FROM source_sentinel')).scalar()==42
            await c.run_sync(run)
    finally:await db.close()
    ddl='\n'.join(str(CreateTable(t).compile(dialect=dialect())) for t in ReconciliationBase.metadata.sorted_tables)
    ddl+='\n'.join(str(CreateIndex(i).compile(dialect=dialect())) for t in ReconciliationBase.metadata.sorted_tables for i in t.indexes)
    assert 'TIMESTAMP WITH TIME ZONE' in ddl and 'FOREIGN KEY' in ddl and 'CREATE UNIQUE INDEX ux_reconciliation_period' in ddl
    assert 'ai_usage_ledger' not in ddl and 'finops_billing_publication' not in ddl
