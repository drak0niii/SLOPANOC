"""Bounded in-memory sinks for development; only sanitized records are supplied."""
from collections import deque
from threading import Lock
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk._logs.export import LogRecordExportResult
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult


class LocalExporter:
    def __init__(self, signal, capacity=2048):
        self.signal, self._records, self._lock = signal, deque(maxlen=capacity), Lock()

    def export(self, records):
        with self._lock:
            self._records.extend(records)
        return SpanExportResult.SUCCESS if self.signal == 'trace' else LogRecordExportResult.SUCCESS

    def snapshot(self):
        with self._lock:
            return tuple(self._records)

    def shutdown(self):
        pass


class LocalMetricExporter(MetricExporter):
    def __init__(self, capacity=64):
        super().__init__()
        self.records = deque(maxlen=capacity)

    def export(self, metrics_data, timeout_millis=10000, **kwargs):
        self.records.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis=10000):
        return True

    def shutdown(self, timeout_millis=30000, **kwargs):
        pass
