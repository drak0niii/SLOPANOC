"""Normalize direct synchronous tool execution without double-counting ADK dispatch."""
from functools import wraps
from .runtime import Activity,current,tool_id

def observed_sync_tool(fn):
    @wraps(fn)
    def invoke(*args,**kwargs):
        runtime=current()
        if runtime is None or tool_id() is not None:return fn(*args,**kwargs)
        activity=Activity('tool',tool_name=fn.__name__)
        try:runtime.sync(activity.start())
        except Exception:runtime.capture_failures+=1
        error=None
        try:
            with activity.attached():result=fn(*args,**kwargs)
            if isinstance(result,dict) and 'error' in result:error=RuntimeError('safe tool failure classification')
            return result
        except BaseException as exc:
            error=exc;raise
        finally:
            try:runtime.sync(activity.finish(error))
            except Exception:runtime.capture_failures+=1
    return invoke
