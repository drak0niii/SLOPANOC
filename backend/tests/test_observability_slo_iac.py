import json,re
from pathlib import Path
import pytest
from backend.observability.slo_iac import artifacts
from backend.observability.slo_contract import DEFINITIONS,BURN_TIERS
from backend.observability.slo_evaluator import evaluate,Counts
from backend.tests.test_observability_slo_evaluator import NOW,source

ROOT=Path(__file__).resolve().parents[2]

def test_generated_definitions_no_drift_and_single_condition():
    manifest,policies,dashboards=artifacts()
    assert json.loads((ROOT/'infra/observability/monitoring/slo_manifest.json').read_text())==json.loads(json.dumps(manifest))
    assert json.loads((ROOT/'infra/observability/monitoring/alert_policies.json').read_text())==policies
    for name,value in dashboards.items():assert json.loads((ROOT/f'infra/observability/dashboards/{name}.json').read_text())==value
    for p in policies:
        assert p['enabled'] is False and p['notificationChannels']==[] and len(p['conditions'])<=1
        book=re.search(r'Runbook: (docs/Telemetry/runbooks/[a-z_]+\.md)',p['documentation']['content'])
        assert book and (ROOT/book[1]).is_file()
    assert len(dashboards)==5


def query_thresholds(query):
    # Independent extraction of generated PromQL numeric threshold, window and volume clauses.
    return [(int(window),float(budget),float(threshold)) for window,budget,threshold in re.findall(r'slopanoc\.slo\.bad_fraction"[^{}]*?window="(\d+)"[^{}]*?\}\) / ([0-9.eE+-]+) > ([0-9.]+)',query)]

@pytest.mark.parametrize('slo_id',['availability','terminal_completion','latency_general','model_ttft'])
@pytest.mark.parametrize('bad',[0,1,5,30,72,73,100,999])
def test_formula_parity_numeric_clauses_against_evaluator(slo_id,bad):
    from backend.observability.slo_alerts import promql
    definition=DEFINITIONS[slo_id]
    result=evaluate(slo_id,source(Counts(good=1000-bad,bad=bad)),now=NOW)
    for action in ('PAGE','TICKET'):
        clauses=query_thresholds(promql(slo_id,action))
        assert len(clauses)==(4 if action=='PAGE' else 2)
        decisions=[]
        for tier in BURN_TIERS:
            if tier.action!=action:continue
            pairs=[c for c in clauses if c[0] in (tier.long_seconds,tier.short_seconds)]
            decisions.append(all((bad/1000)>budget*threshold+1e-15 for _,budget,threshold in pairs))
        assert any(decisions)==bool(set(result['burn_tiers'])&{t.name for t in BURN_TIERS if t.action==action})

def test_cardinality_and_privacy_declarative_artifacts():
    _,policies,dashboards=artifacts()
    serialized=json.dumps([policies,dashboards])
    for forbidden in ('run_id=','session_id=','user_id=','trace_id=','chat_id=','git_sha='):
        assert forbidden not in serialized
    assert 'Power Automate / Teams gateway' in serialized


@pytest.mark.parametrize('tier', BURN_TIERS)
@pytest.mark.parametrize('short_bad,outer_bad', [(0,1000),(100,0),(100,100),(0,0)])
def test_distinct_long_short_populations_match_generated_conditions(tier,short_bad,outer_bad):
    from datetime import timedelta
    from backend.observability.slo_evaluator import Bucket
    from backend.observability.slo_alerts import promql
    fixture=source(buckets=(
        Bucket(NOW-timedelta(minutes=1),Counts(good=1000-short_bad,bad=short_bad)),
        Bucket(NOW-timedelta(seconds=tier.short_seconds+60),Counts(good=1000-outer_bad,bad=outer_bad)),
    ))
    result=evaluate('availability',fixture,now=NOW)
    windows={w['seconds']:w for w in result['burn_windows']}
    clauses=query_thresholds(promql('availability',tier.action))
    actual=True
    for seconds in (tier.long_seconds,tier.short_seconds):
        _,budget,threshold=next(c for c in clauses if c[0]==seconds)
        w=windows[seconds]
        actual &= w['eligible']>=tier.minimum_events and not w['unknown'] and w['bad']/w['eligible']>budget*threshold+1e-15
    assert actual==(tier.name in result['burn_tiers'])


def test_environment_binding_and_matching_are_explicit():
    _,policies,dashboards=artifacts()
    for policy in policies:
        for condition in policy['conditions']:
            query=condition['conditionPrometheusQueryLanguage']['query']
            for selector in re.findall(r'\{[^{}]+\}',query):
                assert 'environment="__ENVIRONMENT__"' in selector
            assert 'and on (environment) {' not in query
    terraform=(ROOT/'infra/observability/terraform/monitoring/main.tf').read_text()
    assert 'replace(each.value.conditions' in terraform
    assert 'dashboard_json = replace(' in terraform
