"""Compose an inert local branch after the existing common privacy gate.

No remote exporter/client, environment resolution or launch. Active configs intact.
"""
from copy import deepcopy
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]

def compose_local(baseline: dict) -> dict:
    value = deepcopy(baseline)
    if any(key.startswith('googlecloud') for key in value['exporters']):
        raise ValueError('Only isolated local Collector baseline accepted')
    pipelines = value['service']['pipelines']
    common = pipelines['traces']['processors']
    if not {'filter/environment', 'filter/safe_scope', 'transform/redact'} <= set(common):
        raise ValueError('Common privacy gate required')
    if common.index('filter/environment') > common.index('resource/environment'):
        raise ValueError('Verify environment before enrichment')
    value['connectors']['forward/operational'] = {}
    value['exporters']['nop/operational-disabled'] = {}
    pipelines['traces']['exporters'].append('forward/operational')
    pipelines['traces/operational'] = {
        'receivers': ['forward/operational'], 'processors': ['batch'],
        'exporters': ['nop/operational-disabled']}
    # Already redacted metrics/logs can fan out independently without new receivers.
    for signal in ('metrics', 'logs'):
        if 'transform/redact' not in pipelines[signal]['processors']:
            raise ValueError('Signal privacy gate required')
        pipelines[signal]['exporters'].append('nop/operational-disabled')
    return value

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    baseline = yaml.safe_load((ROOT/'infra/observability/collector/config.local.yaml').read_text())
    args.output.write_text(yaml.safe_dump(compose_local(baseline), sort_keys=False))
