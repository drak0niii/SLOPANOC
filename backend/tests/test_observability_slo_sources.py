from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from backend.observability.slo_sources import TraceAssessment,model_observation,observe,Tracker,dependency_observation
from backend.observability.slo_contract import CancelOrigin
from backend.tests.test_observability_slo_evaluator import NOW

@pytest.mark.parametrize('component',[None,'agent','model','tool','dependency'])
def test_completeness_applicable_only_and_sampling_independent(component):
    a=TraceAssessment();a.phases.add('session');a.ended.add('session')
    if component:a.require(component);a.component(component,True)
    assert a.complete(root=True,terminal=True,correlation=True,closure=True)
    if component:a.require(component);a.component(component,False)
    else:a.phases.add('missing_phase')
    assert not a.complete(root=True,terminal=True,correlation=True,closure=True)

@pytest.mark.parametrize('streaming,ttft,workload,expected',[(True,1000,'user_turn','good'),(True,None,'user_turn','unknown'),(False,None,'user_turn',None),(True,6000,'user_turn','bad'),(True,0,'warmup',None)])
def test_ttft_provider_boundary_and_coverage(streaming,ttft,workload,expected):
    events=[];writer=SimpleNamespace(publish=events.append,lost=False);runtime=SimpleNamespace(sre=writer)
    value=SimpleNamespace(status=SimpleNamespace(value='COMPLETED'),completed_at=NOW,streaming=streaming,workload=workload,ttft_ms=ttft,ttft_boundary='first_provider_output' if ttft is not None else None,trace_id='a',span_id='b')
    model_observation(runtime,value)
    actual=[c for key,at,c in events if key=='model_ttft']
    assert bool(actual)==(expected is not None)
    if actual:assert getattr(actual[0],expected)==1

def test_observer_exception_is_diagnostic_only():
    writer=SimpleNamespace(lost=False);turn=SimpleNamespace(sre=SimpleNamespace(writer=writer,lock=__import__("threading").RLock(),publish=Mock(side_effect=RuntimeError())))
    assert observe(turn,'publish') is None and writer.lost


def test_repeated_applicable_phase_cannot_hide_missing_closure():
    a=TraceAssessment();a.phase('orchestration',False);a.phase('orchestration',False);a.phase('orchestration',True)
    assert not a.complete(root=True,terminal=True,correlation=True,closure=True)
    a.phase('orchestration',True)
    assert a.complete(root=True,terminal=True,correlation=True,closure=True)
