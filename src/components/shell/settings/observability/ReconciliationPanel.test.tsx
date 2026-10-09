import {controlPage} from '../../../../api/finops.test-support';
import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,cleanup,fireEvent} from '@testing-library/react';
import {ReconciliationPanel} from './ReconciliationPanel';
import {FinOpsPanel} from './FinOpsPanel';
import {reconciliationPage,billingSources,billingSummary,pricingStatus,usage,health} from '../../../../api/finops.test-support';
afterEach(()=>{cleanup();vi.restoreAllMocks();});
function mock(data=reconciliationPage){return vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{const url=String(input);if(/\/finops\/(budgets|forecasts|anomalies)/.test(url))return new Response(JSON.stringify({...controlPage,state:url.includes('budgets')?'NOT_CONFIGURED':'UNEVALUATED'}));return new Response(JSON.stringify(url.includes('reconciliation')?data:url.includes('billing-health')?billingSources:url.includes('billing-summary')?billingSummary:url.includes('pricing-status')?pricingStatus:url.includes('runtime-usage')?usage:health));});}
it('displays exact amounts, prominent unallocated, quantity variance, source coverage and partial state',async()=>{
 mock();render(<ReconciliationPanel/>);await screen.findByText(/Actual billed net: 99.9 EUR/);
 expect(screen.getByText(/Unallocated: 9.9 EUR/)).toBeInTheDocument();expect(screen.getByText(/Direct: 60 EUR/)).toBeInTheDocument();expect(screen.getByText(/Shared: 30 EUR/)).toBeInTheDocument();
 expect(screen.getByText(/Runtime 1000.*Billed 1100.*Difference 100/)).toBeInTheDocument();expect(screen.getByText(/Partial · 2026-01-10/)).toBeInTheDocument();expect(screen.queryByText('Fully reconciled')).not.toBeInTheDocument();expect(screen.getByText(/Mapping amount coverage ratio: 0.9/)).toBeInTheDocument();
});
it('stale backend presentation overrides historical reconciled state and day selector uses protected route',async()=>{
 const data=structuredClone(reconciliationPage);data.items[0].status='RECONCILED';data.items[0].display_state='Stale source';data.items[0].billing_freshness='STALE';const fetcher=mock(data);render(<ReconciliationPanel/>);await screen.findByText(/Stale source ·/);
 fireEvent.change(screen.getByLabelText('Reconciliation day'),{target:{value:'2026-01-10'}});await screen.findByText(/Stale source ·/);expect(fetcher.mock.calls.some(([url])=>String(url).endsWith('/reconciliation/2026-01-10'))).toBe(true);
});
it('revocation removes previous financial data',async()=>{
 const fetcher=mock();render(<ReconciliationPanel/>);await screen.findByText(/Actual billed net/);fetcher.mockResolvedValue(new Response('{}',{status:403}));fireEvent.click(screen.getByRole('button',{name:'Refresh'}));await screen.findByRole('alert');expect(screen.queryByText(/Actual billed net/)).not.toBeInTheDocument();
});
it('mounts M13 and M14 panels inside the current Settings shell',async()=>{
 mock();render(<FinOpsPanel/>);await screen.findByText(/Unallocated: 9.9/);expect(screen.getByRole('region',{name:'Unit economics'})).toBeInTheDocument();expect(screen.queryByText('Available after M14')).not.toBeInTheDocument();expect(screen.getByText('Budgets')).toBeInTheDocument();expect(screen.getByText('Forecasts')).toBeInTheDocument();expect(screen.getByText('Anomalies')).toBeInTheDocument();expect(screen.queryByText('Available after M12')).not.toBeInTheDocument();
});
it('measures fixture render including the protected response',async()=>{
 mock();const samples:number[]=[];
 for(let i=0;i<5;i++){
  const start=performance.now();render(<ReconciliationPanel/>);
  await screen.findByText(/Actual billed net: 99.9 EUR/);
  samples.push(performance.now()-start);cleanup();
 }
 samples.sort((a,b)=>a-b);
 console.info('M12 local jsdom render (one day, one currency, fixture response)',JSON.stringify({median_ms:samples[2],max_ms:samples[4]}));
 expect(samples).toHaveLength(5);
});
