"""OTLP HTTP only. Explicit settings override SDK exporter environment defaults."""
import requests
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http import Compression


class CollectorSession(requests.Session):
    def post(self, url, **kwargs):
        kwargs['allow_redirects'] = False
        return super().post(url, **kwargs)


def exporters(config):
    base = config.otel_exporter_otlp_endpoint.get_secret_value().rstrip('/')
    result = {}
    for signal, cls in [('trace', OTLPSpanExporter), ('metric', OTLPMetricExporter), ('log', OTLPLogExporter)]:
        # Ignore inherited proxy/auth env. Application->sidecar has no credentials.
        session = CollectorSession()
        session.trust_env = False
        result[signal] = cls(endpoint=base+'/v1/'+{'trace':'traces','metric':'metrics','log':'logs'}[signal],
            headers={'Content-Type':'application/x-protobuf'}, certificate_file=True, timeout=config.otel_export_timeout_seconds,
            compression=Compression.NoCompression, session=session)
        # Pinned SDK otherwise falls back to inherited client certificate env.
        # Never adopt credential paths outside the single Settings contract.
        result[signal]._client_cert = None
    return result
