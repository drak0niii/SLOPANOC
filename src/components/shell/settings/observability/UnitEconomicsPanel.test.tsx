import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,cleanup,fireEvent} from '@testing-library/react';
import {UnitEconomicsPanel} from './UnitEconomicsPanel';
import {unitPage,unitResult} from '../../../../api/finops.test-support';
afterEach(()=>{cleanup();vi.restoreAllMocks();});
it('shows backend prices and prominent partial exposure with coverage and freshness',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(unitPage)));
 const began=performance.now();render(<UnitEconomicsPanel/>);await screen.findByText(/Direct average cost \/ unit: 6 EUR/);
 expect(screen.getByText(/Fully loaded average cost \/ unit: 8 EUR/)).toBeInTheDocument();
 expect(screen.getByText(/Undistributed shared: 10 EUR/)).toBeInTheDocument();expect(screen.getByText(/Unallocated exposure: 10 EUR/)).toBeInTheDocument();
 expect(screen.getByText(/Eligible units: 10/)).toBeInTheDocument();
 fireEvent.click(screen.getByText('Unit cost details'));expect(screen.getByText(/shared driver coverage ratio: 0.666666666667/)).toBeInTheDocument();expect(screen.getByText(/Financial freshness: FRESH/)).toBeInTheDocument();
 expect(document.body.textContent).not.toContain('marginal');expect(document.body.textContent).toContain('not additive');
 console.info('M13 local UI render + async fixture ms',performance.now()-began);
});
it('renders deferred outcome without fake price and stale existing values truthfully',async()=>{
 const page=structuredClone(unitPage) as any;page.items[0].results=[{...unitResult,state:'DEFINED_NOT_AVAILABLE',unit_id:'FIRST_TIME_CORRECT',name:'First-time-correct outcome',unit_count:null,direct_cost_per_unit:null,fully_loaded_cost_per_unit:null}];
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(page)));render(<UnitEconomicsPanel/>);
 await screen.findByText('Not available — authoritative outcome data required.');expect(screen.queryByText(/Direct average cost \/ unit:/)).not.toBeInTheDocument();
});
it('filters a bounded unit and clears financial values on revocation',async()=>{
 const fetcher=vi.spyOn(globalThis,'fetch').mockImplementation(async()=>new Response(JSON.stringify(unitPage)));
 render(<UnitEconomicsPanel/>);await screen.findByText(/Direct average cost \/ unit: 6 EUR/);
 fireEvent.change(screen.getByLabelText('Unit economics unit'),{target:{value:'ACCEPTED_TURN'}});
 await screen.findByText(/Direct average cost \/ unit: 6 EUR/);fetcher.mockResolvedValue(new Response('{}',{status:403}));
 fireEvent.click(screen.getByRole('button',{name:'Refresh'}));await screen.findByRole('alert');expect(screen.queryByText(/6 EUR/)).not.toBeInTheDocument();
});
it('shows compatible cohort operational context separately from business quality',async()=>{
 const page=structuredClone(unitPage) as any;
 page.items[0].results[0].operational_context={source:'M9_ACCEPTANCE_COHORT',availability_ratio:'0.8',timely_terminal_ratio:'0.9',mean_latency_ms:'200',latency_observed:8,eligible_turns:10};
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(page)));render(<UnitEconomicsPanel/>);
 await screen.findByText(/M9 acceptance cohort: availability ratio 0.8/);
 expect(screen.getByText(/Mean latency 200 ms.*8 observed.*10 turns/)).toBeInTheDocument();
 expect(screen.getByText(/Operational availability is separate from business quality/)).toBeInTheDocument();
});
