import { getSLOs } from '../../../../api/observability';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState, cardClass } from './ReadState';
const percent = (v: number | null) => v === null ? 'Not evaluated' : `${(v * 100).toFixed(2)}%`;
const states = { HEALTHY: 'Healthy', BREACHED: 'Breached', BURNING: 'Burning', INSUFFICIENT_DATA: 'Insufficient data', DATA_SOURCE_UNAVAILABLE: 'Data source unavailable', STALE_DATA: 'Stale data', DEFINED_NOT_EVALUATED: 'Defined — not evaluated' };
export function SlosPanel() {
    const query = useObservabilityQuery('slos', getSLOs);
    return <section><h3 className="text-base font-medium text-primary">SLOs</h3>
        <p className="my-3 text-sm text-secondary">Backend evaluation over a rolling 28 days. Provisional objectives — calibration required.</p>
        <ReadState query={query}>{data => <><p className="mb-3 text-xs text-tertiary">Environment: {data.environment}</p>
            <div className="grid gap-2 sm:grid-cols-2">{data.items.map(slo => <article key={slo.slo_id} className={cardClass}>
                <h4 className="text-sm font-medium text-primary">{slo.name}</h4>
                <p className="my-1 text-sm text-secondary">{states[slo.state]}</p>
                {slo.state === 'DEFINED_NOT_EVALUATED' ? <p className="text-xs text-tertiary">Data source available after M10</p> : <>
                    <dl className="space-y-1 text-xs text-secondary">
                        <div><dt className="inline">Current SLI: </dt><dd className="inline">{percent(slo.current_value)}</dd></div>
                        <div><dt className="inline">Objective ({slo.objective_status === 'PROVISIONAL' ? 'Provisional' : 'Architectural'}): </dt><dd className="inline">{percent(slo.objective)}{slo.threshold_seconds === null ? '' : ` within ${slo.threshold_seconds}s`}</dd></div>
                        <div><dt className="inline">Budget remaining: </dt><dd className="inline">{percent(slo.remaining_fraction)}</dd></div>
                        <div><dt className="inline">Burn: </dt><dd className="inline">{slo.burn_tiers.length ? slo.burn_tiers.join(', ') : 'No active tier reported'}</dd></div>
                        <div><dt className="inline">Measurement coverage: </dt><dd className="inline">{percent(slo.coverage)}</dd></div>
                        <div><dt className="inline">Population: </dt><dd className="inline">{slo.eligible} measured · {slo.unknown} unknown · {slo.excluded} excluded</dd></div>
                    </dl>
                    <details className="mt-2 text-xs text-tertiary"><summary>Window &amp; freshness</summary>
                        <p>{new Date(slo.window_start).toLocaleString()} — {new Date(slo.window_end).toLocaleString()}</p>
                        <p>Evaluated: {new Date(slo.evaluated_at).toLocaleString()}</p>
                        <p>Source updated: {slo.source_last_updated ? new Date(slo.source_last_updated).toLocaleString() : 'Unavailable'}</p>
                        <p>Freshness limit: {slo.freshness_seconds}s · {slo.source_status}</p>
                    </details>
                </>}
            </article>)}</div></>}</ReadState>
    </section>;
}
