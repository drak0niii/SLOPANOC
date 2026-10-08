import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,cleanup,fireEvent} from '@testing-library/react';
import {FinOpsPanel} from './FinOpsPanel';
import {usage,health,at} from '../../../../api/finops.test-support';
import {sloFixture} from '../../../../api/sloFixture';
afterEach(()=>{cleanup();vi.restoreAllMocks();});
function mock(denied=false){return vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{
 const url=String(input);if(denied)return new Response('{}',{status:403});
 const cost=structuredClone(sloFixture.items.find(x=>x.slo_id==='cost_ledger_completeness')!);
 Object.assign(cost,{state:'BREACHED',source_status:'AVAILABLE',current_value:.99,eligible:100,good:99,bad:1,unknown:0,remaining_fraction:null,evaluated_at:at});
 return new Response(JSON.stringify(url.includes('runtime-usage')?usage:url.includes('ledger-health')?health:cost));
});}
it('shows server usage, debt, completeness and separate quantity coverage with no money',async()=>{
 mock();render(<FinOpsPanel/>);await screen.findByText(/99%/);expect(screen.getByText(/Quantity coverage: 95%/)).toBeInTheDocument();expect(screen.getByText(/Unresolved attempts: 1/)).toBeInTheDocument();expect(screen.getAllByText(/Unknown/).length).toBeGreaterThan(1);expect(document.body.textContent).not.toMatch(/EUR|USD|€|\$[0-9]/);for(const label of ['Billing reconciliation','Allocation','Unit economics','Budgets','Forecasts','Anomalies'])expect(screen.getByText(label)).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('Group usage by'),{target:{value:'workload'}});await screen.findByText(/99%/);
});
it('denies financial access safely and does not expose previous data after refresh revocation',async()=>{
 const fetcher=mock();render(<FinOpsPanel/>);await screen.findByText(/Unresolved attempts: 1/);fetcher.mockResolvedValue(new Response('{}',{status:403}));fireEvent.click(screen.getByRole('button',{name:'Refresh'}));await screen.findByRole('alert');expect(screen.queryByText(/Unresolved attempts/)).not.toBeInTheDocument();
});
