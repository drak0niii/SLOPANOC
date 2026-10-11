"""Production composition. No schema creation or business executor in workers."""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .config import HybridConfig
from .capture import Capture
from ..database import Database
from .store import OutboxStore, ProjectorStore
from .workers import Publisher, Projector
from .google_transport import GooglePubSubAuditTransport


def capture_for(config, observability_config):
    if not config.capture_enabled:return None
    if config.environment != observability_config.otel_environment:
        raise ValueError('Hybrid owner environment mismatch')
    if not observability_config.projection_enabled or not observability_config.finops_enabled:
        raise ValueError('Hybrid capture requires durable run and accounting owners')
    return Capture()


def worker_database_url():
    # Dedicated least-privilege binding; never fall back to the business writer.
    url=os.environ.get('SLOPANOC_HYBRID_DATABASE_URL')
    if not url:raise ValueError('Dedicated hybrid database binding required')
    return url


def create_publisher_app():
    config=HybridConfig.from_environment()
    @asynccontextmanager
    async def lifespan(app):
        database=publisher=transport=None
        try:
            if config.audit_publication_enabled:
                database=Database(worker_database_url())
                transport=GooglePubSubAuditTransport(config)
                publisher=Publisher(OutboxStore(database.sessions,config),transport)
                publisher.start()
            app.state.publisher=publisher
            yield
        finally:
            try:
                if publisher:await publisher.close()
            finally:
                try:
                    if transport:await transport.close()
                finally:
                    if database:await database.close()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    @app.get('/health')
    async def health():return {'state':'ready' if getattr(app.state,'publisher',None) else 'disabled'}
    return app


def create_projector_app():
    from .push_app import create_app
    config=HybridConfig.from_environment()
    @asynccontextmanager
    async def lifespan(app):
        database=projector=None
        try:
            if config.projector_enabled:
                database=Database(worker_database_url())
                projector=Projector(ProjectorStore(database.sessions,config))
            app.state.projector=projector
            yield
        finally:
            if projector:await projector.close()
            if database:await database.close()
    return create_app(config=config,lifespan=lifespan)
