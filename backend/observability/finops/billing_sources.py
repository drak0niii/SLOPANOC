"""Complete source extraction contracts and explicitly synthetic fixture adapters."""
from typing import Protocol
from dataclasses import replace
from datetime import datetime, timezone
from .billing_contracts import Extraction, Window, SourceError, Projection, instant, currency, money, digest

class SourceAdapter(Protocol):
    source: str
    mode: str
    scope: str
    async def extract(self, window: Window) -> Extraction: ...

class FixtureSource:
    mode='TEST_FIXTURE'
    def __init__(self, source, rows=(), scope='fixture', fail=False):
        from .billing_contracts import SOURCES
        if source not in SOURCES: raise SourceError()
        self.source=source; self.rows=tuple(rows); self.scope=digest(scope); self.fail=fail
    async def extract(self, window):
        if self.fail: raise SourceError('SOURCE_UNAVAILABLE')
        field={'DETAILED_BILLING':'usage_start_time','PRICING_EXPORT':'pricing_as_of_time','FOCUS':'ChargePeriodStart'}[self.source]
        selected=tuple(r for r in self.rows if window.start<=instant(r[field])<window.end)
        return Extraction(selected,window,datetime.now(timezone.utc))

class BillingSourceAdapter(FixtureSource):
    def __init__(self, rows=(), **kwargs): super().__init__('DETAILED_BILLING',rows,**kwargs)
class PricingSourceAdapter(FixtureSource):
    def __init__(self, rows=(), **kwargs): super().__init__('PRICING_EXPORT',rows,**kwargs)
class FocusSourceAdapter(FixtureSource):
    def __init__(self, rows=(), **kwargs): super().__init__('FOCUS',rows,**kwargs)

def normalize_focus(extraction, mapping=None, policy=None):
    """FOCUS control totals stay separate; never enter Detailed charge summaries."""
    from collections import Counter
    from decimal import Decimal, localcontext
    from .billing_contracts import decimal, Policy
    policy=policy or Policy()
    if not extraction.complete or extraction.schema_version!=1: raise SourceError('INCOMPLETE_GENERATION')
    if len(extraction.rows)>policy.max_rows: raise SourceError('ROW_LIMIT')
    counts=Counter();controls={};exported=None
    try:
        with localcontext() as ctx:
            ctx.prec=100
            for r in extraction.rows:
                at=instant(r['ChargePeriodStart']);end=instant(r['ChargePeriodEnd']);exp=instant(r['x_ExportTime'])
                if not extraction.window.start<=at<extraction.window.end or end<at: raise SourceError('OUTSIDE_WINDOW')
                cur=currency(r['BillingCurrency'])
                v=dict(start=at.isoformat(),end=end.isoformat(),export_time=exp.isoformat(),currency=cur,
                       billing_start=instant(r['BillingPeriodStart']).isoformat(),billing_end=instant(r['BillingPeriodEnd']).isoformat(),
                       billed=money(r['BilledCost']),effective=money(r['EffectiveCost']),list_cost=money(r['ListCost']),
                       contracted=money(r['ContractedCost']),sku=digest(str(r.get('SkuId',''))),
                       account=digest(str(r['BillingAccountId'])),service=digest(str(r['ServiceName'])))
                counts[digest(v)]+=1
                if cur not in controls: controls[cur]={k:Decimal(0) for k in ('billed','effective','list_cost','contracted')}
                for k in controls[cur]:controls[cur][k]+=decimal(v[k])
                exported=max(exported,exp) if exported else exp
    except SourceError: raise
    except Exception: raise SourceError() from None
    return Projection(digest(dict(schema=1,multiset=sorted(counts.items()))),len(extraction.rows),(),
        tuple(dict(currency=k,**{f:money(v) for f,v in values.items()}) for k,values in sorted(controls.items())),exported)
