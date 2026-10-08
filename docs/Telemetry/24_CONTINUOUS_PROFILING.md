
# 24 — Continuous Profiling

## Production decision

Continuous backend CPU/memory profiling is included in the full observability build.

It complements traces, metrics and logs.

## Goals

Detect:
- CPU regressions,
- memory growth/leaks,
- expensive serialization,
- high-cost instrumentation code,
- pathological retrieval/model-preparation paths,
- backend performance changes not obvious from span latency alone.

## Requirements

Use a production-safe profiling mechanism compatible with deployment policy.

Profile at minimum:
- CPU,
- heap/memory where supported.

Correlate profiles with:
- service version,
- Cloud Run revision,
- environment.

Do not include:
- raw prompt contents,
- secrets,
- customer payloads.

## Overhead budget

Profiling overhead is measured independently.

If profiling creates unacceptable overhead:
- reduce sampling frequency,
- do not disable all profiling without documenting evidence.

## Dashboard integration

Show:
- CPU profile comparison by release,
- top hot paths,
- memory trends,
- observability instrumentation overhead where identifiable.

## Validation

Load tests run:
- profiling off,
- profiling on,

and compare:
- CPU,
- memory,
- p95 latency,
- throughput.
