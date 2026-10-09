import {it,expect} from 'vitest';
import {decodeReconciliation} from './reconciliation';
import {reconciliationPage} from './finops.test-support';
it('preserves exact decimal strings and drops unrestricted extras',()=>{
 const input=structuredClone(reconciliationPage);input.items[0].amounts[0].net='0.123456789012345678901234567890';Object.assign(input.items[0],{prompt:'SECRET_SQL_TEAMS_SENTINEL',project:'PRIVATE_PROJECT'});
 const value=decodeReconciliation(input);expect(value.items[0].amounts[0].net).toBe('0.123456789012345678901234567890');expect(JSON.stringify(value)).not.toMatch(/SECRET|PRIVATE/);
});
it('rejects floats, unknown status and reason text',()=>{
 for(const poison of [(x:typeof reconciliationPage)=>Object.assign(x.items[0].amounts[0],{net:0.1}),(x:typeof reconciliationPage)=>{x.items[0].status='SECRET';},(x:typeof reconciliationPage)=>{x.items[0].reasons=['SQL PRIVATE'];}]){const data=structuredClone(reconciliationPage);poison(data);expect(()=>decodeReconciliation(data)).toThrow();}
});
