import { Link, useRouter } from '../../lib/router';
import { OBSERVABILITY_ROUTE } from '../../lib/routes';
import { Component } from 'react';
import type { ReactNode } from 'react';
import { DataRetentionPanel } from '../shell/settings/observability/DataRetentionPanel';
import { IntegrationsPanel } from '../shell/settings/observability/IntegrationsPanel';
import { AccessPanel } from '../shell/settings/observability/AccessPanel';
import { ConfigTable } from '../shell/settings/observability/ConfigTable';
import { getObservabilityConfig } from '../../api/observability';
import { useObservabilityQuery } from '../shell/settings/observability/useObservabilityQuery';
import { ReadState, buttonClass } from '../shell/settings/observability/ReadState';
const sections = ['Data & Retention', 'Integrations', 'Access', 'Effective Configuration'] as const;
class PanelBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    render() { return this.state.failed ? <p role="alert">This configuration view is unavailable.</p> : this.props.children; }
}
function EffectiveConfiguration() {
    const q = useObservabilityQuery('console-effective-config', getObservabilityConfig);
    return <section><h3 className="mb-3 text-base text-primary">Effective Configuration</h3><p className="mb-3 text-xs text-tertiary">Environment, diagnostics and reliability policy are view-only. Source-controlled configuration is authoritative.</p><ReadState query={q}>{c => <ConfigTable config={c} include={() => true} />}</ReadState></section>;
}
export default function ObservabilityConfiguration() {
    const { pathname } = useRouter();
    const slugs = ['data-retention', 'integrations', 'access', 'effective-configuration'];
    const selected = slugs.indexOf(pathname.split('/')[4] ?? 'data-retention');
    const section = sections[selected < 0 ? 0 : selected];
    return <div className="min-w-0"><h2 className="mb-3 text-base font-medium text-primary">Configuration</h2>
        <p className="mb-3 text-sm text-secondary">Effective configuration and policy. Changes remain owned by infrastructure and backend policy.</p>
        <nav className="mb-4 flex flex-wrap gap-1" aria-label="Observability configuration sections">{sections.map((s, i) => <Link key={s} to={`${OBSERVABILITY_ROUTE}/configuration/${slugs[i]}`} className={buttonClass} aria-current={s === section ? 'page' : undefined}>{s}</Link>)}</nav>
        <PanelBoundary key={section}>{section === 'Data & Retention' ? <DataRetentionPanel /> : section === 'Integrations' ? <IntegrationsPanel /> : section === 'Access' ? <AccessPanel /> : <EffectiveConfiguration />}</PanelBoundary>
    </div>;
}
