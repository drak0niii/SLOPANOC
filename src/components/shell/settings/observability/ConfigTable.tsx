import type { EffectiveConfiguration } from '../../../../api/observabilityTypes';
import { name } from './format';
export function ConfigTable({ config, include }: {
    config: EffectiveConfiguration[];
    include: (setting: string) => boolean;
}) {
    const rows = config.filter(c => include(c.setting));
    return <><p className="mb-3 text-sm text-tertiary">Effective configuration · Read-only. Managed by backend/environment policy.</p>
 {!rows.length ? <p className="text-sm text-tertiary">No effective values reported for this section.</p> : <dl className="divide-y divide-subtle/50 rounded-xl border border-subtle/50 px-4">{rows.map(c => <div key={c.setting} className="flex flex-wrap items-baseline justify-between gap-2 py-3 text-sm"><dt className="text-secondary">{name(c.setting)}</dt><dd className="text-right text-primary">{String(c.value)}{c.setting.endsWith('_seconds') ? 's' : c.setting.endsWith('_hours') ? 'h' : c.setting.endsWith('_days') ? 'd' : ''}<span className="block text-xs text-tertiary">{name(c.owner)} · {c.config_version}</span></dd></div>)}</dl>}
 </>;
}
