import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import inspect,CheckConstraint,text
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects.postgresql import dialect
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.observability.database import Database
from backend.observability.finops.billing_models import BillingBase

@pytest.mark.asyncio
async def test_additive_upgrade_downgrade_and_offline_postgres(tmp_path):
    spec=importlib.util.spec_from_file_location('m11','alembic/versions/d11b4a8c9e32_billing_source_state.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.down_revision=='c10a8f6e2d41'
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'migration.db'),isolated_test=True)
    try:
        async with db.engine.begin() as c:
            await c.execute(text('CREATE TABLE ai_usage_ledger (id INTEGER PRIMARY KEY)'))
            def run(conn):
                with Operations.context(MigrationContext.configure(conn)):m.upgrade()
                i=inspect(conn)
                assert set(BillingBase.metadata.tables)<=set(i.get_table_names())
                for t in BillingBase.metadata.sorted_tables:
                    assert set(t.columns.keys())=={x['name'] for x in i.get_columns(t.name)}
                    assert {x.name for x in t.indexes}=={x['name'] for x in i.get_indexes(t.name)}
                    assert len(t.foreign_key_constraints)==len(i.get_foreign_keys(t.name))
                    assert len([x for x in t.constraints if isinstance(x,CheckConstraint)])==len(i.get_check_constraints(t.name))
                with Operations.context(MigrationContext.configure(conn)):m.downgrade()
                assert inspect(conn).get_table_names()==['ai_usage_ledger']
            await c.run_sync(run)
    finally:await db.close()
    ddl='\n'.join(str(CreateTable(t).compile(dialect=dialect())) for t in BillingBase.metadata.sorted_tables)
    ddl+='\n'.join(str(CreateIndex(i).compile(dialect=dialect())) for t in BillingBase.metadata.sorted_tables for i in t.indexes)
    assert 'TIMESTAMP WITH TIME ZONE' in ddl and 'FOREIGN KEY(generation)' in ddl and 'CREATE UNIQUE INDEX ix_billing_state_scope' in ddl
    assert 'billing_account_id' not in ddl and 'ai_usage_ledger' not in ddl
