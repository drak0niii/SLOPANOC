// Synthetic backend policy DTO; no protected content or deployment binding.
export const policyFixture = {
  "privacy_version": "privacy-v1",
  "retention_version": "retention-v1",
  "sampling_version": "sampling-v1",
  "archive_version": "archive-v1",
  "sdk_capture_percent": 100,
  "healthy_trace_percent": 100,
  "healthy_reduction_status": "PROVISIONAL_DISABLED",
  "production_archive": "NOT_DEPLOYED",
  "production_retention_scheduling": "NOT_EXECUTED",
  "financial_retention_months": 24,
  "retention": [
    {
      "dataset": "active_terminal",
      "horizon": 24,
      "unit": "hours",
      "owner": "application",
      "semantics": "minimum_retention_then_eligible",
      "dependencies": false
    },
    {
      "dataset": "ordinary_summary",
      "horizon": 30,
      "unit": "days",
      "owner": "application",
      "semantics": "minimum_retention_then_eligible",
      "dependencies": false
    },
    {
      "dataset": "exceptional_summary",
      "horizon": 90,
      "unit": "days",
      "owner": "application",
      "semantics": "minimum_retention_then_eligible",
      "dependencies": false
    },
    {
      "dataset": "application_logs",
      "horizon": 30,
      "unit": "days",
      "owner": "cloud_logging",
      "semantics": "minimum_retention_then_eligible",
      "dependencies": false
    },
    {
      "dataset": "cloud_trace",
      "horizon": 30,
      "unit": "days",
      "owner": "provider",
      "semantics": "provider_controlled",
      "dependencies": false
    },
    {
      "dataset": "exceptional_archive",
      "horizon": 90,
      "unit": "days",
      "owner": "archive",
      "semantics": "primary_horizon_async_lifecycle",
      "dependencies": false
    },
    {
      "dataset": "analytical_metrics",
      "horizon": 13,
      "unit": "months",
      "owner": "provider",
      "semantics": "minimum_analytical_access",
      "dependencies": false
    },
    {
      "dataset": "financial_history",
      "horizon": 24,
      "unit": "months",
      "owner": "application",
      "semantics": "minimum_retention_then_eligible",
      "dependencies": true
    },
    {
      "dataset": "slo_receipts",
      "horizon": 2,
      "unit": "days",
      "owner": "application",
      "semantics": "minimum_retention_then_eligible",
      "dependencies": false
    },
    {
      "dataset": "slo_working_buckets",
      "horizon": 35,
      "unit": "days",
      "owner": "application",
      "semantics": "minimum_retention_then_eligible",
      "dependencies": false
    }
  ]
};
