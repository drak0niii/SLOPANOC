"""Generate reviewable local JSON from central contracts. No cloud/client calls."""
from dataclasses import asdict
import json
import re
from pathlib import Path
from .slo_contract import DEFINITIONS,BURN_TIERS,SAFETY_KINDS,VERSION,DURATION_BOUNDS
from .slo_alerts import promql,POLICY_STATUS,SIGNAL_ALERTS


def widget(title,metric,filter_labels=''):
    histogram=metric.endswith(('.duration','.ttft','.lock_wait','_duration'))
    if histogram:
        metric_filter='metric.type="workload.googleapis.com/'+metric+'" AND metric.labels.environment="__ENVIRONMENT__"'
        for key,value in re.findall(r',([a-z_]+)="([^"{}]+)"',filter_labels):
            metric_filter+=' AND metric.labels.'+key+'="'+value+'"'
        query={'timeSeriesFilter':{'filter':metric_filter,'aggregation':{'alignmentPeriod':'60s','perSeriesAligner':'ALIGN_PERCENTILE_95'}}}
        title+=' (descriptive p95)'
    else:
        query={'prometheusQuery':'{__name__="workload.googleapis.com/'+metric+'"'+filter_labels+'}'}
    return {'title':title,'xyChart':{'dataSets':[{'timeSeriesQuery':query,'plotType':'LINE'}],'yAxis':{'scale':'LINEAR'}}}


def platform_widget(title,metric,resource_type,resource_filter,aligner='ALIGN_MEAN'):
    return {'title':title,'xyChart':{'dataSets':[{'timeSeriesQuery':{'timeSeriesFilter':{
        'filter':'metric.type="'+metric+'" AND resource.type="'+resource_type+'" AND '+resource_filter,
        'aggregation':{'alignmentPeriod':'60s','perSeriesAligner':aligner}}},'plotType':'LINE'}],'yAxis':{'scale':'LINEAR'}}}


def artifacts():
    manifest={'schema_version':VERSION,'objective_status':'PROVISIONAL','histogram_contract_version':2,
        'duration_bounds_seconds':DURATION_BOUNDS,'definitions':[asdict(d) for d in DEFINITIONS.values()],
        'burn_tiers':[asdict(t) for t in BURN_TIERS],'policy_status':POLICY_STATUS}
    policies=[]
    for d in DEFINITIONS.values():
        if d.objective==1 or d.source=='DIRECT_GRAPH_UNAVAILABLE':continue
        for action in ('PAGE','TICKET'):
            query=promql(d.slo_id,action)
            policies.append({'policy_id':d.slo_id+'_'+action.lower(),'displayName':d.name+' '+action+' — '+POLICY_STATUS,
                'enabled':False,'combiner':'OR','notificationChannels':[],
                'conditions':[{'displayName':'Canonical paired-window burn','conditionPrometheusQueryLanguage':{'query':query,'duration':'0s','evaluationInterval':'60s'}}],
                'documentation':{'mimeType':'text/markdown','content':'Provisional objective '+str(d.objective)+'. '+POLICY_STATUS+'. Runbook: docs/Telemetry/runbooks/'+d.runbook+'.md. One combined condition; notifications may still repeat. Group by environment/service/SLO family; derivative symptoms supply context. Contacts/channels are deployment inputs.'},
                'userLabels':{'severity':'critical' if action=='PAGE' else 'high','notification_route':'root' if d.slo_id in ('availability','terminal_completion') else 'context'}})
    signal_queries={
        'terminal_integrity':'max by (environment) ({__name__="workload.googleapis.com/slopanoc.slo.bad_fraction",operation="terminal_completion",window="300",status=~"HEALTHY|BREACHED|BURNING"}) > 0',
        'hard_safety':'sum by (environment, operation) (increase({__name__="workload.googleapis.com/slopanoc.safety.violations"}[5m])) > 0',
        'timeout_stall_spike':'sum by (environment) (increase({__name__="workload.googleapis.com/slopanoc.turn.timeouts"}[15m])) / sum by (environment) (increase({__name__="workload.googleapis.com/slopanoc.turn.accepted"}[15m])) > 0.05 and on (environment) sum by (environment) (increase({__name__="workload.googleapis.com/slopanoc.turn.accepted"}[15m])) >= 20',
        'db_acquisition_degradation':'sum by (environment) (increase({__name__="workload.googleapis.com/slopanoc.db.pool.exhaustion"}[5m])) >= 3',
        'persistence_degradation':'max by (environment) (delta({__name__="workload.googleapis.com/slopanoc.persistence.failed"}[5m])) > 0 and on (environment) max by (environment) ({__name__="workload.googleapis.com/slopanoc.persistence.pending"}) > 0',
        'telemetry_export_failure':'sum by (environment) (delta({__name__="workload.googleapis.com/slopanoc.telemetry.failed",operation="trace_export"}[5m])) > 0',
        'queue_saturation':'sum by (environment) (increase({__name__="workload.googleapis.com/slopanoc.queue.saturation"}[5m])) >= 3',
    }
    aliases={'model_degradation':'model_reliability','gateway_degradation':'dependency_gateway','trace_completeness':'trace_completeness','sse_delivery':'sse_delivery'}
    for ident,(severity,action,book,meaning) in SIGNAL_ALERTS.items():
        query=signal_queries.get(ident) or (promql(aliases[ident],'TICKET') if ident in aliases else None)
        policies.append({'policy_id':ident,'displayName':ident+' — '+POLICY_STATUS,'enabled':False,'combiner':'OR','notificationChannels':[],
            'conditions':[] if query is None else [{'displayName':meaning,'conditionPrometheusQueryLanguage':{'query':query,'duration':'300s' if ident=='telemetry_export_failure' else '0s','evaluationInterval':'60s'}}],
            'activation_status':'SOURCE_OR_BASELINE_PENDING' if query is None else 'LOCAL_DEFINITION_ONLY',
            'documentation':{'mimeType':'text/markdown','content':meaning+'. Runbook: docs/Telemetry/runbooks/'+book+'.md. '+POLICY_STATUS+'. Derivative alerts ticket/context; root service burn pages. No guaranteed single notification.'},
            'userLabels':{'severity':severity.lower(),'notification_route':'root' if action=='PAGE' else 'context'}})
    groups={
        'service_health':[('Availability budget','slopanoc.slo.budget_remaining',',operation="availability"'),('Terminal completion','slopanoc.slo.bad_fraction',',operation="terminal_completion"'),('Active execution-local turns','slopanoc.turn.active',''),('Accepted traffic','slopanoc.turn.accepted','')],
        'latency_reliability':[('Root duration by class','slopanoc.turn.duration',''),('Timeouts','slopanoc.turn.timeouts',''),('Session lock','slopanoc.session.lock_wait',''),('Queue saturation','slopanoc.queue.saturation','')],
        'models_agents_tools':[('Provider duration','gen_ai.client.operation.duration',''),('First provider output','slopanoc.model.ttft',''),('Retries','slopanoc.model.retries',''),('Token volume','slopanoc.model.input_tokens',''),('Agent duration','slopanoc.agent.duration',''),('Tool failures','slopanoc.tool.failures','')],
        'dependencies_infrastructure':[('Power Automate / Teams gateway','slopanoc.dependency.duration',',dependency="power_automate_gateway"'),('DB inclusive acquisition','slopanoc.db.connection_acquire_duration',''),('Process DB pool checked out','slopanoc.db.pool.checked_out',''),('Knowledge stages','slopanoc.knowledge.stage.duration','')],
        'telemetry_safety':[('Exporter queue','slopanoc.telemetry.queue_size',''),('Exporter loss','slopanoc.telemetry.dropped',''),('Diagnostic pending','slopanoc.persistence.pending',''),('Trace completeness','slopanoc.slo.bad_fraction',',operation="trace_completeness"'),('SSE delivery','slopanoc.slo.bad_fraction',',operation="sse_delivery"'),('Safety','slopanoc.safety.violations','')],
    }
    dashboards={}
    for key,panels in groups.items():
        tiles=[{'width':6,'height':4,'xPos':(i%2)*6,'yPos':(i//2)*4,'widget':widget(title,metric,labels)} for i,(title,metric,labels) in enumerate(panels)]
        if key=='dependencies_infrastructure':
            run_filter='resource.labels.project_id="__PROJECT_ID__" AND resource.labels.service_name="__CLOUD_RUN_SERVICE__"'
            sql_filter='resource.labels.database_id="__PROJECT_ID__:__CLOUD_SQL_INSTANCE__"'
            platform=[
                platform_widget('Cloud Run CPU saturation','run.googleapis.com/container/cpu/utilizations','cloud_run_revision',run_filter,'ALIGN_PERCENTILE_95'),
                platform_widget('Cloud Run memory saturation','run.googleapis.com/container/memory/utilizations','cloud_run_revision',run_filter,'ALIGN_PERCENTILE_95'),
                platform_widget('Cloud SQL active connections','cloudsql.googleapis.com/database/network/connections','cloudsql_database',sql_filter),
                platform_widget('Cloud SQL storage utilization','cloudsql.googleapis.com/database/disk/utilization','cloudsql_database',sql_filter),
            ]
            for i,view in enumerate(platform,4):tiles.append({'width':6,'height':4,'xPos':(i%2)*6,'yPos':(i//2)*4,'widget':view})
            tiles.append({'width':12,'height':2,'xPos':0,'yPos':16,'widget':{'title':'Platform activation','text':{'format':'MARKDOWN','content':'Platform charts require explicit environment-specific service/instance inputs and approved ingestion. Collector internal saturation and direct Graph health are source-unavailable until integration; application exporter queue is shown separately. BigQuery workloads belong to later milestones.'}}})
        dashboards[key]={'displayName':'SLOPANOC '+key.replace('_',' ').title(),'mosaicLayout':{'columns':12,'tiles':tiles},'dashboardFilters':[{'labelKey':'environment','templateVariable':'environment','filterType':'METRIC_LABEL','stringValue':'__ENVIRONMENT__'}]}
    # Bind every selector to one environment. Terraform substitutes this literal
    # placeholder; JSON itself is a reviewable template, not an active policy.
    def bind(value):
        if isinstance(value,str):
            return re.sub(r'(\{__name__="[^"{}]+")',r'\1,environment="__ENVIRONMENT__"',value)
        if isinstance(value,list):return [bind(v) for v in value]
        if isinstance(value,dict):return {k:bind(v) for k,v in value.items()}
        return value
    return manifest,bind(policies),bind(dashboards)


def write(root):
    manifest,policies,dashboards=artifacts();infra=Path(root)/'infra/observability'
    (infra/'monitoring').mkdir(parents=True,exist_ok=True);(infra/'dashboards').mkdir(exist_ok=True)
    for name,value in [('slo_manifest',manifest),('alert_policies',policies),('synthetic_checks',{'enabled':False,'workload':'synthetic','checks':['general','teams_fixture_or_approved_tenant','governed_knowledge','isolated_db_session','sse_receipt'],'remote_execution':'EXACT_OPERATION_APPROVAL_REQUIRED'})]:
        (infra/'monitoring'/f'{name}.json').write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    for name,value in dashboards.items():(infra/'dashboards'/f'{name}.json').write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')

if __name__=='__main__':write(Path(__file__).resolve().parents[2])
