import { cn } from '../../../../lib/cn';
const styles: Record<string, string> = { COMPLETED: 'text-success', healthy: 'text-success', FAILED: 'text-danger', TIMEOUT: 'text-danger', STALLED: 'text-warning', STALE: 'text-warning', degraded: 'text-warning', unavailable: 'text-danger' };
export function StatusBadge({ status, classification }: {
    status: string;
    classification?: string;
}) {
    const tone = ['COMPLETED', 'healthy'].includes(status) ? 'success' : ['FAILED', 'TIMEOUT', 'unavailable'].includes(status) ? 'danger' : ['STALLED', 'STALE', 'degraded'].includes(status) || classification === 'STALE' ? 'warning' : status === 'RUNNING' ? 'info' : 'neutral';
    return <span data-tone={tone} className={cn('obs-status-chip inline-flex flex-wrap items-center gap-1 rounded-md bg-surface-hover px-2 py-1 text-xs font-medium', styles[status] ?? 'text-secondary')}>
  {status === 'unknown' ? 'Unknown' : status}{classification === 'STALE' && <span className="text-warning"> · STALE</span>}
 </span>;
}
