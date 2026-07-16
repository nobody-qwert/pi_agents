# 034: Safe correlated telemetry and local dashboards

## Objective

Instrument API, runner, model/tool/artifact operations with safe OpenTelemetry and
provide local trace/log/metric exploration linked from application records.

## Context and references

- `docs/design/PLAN.md` Section 8.1.
- `docs/design/TECHNICAL_DETAILS.md` Sections 3.3, 10.4, 11, and 13.

## Dependencies

- 007, 009, and 026-028.

## In scope

- Trace hierarchy and shared correlation attributes for run through validation.
- Metrics for latency, usage, retries, failures, queue/approval wait, and outcomes.
- Structured safe logs, default content-capture-off redaction, and trace links.
- Collector, Tempo, Loki, Prometheus, and Grafana configuration/provisioning.

## Out of scope

- Replacing authoritative audit events, production retention platform, alerting
  program, and unrestricted prompt/tool content capture.

## Implementation constraints

- Collector is the export boundary; application code is backend-agnostic.
- Avoid high-cardinality/sensitive metric labels and credentials in all signals.
- Missing/sampled telemetry cannot affect authoritative completion or recovery.

## Acceptance criteria

- One fixture run correlates API, runner, model/tool, validation, and transition
  spans by safe identifiers.
- Dashboards display representative metrics/logs/traces and app links resolve.
- Redaction tests prove protected fixture content is absent with capture disabled.

## Verification

- Run telemetry/redaction tests and an integration smoke test through collector
  and backends.

## Handoff

- Report signal schema and dashboard locations; stop before whole-stack assembly.
