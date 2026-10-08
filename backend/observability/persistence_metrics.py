"""Bounded local subsystem health; no IDs, payloads or exception strings."""
from threading import Lock
import time
import json
import sys
from .projection import utcnow

FIELDS = ('accepted', 'coalesced', 'attempts', 'persisted', 'failed', 'retries',
          'rejected', 'terminal_unpersisted', 'shutdown_incomplete', 'invalid')


class ProjectionHealth:
    def __init__(self):
        self.lock = Lock()
        self.counts = dict.fromkeys(FIELDS, 0)
        self.last_success = None
        self.last_warning = 0

    def add(self, name, value=1):
        with self.lock:
            self.counts[name] += value
            if name == 'persisted':
                self.last_success = utcnow()
            warn = name in ('failed', 'rejected', 'terminal_unpersisted', 'invalid') and time.monotonic()-self.last_warning >= 30
            if warn:
                self.last_warning = time.monotonic()
        if warn:
            try:
                sys.stderr.write(json.dumps({'timestamp':utcnow().isoformat(), 'severity':'WARNING',
                    'service':'slopanoc', 'environment':'unknown', 'message':'telemetry.degraded',
                    'event_name':'telemetry.degraded', 'error_code':'TELEMETRY_DROPPED',
                    'trace_id':None,'span_id':None,'run_id':None,'metadata':{},'telemetry_schema_version':1})+'\n')
            except Exception:
                pass

    def snapshot(self):
        with self.lock:
            return dict(self.counts), self.last_success
