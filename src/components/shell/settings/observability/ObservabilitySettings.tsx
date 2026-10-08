import { Component, useState } from 'react';
import type { ReactNode } from 'react';
import { ObservabilityNav } from './ObservabilityNav';
import type { Section } from './ObservabilityNav';
import { OverviewPanel } from './OverviewPanel';
import { TracingPanel } from './TracingPanel';
import { ReliabilityPanel } from './ReliabilityPanel';
import { SlosPanel } from './SlosPanel';
import { FinOpsPanel } from './FinOpsPanel';
import { DataRetentionPanel } from './DataRetentionPanel';
import { IntegrationsPanel } from './IntegrationsPanel';
import { AccessPanel } from './AccessPanel';
import { DiagnosticsPanel } from './DiagnosticsPanel';
const panels = { 'Overview': OverviewPanel, 'Tracing': TracingPanel, 'Reliability': ReliabilityPanel, 'SLOs': SlosPanel, 'FinOps': FinOpsPanel, 'Data & Retention': DataRetentionPanel, 'Integrations': IntegrationsPanel, 'Access': AccessPanel, 'Diagnostics': DiagnosticsPanel };
class PanelBoundary extends Component<{
    children: ReactNode;
}, {
    failed: boolean;
}> {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    render() { return this.state.failed ? <p role="alert" className="text-sm text-secondary">This diagnostic view is unavailable. Select another section to continue.</p> : this.props.children; }
}
export default function ObservabilitySettings() { const [section, setSection] = useState<Section>('Overview'); const Panel = panels[section]; return <div className="min-w-0"><h2 className="mb-3 text-base font-medium text-primary">Observability & FinOps</h2><ObservabilityNav value={section} onChange={setSection}/><PanelBoundary key={section}><Panel /></PanelBoundary></div>; }
