import { it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import type { SettingsSection } from '../../../types';
import { mockApi } from './observability/fixtures.test-support';
const selection = vi.hoisted(() => ({ section: 'usage' as SettingsSection }));
vi.mock('../../../state/AppState', () => ({ useAppState: () => { const [section, setSection] = useState(selection.section); return { state: { settingsModal: { section } }, setSettingsSection: setSection, connectorList: [], skillList: [] }; } }));
import { SettingsModal } from './SettingsModal';
afterEach(() => { cleanup(); vi.restoreAllMocks(); selection.section = 'usage'; });
it('preserves old categories, lazy loads observability and close/reopen clears data', async () => {
    const api = mockApi();
    const v = render(<SettingsModal open onOpenChange={() => { }}/>);
    expect(screen.getByRole('heading', { name: 'Usage' })).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
    for (const label of ['Connectors', 'Skills']) {
        fireEvent.click(screen.getByRole('button', { name: label }));
        expect(screen.getByRole('heading', { name: label })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole('button', { name: 'Observability & FinOps' }));
    await screen.findByRole('navigation', { name: 'Observability & FinOps sections' });
    await screen.findAllByRole('button', { name: /Open run/ });
    v.rerender(<SettingsModal open={false} onOpenChange={() => { }}/>);
    expect(screen.queryByRole('navigation', { name: 'Observability & FinOps sections' })).not.toBeInTheDocument();
    v.rerender(<SettingsModal open onOpenChange={() => { }}/>);
    await screen.findByRole('heading', { name: 'Overview' });
});
it('dialog retains keyboard close and labelled categories', async () => { const change = vi.fn(); render(<SettingsModal open onOpenChange={change}/>); expect(screen.getByRole('navigation', { name: 'Settings categories' })).toBeInTheDocument(); await userEvent.setup().keyboard('{Escape}'); expect(change).toHaveBeenCalledWith(false); });
