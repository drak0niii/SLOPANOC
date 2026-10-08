"""Additive M10 migration in a disposable DB and PostgreSQL offline DDL."""
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import inspect,text,CheckConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable,CreateIndex
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.observability.database import Database
from backend.observability.finops.models import FinOpsBase

def module():
    path=Path('alembic/versions/c10a8f6e2d41_finops_runtime_usage.py')
    spec=importlib.util.spec_from_file_location('m10_migration',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.mark.asyncio
async def test_upgrade_downgrade_tables_indexes_relationships(tmp_path):
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'migration.db'),isolated_test=True);m=module()
    assert m.down_revision=='b37e90a14c62'
    try:
        async with db.engine.begin() as c:
            await c.execute(text('CREATE TABLE unrelated (id INTEGER PRIMARY KEY)'))
            def run(conn):
                with Operations.context(MigrationContext.configure(conn)):m.upgrade()
                i=inspect(conn)
                assert set(FinOpsBase.metadata.tables)<=set(i.get_table_names())
                for t in FinOpsBase.metadata.sorted_tables:
                    assert {x['name'] for x in i.get_columns(t.name)}==set(t.columns.keys())
                    assert {x['name'] for x in i.get_indexes(t.name)}=={x.name for x in t.indexes}
                    assert len(i.get_foreign_keys(t.name))==len(t.foreign_key_constraints)
                    assert len(i.get_check_constraints(t.name))==len([v for v in t.constraints if isinstance(v,CheckConstraint)])
                with Operations.context(MigrationContext.configure(conn)):m.downgrade()
                assert i.get_table_names() # inspector cache should not be used for final result
                assert inspect(conn).get_table_names()==['unrelated']
            await c.run_sync(run)
    finally:await db.close()

def test_postgresql_offline_constraints_and_partial_unique_index():
    ddl='\n'.join(str(CreateTable(t).compile(dialect=dialect())) for t in FinOpsBase.metadata.sorted_tables)
    ddl+='\n'.join(str(CreateIndex(i).compile(dialect=dialect())) for t in FinOpsBase.metadata.sorted_tables for i in t.indexes)
    assert 'TIMESTAMP WITH TIME ZONE' in ddl
    assert "WHERE record_kind = 'BASE'" in ddl and 'CREATE UNIQUE INDEX ux_finops_base_attempt' in ddl
    assert 'FOREIGN KEY(original_event_id)' in ddl
    assert not any(x in ddl for x in ('estimated_cost','price_version','currency','billing','sessions'))
