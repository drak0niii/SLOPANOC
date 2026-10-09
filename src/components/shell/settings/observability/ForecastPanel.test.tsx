import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,cleanup} from '@testing-library/react';
import {ForecastPanel} from './ForecastPanel';
import {controlPage,controlItem} from '../../../../api/finops.test-support';
afterEach(()=>{cleanup();vi.restoreAllMocks()});
it('shows unevaluated forecast explicitly',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({...controlPage,state:'UNEVALUATED'})));render(<ForecastPanel/>);await screen.findByText(/Forecast unavailable/);
});
it('renders server low-confidence decomposition and scenario range',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({...controlPage,state:'AVAILABLE',items:[{...controlItem,kind:'FORECAST',state:'AVAILABLE',tier:1,algorithm:'eligible-run-rate-v1',confidence:'LOW',actual_to_cutoff:'700',remaining:'2400',forecast:'3100',lower:'2500',upper:'3700',range_type:'SCENARIO_RANGE',risk:'WATCH',eligible_days:7,period_days:31}]})));render(<ForecastPanel/>);await screen.findByText(/Month-end forecast: 3100/);expect(screen.getByText(/Scenario range: 2500 – 3700 EUR/)).toBeInTheDocument();expect(screen.getByText(/Confidence: LOW/)).toBeInTheDocument();
});
it('measures 100-row local render and revocation removes forecasts',async()=>{
 const fetcher=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({...controlPage,state:'AVAILABLE',items:Array.from({length:100},()=>({...controlItem,kind:'FORECAST',state:'AVAILABLE',tier:1,algorithm:'eligible-run-rate-v1',confidence:'LOW',actual_to_cutoff:'700',remaining:'2400',forecast:'3100',lower:'2500',upper:'3700',range_type:'SCENARIO_RANGE',risk:'WATCH',eligible_days:7,period_days:31}))})));const start=performance.now();render(<ForecastPanel/>);await screen.findAllByText(/Month-end forecast: 3100/);const elapsed=performance.now()-start;console.info('M14 100-row local jsdom render ms',elapsed);expect(elapsed).toBeLessThan(1000);
 fetcher.mockResolvedValue(new Response('{}',{status:403}));const {fireEvent}=await import('@testing-library/react');fireEvent.click(screen.getByRole('button',{name:'Refresh'}));await screen.findByRole('alert');expect(screen.queryByText(/Month-end forecast/)).not.toBeInTheDocument();
});
