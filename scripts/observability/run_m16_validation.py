"""Explicit local-only M16 supervisor. No deployment or production profile execution."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import signal
import shutil

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    'tail-long': ['backend/tests/test_m16_collector_resilience.py::test_full_window_pending_pressure'],
    'sampling': ['backend/tests/test_m16_collector_resilience.py'],
    'ci': ['backend/tests/test_m16_turn_resilience.py', 'backend/tests/test_m16_accounting_resilience.py',
           'backend/tests/test_m16_archive_resilience.py', 'backend/tests/test_m16_finops_resilience.py',
           'backend/tests/test_m16_backend_crashes.py', 'backend/tests/test_m16_governed_no_replay.py',
           'backend/tests/test_m16_financial_cross_generation.py', 'backend/tests/test_m16_additional_queues.py',
           'backend/tests/test_m16_protected_read_capacity.py',
           'backend/tests/test_m16_retention_resilience.py', 'backend/tests/test_m16_security_resilience.py',
           'backend/tests/test_m15_sampling_independence.py', 'backend/tests/test_m16_load_profiles.py::test_three_fault_drain_cycles[False]',
           'backend/tests/test_m16_load_profiles.py::test_capture_memory_is_development_only_and_finite',
           'backend/tests/test_finops_runtime_recovery.py', 'backend/tests/test_observability_reliability.py',
           'backend/tests/test_finops_billing_ingestion.py', 'backend/tests/test_finops_reconciliation_publication.py',
           'backend/tests/test_finops_unit_economics_publication.py', 'backend/tests/test_finops_control_publication.py',
           'backend/tests/test_m15_retention.py', 'backend/tests/test_m15_archive.py',
           'backend/tests/test_m16_queue_resilience.py', 'backend/tests/test_m16_performance.py',
           'backend/tests/test_m16_overhead_decomposition.py', 'backend/tests/test_m16_cpu_projection_differential.py',
           'backend/tests/test_m15_evidence.py', 'backend/tests/test_observability_dependency_performance.py',
           'backend/tests/test_m16_collector_resilience.py::test_sampling_window_derivation_rejects_unsafe_overrides'],
    'native': ['backend/tests/test_m16_collector_resilience.py', 'backend/tests/test_m15_collector.py',
               'backend/tests/test_observability_collector.py', 'backend/tests/test_m16_native_saturation.py',
               'backend/tests/test_m16_combined_crashes.py'],
    'regression': ['backend/tests'],
    'extended': ['backend/tests/test_m16_load_profiles.py::test_three_fault_drain_cycles[True]',
                 'backend/tests/test_m16_load_profiles.py::test_application_soak_with_accounting_and_exporter_faults',
                 'backend/tests/test_m16_governed_performance.py'],
    'postgres': [],
}
LIMITS = dict(ci=180, native=360, sampling=180, **{'tail-long':360}, extended=720, regression=900)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=PROFILES, required=True)
    parser.add_argument('--heap-diagnostic', action='store_true')
    parser.add_argument('--cpu-diagnostic', action='store_true')
    parser.add_argument('--cpu-layer', choices=tuple('ABCDEFGHI'))
    args, extra = parser.parse_known_args()
    if args.profile == 'postgres':
        availability={name:shutil.which(name) is not None for name in ('postgres','initdb','pg_ctl','psql')}
        print('M16_POSTGRES=NOT LIVE-VALIDATED; local binaries='+str(availability))
        print('No shared DSN, install, image pull, daemon or migration fallback is authorized.')
        return 2
    # Whitelist environment: no inherited provider/credential/proxy/cloud/DB policy.
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if k in os.environ}
    with tempfile.TemporaryDirectory(prefix='slopanoc-m16-') as root:
        env.update(HOME=root, TMPDIR=root, PYTHONPATH=str(ROOT/'scripts/observability/guard')+os.pathsep+str(ROOT),
                   SLOPANOC_M16_LOCAL_TEST='1', SLOPANOC_DATABASE_URL='sqlite+aiosqlite:///:memory:',
                   SLOPANOC_KNOWLEDGE_DATABASE_URL='sqlite+aiosqlite:///:memory:',
                   SLOPANOC_MODEL_WARMUP_ENABLED='false', SLOPANOC_VERTEX_AI_ENABLED='false')
        if args.profile == 'tail-long':
            env['SLOPANOC_M16_FULL_WINDOW'] = '1'
        if args.profile == 'extended':
            env['SLOPANOC_M16_EXTENDED'] = '1'
        if args.cpu_diagnostic:env['SLOPANOC_M16_CPU_DIAGNOSTIC']='1'
        if args.cpu_layer:env['SLOPANOC_M16_CPU_LAYER']=args.cpu_layer
        if args.heap_diagnostic:
            env['SLOPANOC_M16_HEAP_DIAGNOSTIC'] = '1'
        command = [str(ROOT/'.venv/bin/python'), '-m', 'pytest', '-p', 'scripts.observability.m16_owned_resources', '-q', *PROFILES[args.profile], '-s', *extra]
        process=subprocess.Popen(command,cwd=ROOT,env=env,start_new_session=True)
        try:
            return process.wait(timeout=LIMITS[args.profile])
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            # Only the supervisor-created process group is terminated.
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
            print('M16_PROFILE_TIMEOUT: failed; owned test group terminated',flush=True)
            return 124
if __name__ == '__main__':
    sys.exit(main())
