import { it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import type { SettingsSection } from '../../../types';
import { mockApi } from './observability/fixtures.test-support';
vi.mock('../../../state/AppState', () => ({ useAppState: () => { const [section, setSection] = useState<SettingsSection>('usage'); return { state: { settingsModal: { section } }, setSettingsSection: setSection, connectorList: [], skillList: [] }; } }));
import { SettingsModal } from './SettingsModal';
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('keeps application categories with no observability entry or API requests', async () => {
    const api = mockApi();
    const view = render(<SettingsModal open onOpenChange={() => { }} />);
    for (const label of ['Usage', 'Connectors', 'Skills']) {
        fireEvent.click(screen.getByRole('button', { name: label }));
        expect(screen.getByRole('heading', { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByText('Observability & FinOps')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open Observability Console' })).not.toBeInTheDocument();
    view.rerender(<SettingsModal open={false} onOpenChange={() => { }} />);
    view.rerender(<SettingsModal open onOpenChange={() => { }} />);
    expect(screen.queryByText('Observability & FinOps')).not.toBeInTheDocument();
    expect(api.mock.calls.some(([url]) => String(url).includes('/observability/'))).toBe(false);
});
it('dialog retains keyboard close and labelled categories', async () => {
    const change = vi.fn(); render(<SettingsModal open onOpenChange={change} />);
    expect(screen.getByRole('navigation', { name: 'Settings categories' })).toBeInTheDocument();
    await userEvent.setup().keyboard('{Escape}'); expect(change).toHaveBeenCalledWith(false);
});
