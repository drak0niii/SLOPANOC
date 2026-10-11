"""Opt-in local CPU attribution; never imported by production.

A-I gates remove portions of the real processor/instrument paths only in the
same fake-provider workflow. Raw diagnostic records stay in nonretaining local
encoding sinks. These ablations are attribution, never acceptance or policy.
"""
import ast
from contextlib import contextmanager
import inspect
import textwrap
from unittest.mock import patch

LAYERS = tuple('ABCDEFGHI')

@contextmanager
def layers(runtime, layer):
    if layer not in LAYERS:
        raise ValueError('Unknown diagnostic layer')
    from contextlib import ExitStack
    from opentelemetry.trace import NoOpTracer
    from opentelemetry.metrics import NoOpMeterProvider
    from backend.observability import tracing, logging, privacy_policy, redaction
    with ExitStack() as stack:
        if layer in 'AB':
            runtime.tracer = NoOpTracer()
        elif layer == 'C':
            original = runtime.tracer
            class RootTracer:
                def start_span(self, name, *args, **kwargs):
                    return (original if name == 'slopanoc.turn' else NoOpTracer()).start_span(name,*args,**kwargs)
            runtime.tracer = RootTracer()
        if layer in 'ABCD':
            meter=NoOpMeterProvider().get_meter('m16.local.ablation')
            for name, value in list(vars(runtime).items()):
                if name.endswith('_instruments') and isinstance(value,dict):
                    setattr(runtime,name,{k:meter.create_counter(k) if hasattr(v,'add') else meter.create_histogram(k) for k,v in value.items()})
        if layer in 'ABCDEF':
            # Emit callsites import this function locally. No diagnostic log body
            # or correlation is built before layer G.
            for name in ('emit','emit_model_observation','emit_dependency_failure','accounting_failure'):
                stack.enter_context(patch.object(logging,name,lambda *a,**kw:None))
        if layer in 'ABCDE':
            stack.enter_context(patch.object(privacy_policy,'attributes',lambda values,**kw:dict(values or {})))
            # Event allowlists stay active; remove only the attribute projection.
            stack.enter_context(patch.object(tracing,'safe_span_attributes',lambda values:dict(values or {})))
            stack.enter_context(patch.object(redaction,'safe_span_attributes',lambda values:dict(values or {})))
        if layer in 'ABCDEFG':
            # Strip only the archive-population block from the real on_end
            # implementation. All other lines (name checks, sink construction,
            # projections at F+, context, queue and encoding) remain identical.
            tree=ast.parse(textwrap.dedent(inspect.getsource(tracing.SafeSpanProcessor.on_end)))
            fn=tree.body[0]
            fn.body=[stmt for stmt in fn.body if not (isinstance(stmt,ast.With) and any(
                isinstance(item.context_expr,ast.Attribute) and item.context_expr.attr=='_lock' for item in stmt.items))]
            namespace=dict(vars(tracing));exec(compile(ast.fix_missing_locations(tree),'<m16 diagnostic processor>','exec'),namespace)
            processor=runtime.processor
            processor.on_start=lambda *a,**kw:None
            processor.on_end=namespace['on_end'].__get__(processor,type(processor))
        # H adds the complete archive policy/population path. I uses the same
        # configuration because no additional production layer exists here.
        yield
