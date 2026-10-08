// Generated local synthetic backend DTO fixture; never runtime data.
export const sloFixture = {
  "schema_version": 1,
  "environment": "development",
  "evaluated_at": "2026-10-08T12:00:00Z",
  "items": [
    {
      "schema_version": 1,
      "slo_id": "availability",
      "name": "Availability",
      "description": "Valid governed application outcome, independent of HTTP status.",
      "objective": 0.995,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "accepted valid turns",
      "threshold_seconds": null,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 5.0,
      "consumed_fraction": 1.0,
      "remaining_fraction": 0.0,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/availability_burn.md"
    },
    {
      "schema_version": 1,
      "slo_id": "terminal_completion",
      "name": "Terminal completion",
      "description": "Exactly one canonical closure within recorded maximum deadline.",
      "objective": 0.999,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "accepted valid turns",
      "threshold_seconds": null,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 1.0,
      "consumed_fraction": 5.0,
      "remaining_fraction": -4.0,
      "burn_tiers": [
        "slow"
      ],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        }
      ],
      "state": "BREACHED",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/terminal_integrity.md"
    },
    {
      "schema_version": 1,
      "slo_id": "latency_general",
      "name": "General latency",
      "description": "Canonical M2 acceptance to backend terminal closure; 95% good event semantics.",
      "objective": 0.95,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "classified terminal turns with measured root duration",
      "threshold_seconds": 8.0,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 50.0,
      "consumed_fraction": 0.1,
      "remaining_fraction": 0.9,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/timeout_stall.md"
    },
    {
      "schema_version": 1,
      "slo_id": "latency_teams",
      "name": "Teams lookup latency",
      "description": "Canonical M2 acceptance to backend terminal closure; 95% good event semantics.",
      "objective": 0.95,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "classified terminal turns with measured root duration",
      "threshold_seconds": 20.0,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 50.0,
      "consumed_fraction": 0.1,
      "remaining_fraction": 0.9,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/timeout_stall.md"
    },
    {
      "schema_version": 1,
      "slo_id": "latency_troubleshooting",
      "name": "Governed troubleshooting latency",
      "description": "Canonical M2 acceptance to backend terminal closure; 95% good event semantics.",
      "objective": 0.95,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "classified terminal turns with measured root duration",
      "threshold_seconds": 30.0,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 50.0,
      "consumed_fraction": 0.1,
      "remaining_fraction": 0.9,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/timeout_stall.md"
    },
    {
      "schema_version": 1,
      "slo_id": "latency_complex",
      "name": "Complex multi-agent latency",
      "description": "Canonical M2 acceptance to backend terminal closure; 95% good event semantics.",
      "objective": 0.95,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "classified terminal turns with measured root duration",
      "threshold_seconds": 45.0,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 50.0,
      "consumed_fraction": 0.1,
      "remaining_fraction": 0.9,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/timeout_stall.md"
    },
    {
      "schema_version": 1,
      "slo_id": "model_ttft",
      "name": "Streaming model TTFT",
      "description": "M3 first_provider_output, not literal token or UI paint.",
      "objective": 0.95,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "streaming user provider operations with supported TTFT",
      "threshold_seconds": 5.0,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 50.0,
      "consumed_fraction": 0.1,
      "remaining_fraction": 0.9,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 0.1
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/model_provider.md"
    },
    {
      "schema_version": 1,
      "slo_id": "trace_completeness",
      "name": "Trace completeness",
      "description": "Unsampled instrumentation structure, independently of export/retention.",
      "objective": 0.999,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "terminal turns",
      "threshold_seconds": null,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 1.0,
      "consumed_fraction": 5.0,
      "remaining_fraction": -4.0,
      "burn_tiers": [
        "slow"
      ],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        }
      ],
      "state": "BREACHED",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/trace_completeness.md"
    },
    {
      "schema_version": 1,
      "slo_id": "sse_delivery",
      "name": "SSE delivery",
      "description": "Browser received message.completed; diagnostic only.",
      "objective": 0.999,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "connected SSE turns expecting completion",
      "threshold_seconds": null,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 1.0,
      "consumed_fraction": 5.0,
      "remaining_fraction": -4.0,
      "burn_tiers": [
        "slow"
      ],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 5.0
        }
      ],
      "state": "BREACHED",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/sse_delivery.md"
    },
    {
      "schema_version": 1,
      "slo_id": "model_reliability",
      "name": "Model reliability",
      "description": "Physical provider transport operations, not validation or logical retries.",
      "objective": 0.995,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "provider calls",
      "threshold_seconds": null,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 5.0,
      "consumed_fraction": 1.0,
      "remaining_fraction": 0.0,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/model_provider.md"
    },
    {
      "schema_version": 1,
      "slo_id": "dependency_gateway",
      "name": "Power Automate / Teams gateway reliability",
      "description": "Distinct physical operation population.",
      "objective": 0.995,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "Power Automate / Teams gateway operations",
      "threshold_seconds": null,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 5.0,
      "consumed_fraction": 1.0,
      "remaining_fraction": 0.0,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/power_automate_gateway.md"
    },
    {
      "schema_version": 1,
      "slo_id": "dependency_database",
      "name": "Database reliability",
      "description": "Distinct physical operation population.",
      "objective": 0.995,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "Database operations",
      "threshold_seconds": null,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 5.0,
      "consumed_fraction": 1.0,
      "remaining_fraction": 0.0,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/database_pool.md"
    },
    {
      "schema_version": 1,
      "slo_id": "dependency_storage",
      "name": "Storage reliability",
      "description": "Distinct physical operation population.",
      "objective": 0.995,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "Storage operations",
      "threshold_seconds": null,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 5.0,
      "consumed_fraction": 1.0,
      "remaining_fraction": 0.0,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/infrastructure_capacity.md"
    },
    {
      "schema_version": 1,
      "slo_id": "dependency_knowledge",
      "name": "Knowledge reliability",
      "description": "Distinct physical operation population.",
      "objective": 0.995,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "Knowledge operations",
      "threshold_seconds": null,
      "eligible": 1000,
      "good": 995,
      "bad": 5,
      "unknown": 0,
      "excluded": 0,
      "current_value": 0.995,
      "allowed_bad": 5.0,
      "consumed_fraction": 1.0,
      "remaining_fraction": 0.0,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 1800,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 3600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 21600,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        },
        {
          "seconds": 259200,
          "eligible": 1000,
          "bad": 5,
          "unknown": 0,
          "burn_rate": 1.0
        }
      ],
      "state": "HEALTHY",
      "reason": "FRESH",
      "data_source": "SRE_ROLLUPS",
      "source_status": "AVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": 1.0,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/infrastructure_capacity.md"
    },
    {
      "schema_version": 1,
      "slo_id": "dependency_graph",
      "name": "Direct Microsoft Graph reliability",
      "description": "Distinct physical operation population.",
      "objective": 0.995,
      "objective_status": "PROVISIONAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "Direct Microsoft Graph operations",
      "threshold_seconds": null,
      "eligible": 0,
      "good": 0,
      "bad": 0,
      "unknown": 0,
      "excluded": 0,
      "current_value": null,
      "allowed_bad": null,
      "consumed_fraction": null,
      "remaining_fraction": null,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        },
        {
          "seconds": 1800,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        },
        {
          "seconds": 3600,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        },
        {
          "seconds": 21600,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        },
        {
          "seconds": 259200,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        }
      ],
      "state": "DATA_SOURCE_UNAVAILABLE",
      "reason": "SOURCE_UNAVAILABLE",
      "data_source": "DIRECT_GRAPH_UNAVAILABLE",
      "source_status": "UNAVAILABLE",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": null,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/infrastructure_capacity.md"
    },
    {
      "schema_version": 1,
      "slo_id": "cost_ledger_completeness",
      "name": "Cost-ledger completeness",
      "description": "Exactly one persisted accounting event per billable operation; M10 owns the source.",
      "objective": 1.0,
      "objective_status": "ARCHITECTURAL",
      "window_seconds": 2419200,
      "window_start": "2026-09-10T12:00:00Z",
      "window_end": "2026-10-08T12:00:00Z",
      "population": "billable provider operations",
      "threshold_seconds": null,
      "eligible": 0,
      "good": 0,
      "bad": 0,
      "unknown": 0,
      "excluded": 0,
      "current_value": null,
      "allowed_bad": null,
      "consumed_fraction": null,
      "remaining_fraction": null,
      "burn_tiers": [],
      "burn_windows": [
        {
          "seconds": 300,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        },
        {
          "seconds": 1800,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        },
        {
          "seconds": 3600,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        },
        {
          "seconds": 21600,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        },
        {
          "seconds": 259200,
          "eligible": 0,
          "bad": 0,
          "unknown": 0,
          "burn_rate": null
        }
      ],
      "state": "DEFINED_NOT_EVALUATED",
      "reason": "DATA_SOURCE_AVAILABLE_IN_M10",
      "data_source": "M10_COST_LEDGER",
      "source_status": "DATA_SOURCE_AVAILABLE_IN_M10",
      "source_kind": "ISOLATED_FIXTURE",
      "coverage": null,
      "evaluated_at": "2026-10-08T12:00:00Z",
      "source_last_updated": "2026-10-08T12:00:00Z",
      "freshness_seconds": 300,
      "population_watermark": "2026-10-08T12:00:00Z",
      "runbook": "docs/Telemetry/runbooks/cost_ledger_completeness.md"
    }
  ]
} as const;
