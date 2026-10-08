"""Allowlisted normalization, signed source charges, cumulative corrections and multisets."""
from collections import Counter
from dataclasses import asdict
from decimal import Decimal, localcontext
from hashlib import sha512
import json
from datetime import timedelta
from .billing_contracts import (SourceError, Projection, decimal, money, instant, code,
                               currency, digest, COST_TYPES, CREDIT_TYPES)
from .sku_mapping import Registry

PRICE_FIELDS = ('list_price', 'effective_price', 'effective_price_default',
                'list_price_consumption_model', 'tier_start_amount', 'pricing_unit_quantity')

def detailed(row, mapping):
    """Unused provider columns never enter canonical content, DB, logs or DTOs."""
    try:
        at, end, exported = (instant(row[k]) for k in ('usage_start_time','usage_end_time','export_time'))
        if end < at: raise SourceError()
        service, sku = code(row['service']['id'] or 'UNKNOWN'), code(row['sku']['id'] or 'UNKNOWN')
        invoice = code(row['invoice']['month'], r'\d{4}(0[1-9]|1[0-2])')
        cost_type = row['cost_type']
        if cost_type not in COST_TYPES: raise SourceError()
        credits = []
        for c in row['credits']:
            credits.append({'amount':money(c['amount']), 'type':c.get('type') if c.get('type') in CREDIT_TYPES else 'OTHER',
                            'identity':digest(str(c.get('id','')))})
        credits.sort(key=lambda c:(c['identity'],c['type'],c['amount']))
        price = row.get('price') or {}
        evidence = {k:money(price[k]) for k in PRICE_FIELDS if price.get(k) is not None}
        if price.get('unit') is not None: evidence['unit'] = code(price['unit'])
        usage = row['usage']
        adjustment = row.get('adjustment_info') or {}
        # Restricted identities exist only as one-way hashes in ephemeral canonical data.
        result = dict(usage_start=at.isoformat(),usage_end=end.isoformat(),export_time=exported.isoformat(),
            invoice_month=invoice,service=digest(service),sku=digest(sku),category=mapping.category(service,sku,at),
            account=digest(code(row['billing_account_id'])),project=digest(str((row.get('project') or {}).get('id',''))),
            resource=digest([str((row.get('resource') or {}).get('global_name','')),str((row.get('resource') or {}).get('name',''))]),
            region=code((row.get('location') or {}).get('region') or 'UNKNOWN'),
            cost=money(row['cost']),currency=currency(row['currency']),cost_type=cost_type,credits=credits,
            usage_amount=None if usage['amount'] is None else money(usage['amount']),usage_unit=code(usage['unit'] or 'UNKNOWN'),
            pricing_amount=None if usage['amount_in_pricing_units'] is None else money(usage['amount_in_pricing_units']),pricing_unit=code(usage['pricing_unit'] or 'UNKNOWN'),
            price_evidence=evidence,consumption_model=digest(str((row.get('consumption_model') or {}).get('id',''))),
            adjustment_id=digest(str(adjustment.get('id',''))),
            adjustment_type=code(adjustment.get('type') or 'NONE'),adjustment_mode=code(adjustment.get('mode') or 'NONE'))
        result['line_reference_amounts']={k:money(row[k]) for k in ('cost_at_list','cost_at_effective_price_default','cost_at_list_consumption_model') if row.get(k) is not None}
        rate = row.get('currency_conversion_rate')
        result['conversion'] = None if rate is None else dict(source_currency='USD',target_currency=result['currency'],
            rate=money(rate),applicable_at=exported.isoformat(),source='BILLING_LINE_METADATA')
        return result
    except SourceError: raise
    except Exception: raise SourceError() from None

FIELDS = ('gross','credits','adjustments','tax','rounding','pre_credit','net','absolute_net','unmapped_absolute_net')

def totals():
    return {**{k:Decimal(0) for k in FIELDS}, 'rows':0, 'unmapped_rows':0, 'price_evidence_rows':0}

def add(group, row):
    cost = decimal(row['cost'])
    credit = sum((decimal(c['amount']) for c in row['credits']), Decimal(0))
    group['gross' if row['cost_type']=='regular' else {'adjustment':'adjustments','tax':'tax','rounding_error':'rounding'}[row['cost_type']]] += cost
    group['pre_credit'] += cost; group['credits'] += credit; group['net'] += cost+credit
    group['absolute_net'] += abs(cost+credit); group['rows'] += 1
    group['price_evidence_rows'] += bool(row['price_evidence'])
    if row['category']=='UNMAPPED':
        group['unmapped_rows'] += 1; group['unmapped_absolute_net'] += abs(cost+credit)

def serialize(group):
    return {k:money(v) if isinstance(v,Decimal) else v for k,v in group.items()}

def normalize(extraction, mapping=None, policy=None):
    from .billing_contracts import Policy
    policy=policy or Policy(); mapping=mapping or Registry()
    if not extraction.complete or extraction.schema_version!=1 or extraction.mode not in ('TEST_FIXTURE','BIGQUERY'):
        raise SourceError('INCOMPLETE_GENERATION')
    if len(extraction.rows)>policy.max_rows: raise SourceError('ROW_LIMIT')
    fingerprints = Counter(); canonical_by_hash = {}; groups={}; controls={}
    exported=None; late=corrections=unmapped=evidence=0
    with localcontext() as ctx:
        ctx.prec=100
        for raw in extraction.rows:
            row=detailed(raw,mapping); at=instant(row['usage_start']); exp=instant(row['export_time'])
            if not extraction.window.start <= at < extraction.window.end:
                raise SourceError('OUTSIDE_WINDOW')
            h=digest(row)
            # Independent canonical witness detects SHA256 collisions without assigning a fictitious row PK.
            witness=sha512(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if h in canonical_by_hash and canonical_by_hash[h]!=witness: raise SourceError('HASH_COLLISION')
            canonical_by_hash[h]=witness; fingerprints[h]+=1
            exported=max(exported,exp) if exported else exp
            late+=exp>at+timedelta(days=1)
            corrections+=row['cost_type']=='adjustment' or row['adjustment_mode']!='NONE'
            unmapped+=row['category']=='UNMAPPED'; evidence+=bool(row['price_evidence'])
            key=(row['invoice_month'],row['currency'],row['category'])
            if key not in groups: groups[key]=totals()
            if len(groups)>policy.max_groups: raise SourceError('GROUP_LIMIT')
            add(groups[key],row)
            key2=(row['currency'],row['cost_type'])
            if key2 not in controls: controls[key2]=totals()
            add(controls[key2],row)
        return Projection(digest(dict(schema=1,mapping=mapping.version,multiset=sorted(fingerprints.items()))),len(extraction.rows),
            tuple(dict(invoice_month=k[0],currency=k[1],category=k[2],**serialize(v)) for k,v in sorted(groups.items())),
            tuple(dict(currency=k[0],cost_type=k[1],**serialize(v)) for k,v in sorted(controls.items())),exported,
            late_rows=late,correction_rows=corrections,unmapped_rows=unmapped,evidence_rows=evidence,mapping_version=mapping.version)
