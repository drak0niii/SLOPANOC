import { RouterProvider } from '../../lib/router';
import { it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ObservabilityConfiguration from './ObservabilityConfiguration';
import { mockApi } from '../shell/settings/observability/fixtures.test-support';
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, '', '/'); });
it('moves all read-only policy views into URL-backed Resources configuration', async () => {
    const api = mockApi(); window.history.replaceState({}, '', '/resources/observability/configuration'); render(<ObservabilityConfiguration />, { wrapper: RouterProvider });
    expect(screen.queryByRole('link', { name: 'Open Observability Console' })).not.toBeInTheDocument();
    for (const section of ['Data & Retention', 'Integrations', 'Access', 'Effective Configuration']) {
        fireEvent.click(screen.getByRole('link', { name: section }));
        expect(screen.getByRole('heading', { name: section })).toBeInTheDocument();
    }
    await screen.findByText(/Environment, diagnostics/);
    expect(screen.queryByRole('button', { name: 'Overview' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'FinOps' })).not.toBeInTheDocument();
    expect(api.mock.calls.some(([url]) => /\/runs|\/sse-receipts/.test(String(url)))).toBe(false);
});
it('keyboard policy navigation is native and labelled', async () => {
    mockApi(); window.history.replaceState({}, '', '/resources/observability/configuration'); render(<ObservabilityConfiguration />, { wrapper: RouterProvider }); const user = userEvent.setup();
    screen.getByRole('link', { name: 'Access' }).focus(); await user.keyboard('{Enter}');
    expect(screen.getByRole('heading', { name: 'Access' })).toBeInTheDocument();
});
it('configuration denial stays safe', async () => {
    mockApi({ configDenied: true }); render(<ObservabilityConfiguration />, { wrapper: RouterProvider });
    await screen.findAllByRole('alert'); expect(screen.queryByRole('table')).not.toBeInTheDocument();
});
