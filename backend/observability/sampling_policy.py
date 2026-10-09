"""Collector policy mirror; never called by SLO/financial or execution decisions."""
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import re

VERSION = 'sampling-v1'
ARCHIVE_VERSION = 'archive-v1'

class TraceClass(str, Enum):
    SAFETY = 'SAFETY'
    GOVERNED = 'GOVERNED'
    FAILED = 'FAILED'
    TIMEOUT = 'TIMEOUT'
    EVER_STALLED = 'EVER_STALLED'
    HEALTHY = 'HEALTHY'
    UNKNOWN = 'UNKNOWN'

EXCEPTIONAL = frozenset(TraceClass) - {TraceClass.HEALTHY, TraceClass.UNKNOWN}

def classify(*, status, safety=False, governed=False, ever_stalled=False):
    # Caller supplies server-owned facts; no text inspection or model classification.
    if safety is True:
        return TraceClass.SAFETY
    if governed is True:
        return TraceClass.GOVERNED
    if status == 'FAILED':
        return TraceClass.FAILED
    if status == 'TIMEOUT':
        return TraceClass.TIMEOUT
    if ever_stalled is True:
        return TraceClass.EVER_STALLED
    return TraceClass.HEALTHY if status == 'COMPLETED' else TraceClass.UNKNOWN

@dataclass(frozen=True)
class Decision:
    cloud_trace: bool
    archive: bool
    degraded: bool
    trace_class: TraceClass

def decide(trace_id, trace_class, healthy_probability=1.0):
    if not isinstance(trace_id, str) or not re.fullmatch('[0-9a-f]{32}', trace_id) or int(trace_id,16)==0:
        raise ValueError('Invalid trace identity')
    degraded = False
    try:
        trace_class = TraceClass(trace_class)
    except (ValueError, TypeError):
        trace_class, degraded = TraceClass.UNKNOWN, True
    if type(healthy_probability) not in (int,float) or not 0 <= healthy_probability <= 1:
        healthy_probability, degraded = 1.0, True
    exceptional = trace_class in EXCEPTIONAL
    retain = exceptional or trace_class == TraceClass.UNKNOWN or (
        int.from_bytes(sha256(bytes.fromhex(trace_id)).digest()[:8], 'big') / 2**64 < healthy_probability)
    return Decision(retain, exceptional, degraded, trace_class)

def collector_environment(values):
    """Resolve untrusted configuration before launching the native Collector.

    Invalid percentages retain 100% healthy traffic and preserve the independent
    exceptional policy. Production reduction remains provisional and disabled.
    The degradation value is bounded operational metadata, never payload content.
    """
    raw=values.get('HEALTHY_TRACE_PERCENT',100)
    try:
        percent=float(raw)
        valid=__import__('math').isfinite(percent) and 0<percent<=100
    except (TypeError,ValueError):valid=False;percent=100
    if not valid:percent=100
    return {'HEALTHY_TRACE_PERCENT':str(percent),'SLOPANOC_SAMPLING_POLICY_STATE':'CURRENT' if valid else 'DEGRADED_SAFE_FALLBACK'}
