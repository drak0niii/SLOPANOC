-- REVIEW ONLY / DO NOT EXECUTE
-- Target proposal: pr-msn-dev-gl-slopai-01 / development / sloc-anoc-sandbox01
-- Existing database slopanoc, schema public. No passwords, LOGINs or membership
-- assignments. Map reviewed dedicated authenticated logins to these NOLOGIN roles
-- separately; existing ANOC business-writer permissions are not replaced here.
-- Migration owner must differ from all runtime logins. No ownership/DDL grants.
CREATE ROLE slopanoc_hybrid_anoc NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE slopanoc_hybrid_publisher NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE slopanoc_hybrid_projector NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE slopanoc_hybrid_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE slopanoc TO slopanoc_hybrid_anoc, slopanoc_hybrid_publisher, slopanoc_hybrid_projector, slopanoc_hybrid_reader;
GRANT USAGE ON SCHEMA public TO slopanoc_hybrid_anoc, slopanoc_hybrid_publisher, slopanoc_hybrid_projector, slopanoc_hybrid_reader;
GRANT INSERT ON public.observability_critical_audit, public.observability_audit_outbox TO slopanoc_hybrid_anoc;
GRANT SELECT (event_id, payload_hash) ON public.observability_critical_audit TO slopanoc_hybrid_anoc;
GRANT SELECT (event_id) ON public.observability_audit_outbox TO slopanoc_hybrid_anoc;
GRANT SELECT ON public.observability_audit_outbox TO slopanoc_hybrid_publisher;
GRANT UPDATE (publication_state, attempt_count, available_at, lease_owner, lease_expires_at, published_at, last_failure_code)
 ON public.observability_audit_outbox TO slopanoc_hybrid_publisher;
GRANT SELECT, INSERT, UPDATE ON public.observability_projection_receipt,
 public.observability_projection_failure, public.observability_run_projection,
 public.observability_agent_projection TO slopanoc_hybrid_projector;
GRANT SELECT ON public.observability_critical_audit, public.observability_audit_outbox,
 public.observability_projection_receipt, public.observability_projection_failure,
 public.observability_run_projection, public.observability_agent_projection,
 public.observability_run_status, public.observability_turn, public.observability_run_event,
 public.observability_sre_receipt, public.observability_sre_bucket, public.observability_sre_source,
 public.observability_archive_receipt, public.observability_retention_checkpoint,
 public.finops_runtime_attempt, public.finops_usage_inbox, public.ai_usage_ledger,
 public.finops_usage_bucket, public.finops_accounting_source,
 public.finops_billing_ingest_state, public.finops_billing_publication,
 public.finops_billing_projection_cache,
 public.finops_reconciliation_registry, public.finops_reconciliation_input,
 public.finops_reconciliation_generation, public.finops_reconciliation_state,
 public.finops_unit_definition_version, public.finops_unit_driver_version,
 public.finops_unit_denominator_snapshot, public.finops_unit_economics_generation,
 public.finops_unit_economics_publication, public.finops_budget_definition_version,
 public.finops_control_input, public.finops_control_generation,
 public.finops_forecast_backtest_generation, public.finops_control_publication,
 public.finops_anomaly_transition TO slopanoc_hybrid_reader;
-- No GRANT ALL / DELETE / TRUNCATE / REFERENCES / schema CREATE / default grants.
-- Before approval inspect existing role memberships, PUBLIC/schema privileges,
-- login inheritance, table names and effective grants; these statements alone
-- cannot remove pre-existing inherited privileges. No runtime auto-provisioning.

-- Additive execution tranche, e27b2c8d0f51; still REVIEW ONLY outside disposable tests.
GRANT SELECT,INSERT ON public.execution_action_record TO slopanoc_hybrid_anoc;
GRANT UPDATE (state,updated_at,downstream_request_id) ON public.execution_action_record TO slopanoc_hybrid_anoc;
GRANT SELECT,INSERT,UPDATE ON public.execution_activity_record TO slopanoc_hybrid_projector;
GRANT SELECT,INSERT ON public.execution_audit_timeline TO slopanoc_hybrid_projector;
GRANT SELECT ON public.execution_action_record,public.execution_activity_record,
 public.execution_audit_timeline TO slopanoc_hybrid_reader;
-- Journal remains observability_critical_audit, protected by immutable trigger.
-- No publisher action access; no projector/reader action writes; no egress jobs.

-- Publisher hydrates immutable journal references; never trusts a mutable delivery copy.
GRANT SELECT ON public.observability_critical_audit TO slopanoc_hybrid_publisher;
