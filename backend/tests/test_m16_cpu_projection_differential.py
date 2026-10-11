"""Independent pre-optimization policy reference, dynamic corpus and concurrency."""
import importlib.util
from pathlib import Path
import random
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.observability import redaction,privacy_policy
from backend.observability.attributes import Attribute
from backend.observability.stages import Stage,RunStatus

spec=importlib.util.spec_from_file_location('backend.observability._m16_cpu_reference',Path(__file__).with_name('_m16_cpu_projection_reference.py'))
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)


def outcome(fn,values):
    try:return ('safe',fn(values))
    except (ValueError,TypeError,OverflowError) as exc:return ('rejected',type(exc).__name__,str(exc))


def corpus():
    keys=sorted(privacy_policy.TRACE_FIELDS|redaction.COUNT_FIELDS|{'prompt','response','authorization','command','sql','teams_message_body','reasoning','unknown'})
    values=[None,True,False,-1,0,1,2**63-1,2**63,2**1024,.5,float('nan'),float('inf'),'',
        'COMPLETED',RunStatus.COMPLETED,'model.request.completed',Stage.MODEL_REQUEST_COMPLETED,
        'team_manager','gemini-2.5-flash','aaaaaaaaaaaaaaaa','00000000-0000-0000-0000-000000000001',
        'M3_PROMPT_SENTINEL','M3_RESPONSE_SENTINEL','Bearer M3_CREDENTIAL_SENTINEL',
        'M3_SECRET_SENTINEL','show M3_COMMAND_SENTINEL','SELECT M3_SQL_SENTINEL',
        'M3_TEAMS_SENTINEL','M3_REASONING_SENTINEL','x'*170000,[],{},['stop'],('stop',),['stop']*17]
    for key in keys:
        for value in values:yield {key:value}
    rng=random.Random(1641)
    for _ in range(10000):yield {key:rng.choice(values) for key in rng.sample(keys,rng.randint(0,12))}
    yield {Attribute.STATUS:RunStatus.COMPLETED,'slopanoc.stage':Stage.MODEL_REQUEST_COMPLETED}


def test_all_dynamic_projections_match_independent_reference():
    for values in corpus():
        assert outcome(redaction.safe_span_attributes,values)==outcome(reference.safe_span_attributes,values)
        assert outcome(redaction.safe_model_attributes,values)==outcome(reference.safe_model_attributes,values)
    for values in (None,[],False,0,'',123,'unsafe'):
        assert outcome(redaction.safe_span_attributes,values)==outcome(reference.safe_span_attributes,values)


@pytest.mark.parametrize('surface',list(privacy_policy.Surface))
def test_surface_producer_and_environment_policy_preserved(surface):
    values={'slopanoc.run_id':'run-1','slopanoc.status':'COMPLETED','input_tokens':2,
        'prompt':'M3_PROMPT_SENTINEL','response':'M3_RESPONSE_SENTINEL','authorization':'Bearer M3_CREDENTIAL_SENTINEL',
        'secret':'M3_SECRET_SENTINEL','command':'show M3_COMMAND_SENTINEL','sql':'SELECT M3_SQL_SENTINEL',
        'teams_message_body':'M3_TEAMS_SENTINEL','reasoning':'M3_REASONING_SENTINEL','unknown':1}
    for producer in (*privacy_policy.PRODUCERS,'untrusted'):
        for environment in (*privacy_policy.ENVIRONMENTS,'unknown'):
            if producer not in privacy_policy.PRODUCERS or environment not in privacy_policy.ENVIRONMENTS:
                expected={}
            elif surface in (privacy_policy.Surface.METRIC,privacy_policy.Surface.LOG,privacy_policy.Surface.API,privacy_policy.Surface.UI):
                expected=reference.sanitize_metadata(values)
            else:
                expected={key:value for key,value in reference.safe_span_attributes(values).items()
                    if (policy:=privacy_policy.REGISTRY.get(key)) is not None and producer in policy.producers and surface in policy.surfaces}
            assert privacy_policy.attributes(values,producer=producer,surface=surface,environment=environment)==expected


def test_concurrent_projection_has_no_shared_request_state():
    values=[{'slopanoc.run_id':f'run-{i}','slopanoc.status':'COMPLETED','request_count':i,'secret':'M3_SECRET_SENTINEL'} for i in range(512)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        observed=list(executor.map(redaction.safe_span_attributes,values))
    assert observed==[reference.safe_span_attributes(v) for v in values]
    assert len({v['slopanoc.run_id'] for v in observed})==512


def test_event_filter_and_final_metadata_match_reference():
    from opentelemetry.sdk.trace import Event
    from backend.observability.tracing import safe_events
    from backend.observability.reliability_contract import EVENTS
    names=[*(v.value for v in Stage),*EVENTS,'unknown.event','M3_PROMPT_SENTINEL']
    attrs={'slopanoc.run_id':'run-1','slopanoc.status':'COMPLETED','request_count':1,
        'slopanoc.deadline_category':'model','slopanoc.remaining_seconds':1,
        'prompt':'M3_PROMPT_SENTINEL','secret':'M3_SECRET_SENTINEL'}
    events=[Event(name,attrs,timestamp=123) for name in names]
    shape=lambda values:[(e.name,e.timestamp,dict(e.attributes)) for e in values]
    for model,execution in ((False,False),(True,False),(False,True),(True,True)):
        assert shape(safe_events(events,model=model,execution=execution))==shape(reference.safe_events(events,model=model,execution=execution))
