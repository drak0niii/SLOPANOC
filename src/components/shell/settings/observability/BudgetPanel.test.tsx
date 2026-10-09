import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,cleanup} from '@testing-library/react';
import {BudgetPanel} from './BudgetPanel';
import {controlPage,controlItem} from '../../../../api/finops.test-support';
afterEach(()=>{cleanup();vi.restoreAllMocks()});
it('shows no budget configured instead of zero',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(controlPage)));render(<BudgetPanel/>);await screen.findByText('No budget configured');expect(screen.queryByText(/Budget: 0/)).not.toBeInTheDocument();
});
for(const state of ['HEALTHY','WATCH','AT_RISK','EXCEEDED','SOURCE_STALE'])it('renders server budget state '+state,async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({...controlPage,state:'AVAILABLE',items:[{...controlItem,state,utilization_pct:'120'}]})));render(<BudgetPanel/>);await screen.findByText(/Utilization: 120%/);expect(document.body.textContent).toContain(state.replaceAll('_',' '));
});
