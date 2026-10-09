"""Disabled source-controlled alerts and local dry-run matcher. No external transport."""
import json
from pathlib import Path

RULES={
 'budget-exceeded':('budget_exceeded','finops-budget-exceeded.md'),
 'projected-overrun':('projected_overrun','finops-projected-overrun.md'),
 'cost-anomaly':('cost_anomalies','finops-cost-anomaly.md'),
 'unit-cost-anomaly':('unit_cost_anomalies','finops-unit-cost-anomaly.md'),
 'unallocated-cost':('unallocated_surges','finops-unallocated-cost.md'),
 'stale-source':('source_degradation','finops-stale-source.md')}

def definitions():
    return [dict(policy_id=key,displayName='FinOps '+key,enabled=False,notificationChannels=[],
        userLabels=dict(milestone='m14',policy_status='provisional',notification_route='none',group='financial_source'),
        documentation=dict(content='Runbook: docs/runbooks/'+book+'\nSource quality is the root; suppress financial symptoms during source degradation. Diagnosis only; exact approval required for any external mutation.',mimeType='text/markdown'),
        conditions=[dict(displayName=key,conditionPrometheusQueryLanguage=dict(query='sum by (environment) (increase(slopanoc_finops_controls_'+metric+'{environment="__ENVIRONMENT__"}[24h])) > 0',duration='0s'))]) for key,(metric,book) in RULES.items()]

def dry_run(budgets,forecasts,anomalies):
    """Match and deduplicate root/symptom signals without sending notifications."""
    signals=set()
    degraded=any(r.get('type')=='RECONCILIATION_QUALITY' and r.get('state')!='NORMAL' for r in anomalies.get('results',[]))
    if degraded:return ['stale-source']
    if any(b['state']=='EXCEEDED' or b.get('positive_zero_budget_breach') for b in budgets):signals.add('budget-exceeded')
    if any(f['risk']=='PROJECTED_OVER' and f['confidence']=='QUALIFIED' for f in forecasts):signals.add('projected-overrun')
    for r in anomalies.get('results',[]):
        if r['state']!='ACTIVE' or r['confidence']!='HIGH':continue
        if r['type']=='UNIT_COST':signals.add('unit-cost-anomaly')
        elif r['type']=='UNALLOCATED_EXPOSURE':signals.add('unallocated-cost')
        elif r['label']=='MONETARY':signals.add('cost-anomaly')
    return sorted(signals)

def validate(root):
    root=Path(root);actual=json.loads((root/'infra/observability/monitoring/finops_alert_policies.json').read_text())
    if actual!=definitions():raise ValueError('Alert definitions differ')
    for p in actual:
        if p['enabled'] or p['notificationChannels']:raise ValueError('Unsafe alert definition')
    for metric,book in RULES.values():
        if not (root/'docs/runbooks'/book).is_file():raise ValueError('Runbook absent')
    return True
