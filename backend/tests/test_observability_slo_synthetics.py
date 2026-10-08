from unittest.mock import AsyncMock
import pytest
from backend.observability.slo_synthetics import Check,run_suite


@pytest.mark.asyncio
async def test_disabled_missing_failed_and_recovery_are_truthful():
    adapters={check:AsyncMock(return_value=True) for check in Check}
    assert all(r.state=='DISABLED' for r in await run_suite(adapters))
    assert all(adapter.await_count==0 for adapter in adapters.values())
    assert all(r.state=='PASSED' and r.workload=='synthetic' for r in await run_suite(adapters,enabled=True))
    adapters[Check.SSE]=AsyncMock(side_effect=RuntimeError('SYNTHETIC_PRIVATE_MARKER'))
    results=await run_suite(adapters,enabled=True)
    assert next(r for r in results if r.check==Check.SSE).state=='FAILED'
    assert 'SYNTHETIC_PRIVATE_MARKER' not in str(results)
    assert all(r.state=='DATA_SOURCE_UNAVAILABLE' for r in await run_suite({},enabled=True))
    with pytest.raises(ValueError):await run_suite({'arbitrary_route':AsyncMock()})
