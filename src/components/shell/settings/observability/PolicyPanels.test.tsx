import { afterEach, it, expect, vi } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { DataRetentionPanel } from './DataRetentionPanel';
import { AccessPanel } from './AccessPanel';
import { config, response } from './fixtures.test-support';
import { policyFixture } from '../../../../api/observabilityPolicy.test-support';
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('renders truthful read-only policy targets and never persists protected responses', async () => {
    const marker = 'PRIVATE_PROMPT_RESPONSE_TEAMS_SECRET_REASONING';
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => String(input).endsWith('/policies') ? response({ ...policyFixture, secret: marker }) : response(config));
    render(<DataRetentionPanel />);
    await screen.findByText('Cloud Trace full traces');
    expect(screen.getByText('Exceptional full trace archive')).toBeInTheDocument();
    expect(screen.getByText(/Production archive: not deployed/)).toHaveTextContent('soft-delete behavior');
    expect(screen.getByText(/SDK capture: 100%/)).toHaveTextContent('reduction is provisional and disabled');
    expect(document.body.textContent).not.toContain(marker);
    expect(storage).not.toHaveBeenCalled();
    for (const label of ['Save', 'Delete', 'Download', 'Grant access']) expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
});
it('shows only server capability scope and ignores a delayed response after unmount', async () => {
    let delayed!: (value: Response) => void;
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
        if (String(input).endsWith('/access')) return new Promise(resolve => { delayed = resolve; });
        return response({}, 403);
    });
    const view = render(<AccessPanel />);
    view.unmount();
    await act(async () => delayed(response({ verified: true, capabilities: ['financial_data'], environments: ['production'], email: 'PRIVATE_IDENTITY' })));
    expect(document.body.textContent).not.toContain('financial_data');
    expect(document.body.textContent).not.toContain('PRIVATE_IDENTITY');
    expect(storage).not.toHaveBeenCalled();
});

it('does not restore delayed archive state after run authorization is revoked', async () => {
    const { RunDetail } = await import('./RunDetail');
    const { fireEvent, waitFor } = await import('@testing-library/react');
    const { run, runId, technical } = await import('./fixtures.test-support');
    let delayed!: (value: Response) => void;
    let revoked = false;
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
        const path = String(input);
        if (path.endsWith('/retention')) return new Promise(resolve => { delayed = resolve; });
        if (path.includes('/timeline')) return response({ events: [], truncated_count: 0 });
        return revoked ? response({}, 403) : response(run({ technical }));
    });
    render(<RunDetail runId={runId}/>);
    await screen.findByText('Deployment & trace correlation');
    await waitFor(() => expect(delayed).toBeDefined());
    revoked = true;
    fireEvent.click(screen.getAllByText('Refresh')[0]);
    await waitFor(() => expect(screen.queryByText('Deployment & trace correlation')).not.toBeInTheDocument());
    await act(async () => delayed(response({ run_id: runId, instrumentation: 'COMPLETE', cloud_trace: 'NOT_LIVE_CONFIRMED', exceptional_archive: 'RETAINED', production_binding: 'NOT_DEPLOYED', prompt: 'PRIVATE_PROMPT' })));
    expect(screen.queryByText('RETAINED')).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain('PRIVATE_PROMPT');
    expect(storage).not.toHaveBeenCalled();
});
