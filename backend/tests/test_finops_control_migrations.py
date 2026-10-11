import importlib.util
import pytest
from sqlalchemy import inspect,text,CheckConstraint
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects.postgresql import dialect
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic.script import ScriptDirectory
from backend.observability.database import Database
from backend.observability.finops.control_models import ControlBase

@pytest.mark.asyncio
async def test_additive_frozen_migration_head_parity_preserves_source_sentinels(tmp_path):
    spec=importlib.util.spec_from_file_location('m14','alembic/versions/a14d2e6f8c70_finops_controls.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.down_revision=='f13a7c9d2e61'
    cfg=Config();cfg.set_main_option('script_location','alembic');assert ScriptDirectory.from_config(cfg).get_heads()==['e27b2c8d0f51']
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'m13.db'),isolated_test=True)
    try:
        async with db.engine.begin() as c:
            for name in ('observability_sre_receipt','ai_usage_ledger','finops_billing_publication','finops_reconciliation_generation','unrelated'):
                await c.execute(text(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY)'))
                await c.execute(text(f'INSERT INTO {name} VALUES (42)'))
            def run(conn):
                with Operations.context(MigrationContext.configure(conn)):m.upgrade()
                i=inspect(conn)
                for t in ControlBase.metadata.sorted_tables:
                    assert set(t.columns.keys())=={x['name'] for x in i.get_columns(t.name)}
                    assert {x.name for x in t.indexes}=={x['name'] for x in i.get_indexes(t.name)}
                    assert len(t.foreign_key_constraints)==len(i.get_foreign_keys(t.name))
                    assert len([x for x in t.constraints if isinstance(x,CheckConstraint)])==len(i.get_check_constraints(t.name))
                with Operations.context(MigrationContext.configure(conn)):m.downgrade()
                assert not set(ControlBase.metadata.tables)&set(inspect(conn).get_table_names())
                for name in ('observability_sre_receipt','ai_usage_ledger','finops_billing_publication','finops_reconciliation_generation','unrelated'):
                    assert conn.execute(text(f'SELECT id FROM {name}')).scalar()==42
            await c.run_sync(run)
    finally:await db.close()
    ddl='\n'.join(str(CreateTable(t).compile(dialect=dialect())) for t in ControlBase.metadata.sorted_tables)
    ddl+='\n'.join(str(CreateIndex(i).compile(dialect=dialect())) for t in ControlBase.metadata.sorted_tables for i in t.indexes)
    assert 'TIMESTAMP WITH TIME ZONE' in ddl and 'FOREIGN KEY' in ddl
    assert 'ALTER TABLE ai_usage_ledger' not in ddl
