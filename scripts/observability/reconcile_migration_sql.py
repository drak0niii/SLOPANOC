"""Offline-only reconciliation proof; never resolves a live database URL.

Historical modules require pgvector. For static graph validation their identifiers
are parsed from exact source; historical upgrade/downgrade callables deliberately
raise if visited. Forward sibling modules are imported unchanged. The actual
Alembic env runs in offline mode with an explicit fake PostgreSQL URL.
This does not substitute for full migration import validation or PG rehearsal.
"""
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import Script, ScriptDirectory
from alembic.script.revision import Revision, RevisionMap

ROOT = Path(__file__).resolve().parents[2]
MERGE = 'c16f4a8b2d90'
LIVE = 'd3f8b1c6a942'
FORWARD = ['7c4e9a2d1f05','9f71c2a64e08','b37e90a14c62','c10a8f6e2d41',
           'd11b4a8c9e32','e12c6d8a4b50','f13a7c9d2e61','a14d2e6f8c70','b15e3f7a9c81',MERGE]
HISTORY = ['d2081e4fd455','b85242503972','3e59584b1012','9b6df6490c0e',
           'a1f3c9e07b21','c7e2a4f9b83d',LIVE]


def forbidden_history_execution():
    raise AssertionError('Historical migration must never execute in this proof')


def graph():
    cfg = Config();cfg.set_main_option('script_location',str(ROOT/'alembic'))
    script = ScriptDirectory.from_config(cfg)
    nodes = []
    for path in sorted((ROOT/'alembic/versions').glob('*.py')):
        tree = ast.parse(path.read_text());attrs = {}
        for n in tree.body:
            if isinstance(n,(ast.Assign,ast.AnnAssign)):
                targets = n.targets if isinstance(n,ast.Assign) else [n.target]
                for target in targets:
                    if isinstance(target,ast.Name) and target.id in {'revision','down_revision','branch_labels','depends_on'}:
                        attrs[target.id] = ast.literal_eval(n.value)
        if attrs['revision'] in HISTORY:
            module = SimpleNamespace(**attrs,upgrade=forbidden_history_execution,
                                     downgrade=forbidden_history_execution,__doc__='Exact historical metadata (AST)')
        else:
            spec = importlib.util.spec_from_file_location('reconcile_'+attrs['revision'],path)
            module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        nodes.append(Script(module,attrs['revision'],str(path)))
    script.revision_map = RevisionMap(lambda:iter(nodes))
    return cfg,script,nodes


def generate():
    cfg,script,nodes = graph()
    assert script.get_heads() == ['e27b2c8d0f51']
    assert script.get_revision('d26a1b7c9e40').down_revision == MERGE
    assert script.get_bases() == ['d2081e4fd455']
    assert len(list(script.walk_revisions())) == 19
    before = RevisionMap(lambda:iter(Revision(n.revision,n.down_revision) for n in nodes if n.revision not in (MERGE,'d26a1b7c9e40','e27b2c8d0f51')))
    assert set(before.heads) == {LIVE,'b15e3f7a9c81'}
    steps = script._upgrade_revs(MERGE,LIVE)
    path = [s.revision.revision for s in steps]
    assert path == FORWARD,(path,FORWARD)
    assert not set(path)&set(HISTORY)
    from backend.config.settings import Settings
    buf=io.StringIO();cfg.output_buffer=buf
    fake=Settings({'SLOPANOC_DATABASE_URL':'postgresql+asyncpg://unused:unused@127.0.0.1/unused'})
    with patch('backend.config.settings.get_settings',return_value=fake):
        with EnvironmentContext(cfg,script,fn=lambda rev,ctx:script._upgrade_revs(MERGE,rev),
                                as_sql=True,starting_rev=LIVE,destination_rev=MERGE):
            script.run_env()
    return buf.getvalue(),path,nodes


if __name__ == '__main__':
    sql,path,nodes=generate()
    dest=ROOT/'docs/Telemetry/validation/M7_M15_RECONCILED_ROLLOUT.sql'
    dest.write_text(sql)
    sources=json.loads((ROOT/'docs/Telemetry/validation/MIGRATION_RECOVERY_SOURCES.json').read_text())
    hashes={}
    for item in sources['migration_sources']:
        if item.get('revision') not in HISTORY:continue
        digest=hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()
        assert digest==item['sha256'];hashes[item['revision']]=digest
    evidence={'validation_mode':'AST historical metadata + Alembic graph; actual unchanged forward modules and actual offline env',
              'full_module_import_gate':'NOT RUN: static historical metadata; full imports require pgvector',
              'two_heads_before_merge':[LIVE,'b15e3f7a9c81'],'final_head':'d26a1b7c9e40','reconciliation_rollout_target':MERGE,
              'revision_count':len(nodes),'forward_path':path,'excluded_history':HISTORY,
              'historical_hashes':hashes,'sql_sha256':hashlib.sha256(sql.encode()).hexdigest()}
    (dest.parent/'MIGRATION_RECONCILIATION_GRAPH.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence,indent=2))
