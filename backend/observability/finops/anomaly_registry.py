"""Independent financial, usage and quality detector families."""
VERSION='detectors-v1'
DETECTORS={
 'TOTAL_SPEND':('M12','MONETARY'),
 'PROVIDER_MODEL_SPEND':('M12','MONETARY'),
 'SHARED_INFRASTRUCTURE':('M12','MONETARY'),
 'OBSERVABILITY_SPEND':('M12','MONETARY'),
 'UNIT_COST':('M13','MONETARY'),
 'USAGE_VOLUME':('M12_FROZEN_M10','USAGE'),
 'BILLING_CORRECTION':('M11_M12','QUALITY'),
 'UNALLOCATED_EXPOSURE':('M12','QUALITY'),
 'RECONCILIATION_QUALITY':('M12','QUALITY')}

def features(day,currency,scope):
    values={('TOTAL_SPEND',scope):day.values.get(scope+':'+currency),
        ('SHARED_INFRASTRUCTURE','SHARED'):day.values.get('CLASS_SHARED:'+currency),
        ('UNALLOCATED_EXPOSURE','UNALLOCATED'):day.unallocated.get(currency),
        ('RECONCILIATION_QUALITY','COVERAGE'):day.coverage}
    for k,v in day.values.items():
        if k.startswith('MODEL_') and k.endswith(':'+currency):values[('PROVIDER_MODEL_SPEND',k[:-4])]=v
    # MONITORING_LOGGING category alone is NOT a full observability population.
    if 'OBSERVABILITY:'+currency in day.values:values[('OBSERVABILITY_SPEND','OBSERVABILITY')]=day.values['OBSERVABILITY:'+currency]
    for k,v in day.units.items():
        if k.endswith(':'+currency):values[('UNIT_COST',k[:-4])]=v
    for k,v in day.usage.items():values[('USAGE_VOLUME',k)]=v
    return values
