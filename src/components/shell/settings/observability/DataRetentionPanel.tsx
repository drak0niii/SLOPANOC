import { getObservabilityConfig } from '../../../../api/observability';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState } from './ReadState';
import { ConfigTable } from './ConfigTable';
import { getPolicies } from '../../../../api/observabilityPolicy';
const labels: Record<string, string> = { active_terminal: 'Terminal runtime state', ordinary_summary: 'Ordinary summaries', exceptional_summary: 'Exceptional summaries', application_logs: 'Application logs', cloud_trace: 'Cloud Trace full traces', exceptional_archive: 'Exceptional full trace archive', analytical_metrics: 'Minimum analytical access', financial_history: 'Financial history', slo_receipts: 'SLO receipts', slo_working_buckets: 'SLO working aggregates' };
export function DataRetentionPanel() {
    const q = useObservabilityQuery('retention', getObservabilityConfig);
    const policies = useObservabilityQuery('retention-policies', getPolicies);
    return <section><h3 className="mb-3 text-base font-medium text-primary">Data & Retention</h3>
        <ReadState query={q}>{c => <ConfigTable config={c} include={k => /^projection_(terminal_hours|success_days|exceptional_days|checkpoint_seconds|stale_seconds|clock_grace_seconds)$/.test(k) || k === 'telemetry_schema_version' || k === 'trace_sample_rate'} />}</ReadState>
        <ReadState query={policies}>{p => <><table className="mt-4 w-full text-left text-sm text-secondary"><caption className="mb-2 text-left text-tertiary">Read-only policy targets</caption><tbody>{p.retention.map(r => <tr key={r.dataset}><th className="py-2 font-normal">{labels[r.dataset]}</th><td>{r.dataset === 'financial_history' ? p.financialMonths : r.horizon} {r.unit}</td><td>{r.dependencies ? 'Dependency protected' : r.owner === 'provider' ? 'Provider controlled' : 'Eligibility target'}</td></tr>)}</tbody></table>
            <p className="mt-3 text-sm text-tertiary">SDK capture: 100%. Healthy trace retention: 100%; reduction is provisional and disabled. Exceptional classes are always retained. Financial accounting and SLO populations are unsampled.</p>
            <p className="mt-2 text-sm text-tertiary">Policy versions: {p.versions.join(', ')}.</p>
            <p className="mt-2 text-sm text-tertiary">Production archive: not deployed. Production retention scheduling: not executed. The archive has a 90-day primary horizon; lifecycle deletion is asynchronous and soft-delete behavior may extend physical retention.</p></>}</ReadState>
        <p className="mt-4 text-sm text-tertiary">M7 v1 contract: timeline cap of 128 significant events. This is a contract limit, not an editable setting.</p>
    </section>;
}
