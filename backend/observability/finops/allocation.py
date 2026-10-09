"""One classification per source financial component; exact independent controls."""
from decimal import Decimal,localcontext
from .billing_contracts import decimal,money,SourceError
from .reconciliation_contracts import CLASSES,controls


def classify(components,decisions):
    result=[]
    for component in components:
        classification,evidence,category=decisions.get(component['bucket'],('UNALLOCATED','NONE','UNMAPPED'))
        # Tax/rounding and their attached credits retain nonusage treatment.
        if component['kind'] in ('tax','rounding'):classification,evidence='UNALLOCATED','NONE'
        result.append(dict(component,classification=classification,evidence=evidence,category=category,
              method='OWNERSHIP_POOL' if classification=='SHARED' else 'VERIFIED_OWNERSHIP' if classification=='DIRECT' else 'NO_ATTRIBUTION',
              driver='VERIFIED_POOL_MEMBERSHIP' if classification=='SHARED' else 'NONE',
              confidence='HIGH' if classification in ('DIRECT','SHARED') else 'UNMATCHED'))
    validate(components,result)
    return result


def validate(source,classified):
    ids=[c['id'] for c in source];out=[c['id'] for c in classified]
    if len(ids)!=len(set(ids)) or len(out)!=len(set(out)) or set(ids)!=set(out):raise SourceError('FINANCIAL_IMBALANCE')
    original={c['id']:c for c in source}
    for c in classified:
        if c['classification'] not in CLASSES or c['classification']=='EXCLUDED':raise SourceError('FINANCIAL_IMBALANCE')
        # Totals alone cannot detect swapping one source component for another.
        if any(c[k]!=original[c['id']][k] for k in ('bucket','currency','kind','amount','absolute','count','credit_type')):
            raise SourceError('FINANCIAL_IMBALANCE')
    if controls(source)!=controls(classified):raise SourceError('FINANCIAL_IMBALANCE')


def totals(classified):
    result={}
    with localcontext() as ctx:
        ctx.prec=100
        for c in classified:
            key=(c['currency'],c['classification'])
            v=result.setdefault(key,dict(net=Decimal(0),absolute=Decimal(0),count=0,components={}))
            v['net']+=decimal(c['amount']);v['absolute']+=decimal(c['absolute']);v['count']+=c['count']
            v['components'][c['kind']]=v['components'].get(c['kind'],Decimal(0))+decimal(c['amount'])
        return [dict(currency=k[0],classification=k[1],confidence='HIGH' if k[1]!='UNALLOCATED' else 'UNMATCHED',
                     method='OWNERSHIP_POOL' if k[1]=='SHARED' else 'VERIFIED_OWNERSHIP' if k[1]=='DIRECT' else 'NO_ATTRIBUTION',
                     driver='VERIFIED_POOL_MEMBERSHIP' if k[1]=='SHARED' else 'NONE',net=money(v['net']),absolute=money(v['absolute']),count=v['count'],
                     components={name:money(value) for name,value in sorted(v['components'].items())}) for k,v in sorted(result.items())]
