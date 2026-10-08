"""Synthetic financial data for isolated tests, never cloud source bindings."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from backend.observability.finops.billing_contracts import Window,Extraction
from backend.observability.finops.sku_mapping import Registry,Mapping
NOW=datetime(2026,10,8,12,tzinfo=timezone.utc)
JAN=datetime(2026,1,10,tzinfo=timezone.utc)
WINDOW=Window(JAN,JAN+timedelta(days=1))

def billing(cost='10',invoice='202601',currency='EUR',**changes):
    row=dict(billing_account_id='ACCOUNT_SENTINEL',service={'id':'service-ai'},sku={'id':'sku-input'},
        project={'id':'PROJECT_SENTINEL'},resource={'global_name':'RESOURCE_SENTINEL'},location={'region':'europe-west1'},
        usage_start_time=JAN.isoformat(),usage_end_time=(JAN+timedelta(hours=1)).isoformat(),export_time=(JAN+timedelta(hours=2)).isoformat(),
        invoice={'month':invoice},usage=dict(amount='0.1',unit='seconds',amount_in_pricing_units='0.1',pricing_unit='seconds'),
        cost=cost,currency=currency,currency_conversion_rate='1',credits=[],cost_type='regular',
        price={'list_price':'100','effective_price':'80','unit':'seconds','pricing_unit_quantity':'1'},
        labels=[{'key':'api_key','value':'SECRET_SENTINEL'}],tags=[{'value':'TAG_SENTINEL'}])
    row.update(changes);return row

def catalog(price='100',at=JAN,**changes):
    row=dict(billing_account_id='ACCOUNT_SENTINEL',service={'id':'service-ai'},sku={'id':'sku-input'},
        pricing_unit='seconds',account_currency_code='EUR',currency_conversion_rate='1',
        pricing_as_of_time=at.isoformat(),export_time=(at+timedelta(hours=2)).isoformat(),
        list_price=dict(aggregation_info=dict(aggregation_level='ACCOUNT',aggregation_interval='ONE_MONTH'),
                        tiered_rates=[dict(start_usage_amount='0',pricing_unit_quantity='1',usd_amount=price,account_currency_amount=price),
                                      dict(start_usage_amount='100',pricing_unit_quantity='1',usd_amount='50',account_currency_amount='50')]))
    row.update(changes);return row

def extraction(rows,window=WINDOW,**changes):return Extraction(tuple(rows),window,NOW,**changes)
def mapping():return Registry([Mapping('service-ai','sku-input','MODEL_AI',JAN-timedelta(days=100),NOW+timedelta(days=100))],version='map-v1')
