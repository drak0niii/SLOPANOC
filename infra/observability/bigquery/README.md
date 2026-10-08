# M11 normalized financial contracts (v1)

SQL files are definitions only, never executed by application startup or CI.
Raw Detailed/Pricing/optional FOCUS tables remain analytical source truth. Compatibility
views must be bound to reviewed schemas and scoped datasets with approved access.
All consuming source queries are fixed in bigquery_source.py, with one daily logical
window, account/project scope where the source provides it, export cutoff, explicit
byte cap, job timeout and overflow row guard. Pricing also requires physical ingest
partition bounds (including null streaming partitions); scope gaps are not finality.
Detailed usage_start_time predicates prune its usage partitions, including later
corrective lines for old usage dates. FOCUS requires verification that its actual
partition supports ChargePeriodStart pruning before production activation.

Provider docs checked 2026-10-08:
- https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/detailed-usage
- https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/pricing-data
- https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/focus-export

SQL casts source floats to BIGNUMERIC before the Python boundary. That preserves
export precision, not unavailable original precision. No float is accepted by the
normalizer. CAST rounding to38 places follows BigQuery; overflow/missing required
fields reject generation. Price.* evidence stays with billing lines; pricing catalog
is separate. Credits arrays never multiply parent cost; cumulative corrective lines
are retained. Actual export schemas/permissions/partition cost require live validation.

Resource daily view is warehouse-restricted and unallocated source aggregation,
not run/team allocation. API consumers use bounded published summaries, not raw SQL.
No prices/totals from fixtures imply cloud spend. Retention target24months; no delete
or settlement executor. Source-line audit lives in warehouse; DB manifests retain
fingerprint/multiplicity row count/control totals/previous generations without copying
raw lines. Historical catalog price before proven daily observation is unavailable.
