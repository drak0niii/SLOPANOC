"""Separate projector process boundary, disabled by default; local test composition.

Future authenticated Pub/Sub ingress is not implemented. No cloud credentials,
DB resolution, schema creation or business runtime is imported by this factory.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .contracts import MAX_EVENT_BYTES


def create_app(*,projector=None,isolated_local=False):
    if projector is not None and (not isolated_local or projector.store.config.environment not in ('local','test','development')):
        raise ValueError('Only explicit isolated local projector binding permitted')
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @app.get('/health')
    async def health():return {'service':'observability_projector','status':'ready' if projector else 'disabled'}
    @app.post('/events')
    async def event(request:Request):
        if projector is None:return JSONResponse(status_code=503,content={'state':'DISABLED'})
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>MAX_EVENT_BYTES:
                await projector.store.failure('INVALID')
                return JSONResponse(status_code=413,content={'state':'INVALID'})
            data.extend(chunk)
        result=await projector.consume(bytes(data))
        return JSONResponse(status_code=503 if result=='RETRY' else 409 if result=='CONFLICT' else 400 if result=='INVALID' else 200,
            content={'state':result})
    return app
