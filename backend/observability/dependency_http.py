"""Gateway route projection never consults or exports its sensitive endpoint."""
from .dependency_contract import ROUTES
from .dependency_instrumentation import update
from .model_instrumentation import guarded
from .errors import ErrorCode


def route_projection(operation, url=None):
    # url is intentionally never inspected: unknown input cannot become output.
    return ROUTES.get(operation, 'unknown') if type(operation) is str else 'unknown'


def response_metadata(scope, response):
    if scope is None:
        return
    def observe():
        code = response.status_code
        if type(code) is not int or not 100 <= code <= 599:
            return
        attrs = {'http.response.status_code': code, 'slopanoc.http_status_class': str(code // 100) + 'xx'}
        if code >= 400:
            kind = 'auth' if code in (401, 403) else 'not_found' if code == 404 else 'rate_limit' if code == 429 else 'provider'
            # A gateway status is NOT a trustworthy downstream Graph status.
            scope.fail(ErrorCode.TOOL_ERROR, kind, 'gateway_http')
        if code == 429:
            attrs['slopanoc.rate_limited'] = True
            try:
                value = response.headers.get('Retry-After')
                seconds = float(value)
                if 0 <= seconds <= 3600:
                    attrs['slopanoc.retry_after_seconds'] = seconds
            except (ValueError, TypeError, AttributeError):
                pass
        update(scope, **attrs)
    guarded(scope.runtime, observe)
