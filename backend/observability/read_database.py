"""Independent bounded pool; no DDL, migrations, writer tasks or remote discovery."""
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


class ReadDatabase:
    def __init__(self, settings, *, isolated_test=False):
        url=settings.database_url.get_secret_value()
        parsed=make_url(url)
        if parsed.get_backend_name()=='sqlite':
            if not isolated_test or not parsed.database or parsed.database==':memory:':
                raise ValueError('SQLite reader requires an isolated disposable file')
            from pathlib import Path
            if not Path(parsed.database).is_file(): raise ValueError('Reader does not create databases')
            kwargs={'pool_size':settings.pool_size,'max_overflow':0,'pool_timeout':settings.pool_timeout}
        elif parsed.drivername=='postgresql+asyncpg':
            kwargs={'pool_size':settings.pool_size,'max_overflow':0,'pool_timeout':settings.pool_timeout,
                'connect_args':{'server_settings':{'default_transaction_read_only':'on',
                    'statement_timeout':str(int(settings.read_deadline*1000)),
                    'lock_timeout':str(int(settings.pool_timeout*1000))}}}
        else: raise ValueError('Reader requires PostgreSQL asyncpg')
        self.engine=create_async_engine(url,**kwargs)
        if parsed.get_backend_name()=='sqlite':
            @event.listens_for(self.engine.sync_engine,'connect')
            def read_only(connection,_record):
                cursor=connection.cursor();cursor.execute('PRAGMA query_only=ON');cursor.close()
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False,autoflush=False)

    async def close(self): await self.engine.dispose()
