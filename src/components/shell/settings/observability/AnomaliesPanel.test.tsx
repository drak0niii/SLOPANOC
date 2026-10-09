import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,cleanup} from '@testing-library/react';
import {AnomaliesPanel} from './AnomaliesPanel';
import {controlPage,controlItem} from '../../../../api/finops.test-support';
afterEach(()=>{cleanup();vi.restoreAllMocks()});
it('does not claim no anomalies before evaluation',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({...controlPage,state:'UNEVALUATED'})));render(<AnomaliesPanel/>);await screen.findByText(/Anomaly evaluation unavailable/);expect(screen.queryByText(/No anomaly detected/)).not.toBeInTheDocument();
});
it('shows successful empty evaluation explicitly',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({...controlPage,state:'EVALUATED',evaluated:3})));render(<AnomaliesPanel/>);await screen.findByText('No anomaly detected for evaluated periods');
});
it('renders source quality and active anomaly without a savings claim',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({...controlPage,state:'EVALUATED',items:[{...controlItem,kind:'ANOMALY',state:'ACTIVE',anomaly_id:'b'.repeat(64),type:'RECONCILIATION_QUALITY',day:'2026-10-07',severity:'HIGH',confidence:'QUALITY',reasons:['SOURCE_QUALITY_DEGRADATION'],label:'QUALITY'}]})));render(<AnomaliesPanel/>);await screen.findByText(/SOURCE QUALITY DEGRADATION/);expect(document.body.textContent).not.toContain('SAVINGS');
});
