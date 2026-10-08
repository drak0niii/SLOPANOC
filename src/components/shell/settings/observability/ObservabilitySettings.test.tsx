import { it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ObservabilitySettings from './ObservabilitySettings';
import { sections } from './ObservabilityNav';
import { mockApi } from './fixtures.test-support';
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('all nine sections navigate with active state and contain local errors', async () => {
    mockApi({ configDenied: true });
    render(<ObservabilitySettings />);
    for (const section of sections) {
        fireEvent.click(screen.getByRole('button', { name: section }));
        expect(screen.getByRole('button', { name: section })).toHaveAttribute('aria-current', 'page');
        expect(screen.getByRole('heading', { name: section })).toBeInTheDocument();
    }
    await screen.findByRole('button', { name: /Open run/ });
    fireEvent.click(screen.getByRole('button', { name: 'Reliability' }));
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: 'Diagnostics' }));
    await screen.findByRole('button', { name: /Open run/ });
});
it('keyboard section navigation is native and labelled', async () => { mockApi(); render(<ObservabilitySettings />); const user = userEvent.setup(); const button = screen.getByRole('button', { name: 'SLOs' }); button.focus(); await user.keyboard('{Enter}'); expect(button).toHaveAttribute('aria-current', 'page'); await user.tab(); expect(screen.getByRole('button', { name: 'FinOps' })).toHaveFocus(); await user.keyboard(' '); expect(screen.getByText('Accounting starts in M10.')).toBeInTheDocument(); });
it('scope failures remain local in the inverse direction', async () => { mockApi({ healthFailed: true }); render(<ObservabilitySettings />); await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: 'Data & Retention' })); await screen.findByText('24h'); });
