from datetime import datetime,timedelta,timezone
import pytest
from backend.observability.slo_contract import DEFINITIONS,BURN_TIERS,RequestClass,classify,LATENCY_IDS
from backend.observability.slo_evaluator import Counts,Bucket,Snapshot,evaluate,budget

NOW=datetime(2026,10,8,12,tzinfo=timezone.utc)

def source(counts=Counts(),*,at=None,**changes):
    values=dict(environment='development',buckets=(Bucket(at or NOW-timedelta(minutes=1),counts),),updated_at=NOW,coverage_start=NOW-timedelta(days=29))
    values.update(changes);return Snapshot(**values)

@pytest.mark.parametrize('key',[k for k,d in DEFINITIONS.items() if d.source=='SRE_ROLLUPS'])
@pytest.mark.parametrize('counts,state',[(Counts(),'INSUFFICIENT_DATA'),(Counts(good=1000),'HEALTHY'),(Counts(bad=1000),'BREACHED'),(Counts(good=1000,unknown=1),'INSUFFICIENT_DATA')])
def test_every_slo_zero_good_bad_unknown(key,counts,state):
    value=evaluate(key,source(counts),now=NOW)
    assert value['state']==state
    assert value['eligible']==counts.eligible
    assert value['current_value']==(counts.good/counts.eligible if counts.eligible else None)
    assert value['objective_status']=='PROVISIONAL'

@pytest.mark.parametrize('change,state',[({'available':False},'DATA_SOURCE_UNAVAILABLE'),({'updated_at':NOW-timedelta(seconds=301)},'STALE_DATA'),({'capture_complete':False},'INSUFFICIENT_DATA'),({'coverage_start':NOW-timedelta(days=27)},'INSUFFICIENT_DATA')])
def test_source_truth(change,state):assert evaluate('availability',source(Counts(good=1000),**change),now=NOW)['state']==state

def test_cost_graph_no_fake_values():
    v=evaluate('cost_ledger_completeness',source(Counts(good=1000)),now=NOW)
    assert v['state']=='DEFINED_NOT_EVALUATED' and v['source_status']=='DATA_SOURCE_AVAILABLE_IN_M10'
    assert v['current_value'] is v['remaining_fraction'] is None
    assert evaluate('dependency_graph',source(Counts(good=1000)),now=NOW)['state']=='DATA_SOURCE_UNAVAILABLE'

def test_half_open_utc_28day_edges_and_future():
    cutoff=NOW-timedelta(days=28)
    fixture=source(buckets=(Bucket(cutoff,Counts(good=2)),Bucket(cutoff-timedelta(microseconds=1),Counts(bad=10)),Bucket(NOW,Counts(bad=10)),Bucket(NOW-timedelta(microseconds=1),Counts(good=3))))
    assert evaluate('availability',fixture,now=NOW)['good']==5
    assert evaluate('availability',fixture,now=NOW.astimezone(timezone(timedelta(hours=3))))['good']==5
    with pytest.raises(ValueError):evaluate('availability',source(watermark=NOW+timedelta(minutes=1)),now=NOW)
    with pytest.raises(ValueError):evaluate('availability',fixture,now=NOW.replace(tzinfo=None))

def test_budget_exact_exhausted_negative_and_zero():
    assert budget(Counts(good=995,bad=5),.995)==(5.,1.,0.)
    assert budget(Counts(good=990,bad=10),.995)==(5.,2.,-1.)
    assert budget(Counts(),.995)==(None,None,None)
    assert budget(Counts(bad=1),1)==(None,None,None)
    assert evaluate('availability',source(Counts(good=995,bad=5)),now=NOW)['state']=='HEALTHY'

@pytest.mark.parametrize('tier',BURN_TIERS)
def test_burn_pairs_strict_threshold_and_both_windows(tier):
    # All traffic in last minute enters both windows; enough population for alert gate.
    bad=80 if tier.name=='fast' else 40 if tier.name=='sustained' else 10
    v=evaluate('availability',source(Counts(good=1000-bad,bad=bad)),now=NOW)
    assert tier.name in v['burn_tiers']
    long_only=source(Counts(good=1000-bad,bad=bad),at=NOW-timedelta(seconds=tier.short_seconds+60))
    assert tier.name not in evaluate('availability',long_only,now=NOW)['burn_tiers']
    exact_bad=72 if tier.name=='fast' else 30 if tier.name=='sustained' else 5
    assert tier.name not in evaluate('availability',source(Counts(good=1000-exact_bad,bad=exact_bad)),now=NOW)['burn_tiers']
    assert not evaluate('availability',source(Counts(good=1,bad=1)),now=NOW)['burn_tiers']

def test_classification_bounded_precedence_unknown():
    assert classify()==RequestClass.UNKNOWN
    assert classify(general=True)==RequestClass.GENERAL
    assert classify(teams_lookup=True,general=True)==RequestClass.TEAMS_LOOKUP
    assert classify(governed=True,teams_lookup=True)==RequestClass.GOVERNED_TROUBLESHOOTING
    assert classify(specialists=('incident_manager','technical_authority_engineer'),governed=True,teams_lookup=True)==RequestClass.COMPLEX_MULTI_AGENT
    assert classify(specialists=('incident_manager','incident_manager'))==RequestClass.UNKNOWN


def test_short_window_burn_can_coexist_with_an_unbreached_28day_objective():
    fixture=source(buckets=(
        Bucket(NOW-timedelta(minutes=1),Counts(good=900,bad=100)),
        Bucket(NOW-timedelta(days=7),Counts(good=100000)),
    ))
    result=evaluate('availability',fixture,now=NOW)
    assert result['state']=='BURNING' and result['current_value']>.995
    assert 'fast' in result['burn_tiers']
