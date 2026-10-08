"""Bounded asynchronous transport shared by safe span/log processors.

Only already-redacted SDK records enter this queue. Exporters never run on the
producer thread. Daemon workers and bounded joins prevent outages holding exit.
"""
from collections import deque
from threading import Condition, Thread
import time


class ExportQueue:
    def __init__(self, exporter, signal, config, health):
        self.exporter, self.signal, self.config, self.health = exporter, signal, config, health
        self._records = deque()
        self._condition = Condition()
        self._busy = False
        self._flush_waiters = 0
        self._closed = False
        self._shutdown_started = False
        self._worker = Thread(target=self._run, name=f'slopanoc-{signal}-export', daemon=True)
        self._worker.start()

    def put(self, record):
        with self._condition:
            if self._closed or len(self._records) >= self.config.otel_queue_capacity:
                self.health.add(self.signal, 'dropped')
                return False
            self._records.append(record)
            self.health.add(self.signal, 'accepted')
            self.health.set(self.signal, 'queue_size', len(self._records))
            if len(self._records) >= self.config.otel_batch_size:
                self._condition.notify_all()
            return True

    def _run(self):
        while True:
            with self._condition:
                if not self._records and self._closed:
                    return
                if not self._closed and not self._flush_waiters and len(self._records) < self.config.otel_batch_size:
                    self._condition.wait(self.config.otel_batch_interval_seconds)
                if not self._records:
                    continue
                batch = [self._records.popleft() for _ in range(min(len(self._records), self.config.otel_batch_size))]
                self._busy = True
                self.health.set(self.signal, 'queue_size', len(self._records))
            started = time.monotonic()
            self.health.add(self.signal, 'attempts')
            try:
                result = self.exporter.export(batch)
                ok = result.name == 'SUCCESS'
            except Exception:
                ok = False
            self.health.add(self.signal, 'exported' if ok else 'failed', len(batch))
            if not ok:
                self.health.add(self.signal, 'dropped', len(batch))
            self.health.set(self.signal, 'export_duration_ms', (time.monotonic()-started)*1000)
            with self._condition:
                self._busy = False
                self._condition.notify_all()

    def flush(self, timeout_millis=30000):
        deadline = time.monotonic()+max(timeout_millis, 0)/1000
        with self._condition:
            self._flush_waiters += 1
            self._condition.notify_all()
            try:
                while self._records or self._busy:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        return False
                    self._condition.wait(remaining)
                return True
            finally:
                self._flush_waiters -= 1

    def shutdown(self, timeout_millis=0):
        with self._condition:
            if self._shutdown_started:
                return
            self._shutdown_started = True
            self._closed = True
            self._condition.notify_all()
        self._worker.join(max(0, timeout_millis)/1000)
        if self._worker.is_alive():
            with self._condition:
                self.health.add(self.signal, 'shutdown_failed')
                self.health.add(self.signal, 'dropped', len(self._records))
                self._records.clear()
                self.health.set(self.signal, 'queue_size', 0)
        # Even an injected broken shutdown cannot hold interpreter exit.
        Thread(target=self._close_exporter, daemon=True).start()

    def _close_exporter(self):
        try:
            self.exporter.shutdown()
        except Exception:
            self.health.add(self.signal, 'shutdown_failed')
