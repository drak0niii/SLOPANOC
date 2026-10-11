"""Test-only owner cleanup for disposable async engines before their loop closes.

Legacy API fixtures construct SQLite stores without closing them. Track constructor
ownership in the test scope; dispose after dependent fixtures and before pytest's
loop teardown. No warning suppression, production import or remote DSN support.
"""
import os
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

@pytest_asyncio.fixture(autouse=True)
async def m16_disposable_engine_ownership():
    assert os.environ.get('SLOPANOC_M16_LOCAL_TEST')=='1'
    engines=[];original=AsyncEngine.__init__
    def owned(engine,*args,**kwargs):
        original(engine,*args,**kwargs)
        if engine.url.get_backend_name()=='sqlite':engines.append(engine)
    AsyncEngine.__init__=owned
    try:yield
    finally:
        AsyncEngine.__init__=original
        for engine in reversed(engines):await engine.dispose()
        # Case runtime factories cache the same in-memory database and its
        # initialized schema flag. Disposing that database without invalidating
        # the test's cached owners made later case tests reuse an empty database.
        # Clear only these local test factories between tests, after closing the
        # engines; production lifecycle and in-test singleton behavior stay intact.
        from backend.cases.service import get_case_service
        from backend.cases.progression_store import get_case_progression_store
        from backend.cases.db import get_case_database
        for factory in (get_case_service,get_case_progression_store,get_case_database):factory.cache_clear()
