import {it,expect} from 'vitest';
import {decodeBillingSummary,decodeBillingHealth,decodePricingStatus} from './finops';
import {billingSummary,billingSources,billingAmount,pricingStatus} from './finops.test-support';
it('projects safe exact native money and keeps catalog authority separate',()=>{
 const projected=decodeBillingSummary({...billingSummary,amounts:[{...billingAmount,prompt:'SENTINEL',billing_account:'SENTINEL'}]});
 expect(projected.amounts![0].net).toBe('0.08');expect(JSON.stringify(projected)).not.toContain('SENTINEL');
 expect(decodePricingStatus(pricingStatus).amount_basis).toBe('PRICING_EXPORT_CATALOG');expect(decodeBillingHealth(billingSources).sources).toHaveLength(3);
});
it('missing actual billing remains null and rejects binary floats or invalid financial classifications',()=>{
 expect(decodeBillingSummary(billingSummary).amounts).toBeNull();
 for(const changes of [{net:0.08},{currency:'EUR+USD'},{category:'SECRET_SENTINEL'},{eur_net:null}])expect(()=>decodeBillingSummary({...billingSummary,amounts:[{...billingAmount,...changes}]})).toThrow();
 expect(()=>decodeBillingSummary({...billingSummary,amount_basis:'CATALOG_LIST_PRICE'})).toThrow();
 expect(()=>decodePricingStatus({...pricingStatus,historical_policy:'CURRENT_PRICE_FALLBACK'})).toThrow();
});
