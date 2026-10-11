"""Local planning gate only. Does not contact or configure a Collector.

Defaults are inspected source limits. An envelope is valid only while queue
admission, transport deadlines and trace affinity actually hold. Drop/restart
loss cannot be cured by extending the tail timer.
"""
import math
from backend.observability.config import ObservabilityConfig

def evaluate(config=None, *, decision_wait=240, num_traces=256, traces_per_second=.5,
             burst=64, transport_allowance=9):
    config=config or ObservabilityConfig()
    for value in (decision_wait,num_traces,traces_per_second,burst,transport_allowance):
        if not math.isfinite(value) or value<0:raise ValueError('Invalid local sampling envelope')
    batches=math.ceil(config.otel_queue_capacity/config.otel_batch_size)+1
    sdk_delay=config.otel_batch_interval_seconds+batches*config.otel_export_timeout_seconds
    required_window=config.turn_timeout_seconds+sdk_delay+transport_allowance
    required_population=math.ceil((traces_per_second*decision_wait+burst)*1.25)
    return dict(decision_wait_seconds=decision_wait,required_window_seconds=required_window,
                sdk_residence_allowance_seconds=sdk_delay,transport_allowance_seconds=transport_allowance,
                num_traces=num_traces,required_population=required_population,
                traces_per_second=traces_per_second,burst=burst,
                pass_local_planning=decision_wait>=required_window and num_traces>=required_population,
                production_qualified=False)
if __name__=='__main__':
    import json
    result=evaluate();print(json.dumps(result,sort_keys=True))
    raise SystemExit(0 if result['pass_local_planning'] else 1)
