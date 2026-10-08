import { getObservabilityHealth } from '../../../../api/observability';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState, cardClass } from './ReadState';
import { StatusBadge } from './StatusBadge';
export function IntegrationsPanel() {
    const q = useObservabilityQuery('integrations', getObservabilityHealth);
    return <section><h3 className="mb-3 text-base font-medium text-primary">Integrations</h3><ReadState query={q}>{h => <div className="mb-3 grid gap-3 sm:grid-cols-2"><div className={cardClass}><h4 className="text-sm text-secondary">OpenTelemetry Collector</h4><p className="my-2 text-sm text-tertiary">Endpoint {h.collector_configured ? 'configured' : 'not configured'} · Collector health not reported.</p><span className="text-xs text-tertiary">Application exporter: </span><StatusBadge status={h.exporter_state}/></div><div className={cardClass}><h4 className="text-sm text-secondary">Persistence / Cloud SQL</h4><p className="my-2 text-sm text-tertiary">Diagnostic repository: <StatusBadge status={h.persistence}/></p><p className="text-xs text-tertiary">Cloud SQL infrastructure health is not reported.</p></div></div>}</ReadState>
 <div className="grid gap-3 sm:grid-cols-2">{['Cloud Trace', 'Cloud Monitoring', 'Cloud Logging', 'Teams gateway — Power Automate', 'GCS', 'Secret Manager', 'Knowledge subsystem', 'Model provider'].map(label => <div key={label} className={cardClass}><h4 className="text-sm text-secondary">{label}</h4><p className="mt-2 text-xs text-tertiary">Integration health is not reported.</p>{label.includes('Power Automate') && <p className="mt-2 text-xs text-tertiary">Direct Microsoft Graph telemetry is not available.</p>}</div>)}</div></section>;
}
