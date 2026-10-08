"""Private local M5 fixtures: no real SDK/network/database credentials."""
from contextlib import contextmanager
from backend.observability.runtime import Runtime
from backend.observability.turn_trace import TurnTrace
from backend.tests.test_observability_runtime import config

@contextmanager
def environment():
    runtime = Runtime(config())
    turn = TurnTrace('m5-run', 'm5-session', runtime)
    try:
        with turn.attached():
            yield runtime, turn
    finally:
        turn.finish()
        runtime.close()


def spans(runtime, name=None):
    runtime.flush()
    records = runtime.exporters['trace'].snapshot()
    return [s for s in records if name is None or s.name == name]


def capture(runtime):
    runtime.flush()
    return repr([(s.name, dict(s.attributes), [(e.name, dict(e.attributes)) for e in s.events],
        s.status.description) for s in runtime.exporters['trace'].snapshot()]) + repr(runtime.exporters['log'].snapshot()) + repr(runtime.exporters['metric'].records)


def points(runtime, name, dependency=None):
    runtime.flush()
    return [p for batch in runtime.exporters['metric'].records for rm in batch.resource_metrics
        for sm in rm.scope_metrics for metric in sm.metrics if metric.name == name for p in metric.data.data_points
        if dependency is None or p.attributes.get('dependency') == dependency]
