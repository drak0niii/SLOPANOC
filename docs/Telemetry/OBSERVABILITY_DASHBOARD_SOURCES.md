# Dashboard source audit — 2026-10-11

All routes below are prefixed `/api/observability` and installed by the independent
`backend.api.observability_app:create_app`. No business endpoints or POST receipts.
Environment is server verified on every read; config/policy/archive are deployment
bound. Status means the read is supported, not that production contains data.

| Endpoint | DTO | Source | Capability | Exact fields consumed / available | Time support | Environment | Status |
|---|---|---|---|---|---|---|---|
| `/policies` | PolicyView | M15 | EFFECTIVE_CONFIGURATION | retention, privacy_version, production_archive | snapshot | server scope | SUPPORTED |
| `/access` | AccessView | M15 | verified identity | capabilities, environments | snapshot | server scope | SUPPORTED |
| `/archive/{trace_id}` | ArchivePage | M15 | TECHNICAL_DIAGNOSTICS | spans[].span_id, parent_span_id, start_ns, end_ns, status, name; next_offset | trace + digest | server scope | SUPPORTED |
| `/runs` | RunPage | M7 | OPERATIONAL_METADATA | items[]/run: run_id, status, classification, elapsed_ms, started_at, current_agent; next_cursor, as_of | since/until for /runs and /active only | server scope | SUPPORTED |
| `/active` | RunPage | M7 | OPERATIONAL_METADATA | items[]/run: run_id, status, classification, elapsed_ms, started_at, current_agent; next_cursor, as_of | since/until for /runs and /active only | server scope | SUPPORTED |
| `/runs/{run_id}` | RunView | M7 | OPERATIONAL_METADATA | items[]/run: run_id, status, classification, elapsed_ms, started_at, current_agent; next_cursor, as_of | snapshot / selected run | server scope | SUPPORTED |
| `/runs/{run_id}/retention` | RunRetentionView | M15 | OPERATIONAL_METADATA | instrumentation, cloud_trace, exceptional_archive | snapshot / selected run | server scope | SUPPORTED |
| `/runs/{run_id}/timeline` | TimelinePage | M7 | OPERATIONAL_METADATA | items[].event_seq, timestamp, elapsed_ms, stage, status, agent, tool, dependency | snapshot / selected run | server scope | SUPPORTED |
| `/sessions/{session_id}/runs` | RunPage | M7 | OPERATIONAL_METADATA | items[]/run: run_id, status, classification, elapsed_ms, started_at, current_agent; next_cursor, as_of | snapshot / selected run | server scope | SUPPORTED |
| `/health` | HealthView | M7 | OPERATIONAL_METADATA | persistence, read_plane_state, anoc_state, collector_state, as_of | snapshot / selected run | server scope | SUPPORTED |
| `/config` | list[EffectiveConfiguration] | M15 | EFFECTIVE_CONFIGURATION | setting, value, owner, read_only | snapshot | server scope | SUPPORTED |
| `/slos` | SLOPage | M9 | OPERATIONAL_METADATA | items[].current_value, objective, coverage, burn_tiers, source_status, state | rolling 28d | server scope | SUPPORTED |
| `/slos/{slo_id}` | SLOView | M9 | OPERATIONAL_METADATA | items[].current_value, objective, coverage, burn_tiers, source_status, state | rolling 28d | server scope | SUPPORTED |
| `/finops/runtime-usage` | UsagePage | M10 | FINANCIAL_DATA | items[].attempts, captured_attempts, quantities, quantity_coverage | start/end UTC datetime ≤31d | server scope | SUPPORTED |
| `/finops/ledger-health` | LedgerHealth | M10 | FINANCIAL_DATA | state, pending, debt, last_persistence | snapshot | server scope | SUPPORTED |
| `/finops/ledger-completeness` | SLOView | M10 | FINANCIAL_DATA | current_value, coverage, state | snapshot | server scope | SUPPORTED |
| `/finops/billing-health` | HealthPage | M11 | FINANCIAL_DATA | sources[].state, as_of, coverage_start/end | snapshot | server scope | SUPPORTED |
| `/finops/pricing-status` | PricingStatus | M11 | FINANCIAL_DATA | source, earliest_pricing_as_of, latest_pricing_as_of | snapshot | server scope | SUPPORTED |
| `/finops/billing-summary` | BillingSummary | M11 | FINANCIAL_DATA | amounts[].net, currency, category; source.state; coverage_complete | start/end UTC dates; invoice_month for billing | server scope | SUPPORTED |
| `/finops/reconciliation` | ReconciliationPage | M12 | FINANCIAL_DATA | items[].amounts, coverage, runtime_quality, display_state | start/end UTC dates; invoice_month for billing | server scope | SUPPORTED |
| `/finops/reconciliation/{period}` | ReconciliationPage | M12 | FINANCIAL_DATA | items[].amounts, coverage, runtime_quality, display_state | start/end UTC dates; invoice_month for billing | server scope | SUPPORTED |
| `/finops/unit-economics` | UnitEconomicsPage | M13 | FINANCIAL_DATA | items[].results, published_at, job_state | start/end UTC dates; invoice_month for billing | server scope | SUPPORTED |
| `/finops/unit-economics/{unit_id}` | UnitEconomicsPage | M13 | FINANCIAL_DATA | items[].results, published_at, job_state | start/end UTC dates; invoice_month for billing | server scope | SUPPORTED |
| `/finops/allocation` | ReconciliationPage | M12 | FINANCIAL_DATA | items[].amounts, coverage, runtime_quality, display_state | start/end UTC dates; invoice_month for billing | server scope | SUPPORTED |
| `/finops/budgets` | ControlPage | M14 | FINANCIAL_DATA | items[].actual_observed, amount, forecast, confidence, risk, severity, reasons, state, freshness | published month, not global days | server scope | SUPPORTED |
| `/finops/budgets/{budget_id}` | ControlPage | M14 | FINANCIAL_DATA | items[].actual_observed, amount, forecast, confidence, risk, severity, reasons, state, freshness | published month, not global days | server scope | SUPPORTED |
| `/finops/forecasts` | ControlPage | M14 | FINANCIAL_DATA | items[].actual_observed, amount, forecast, confidence, risk, severity, reasons, state, freshness | published month, not global days | server scope | SUPPORTED |
| `/finops/forecasts/{generation_id}` | ControlPage | M14 | FINANCIAL_DATA | items[].actual_observed, amount, forecast, confidence, risk, severity, reasons, state, freshness | published month, not global days | server scope | SUPPORTED |
| `/finops/anomalies` | ControlPage | M14 | FINANCIAL_DATA | items[].actual_observed, amount, forecast, confidence, risk, severity, reasons, state, freshness | published month, not global days | server scope | SUPPORTED |
| `/finops/anomalies/{anomaly_id}` | ControlPage | M14 | FINANCIAL_DATA | items[].actual_observed, amount, forecast, confidence, risk, severity, reasons, state, freshness | published month, not global days | server scope | SUPPORTED |

## Dashboard component mapping

| Component / required value | Endpoint | Exact DTO fields | Capability | Range | Environment | Status |
|---|---|---|---|---|---|---|
| Total tokens / breakdown | finops/runtime-usage?group=agent | quantities.total_tokens.observed/known/unknown; input/output/cached/thought/tool_input; attempts/captured_attempts; next_cursor | FINANCIAL_DATA | selected UTC days | server scope | PARTIAL: observed only, unknown coverage disclosed; incomplete page unavailable |
| Actual billed cost / breakdown | finops/billing-summary | amounts[].net/currency/category; coverage_complete; source.state | FINANCIAL_DATA | selected UTC dates | server scope | SUPPORTED per currency/category; no conversion |
| Active runs | active | items, next_cursor, as_of | OPERATIONAL_METADATA | current snapshot, ignores day range (includes older active runs) | server scope | PARTIAL: unavailable aggregate if paginated |
| Agents observed / activity | finops/runtime-usage?group=agent | dimension, attempts, quantities, quantity_coverage | FINANCIAL_DATA | selected UTC days | server scope | SUPPORTED model usage, not online state |
| Success rate | slos | availability.current_value, eligible/good/unknown/excluded, source_status/state | OPERATIONAL_METADATA | fixed rolling 28d | server scope | SUPPORTED server availability semantics |
| P95 turn latency | none | M9 latency.current_value is threshold compliance, not p95 | OPERATIONAL_METADATA | unavailable | server scope | UNSUPPORTED |
| Token & cost time series | none jointly | M10 has dimensions but no time bucket; M12 daily financial rows have no compatible bucketed M10 total | FINANCIAL_DATA | unavailable | server scope | UNSUPPORTED; hide chart, no interpolated zeros |
| Runs / detail | runs; runs/{run_id}; timeline; retention; archive/{trace_id} | safe M7 metadata/events, retained span timing/parent IDs | OPERATIONAL_METADATA / TECHNICAL_DIAGNOSTICS for archive | selected days / selected run | server scope | PARTIAL: no per-run token/cost/request-class/model-call join |
| Finance / unit economics | reconciliation; allocation; unit-economics | amounts.direct/shared/unallocated, percentages; results.direct_cost_per_unit/fully_loaded_cost_per_unit/coverage | FINANCIAL_DATA | selected UTC dates | server scope | SUPPORTED; analytical views non-additive |
| Forecast / budgets / anomalies | budgets; forecasts; anomalies | actual_observed, amount, utilization_pct, forecast, confidence, severity, reasons, state, freshness | FINANCIAL_DATA | published month | server scope | SUPPORTED; not global day interval |
| SLO / reliability / diagnostics | slos; health; retention | current_value, objective, burn_tiers, state/source_status; persistence, read_plane_state, collector_state | OPERATIONAL_METADATA | fixed rolling window / snapshot | server scope | PARTIAL: producer-local/Collector health unknown |
| Configuration | config; policies; access; billing-health; pricing-status | effective values/owners, retention, capabilities/environments, source health | EFFECTIVE_CONFIGURATION / FINANCIAL_DATA for financial health | snapshot | deployment/server scope | SUPPORTED read-only |

Unknown usage never becomes zero. Total tokens uses the provider-reported total
component only; cached/thought/candidate components can overlap and are not added
a second time. Characters are displayed separately. No financial estimates,
frontend SLO formulas, prior-period comparisons, invented topology or permanent
workflow. M7 has no session IDs in its public RunView; label runs accurately.

## Hybrid projection source planning — 2026-10-11

[Doc 26](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) defines future durable activity/
latency/alert projections from audit reference events and sanitized operational
OTLP. These sources are NOT IMPLEMENTED by this architecture/scaffolding pass;
every unsupported/partial mapping above remains truthful. No new chart/count or
historical drill-down coverage is implied. Future endpoint mappings must explicitly
state source generation, capture classification, watermark, coverage and freshness;
M10 usage and M11–M14 financial generations remain authoritative.
