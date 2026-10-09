import {it,expect,vi,afterEach} from 'vitest';
import {decodeControls,getControls} from './financialControls';
import {controlItem,controlPage} from './finops.test-support';
afterEach(()=>vi.restoreAllMocks());
it('decodes exact safe server values and drops unknown content',()=>{
 const p=decodeControls({...controlPage,items:[{...controlItem,raw_source:'SECRET',prompt:'SECRET'}]});expect(p.items[0].utilization_pct).toBe('50');expect(JSON.stringify(p)).not.toContain('SECRET');
});
it('rejects unbounded, wrong schema, money floats and bad state',()=>{
 for(const p of [{...controlPage,schema_version:2},{...controlPage,items:Array(101).fill(controlItem)},{...controlPage,items:[{...controlItem,amount:100}]},{...controlPage,items:[{...controlItem,state:'SAVINGS'}]}])expect(()=>decodeControls(p)).toThrow();
});
it('fetches cache route and safe errors',async()=>{
 const fetcher=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(controlPage)));await getControls('budgets');expect(String(fetcher.mock.calls[0][0])).toContain('/finops/budgets');
 fetcher.mockResolvedValue(new Response('SECRET',{status:403}));await expect(getControls('forecasts')).rejects.toThrow('Financial controls unavailable.');
});
