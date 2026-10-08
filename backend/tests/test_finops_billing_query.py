from pathlib import Path
from datetime import timedelta
import pytest
from backend.tests._m11_financial import *
from backend.observability.finops.bigquery_source import Binding,BigQuerySource
from backend.observability.finops.billing_contracts import SourceError,Policy

class FakeTransport:
    def __init__(self,rows):self.rows=rows;self.queries=[]
    async def execute(self,query):self.queries.append(query);return self.rows,100

def binding(source='DETAILED_BILLING',**changes):
    d=dict(source=source,table='fixture-project.billing.normalized',billing_account='ACCOUNT_SENTINEL',projects=('fixture-project',),maximum_bytes_billed=100000,location='EU')
    d.update(changes);return Binding(**d)

@pytest.mark.parametrize('source',['DETAILED_BILLING','PRICING_EXPORT','FOCUS'])
def test_fixed_parameter_scope_partition_and_bytes(source):
    query=BigQuerySource(binding(source),FakeTransport([])).query(WINDOW,NOW)
    assert 'SELECT *' not in query.sql and '@start' in query.sql and '@end' in query.sql and '@account' in query.sql and '@cutoff' in query.sql and 'LIMIT @row_limit' in query.sql
    assert query.maximum_bytes_billed==100000 and query.timeout_seconds<=30 and query.max_rows==100001
    assert 'ACCOUNT_SENTINEL' not in query.sql
    if source=='PRICING_EXPORT':assert 'source_partition_time >= @partition_start' in query.sql and 'IS NULL' in query.sql
    if source=='DETAILED_BILLING':assert 'project.id IN UNNEST(@projects)' in query.sql and 'usage_start_time >= @start' in query.sql

@pytest.mark.parametrize('changes',[{'table':'foo`; DROP TABLE secret;--'},{'maximum_bytes_billed':0},{'projects':()},{'location':'EU;SELECT'},{'pricing_partition_overlap_days':32}])
def test_bad_binding_rejected(changes):
    with pytest.raises(SourceError):binding(**changes)

@pytest.mark.asyncio
async def test_real_adapter_fake_query_and_overflow_rejects():
    transport=FakeTransport([billing()]);adapter=BigQuerySource(binding(),transport,Policy(max_rows=1))
    data=await adapter.extract(WINDOW);assert data.complete and data.mode=='BIGQUERY' and len(transport.queries)==1
    transport.rows=[billing(),billing()]
    with pytest.raises(SourceError):await adapter.extract(WINDOW)

def test_sql_static_types_no_raw_labels_or_mutation():
    for path in Path('infra/observability/bigquery').glob('*.sql'):
        text=path.read_text();assert 'SELECT' in text and 'SELECT *' not in text
        assert 'CREATE ' not in text and 'DELETE ' not in text
    billing_sql=Path('infra/observability/bigquery/finops_billing_normalized.sql').read_text()
    assert 'CAST(cost AS BIGNUMERIC)' in billing_sql and 'price.effective_price' in billing_sql
    assert 'FROM UNNEST(credits)' in billing_sql and 'labels' not in billing_sql
    sql=Path('infra/observability/bigquery/finops_resource_cost_daily.sql').read_text()
    assert 'invoice.month' in sql and 'GROUP BY' in sql

@pytest.mark.asyncio
async def test_sdk_transport_uses_real_job_configuration_with_fake_client():
    from backend.observability.finops.bigquery_source import SDKTransport
    class Job:
        total_bytes_processed=123
        cancelled=False
        def result(self,**kwargs):return [billing()]
        def cancel(self):self.cancelled=True
    class Client:
        def query(self,sql,**kwargs):self.sql=sql;self.kwargs=kwargs;return Job()
    client=Client();a=BigQuerySource(binding(),SDKTransport(client))
    ex=await a.extract(WINDOW)
    cfg=client.kwargs['job_config']
    assert cfg.maximum_bytes_billed==100000 and not cfg.use_legacy_sql and int(cfg.job_timeout_ms)==30000
    assert {p.name for p in cfg.query_parameters}>={'start','end','account','cutoff','row_limit','projects'}
    assert ex.bytes_processed==123 and ex.rows[0]['cost']=='10'
