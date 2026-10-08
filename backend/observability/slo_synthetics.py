"""Disabled-by-default bounded synthetic checks with explicitly supplied adapters.

No credentials, tenant discovery, network clients or implicit execution. Runtime
deployment must supply approved isolated synthetic adapters and scheduling.
"""
import asyncio
from dataclasses import dataclass
from enum import Enum
from .slo_sources import now_utc


class Check(str, Enum):
    GENERAL='general'
    TEAMS='teams_fixture_or_approved_tenant'
    KNOWLEDGE='governed_knowledge'
    DATABASE='isolated_db_session'
    SSE='sse_receipt'


@dataclass(frozen=True)
class Result:
    check: Check
    state: str
    evaluated_at: object
    workload: str = 'synthetic'


async def run_suite(adapters, *, enabled=False, clock=now_utc):
    """Only literal True is success; exceptions/timeout never expose raw details."""
    if not set(adapters)<=set(Check):raise ValueError('Unregistered synthetic check')
    results=[]
    for check in Check:
        state='DISABLED' if not enabled else 'DATA_SOURCE_UNAVAILABLE'
        if enabled and check in adapters:
            try:
                async with asyncio.timeout(5):
                    state='PASSED' if await adapters[check]() is True else 'FAILED'
            except asyncio.CancelledError:raise
            except Exception:state='FAILED'
        results.append(Result(check,state,clock()))
    return tuple(results)
