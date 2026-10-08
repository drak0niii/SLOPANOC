"""GenAI 1.75.0 compatibility adapter. ONLY this module accesses SDK internals.

Public BaseLlm delegation owns logical calls. Per-instance transport interception
observes physical submissions, including aiohttp's retry *inside* request_once.
No class/global patch, SDK source edit, content instrumentation or retry change.
"""
import inspect
import json
import re
from contextvars import ContextVar
from functools import wraps, lru_cache
from uuid import uuid4
from importlib.metadata import version
from .model_instrumentation import _active, guarded, protect_content
from .turn_trace import degraded

COMPATIBLE_GENAI = '1.75.0'
COMPATIBLE_ADK = '1.33.0'
_submission = ContextVar('slopanoc_provider_submission', default=None)
_last_attempt = ContextVar('slopanoc_provider_attempt', default=None)


def safe_model(value):
    if isinstance(value, str):
        # Configured endpoint names may include project paths; do not export paths.
        name = value.rsplit('/', 1)[-1]
        if re.fullmatch(r'(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100}', name):
            return name
    return 'other'


@lru_cache(maxsize=1)
def compatible():
    from google.genai._api_client import BaseApiClient, HttpResponse
    return (version('google-genai') == COMPATIBLE_GENAI and version('google-adk') == COMPATIBLE_ADK
        and tuple(inspect.signature(BaseApiClient._async_request_once).parameters) == ('self','http_request','stream')
        and tuple(inspect.signature(BaseApiClient._get_aiohttp_session).parameters) == ('self',)
        and tuple(inspect.signature(BaseApiClient._async_request).parameters) == ('self','http_request','http_options','stream')
        and hasattr(HttpResponse, 'async_segments'))


class _ObservedResponse:
    def __init__(self, response, attempt):
        self._response, self._attempt = response, attempt
        self._iterator = None

    def __getattr__(self, name):
        return getattr(self._response, name)

    def __aiter__(self):
        self._iterator = self._response.__aiter__()
        return self

    async def aclose(self):
        if self._iterator is not None and hasattr(self._iterator, "segment_iterator"):
            await self._iterator.segment_iterator.aclose()
        raw = self._response.response_stream
        if hasattr(raw, "aclose"):
            await raw.aclose()
        elif hasattr(raw, "release"):
            raw.release()

    async def __anext__(self):
        try:
            payload = await self._iterator.__anext__()
        except StopAsyncIteration:
            guarded(self._attempt.operation.runtime, self._attempt.finish)
            raise
        except BaseException as exc:
            guarded(self._attempt.operation.runtime, self._attempt.finish, exc)
            raise
        guarded(self._attempt.operation.runtime, self._attempt.observe, payload)
        if isinstance(payload, dict) and isinstance(payload.get('error'), dict):
            code = payload['error'].get('code')
            guarded(self._attempt.operation.runtime, self._attempt.finish, code=code if type(code) is int else 500)
        return payload


class _AwaitableRequest:
    """Preserve aiohttp's awaitable/context-manager request interface."""
    def __init__(self, fn, args, kwargs):
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.value = None

    def __await__(self):
        return self.run().__await__()

    async def run(self):
        self.value = await _physical(self.fn, *self.args, **self.kwargs)
        return self.value

    async def __aenter__(self):
        return await self.run()

    async def __aexit__(self, *args):
        self.value.release()
        await self.value.wait_for_close()


async def _physical(fn, *args, **kwargs):
    op = _active.get()
    from .deadlines import check, retry_allowed
    check()
    if op is not None and not op.closed and op.attempts:
        retry_allowed()
    attempt = guarded(op.runtime, op.attempt, _submission.get()) if op is not None and not op.closed else None
    if attempt is not None:
        _last_attempt.set(attempt)
    try:
        result = await fn(*args, **kwargs)
    except BaseException as exc:
        if attempt is not None:
            guarded(op.runtime, attempt.finish, exc)
        raise
    if attempt is not None:
        code = getattr(result, 'status_code', getattr(result, 'status', 200))
        if type(code) is int and code >= 400:
            guarded(op.runtime, attempt.finish, code=code)
    return result


def _install_session(session):
    if getattr(session, '_slopanoc_model_observed', False):
        return session
    request = session.request
    @wraps(request)
    def wrapped(*args, **kwargs):
        return _AwaitableRequest(request, args, kwargs)
    session.request = wrapped
    session._slopanoc_model_observed = True
    return session


def instrument_client(client):
    """Return the SAME client, preserving SDK backend, pools, options and retries."""
    protect_content()
    op = _active.get()
    runtime = op.runtime if op else None
    try:
        if not compatible():
            degraded(runtime)
            return client
        api = client._api_client
        if getattr(api, '_slopanoc_model_observed', False):
            return client
        once, get_session, request = api._async_request_once, api._get_aiohttp_session, api._async_request
        httpx_client = api._async_httpx_client
        send = httpx_client.send
        @wraps(send)
        async def observed_send(*args, **kwargs):
            return await _physical(send, *args, **kwargs)
        @wraps(get_session)
        async def observed_session():
            session = await get_session()
            try:
                return _install_session(session)
            except Exception:
                degraded(_active.get().runtime if _active.get() else runtime)
                return session
        @wraps(request)
        async def observed_request(*args, **kwargs):
            token = _submission.set(str(uuid4()))
            try:
                return await request(*args, **kwargs)
            except BaseException as exc:
                import asyncio
                operation = _active.get()
                if operation is not None and operation.requests.get(_submission.get(),0) > 1 and not isinstance(exc,(TimeoutError,asyncio.CancelledError)):
                    from .deadlines import observed
                    from .reliability_contract import Category
                    observed('retry_exhausted',Category.MODEL,status='FAILED')
                raise
            finally:
                _submission.reset(token)
        @wraps(once)
        async def observed_once(http_request, stream=False):
            token = _last_attempt.set(None)
            try:
                from .deadlines import current_budget, check
                check()
                budget = current_budget()
                if budget is not None:
                    remaining = budget.remaining
                    previous = http_request.timeout
                    http_request.timeout = min(previous, remaining) if previous else remaining
                response = await once(http_request, stream)
                attempt = _last_attempt.get()
                if attempt is None:
                    return response
                if stream:
                    observed = _ObservedResponse(response, attempt)
                    attempt.operation.responses.append(observed)
                    return observed
                try:
                    body = response.response_stream[0] if response.response_stream else ''
                    payload = json.loads(body) if isinstance(body, (str, bytes)) else body
                    guarded(attempt.operation.runtime, attempt.observe, payload)
                except Exception:
                    degraded(attempt.operation.runtime)
                guarded(attempt.operation.runtime, attempt.finish)
                return response
            except BaseException as exc:
                attempt = _last_attempt.get()
                if attempt is not None:
                    guarded(attempt.operation.runtime, attempt.finish, exc)
                raise
            finally:
                _last_attempt.reset(token)
        # Assignment is instance-local and idempotent. Validate all shapes before
        # any assignment; no provider construction or credential access here.
        installed = []
        try:
            for owner, name, original, replacement in (
                    (httpx_client, 'send', send, observed_send),
                    (api, '_get_aiohttp_session', get_session, observed_session),
                    (api, '_async_request', request, observed_request),
                    (api, '_async_request_once', once, observed_once)):
                installed.append((owner, name, original))
                setattr(owner, name, replacement)
            api._slopanoc_model_observed = True
        except Exception:
            # Unsupported instance shapes must not leave stacked partial wrappers
            # that double-count attempts on a subsequent installation attempt.
            for owner, name, original in reversed(installed):
                try:
                    setattr(owner, name, original)
                except Exception:
                    degraded(runtime)
            raise
    except Exception:
        degraded(runtime)
    return client


async def embedding_request(client, *, agent, operation, **kwargs):
    """Public SDK embedding call with explicit attribution; actual SDK splits count."""
    from .model_context import Attribution, ModelAgent, ModelPurpose
    from .model_instrumentation import ModelOperation
    model = safe_model(kwargs.get('model'))
    provider = 'gcp.vertex_ai' if getattr(client, 'vertexai', False) else 'gcp.gemini'
    attribution = Attribution(ModelAgent(agent), ModelPurpose(operation))
    op = None
    try:
        op = ModelOperation(model, attribution, provider=provider)
    except Exception:
        degraded(None)
    try:
        if op is None:
            from .deadlines import boundary
            from .reliability_contract import Category
            async with boundary(Category.MODEL):
                return await client.aio.models.embed_content(**kwargs)
        with op.attached():
            instrument_client(client)
            from .deadlines import boundary
            from .reliability_contract import Category
            async with boundary(Category.MODEL):
                result = await client.aio.models.embed_content(**kwargs)
        guarded(op.runtime, op.finish)
        return result
    except BaseException as exc:
        if op is not None:
            guarded(op.runtime, op.finish, exc)
        raise
