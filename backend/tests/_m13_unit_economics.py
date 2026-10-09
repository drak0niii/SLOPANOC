"""Explicit isolated M13 fixtures; deployment attestations never inferred."""
from copy import deepcopy
from datetime import timedelta
from backend.tests._m12_reconciliation import inputs,registry,charge,JAN,NOW,WINDOW,SCOPE,system,seed,capture
from backend.observability.finops.reconciliation import reconcile
from backend.observability.finops.reconciliation_mapping import Mapping,Registry as FinancialRegistry
from backend.observability.finops.unit_drivers import ScopeRule,Driver,Registry as DriverRegistry
from backend.observability.finops.unit_economics_contracts import Snapshot,Population
from backend.observability.finops.billing_contracts import digest

def ownership(sku='sku-input',scope='USER_TURN_WORKLOAD',request_class=None):
    return ScopeRule(service=digest('service-ai'),sku=digest(sku),project=digest('PROJECT_SENTINEL'),
        region='europe-west1',financial_scope=scope,request_class=request_class,evidence_version='ownership-fixture-v1')

def economics_inputs(direct='60',shared='30',unallocated='10',warmup=None,credits=None,currency='EUR'):
    rows=[charge(direct,currency=currency,**({'credits':credits} if credits is not None else {})),
        charge(shared,sku={'id':'shared'},currency=currency),charge(unallocated,sku={'id':'unmapped'},currency=currency)]
    maps=list(registry().mappings)
    maps.append(maps[0].model_copy(update=dict(sku=digest('shared'),classification='SHARED',evidence='SUPPORTING_POOL',field=None,category='CLOUD_SQL')))
    if warmup is not None:
        rows.append(charge(warmup,sku={'id':'warmup'},currency=currency))
        maps.append(maps[0].model_copy(update=dict(sku=digest('warmup'),evidence='DEDICATED_RESOURCE',field=None)))
    r,b,p=inputs(rows)
    reg=FinancialRegistry(environment='development',runtime_scope=SCOPE,mappings=tuple(maps))
    f=reconcile(r,b,p,reg)
    turn=Snapshot(source='M9',environment='development',period=WINDOW.partition,source_digest='c'*64,
        watermark=WINDOW.end,captured_at=NOW,complete=True,freshness='FRESH',business_population_proven=True,
        counts={'ACCEPTED_TURN':10,'AVAILABLE_TURN':8,'TIMELY_TERMINAL_TURN':9,
            'GENERAL_TURN':5,'TEAMS_LOOKUP_TURN':5,'GOVERNED_TROUBLESHOOTING_TURN':0,'COMPLEX_MULTI_AGENT_TURN':0,'UNKNOWN_TURN':0})
    model=Snapshot(source='M10',environment='development',period=WINDOW.partition,source_digest='d'*64,
        runtime_digest=r['ledger_digest'],watermark=WINDOW.end,captured_at=NOW,complete=True,freshness='FRESH',
        counts={'PROVIDER_ATTEMPT':100,'MODEL_OPERATION':80},buckets=(Population(provider='gcp.gemini',model='gemini-2.5-flash',region='europe-west1',operation_type='GENERATION',workload='USER_TURN',attempts=100,operations=80,complete=True),))
    d=Driver(category='CLOUD_SQL',ownership=ownership('shared'),units=('ACCEPTED_TURN','AVAILABLE_TURN','TIMELY_TERMINAL_TURN'),driver='ACCEPTED_TURNS',version='turn-volume-fixture-v1')
    drivers=DriverRegistry(environment='development',financial_scope=SCOPE,scope_rules=(ownership(),),drivers=(d,))
    return dict(financial_result=f,billing=b,runtime=r,financial_registry=reg,snapshots=[turn,model],drivers=drivers)

def find(result,unit='ACCEPTED_TURN',dimension='ALL',currency='EUR'):
    return next(r for r in result['results'] if (r['unit_id'],r['dimension'],r['currency'])==(unit,dimension,currency))
