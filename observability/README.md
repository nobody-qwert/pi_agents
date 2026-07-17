# Local observability

The collector is the only OTLP export boundary. Application telemetry exposes
only allowlisted correlation identifiers, stage, status, and bounded numbers.
Prompts, tool payloads, paths, and credentials are excluded by default.
