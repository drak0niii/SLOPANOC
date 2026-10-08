import type { ReactNode } from 'react';
import type { QueryState, ReadError } from './useObservabilityQuery';
export const buttonClass = 'inline-flex items-center justify-center rounded-lg px-3 py-2 text-sm font-medium text-secondary hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:opacity-40';
export const cardClass = 'rounded-xl border border-subtle/50 p-4';
const messages: Record<ReadError, string> = {
    unauthorized: 'Diagnostics are unavailable for the current authenticated context.',
    denied: 'Diagnostic access is not permitted for this view or environment.',
    'not-found': 'Run or diagnostic API is unavailable.', unavailable: 'Diagnostics persistence is unavailable.',
    failed: 'Diagnostic request failed. Please try again.', malformed: 'The diagnostic response could not be read safely.',
    incompatible: 'This diagnostics schema is not supported by this UI version.', throttled: 'Diagnostic request limit reached. Try refreshing later.'
};
export function ReadState<T>({ query, children, empty }: {
    query: QueryState<T> & {
        refresh: () => void;
    };
    children: (data: T) => ReactNode;
    empty?: boolean;
}) {
    return <div aria-busy={query.loading}>
  <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm text-tertiary">
   <span>{query.updatedAt ? `Last API refresh: ${new Date(query.updatedAt).toLocaleTimeString()}` : 'No data fetched yet.'}</span>
   <button type="button" className={buttonClass} disabled={query.loading} onClick={query.refresh}>Refresh</button>
  </div>
  {query.loading && <p role="status" className="text-sm text-tertiary">Loading diagnostics…</p>}
  {query.error && <p role="alert" className="rounded-lg border border-subtle/50 p-3 text-sm text-secondary">{messages[query.error]}</p>}
  {query.data !== undefined && !query.error && (empty ? <p className="text-sm text-tertiary">No data available.</p> : children(query.data))}
 </div>;
}
