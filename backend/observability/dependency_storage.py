"""Metadata-only SDK adapters, with explicit bounded transport/retry policies."""
import inspect
from .dependency_instrumentation import observe_call
from .deadlines import synchronous_boundary, current_budget, check
from .reliability_contract import Category


def transport_options(fn, category, *, retry_safe):
    # Public SDK parameters only. Narrow doubles without kwargs stay compatible.
    parameters=inspect.signature(fn).parameters
    if 'timeout' not in parameters:
        return {}
    from google.api_core.retry import Retry
    budget=current_budget()
    remaining=budget.remaining
    options={'timeout':remaining}
    if 'retry' in parameters:
        options['retry']=Retry(timeout=remaining) if retry_safe else None
    return options


def storage_call(role, operation, fn, *args, byte_count=None, **kwargs):
    with synchronous_boundary(Category.STORAGE):
        options=transport_options(fn,Category.STORAGE,retry_safe=operation in ('download','exists'))
        requested = kwargs.pop('timeout', None)
        if requested is not None and 'timeout' in options:
            options['timeout'] = min(options['timeout'], requested)
        kwargs.pop('retry', None)  # enforce bounded reads; never SDK-retry writes
        options.update(kwargs)
        result=observe_call(role,operation,'storage.client',fn,*args,byte_count=byte_count,**options)
        check()
        return result


def secret_read(fn, **kwargs):
    with synchronous_boundary(Category.SECRET):
        options=transport_options(fn,Category.SECRET,retry_safe=True)
        requested = kwargs.pop('timeout', None)
        if requested is not None and 'timeout' in options:
            options['timeout'] = min(options['timeout'], requested)
        kwargs.pop('retry', None)  # enforce bounded reads; never SDK-retry writes
        options.update(kwargs)
        return observe_call('secret_manager','access_secret_version','secretmanager.client',fn,**options)
