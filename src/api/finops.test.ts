import { describe,it,expect,vi,afterEach } from 'vitest';
import { decodeUsage,decodeLedgerHealth,getRuntimeUsage } from './finops';
import {usage,health,group} from './finops.test-support';
afterEach(()=>vi.restoreAllMocks());
describe('safe runtime usage contract',()=>{
 it('preserves null and explicit zero with independent coverage',()=>{expect(decodeUsage(usage).items[0].quantities.input_tokens.observed).toBeNull();const copy=structuredClone(usage);copy.items[0].quantities.input_tokens={observed:0,known:100,unknown:0};expect(decodeUsage(copy).items[0].quantities.input_tokens.observed).toBe(0);expect(decodeLedgerHealth(health).debt).toBe(1);});
 it('rejects unsafe counts, enums, schema and metadata',()=>{for(const bad of [{...usage,schema_version:2},{...usage,environment:'secret'},{...usage,items:[{...group,attempts:-1}]},{...usage,items:[{...group,dimension:'https://private'}]},{...usage,items:[{...group,quantity_coverage:Infinity}]}])expect(()=>decodeUsage(bad)).toThrow();});
 it('projects away unknown payload fields',()=>{expect(JSON.stringify(decodeUsage({...usage,prompt:'PRIVATE_PAYLOAD'}))).not.toContain('PRIVATE_PAYLOAD');});
 it('passes cancellation and bounds grouping',async()=>{const signal=new AbortController().signal;const fetcher=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(usage)));await getRuntimeUsage('model',signal);expect(fetcher.mock.calls[0][0]).toContain('/api/observability/finops/runtime-usage');expect(fetcher.mock.calls[0][1]?.signal).toBe(signal);expect(()=>getRuntimeUsage('prompt')).toThrow();});
});
