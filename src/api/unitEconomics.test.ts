import {it,expect,vi,afterEach} from 'vitest';
import {decodeUnitEconomics,getUnitEconomics} from './unitEconomics';
import {unitPage} from './finops.test-support';
afterEach(()=>vi.restoreAllMocks());
it('preserves exact decimal strings and separates null from zero',()=>{
 const page=structuredClone(unitPage);page.items[0].results[0].direct_cost_per_unit='0.12345678901234567890123456789012345678';
 expect(decodeUnitEconomics(page).items[0].results[0].direct_cost_per_unit).toBe(page.items[0].results[0].direct_cost_per_unit);
 const value=structuredClone(page) as unknown as Record<string,unknown>;expect(decodeUnitEconomics(value).analytical_views).toBe('ALTERNATIVE_NON_ADDITIVE');
});
it.each([NaN,Infinity,0.2,'NaN','1e4','Infinity'])('rejects financial nonstrings or invalid decimal %s',value=>{
 const p=structuredClone(unitPage) as any;p.items[0].results[0].direct_cost_per_unit=value;expect(()=>decodeUnitEconomics(p)).toThrow();
});
it('rejects unknown vocabulary, versions, currency, identity and negative population',()=>{
 for(const mutate of [(p:any)=>p.schema_version=2,(p:any)=>p.items[0].results[0].state='GUESS',(p:any)=>p.items[0].results[0].unit_count=-1,(p:any)=>p.items[0].generation='secret',(p:any)=>p.items[0].results[0].currency='EUR+USD']){
  const p=structuredClone(unitPage);mutate(p);expect(()=>decodeUnitEconomics(p)).toThrow();
 }
});
it('uses protected cache routes and date filters without client financial calculation',async()=>{
 const spy=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(unitPage)));
 const value=await getUnitEconomics(undefined,'2026-01-10','ACCEPTED_TURN');expect(value.items[0].results[0].direct_cost_per_unit).toBe('6');
 expect(String(spy.mock.calls[0][0])).toContain('/unit-economics/ACCEPTED_TURN?start=2026-01-10&end=2026-01-11');
 await expect(getUnitEconomics(undefined,'2026-02-30')).rejects.toThrow();
});
