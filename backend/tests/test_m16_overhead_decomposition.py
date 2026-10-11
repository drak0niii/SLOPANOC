"""Matched component diagnosis; this does not replace the full dependency gate."""
import cProfile
import json
import statistics
import time
import pytest
from opentelemetry.sdk.trace import TracerProvider
from backend.observability.config import ObservabilityConfig
from backend.observability.runtime import Runtime
from backend.observability.turn_trace import TurnTrace
from backend.observability.dependency_instrumentation import dependency_scope
from backend.observability.redaction import safe_span_attributes


def stats(values):
    ordered=sorted(values)
    return dict(n=len(values),p50_us=statistics.median(values),p95_us=ordered[int(.95*(len(values)-1))],
                p99_us=ordered[int(.99*(len(values)-1))] if len(values)>=1000 else None)


def test_overhead_component_decomposition():
    provider=TracerProvider(); tracer=provider.get_tracer('m16.diagnosis')
    runtime=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='local'))
    turn=TurnTrace('m16-decomposition','local',runtime)
    counter=runtime.meter_provider.get_meter('m16.diagnosis').create_counter('m16.diagnosis.calls')
    attrs={'slopanoc.dependency':'chat_attachments','slopanoc.dependency_operation':'exists',
           'slopanoc.dependency_kind':'external','slopanoc.status':'COMPLETED'}
    modes=['baseline','tracing','safe_attributes','metrics','complete']
    samples={m:[] for m in modes}
    def operation(mode):
        if mode=='baseline':return
        if mode=='complete':
            with dependency_scope('chat_attachments','exists','storage.client'):pass
            return
        if mode=='metrics':
            counter.add(1,{})
            return
        span=tracer.start_span('storage.client',attributes=safe_span_attributes(attrs) if mode=='safe_attributes' else {})
        span.end()
    try:
        with turn.attached():
            for n in range(1100):
                order=modes[n%len(modes):]+modes[:n%len(modes)]
                for mode in order:
                    start=time.thread_time_ns();operation(mode);elapsed=(time.thread_time_ns()-start)/1000
                    if n>=100:samples[mode].append(elapsed)
            profile=cProfile.Profile();profile.enable()
            for _ in range(1000):operation('complete')
            profile.disable()
        functions=[]
        for (filename,line,name),entry in __import__('pstats').Stats(profile).stats.items():
            cc,nc,tt,ct,callers=entry
            if '/backend/observability/' in filename:
                functions.append(dict(function=filename.split('/backend/')[-1]+':'+str(line)+':'+name,
                                      calls=nc,self_ms=tt*1000,total_ms=ct*1000))
        print('M16_OVERHEAD_DECOMPOSITION='+json.dumps(dict(component_thread_cpu={m:stats(v) for m,v in samples.items()},
            profile_top=sorted(functions,key=lambda x:x['self_ms'],reverse=True)[:20]),sort_keys=True))
        assert runtime.flush(2)
    finally:
        turn.finish();runtime.close();provider.shutdown()

@pytest.mark.parametrize('limit',[0,1,2,3,128])
def test_final_model_attribute_optimization_preserves_sdk_limits(limit):
    from opentelemetry.sdk.trace import SpanLimits
    from backend.observability.tracing import set_final_attributes
    provider=TracerProvider(span_limits=SpanLimits(max_span_attributes=limit))
    tracer=provider.get_tracer('m16.limit-regression')
    original=tracer.start_span('model',attributes={'a':1,'b':2})
    optimized=tracer.start_span('model',attributes={'a':1,'b':2})
    values={'a':1,'c':3,'d':True}
    try:
        original.set_attributes(values);set_final_attributes(optimized,values)
        assert dict(original.attributes)==dict(optimized.attributes)
        assert original.dropped_attributes==optimized.dropped_attributes
    finally:
        original.end();optimized.end();provider.shutdown()
