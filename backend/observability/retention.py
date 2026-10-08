"""Operational retention is separate from OTel and financial retention."""
from datetime import timedelta
from .stages import TERMINAL_STATUSES
from .projection import utc


def classification(state, now, config):
    if state.status in TERMINAL_STATUSES:
        return 'TERMINAL'
    now = utc(now)
    grace = timedelta(seconds=config.projection_clock_grace_seconds)
    stale = now > state.heartbeat_at + timedelta(seconds=config.projection_stale_seconds) + grace
    stale |= state.total_deadline_at is not None and now > state.total_deadline_at + grace
    return 'STALE' if stale else 'STALLED' if state.status.value == 'STALLED' else 'ACTIVE'


def expiry(state, config):
    exceptional = state.retention_class == 'exceptional' or state.ever_stalled or state.status.value != 'COMPLETED'
    days = config.projection_exceptional_days if exceptional else config.projection_success_days
    anchor = state.terminal_at or max(state.heartbeat_at, state.total_deadline_at or state.heartbeat_at)
    return anchor + timedelta(days=days)
