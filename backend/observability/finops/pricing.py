"""Pricing observations retain complete tiers and never extrapolate missing history."""
from datetime import timedelta
from .billing_contracts import SourceError, Projection, instant, code, currency, money, digest, decimal, Policy

def normalize(extraction, mapping=None, policy=None):
    policy=policy or Policy()
    if not extraction.complete or extraction.schema_version!=1: raise SourceError('INCOMPLETE_GENERATION')
    prices=[]; times=[]; exports=[]; unmapped=0
    from .sku_mapping import Registry
    mapping=mapping or Registry()
    try:
        for row in extraction.rows:
            at=instant(row['pricing_as_of_time']); exp=instant(row['export_time'])
            if not extraction.window.start<=at<extraction.window.end: raise SourceError('OUTSIDE_WINDOW')
            times.append(at); exports.append(exp)
            service=code(row['service']['id']); sku=code(row['sku']['id']); unit=code(row['pricing_unit'])
            category=mapping.category(service,sku,at);unmapped+=category=='UNMAPPED'
            cur=currency(row['account_currency_code'])
            conversion=None if row.get('currency_conversion_rate') is None else dict(source_currency='USD',target_currency=cur,
                rate=money(row['currency_conversion_rate']),applicable_at=at.isoformat(),source='PRICING_EXPORT_CATALOG')
            models=[dict(consumption_model_id='DEFAULT',list_price=row['list_price'],billing_account_price=row.get('billing_account_price'))]
            # New exports also repeat DEFAULT in consumption_model_prices; retain it once.
            models.extend(m for m in row.get('consumption_model_prices',[]) if m['consumption_model_id']!='DEFAULT')
            for model in models:
                for kind, field in [('CATALOG_LIST_PRICE','list_price'),('CATALOG_ACCOUNT_PRICE','billing_account_price')]:
                    structure=model.get(field)
                    if structure is None: continue
                    agg=structure['aggregation_info']
                    level=code(agg['aggregation_level']); interval=code(agg['aggregation_interval'])
                    tiers=[]
                    for t in structure['tiered_rates']:
                        lower=decimal(t['start_usage_amount']); quantity=decimal(t['pricing_unit_quantity'])
                        if lower<0 or quantity<=0: raise SourceError('INVALID_TIER')
                        tiers.append(dict(lower=money(lower),unit_quantity=money(quantity),usd_price=money(t['usd_amount']),
                                          account_price=money(t['account_currency_amount'])))
                    tiers.sort(key=lambda t:decimal(t['lower']))
                    if not tiers or len({t['lower'] for t in tiers})!=len(tiers): raise SourceError('INVALID_TIER')
                    for i,t in enumerate(tiers): t['upper']=tiers[i+1]['lower'] if i+1<len(tiers) else None
                    prices.append(dict(sku=digest(sku),service=digest(service),unit=unit,currency=cur,kind=kind,
                        consumption_model=code(model['consumption_model_id']),category=category,aggregation_level=level,aggregation_interval=interval,
                        observed_from=at.isoformat(),observed_to=min(at+timedelta(days=1),extraction.window.end).isoformat(),
                        effective_basis='OBSERVED_DAILY_SNAPSHOT',source='PRICING_EXPORT_CATALOG',export_time=exp.isoformat(),
                        conversion=conversion,tiers=tiers))
                    if len(prices)>policy.max_groups: raise SourceError('PRICE_CACHE_LIMIT')
        if len(extraction.rows)>policy.max_rows: raise SourceError('ROW_LIMIT')
    except SourceError: raise
    except Exception: raise SourceError() from None
    # Sorted list (not set) preserves pricing row multiplicity in the manifest.
    values=sorted(prices,key=lambda p:(p['sku'],p['kind'],p['consumption_model'],p['observed_from'],digest(p)))
    return Projection(digest(dict(schema=1,mapping_version=mapping.version,prices=values)),len(extraction.rows),(),(),max(exports) if exports else None,
        min(times) if times else None,max((instant(p['observed_to']) for p in prices),default=None),tuple(values),unmapped_rows=unmapped,mapping_version=mapping.version)

def lookup(prices, sku, at, kind='CATALOG_LIST_PRICE', consumption_model='DEFAULT'):
    at=instant(at); sku=digest(code(sku))
    matches=[p for p in prices if p['sku']==sku and p['kind']==kind and p['consumption_model']==consumption_model
             and instant(p['observed_from'])<=at<instant(p['observed_to'])]
    if len(matches)!=1: return {'state':'PRICE_UNAVAILABLE','price':None}
    return {'state':'AVAILABLE','price':matches[0]}
