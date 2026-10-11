import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ArchiveTrace } from './ArchiveTrace';
import { getArchive } from '../../api/observabilityArchive';
const trace = '1'.repeat(32), digest = 'a'.repeat(64);
const page = { trace_id: trace, digest, spans: [
    { trace_id: trace, span_id: '2'.repeat(16), name: 'slopanoc.turn', start_ns: 100, end_ns: 300, status: 1, attributes: { prompt: 'PRIVATE_PROMPT' } },
    { trace_id: trace, span_id: '3'.repeat(16), parent_span_id: '2'.repeat(16), name: 'slopanoc.agent', start_ns: 150, end_ns: 250, status: 1, attributes: { 'slopanoc.agent': 'team_manager', reasoning: 'PRIVATE_REASONING' } }
] };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('projects retained hierarchy without exposing raw attributes and accepts omitted root/terminal page fields', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(page)));
    const result = await getArchive(trace, digest, 0);
    expect(result.spans[0].parent_span_id).toBeNull(); expect(result.next_offset).toBeNull();
    expect(JSON.stringify(result)).not.toContain('PRIVATE_');
    render(<ArchiveTrace traceId={trace} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Archive manifest digest' }), { target: { value: digest } });
    fireEvent.click(screen.getByRole('button', { name: 'Read retained trace' }));
    await screen.findByText(/team manager/); expect(document.body.textContent).not.toContain('PRIVATE_');
    expect(screen.getAllByLabelText('Retained span timing')).toHaveLength(2);
});
it('unregistered names are fixed labels, never arbitrary diagnostic content', async () => {
    const raw = structuredClone(page); raw.spans[0].name = 'PRIVATE_REASONING';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(raw)));
    expect((await getArchive(trace, digest, 0)).spans[0].name).toBe('Unknown span');
});
it('protected archive denial clears retained spans and ignores delayed older response', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(page)));
    render(<ArchiveTrace traceId={trace} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Archive manifest digest' }), { target: { value: digest } });
    fireEvent.click(screen.getByRole('button', { name: 'Read retained trace' })); await screen.findByText(/team manager/);
    fetcher.mockResolvedValue(new Response('{}', { status: 403 }));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); await screen.findByRole('alert');
    expect(screen.queryByText(/team manager/)).not.toBeInTheDocument();
});
