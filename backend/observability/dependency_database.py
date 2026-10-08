"""Instance-owned SQLAlchemy observers. SQL/DSN/parameters never enter telemetry."""
from functools import lru_cache, wraps
from contextvars import ContextVar
from weakref import WeakKeyDictionary, ref
from threading import RLock
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession
from .dependency_contract import DB_OWNERS
from .dependency_instrumentation import Dependency, dependency_scope, active_dependency, current_turn
from .model_instrumentation import guarded
from .dependency_metrics import pool_registered, pool_connection, outcome
from .errors import ErrorCode
from .deadlines import synchronous_boundary, boundary, retry_allowed, policy
from .reliability_contract import Category

_acquiring_pool = ContextVar("slopanoc_acquiring_pool", default=None)
_records = WeakKeyDictionary()
_attempt = ContextVar("slopanoc_db_read_attempt", default=None)
_engines = WeakKeyDictionary()
_lock = RLock()


def _safe(fn):
    def callback(*args, **kwargs):
        return guarded(None, fn, *args, **kwargs)
    return callback


@lru_cache(maxsize=32)
def observed_pool_class(base, owner):
    class ObservedPool(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            guarded(None, pool_registered, self, owner)

        def connect(self):
            token = _acquiring_pool.set(self)
            try:
                with synchronous_boundary(Category.DATABASE_ACQUIRE), dependency_scope(owner, 'CHECKOUT', 'db.connection', kind='acquisition', attributes={
                    'slopanoc.acquisition_boundary': 'pool_connect_including_pre_ping',
                    'slopanoc.retry_visibility': 'unknown'}):
                    return super().connect()  # original acquisition, exactly once
            finally:
                _acquiring_pool.reset(token)

    @_safe
    def connected(connection, record):
        pool = _acquiring_pool.get()
        if pool is not None:
            with _lock:
                _records[record] = ref(pool)
            pool_connection(pool, 1)

    @_safe
    def closed(connection, record):
        with _lock:
            pool_ref = _records.pop(record, None)
        if pool_ref is not None and pool_ref() is not None:
            pool_connection(pool_ref(), -1)

    # Class listeners are synchronous metadata callbacks. Explicit asyncio=False
    # avoids instance-only async dispatch initialization on a class target.
    event.listen(ObservedPool, 'connect', connected, asyncio=False)
    event.listen(ObservedPool, 'close', closed, asyncio=False)

    ObservedPool.__name__ = 'Observed' + base.__name__
    return ObservedPool


def pool_kwargs(database_url, kwargs, owner):
    """Public dialect pool selection; no change to pool size/timeouts/retry policy."""
    result = dict(kwargs)
    def prepare():
        url = make_url(database_url)
        base = result.get('poolclass') or url.get_dialect().get_pool_class(url)
        result['poolclass'] = observed_pool_class(base, owner)
        from sqlalchemy.pool import QueuePool
        if issubclass(base, QueuePool):
            result.setdefault('pool_timeout', policy().database_acquire_timeout_seconds)
        if url.drivername == 'postgresql+asyncpg':
            args = dict(result.get('connect_args', {}))
            args.setdefault('timeout', policy().database_acquire_timeout_seconds)
            args.setdefault('command_timeout', policy().database_query_timeout_seconds)
            result['connect_args'] = args
    guarded(None, prepare)
    return result


@lru_cache(maxsize=16)
def observed_session_class(base=AsyncSession):
    if getattr(base, "_slopanoc_observed_session", False):
        return base
    class ObservedSession(base):
        _slopanoc_observed_session = True
        async def execute(self, *args, **kwargs):
            async with boundary(Category.DATABASE_QUERY):
                return await super().execute(*args, **kwargs)

        async def connection(self, *args, **kwargs):
            async with boundary(Category.DATABASE_ACQUIRE):
                return await super().connection(*args, **kwargs)

        async def commit(self):
            owner = guarded(None, session_owner, self)
            async with boundary(Category.PERSISTENCE):
                if owner is None:
                    return await super().commit()
                return await self._commit_observed(owner)

        async def _commit_observed(self, owner):
            with dependency_scope(owner, 'COMMIT', 'db.transaction', kind='transaction') as scope:
                result = await super().commit()
                if scope:
                    guarded(scope.runtime, scope.update, **{'slopanoc.transaction_outcome': 'commit'})
                return result

        async def rollback(self):
            owner = guarded(None, session_owner, self)
            if owner is None:
                return await super().rollback()
            with dependency_scope(owner, 'ROLLBACK', 'db.transaction', kind='transaction') as scope:
                result = await super().rollback()
                if scope:
                    guarded(scope.runtime, scope.update, **{'slopanoc.transaction_outcome': 'rollback'})
                return result
    return ObservedSession


def session_owner(session):
    bind = session.get_bind()
    with _lock:
        return _engines.get(bind)


def observe_factory(factory):
    # Public instance-local async_sessionmaker class_ seam; never mutate AsyncSession.
    guarded(None, _observe_factory, factory)


def _observe_factory(factory):
    factory.class_ = observed_session_class(factory.class_)


def observe_engine(engine, owner):
    guarded(None, _observe_engine, engine, owner)
    return engine


def _observe_engine(engine, owner):
    if owner not in DB_OWNERS:
        raise ValueError('Unregistered DB owner')
    target = engine.sync_engine
    with _lock:
        if target in _engines:
            return
        _engines[target] = owner
    pool = target.pool
    pool_registered(pool, owner)
    system = target.dialect.name if target.dialect.name in {'sqlite', 'postgresql'} else 'other'

    @_safe
    def before(conn, cursor, statement, parameters, execution, many):
        # Never copy statement or parameters. Compiled structural flags only.
        compiled = execution.compiled
        operation = 'other'
        if compiled is not None:
            if execution.isinsert:
                operation = 'INSERT'
            elif execution.isupdate:
                operation = 'UPDATE'
            elif execution.isdelete:
                operation = 'DELETE'
            elif getattr(compiled.statement, 'is_select', False):
                operation = 'SELECT'
            elif execution.isddl:
                operation = 'DDL'
        attempt = _attempt.get()
        retry_attrs = {"slopanoc.dependency_attempt": attempt[1], "slopanoc.dependency_retry_count": attempt[1] - 1, "slopanoc.retry_visibility": "observable"} if attempt and attempt[0] == owner else {}
        scope = Dependency(owner, operation, 'db.client', attributes={'db.system.name': system,
            'db.operation.name': operation, 'slopanoc.retry_visibility': 'unknown'} | retry_attrs)
        execution._slopanoc_dependency = scope
        scope.begin()

    @_safe
    def after(conn, cursor, statement, parameters, execution, many):
        scope = getattr(execution, '_slopanoc_dependency', None)
        if scope:
            scope.close()
            execution._slopanoc_dependency = None

    @_safe
    def failure(error_context):
        execution = error_context.execution_context
        scope = getattr(execution, '_slopanoc_dependency', None) if execution else None
        if scope:
            if error_context.is_disconnect:
                scope.fail(ErrorCode.DATABASE_CONNECTION_ERROR, 'connection', 'database')
            scope.close(error_context.sqlalchemy_exception or error_context.original_exception)
            execution._slopanoc_dependency = None
        elif active_dependency(current_turn()) is None:
            with dependency_scope(owner, 'CONNECT', 'db.connection', kind='acquisition') as scope:
                if scope:
                    scope.fail(ErrorCode.DATABASE_CONNECTION_ERROR, 'connection', 'database')

    def transaction(operation):
        @_safe
        def observe(conn):
            parent = active_dependency(current_turn())
            if parent and parent.kind == 'transaction' and parent.dependency == owner:
                return
            # Engine events fire BEFORE DBAPI commit/rollback: report request only.
            with dependency_scope(owner, operation, 'db.transaction', kind='transaction', attributes={
                    'slopanoc.transaction_outcome': operation.lower() + '_requested'}):
                pass
        return observe

    event.listen(target, 'before_cursor_execute', before)
    event.listen(target, 'after_cursor_execute', after)
    event.listen(target, 'handle_error', failure)
    event.listen(target, 'commit', transaction('COMMIT'))
    event.listen(target, 'rollback', transaction('ROLLBACK'))



def observe_retry(owner, operation):
    """Called only inside the application's existing read retry handler."""
    def emit():
        if _attempt.get() is not None:
            _attempt.set((owner, 2))
        with dependency_scope(owner, operation, 'knowledge.retrieval', kind='local', attributes={
                'slopanoc.dependency_retry_count': 1, 'slopanoc.retry_visibility': 'observable'} ) as scope:
            if scope:
                outcome(scope, 'slopanoc.dependency.retries')
    retry_allowed()
    guarded(None, emit)


def db_read_operation(owner):
    def decorate(fn):
        @wraps(fn)
        async def observed(*args, **kwargs):
            token = _attempt.set((owner, 1))
            try:
                return await fn(*args, **kwargs)
            finally:
                _attempt.reset(token)
        return observed
    return decorate


def observe_session_service(service):
    def register():
        observe_engine(service.db_engine, "session_db")
        observe_factory(service.database_session_factory)
    guarded(None, register)
