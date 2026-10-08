"""Local bounded fixture timings only; not BigQuery/Cloud SQL production capacity."""
import json,time,statistics,resource
from decimal import Decimal
import pytest
from backend.tests._m11_financial import *
from backend.tests.test_finops_billing_ingestion import financial
from backend.tests.test_observability_api import api,authorize
from backend.observability.finops.billing_normalization import normalize,detailed
from backend.observability.finops.pricing import normalize as prices,lookup
from backend.observability.finops.billing_contracts import Policy
from backend.observability.schemas import Role

def report(samples):
    samples=sorted(samples)
    return {'median_ms':statistics.median(samples)*1000,'p95_ms':samples[min(len(samples)-1,int(len(samples)*.95))]*1000,'n':len(samples)}

@pytest.mark.asyncio
async def test_local_1k_10k_100k_financial_pipeline_measurement(financial,api):
    db,repo,a,p,w,s=financial;app,client,_,_,backend=api;backend.financial_sources=s;authorize(app,Role.FINOPS)
    result={};rss_before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    raw=billing('0.1');registry=mapping()
    samples=[]
    for _ in range(30):
        began=time.perf_counter();detailed(raw,registry);samples.append(time.perf_counter()-began)
    result['row_normalization']=report(samples)
    for count in (1000,10000,100000):
        began=time.perf_counter();rows=tuple(dict(raw,cost=str(Decimal(i)/Decimal(100))) for i in range(count))
        simulation=time.perf_counter()-began
        began=time.perf_counter();projection=normalize(extraction(rows),registry);elapsed=time.perf_counter()-began
        expected=Decimal(count*(count-1))/Decimal(200)
        assert Decimal(projection.groups[0]['net'])==expected and projection.rows==count
        assert elapsed<120
        result[str(count)]={'extraction_simulation_ms':simulation*1000,'normalization_multiset_controls_ms':elapsed*1000,'rows':count,'groups':len(projection.groups)}
    a.rows=tuple(dict(raw,cost=str(Decimal(i)/Decimal(100))) for i in range(1000))
    began=time.perf_counter();assert await w.ingest(a.source,WINDOW)=='PUBLISHED';result['publication_1000_ms']=(time.perf_counter()-began)*1000
    for name,fn in [('duplicate_snapshot',lambda:w.ingest(a.source,WINDOW)),('summary_read',lambda:repo.current('development',a.source,a.scope)),('api_response',lambda:client.get('/api/observability/finops/billing-summary',params={'start':'2026-01-10','end':'2026-01-11'}))]:
        samples=[]
        for _ in range(5):
            began=time.perf_counter();value=await fn();samples.append(time.perf_counter()-began)
            if name=='api_response':assert value.status_code==200
        result[name]=report(samples)
    catalog_snapshot=prices(extraction([catalog()]))
    samples=[]
    for _ in range(30):
        began=time.perf_counter();assert lookup(catalog_snapshot.prices,'sku-input',JAN)['state']=='AVAILABLE';samples.append(time.perf_counter()-began)
    result['pricing_lookup']=report(samples)
    result['rss_peak_growth_bytes_macos']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss-rss_before
    result['scope']='local SQLite; simulated extraction, no network; one sample per bulk size'
    print('M11_PERFORMANCE '+json.dumps(result,sort_keys=True))
