-- POST-APPROVED-ROLLOUT VERIFICATION ONLY. SELECTs only; no mutation.
-- Desired local target; replace only after a genuine reconciled head is reviewed.
SELECT version_num FROM public.alembic_version;
SELECT current_database(), current_schema(), current_setting('transaction_read_only');

-- Expected tables
WITH expected(name) AS (VALUES
    ('ai_usage_ledger'),
    ('finops_accounting_source'),
    ('finops_anomaly_transition'),
    ('finops_billing_ingest_state'),
    ('finops_billing_projection_cache'),
    ('finops_billing_publication'),
    ('finops_budget_definition_version'),
    ('finops_control_generation'),
    ('finops_control_input'),
    ('finops_control_publication'),
    ('finops_forecast_backtest_generation'),
    ('finops_reconciliation_generation'),
    ('finops_reconciliation_input'),
    ('finops_reconciliation_registry'),
    ('finops_reconciliation_state'),
    ('finops_runtime_attempt'),
    ('finops_unit_definition_version'),
    ('finops_unit_denominator_snapshot'),
    ('finops_unit_driver_version'),
    ('finops_unit_economics_generation'),
    ('finops_unit_economics_publication'),
    ('finops_usage_bucket'),
    ('finops_usage_inbox'),
    ('observability_archive_receipt'),
    ('observability_retention_checkpoint'),
    ('observability_run_event'),
    ('observability_run_status'),
    ('observability_sre_bucket'),
    ('observability_sre_receipt'),
    ('observability_sre_source'),
    ('observability_turn'))
SELECT e.name, to_regclass('public.' || e.name) IS NOT NULL AS present FROM expected e ORDER BY e.name;

-- Expected explicit_indexes
WITH expected(name) AS (VALUES
    ('ix_archive_receipt_environment_terminal'),
    ('ix_billing_publication_history'),
    ('ix_billing_state_scope'),
    ('ix_control_anomaly_history'),
    ('ix_control_backtest_asof'),
    ('ix_control_history'),
    ('ix_control_input_asof'),
    ('ix_finops_attempt_debt'),
    ('ix_finops_bucket_time'),
    ('ix_finops_inbox_pending'),
    ('ix_finops_ledger_time'),
    ('ix_m15_run_retention'),
    ('ix_m15_summary_retention'),
    ('ix_observability_active'),
    ('ix_observability_recent'),
    ('ix_observability_session'),
    ('ix_observability_status_expiry'),
    ('ix_observability_summary_expiry'),
    ('ix_observability_summary_session'),
    ('ix_observability_terminal'),
    ('ix_reconciliation_inputs'),
    ('ix_sre_due'),
    ('ix_unit_snapshot_period'),
    ('ux_control_publication'),
    ('ux_finops_base_attempt'),
    ('ux_reconciliation_period'),
    ('ux_reconciliation_registry_version'),
    ('ux_unit_driver_version'),
    ('ux_unit_publication'))
SELECT e.name, to_regclass('public.' || e.name) IS NOT NULL AS present FROM expected e ORDER BY e.name;

-- Explicit constraints exclude PostgreSQL 18 NOT NULL catalog entries.
SELECT c.relname AS table_name, k.contype, count(*) AS count FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND (c.relname LIKE 'observability_%' OR c.relname LIKE 'finops_%' OR c.relname='ai_usage_ledger') AND k.contype IN ('p','f','c','u') GROUP BY c.relname,k.contype ORDER BY c.relname,k.contype;

-- Separate business prerequisite; no writes.
SELECT to_regclass('public.slopanoc_case_troubleshooting_progressions') AS progression_table;

-- Preserve existing extensions; do not create/change them.
SELECT extname,extversion FROM pg_extension ORDER BY extname;
