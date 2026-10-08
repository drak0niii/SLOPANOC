import { cn } from '../../../../lib/cn';
import { buttonClass } from './ReadState';
export const sections = ['Overview', 'Tracing', 'Reliability', 'SLOs', 'FinOps', 'Data & Retention', 'Integrations', 'Access', 'Diagnostics'] as const;
export type Section = typeof sections[number];
export function ObservabilityNav({ value, onChange }: {
    value: Section;
    onChange: (value: Section) => void;
}) { return <nav aria-label="Observability & FinOps sections" className="mb-5 flex flex-wrap gap-1 border-b border-subtle/50 pb-3">{sections.map(s => <button key={s} type="button" aria-current={value === s ? 'page' : undefined} onClick={() => onChange(s)} className={cn(buttonClass, value === s && 'bg-surface-hover text-primary')}>{s}</button>)}</nav>; }
