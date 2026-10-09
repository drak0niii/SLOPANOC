from decimal import Decimal
from backend.tests._m14_controls import *
from backend.observability.finops.cost_drivers import explain

def test_authoritative_symmetric_price_volume_shared_residual():
    a=day(date(2026,10,1),factors={'sku:EUR':('10','2')})
    b=day(date(2026,10,2),factors={'sku:EUR':('20','3')})
    r=explain(a,b,'EUR',Decimal(40))
    assert r['reasons']==['PRICE_CHANGE','USAGE_VOLUME_INCREASE']
    assert sum(Decimal(c['amount']) for c in r['contributors'])==40 and Decimal(r['residual'])==0
    assert explain(a,b.model_copy(update={'factors':{}}),'EUR',Decimal(40))['reasons']==['UNEXPLAINED']
    assert 'WORKLOAD_MIX_SHIFT' not in str(explain(a,b,'EUR',Decimal(40)))
    shared_a=a.model_copy(update={'values':{'CLASS_SHARED:EUR':'10'}});shared_b=b.model_copy(update={'values':{'CLASS_SHARED:EUR':'50'},'factors':{}})
    assert 'SHARED_INFRASTRUCTURE_CHANGE' in explain(shared_a,shared_b,'EUR',Decimal(40))['reasons']

def test_workload_mix_requires_full_m13_population_proof():
    a=day(date(2026,10,1),mix={'GENERAL':('80','80'),'COMPLEX':('20','100')},mix_proven=True)
    b=day(date(2026,10,2),mix={'GENERAL':('50','50'),'COMPLEX':('50','250')},mix_proven=True)
    explained=explain(a,b,'EUR',Decimal(120))
    assert 'WORKLOAD_MIX_SHIFT' in explained['reasons']
    assert sum(Decimal(c['amount']) for c in explained['contributors'])+Decimal(explained['residual'])==120
    assert 'WORKLOAD_MIX_SHIFT' not in explain(a,b.model_copy(update={'mix_proven':False}),'EUR',Decimal(120))['reasons']
