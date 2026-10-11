"""Forward-only graph and SQL proof; all execution here is offline."""
import ast
import hashlib
import json
import re
from pathlib import Path
from scripts.observability.reconcile_migration_sql import generate, FORWARD, HISTORY, MERGE


def test_exact_history_and_merge_are_immutable_and_noop():
    sources=json.loads(Path('docs/Telemetry/validation/MIGRATION_RECOVERY_SOURCES.json').read_text())
    for item in sources['migration_sources']:
        if item.get('revision') in HISTORY:
            assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256']
    tree=ast.parse(Path('alembic/versions/c16f4a8b2d90_reconcile_historical_and_observability.py').read_text())
    for node in tree.body:
        if isinstance(node,ast.FunctionDef):assert len(node.body)==1 and isinstance(node.body[0],ast.Pass)


def test_live_to_merge_sql_path_objects_and_metadata_only_dml():
    sql,path,nodes=generate()
    assert path==FORWARD and not set(path)&set(HISTORY)
    assert len(nodes)==19
    assert len(re.findall(r'^CREATE TABLE ',sql,re.M))==32
    assert len(re.findall(r'^CREATE (?:UNIQUE )?INDEX ',sql,re.M))==29
    assert not re.search(r'\b(DROP|TRUNCATE|ALTER|RENAME)\b',sql)
    for stmt in re.sub(r'--[^\n]*','',sql).split(';'):
        stmt=stmt.strip()
        if re.match(r'(INSERT|UPDATE|DELETE)\b',stmt):
            assert re.match(r'(INSERT INTO|UPDATE|DELETE FROM) alembic_version\b',stmt)
    assert sql.count('BEGIN;')==sql.count('COMMIT;')==1
    assert "version_num='"+MERGE+"'" in sql
    assert 'slopanoc_experience_records' not in sql
    assert 'FOREIGN KEY(case_id) REFERENCES slopanoc_cases (case_id)' in sql
