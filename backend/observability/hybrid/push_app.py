"""Authenticated wrapped Pub/Sub push. Permanently bad messages NACK to broker DLQ."""
import asyncio
import base64
import json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .contracts import decode_event, AuditEvent, MAX_EVENT_BYTES
from .capture import RUNTIME_TYPES

MAX_PUSH_BYTES=24576

class PushAuthenticationUnavailable(RuntimeError):
    pass

class GooglePushVerifier:
    def __init__(self, config):
        if not config.push_audience or not config.push_service_account:
            raise ValueError('Exact push audience and identity required')
        self.config=config
        self.slots=asyncio.Semaphore(config.projector_max_inflight)
        self.active=set()

    async def verify(self, authorization):
        if not authorization or not authorization.startswith('Bearer ') or len(authorization)>8192:
            return False
        if self.slots.locked():raise PushAuthenticationUnavailable("Authentication capacity unavailable")
        await self.slots.acquire()
        def validate():
            from google.oauth2 import id_token
            from google.auth.transport.requests import Request as GoogleRequest
            class BoundedRequest(GoogleRequest):
                def __call__(self,*args,**kwargs):
                    kwargs['timeout']=2
                    return super().__call__(*args,**kwargs)
            request=BoundedRequest()
            try:
                claims=id_token.verify_oauth2_token(authorization[7:],request,
                    audience=self.config.push_audience)
                return claims.get('email_verified') is True and claims.get('email')==self.config.push_service_account
            finally:request.session.close()
        task=asyncio.create_task(asyncio.to_thread(validate))
        self.active.add(task)
        def finished(task):
            self.active.discard(task)
            self.slots.release()
            if not task.cancelled():task.exception()  # retrieve safe failure without logging
        task.add_done_callback(finished)
        try:
            # Caller cancellation must not free a slot while certificate I/O continues.
            return await asyncio.shield(task)
        except Exception as exc:
            from google.auth.exceptions import TransportError
            from requests.exceptions import RequestException
            if isinstance(exc,(TransportError,RequestException)):
                raise PushAuthenticationUnavailable("Authentication verification unavailable") from None
            return False



def create_app(*, config, lifespan=None, projector=None, test_verifier=None, isolated_test=False):
    if (projector is not None or test_verifier is not None) and (
        not isolated_test or config.environment not in ('local','development','test')):
        raise ValueError('Explicit isolated test binding required')
    verifier=(test_verifier or GooglePushVerifier(config)) if config.projector_enabled else None
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    if projector is not None:app.state.projector=projector
    @app.get('/health')
    async def health():return {'state':'ready' if config.projector_enabled else 'disabled'}
    @app.post('/internal/observability/events/audit')
    async def push(request:Request):
        worker=getattr(app.state,'projector',None)
        if not config.projector_enabled or worker is None:
            return JSONResponse(status_code=503,content={'state':'DISABLED'})
        try:
            authenticated=await verifier.verify(request.headers.get('authorization'))
        except PushAuthenticationUnavailable:
            return JSONResponse(status_code=503,content={'state':'TRANSIENT_FAILURE'})
        if not authenticated:
            return JSONResponse(status_code=401,content={'state':'UNAUTHENTICATED'})
        def response(code,state):return JSONResponse(status_code=code,content={'state':state})
        # Non-2xx deliberately preserves broker delivery and eventual DLQ forwarding.
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
            return response(415,'PERMANENT_INVALID')
        body=bytearray()
        try:
            async with asyncio.timeout(config.projector_deadline_seconds):
                async for chunk in request.stream():
                    if len(body)+len(chunk)>MAX_PUSH_BYTES:return response(413,'PERMANENT_INVALID')
                    body.extend(chunk)
            wrapper=json.loads(body)
            if not isinstance(wrapper,dict) or set(wrapper)-{'message','subscription','deliveryAttempt'}:
                raise ValueError()
            subscription=f'projects/{config.project}/subscriptions/slopanoc-{config.environment}-audit-projector-v1'
            if wrapper.get('subscription')!=subscription:return response(400,'ENVIRONMENT_MISMATCH')
            message=wrapper['message']
            if not isinstance(message,dict) or set(message)-{'data','attributes','messageId','publishTime','orderingKey'}:
                raise ValueError()
            data=base64.b64decode(message['data'],validate=True)
            if len(data)>MAX_EVENT_BYTES:raise ValueError()
            # Validate the strict schema before distinguishing its environment.
            raw=json.loads(data)
            if not isinstance(raw,dict):raise ValueError()
            event=decode_event(data,environment=raw.get('environment'))
            if event.environment!=config.environment:
                if await worker.record_failure('INVALID') is None:return response(503,'TRANSIENT_FAILURE')
                return response(400,'ENVIRONMENT_MISMATCH')
            if not isinstance(event,AuditEvent) or event.event_type not in RUNTIME_TYPES:raise ValueError()
            attributes=message.get('attributes',{})
            expected={'event_id':str(event.event_id),'schema_version':'1',
                'event_type':event.event_type,'environment':event.environment}
            for field in ('run_id','turn_id','trace_id'):
                value=getattr(event,field)
                if value is not None:expected[field]=str(value)
            if attributes!=expected:raise ValueError()
            if not isinstance(message.get('messageId'),str) or not 1<=len(message['messageId'])<=128:
                raise ValueError()
        except (ValueError,KeyError,TypeError):
            if await worker.record_failure('INVALID') is None:return response(503,'TRANSIENT_FAILURE')
            return response(400,'PERMANENT_INVALID')
        except Exception:return response(503,'TRANSIENT_FAILURE')
        result=await worker.consume(data)
        if result in ('PROCESSED','DUPLICATE'):return response(200,result)
        if result=='CONFLICT':return response(409,'EVENT_CONFLICT')
        if result=='INVALID':return response(400,'PERMANENT_INVALID')
        return response(503,'TRANSIENT_FAILURE')
    return app
