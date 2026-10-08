"""Owned bounded pool; construction is inert and never creates/migrates schema."""
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool


class Database:
    def __init__(self, url, *, isolated_test=False):
        dialect = make_url(url).get_backend_name()
        if dialect != 'postgresql' and not isolated_test:
            raise ValueError('Observability requires PostgreSQL')
        kwargs = {'pool_size':2, 'max_overflow':0, 'pool_timeout':1} if dialect == 'postgresql' else {}
        if dialect == 'sqlite' and make_url(url).database in (None, ':memory:'):
            kwargs['poolclass'] = StaticPool
        self.engine = create_async_engine(url, **kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self):
        await self.engine.dispose()
