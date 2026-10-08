"""Fixed bounded normalized-view queries; injected transport, no implicit cloud client."""
import asyncio
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
from pathlib import Path
import re
from .billing_contracts import Extraction,SourceError,code,digest,Policy

SELECTS={
 'DETAILED_BILLING':'billing_account_id, service, sku, project, resource, location, usage_start_time, usage_end_time, export_time, invoice, cost_type, cost, currency, currency_conversion_rate, usage, credits, adjustment_info, price, consumption_model, cost_at_list, cost_at_effective_price_default, cost_at_list_consumption_model',
 'PRICING_EXPORT':'billing_account_id, service, sku, pricing_unit, account_currency_code, currency_conversion_rate, pricing_as_of_time, export_time, list_price, billing_account_price, consumption_model_prices',
 'FOCUS':'BillingAccountId, BillingCurrency, BillingPeriodStart, BillingPeriodEnd, ChargePeriodStart, ChargePeriodEnd, ServiceName, SkuId, x_ExportTime, BilledCost, EffectiveCost, ListCost, ContractedCost',
}

@dataclass(frozen=True)
class Binding:
    source: str
    table: str
    billing_account: str
    projects: tuple[str,...]
    maximum_bytes_billed: int
    location: str
    # Pricing ingestion partitions may differ from as-of dates. Explicit physical
    # range is provisioned by source discovery, never inferred from export_time.
    pricing_partition_overlap_days: int = 7
    def __post_init__(self):
        if self.source not in SELECTS or not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]\.[A-Za-z_][A-Za-z0-9_]{0,1023}\.[A-Za-z_][A-Za-z0-9_]{0,1023}',self.table):raise SourceError('INVALID_BINDING')
        code(self.billing_account)
        if type(self.maximum_bytes_billed) is not int or not 1<=self.maximum_bytes_billed<=10**12 or not self.projects or len(self.projects)>16:raise SourceError('INVALID_BINDING')
        if any(not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]',p) for p in self.projects):raise SourceError('INVALID_BINDING')
        code(self.location,r'[A-Za-z0-9-]{2,32}')
        if not 0<=self.pricing_partition_overlap_days<=31:raise SourceError('INVALID_BINDING')

@dataclass(frozen=True)
class Query:
    sql: str
    parameters: dict
    maximum_bytes_billed: int
    timeout_seconds: int
    location: str
    max_rows: int

class BigQuerySource:
    mode='BIGQUERY'
    def __init__(self,binding,transport,policy=None):
        self.binding=binding;self.source=binding.source;self.transport=transport;self.policy=policy or Policy()
        self.scope=digest([binding.table,binding.billing_account,sorted(binding.projects),binding.location])

    def query(self,window,cutoff):
        b=self.binding
        field={'DETAILED_BILLING':'usage_start_time','PRICING_EXPORT':'pricing_as_of_time','FOCUS':'ChargePeriodStart'}[self.source]
        account='BillingAccountId' if self.source=='FOCUS' else 'billing_account_id'
        export='x_ExportTime' if self.source=='FOCUS' else 'export_time'
        scope=' AND project.id IN UNNEST(@projects)' if self.source=='DETAILED_BILLING' else ''
        # Pricing catalog is account-scoped; a project scope cannot be fabricated.
        partition=''
        if self.source=='PRICING_EXPORT':
            partition=' AND (source_partition_time >= @partition_start AND source_partition_time < @partition_end OR source_partition_time IS NULL)'
        sql=f'SELECT {SELECTS[self.source]} FROM `{b.table}` WHERE {field} >= @start AND {field} < @end AND {account} = @account AND {export} <= @cutoff{scope}{partition} LIMIT @row_limit'
        params=dict(start=window.start,end=window.end,account=b.billing_account,cutoff=cutoff,row_limit=self.policy.max_rows+1)
        if scope:params['projects']=b.projects
        if partition:params.update(partition_start=window.start-timedelta(days=b.pricing_partition_overlap_days),partition_end=window.end+timedelta(days=b.pricing_partition_overlap_days))
        return Query(sql,params,b.maximum_bytes_billed,self.policy.query_seconds,b.location,self.policy.max_rows+1)

    async def extract(self,window):
        cutoff=datetime.now(timezone.utc)
        async with asyncio.timeout(self.policy.query_seconds):
            # Transport returns a complete, consistent single query result. It must
            # reject incomplete pagination and never use separate per-page queries.
            rows,bytes_processed=await self.transport.execute(self.query(window,cutoff))
        if len(rows)>self.policy.max_rows:raise SourceError('ROW_LIMIT')
        return Extraction(tuple(rows),window,cutoff,mode=self.mode,bytes_processed=bytes_processed)

class SDKTransport:
    """Adapter for an explicitly provided google.cloud.bigquery client.

    Construction does not discover credentials, instantiate clients or run queries.
    Execution is an external job requiring prior exact-operation user approval.
    """
    def __init__(self,client):self.client=client
    async def execute(self,query):
        from google.cloud import bigquery
        parameters=[]
        for name,value in query.parameters.items():
            if isinstance(value,tuple):parameters.append(bigquery.ArrayQueryParameter(name,'STRING',list(value)))
            else:
                kind='TIMESTAMP' if isinstance(value,datetime) else 'INT64' if isinstance(value,int) else 'STRING'
                parameters.append(bigquery.ScalarQueryParameter(name,kind,value))
        config=bigquery.QueryJobConfig(query_parameters=parameters,maximum_bytes_billed=query.maximum_bytes_billed,
                                      use_legacy_sql=False,job_timeout_ms=query.timeout_seconds*1000)
        def run():
            job=self.client.query(query.sql,job_config=config,location=query.location,timeout=query.timeout_seconds)
            try:
                result=job.result(timeout=query.timeout_seconds,page_size=min(1000,query.max_rows))
                rows=[]
                for row in result:
                    rows.append(dict(row))
                    if len(rows)>=query.max_rows:raise SourceError('ROW_LIMIT')
                return rows,job.total_bytes_processed or 0
            except Exception:
                job.cancel()
                raise SourceError('SOURCE_UNAVAILABLE') from None
        return await asyncio.to_thread(run)
