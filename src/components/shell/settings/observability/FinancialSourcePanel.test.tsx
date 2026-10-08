import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,cleanup,fireEvent,within} from '@testing-library/react';
import {FinancialSourcePanel} from './FinancialSourcePanel';
import {billingSummary,billingSources,billingAmount,pricingStatus,financialSource} from '../../../../api/finops.test-support';
afterEach(()=>{cleanup();vi.restoreAllMocks();});
function mock(summary=billingSummary,price=pricingStatus){return vi.spyOn(globalThis,'fetch').mockImplementation(async input=>new Response(JSON.stringify(String(input).includes('billing-health')?billingSources:String(input).includes('pricing-status')?price:summary)));}
it('unconfigured sources never display synthetic zero spend',async()=>{
 mock();render(<FinancialSourcePanel/>);await screen.findByText('Actual billing unavailable.');expect(document.body.textContent).not.toContain('Net including source tax: 0');expect(screen.getByText(/Configuration is read-only/)).toBeInTheDocument();
});
it('fixture money preserves decimal strings, credits and currency while stale catalog stays separate',async()=>{
 mock({...billingSummary,source:{...financialSource,mode:'TEST_FIXTURE',state:'STALE',reason:'SOURCE_AGE'},amounts:[billingAmount,{...billingAmount,currency:'USD',net:'100.123456789012345678901',eur_net:null,eur_state:'EUR_REPORTING_UNAVAILABLE'}]});
 render(<FinancialSourcePanel/>);await screen.findByText('TEST FIXTURE — synthetic financial data');expect(screen.getByText('Net including source tax: 0.08 EUR')).toBeInTheDocument();expect(screen.getByText('Net including source tax: 100.123456789012345678901 USD')).toBeInTheDocument();expect(screen.getByText('EUR reporting unavailable.')).toBeInTheDocument();expect(screen.getByText(/Credits: -0.02 EUR/)).toBeInTheDocument();
});
it('invoice control changes server query and financial revocation clears money',async()=>{
 const fetcher=mock({...billingSummary,amounts:[billingAmount]});render(<FinancialSourcePanel/>);await screen.findByText('Net including source tax: 0.08 EUR');
 fireEvent.change(screen.getByLabelText('Billing period basis'),{target:{value:'invoice'}});await screen.findByLabelText('Invoice month');
 expect(fetcher.mock.calls.some(c=>String(c[0]).includes('period_basis=invoice'))).toBe(true);
 fetcher.mockImplementation(async()=>new Response('{}',{status:403}));fireEvent.click(within(screen.getByRole('region',{name:'Actual billed amount'})).getByText('Refresh'));
 await screen.findByRole('alert');expect(screen.queryByText('Net including source tax: 0.08 EUR')).not.toBeInTheDocument();
});
it('pricing outage does not hide actual billing',async()=>{
 vi.spyOn(globalThis,'fetch').mockImplementation(async input=>String(input).includes('pricing-status')?new Response('{}',{status:503}):new Response(JSON.stringify(String(input).includes('billing-health')?billingSources:{...billingSummary,amounts:[billingAmount]})));
 render(<FinancialSourcePanel/>);await screen.findByText('Net including source tax: 0.08 EUR');await screen.findByRole('alert');
});
