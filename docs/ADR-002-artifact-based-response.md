
ADR-002 — Artifact-Based Response
Status

Accepted.

Context

The current system produces different response shapes depending on the execution path. Some paths return layers, others return outputs, others return reports or direct dictionaries.

This creates frontend complexity and backend inconsistency.

Decision

All plugin and execution outputs will be normalized into artifacts.

Canonical artifact types:

text
vector_layer
raster_layer
table
report
file
map_view
chart
text
json


A single response assembler will convert artifacts into the public API response.

Canonical Response Direction

The public response should include:

json
{
  "schema_version": "1.0",
  "status": "success|partial_success|failed",
  "success": true,
  "request_id": "string",
  "answer": "string",
  "artifacts": [],
  "layers": [],
  "outputs": {
    "files": [],
    "vectors": [],
    "tables": [],
    "rasters": []
  },
  "report": null,
  "steps": [],
  "warnings": [],
  "next_actions": [],
  "metadata": {}
}

Compatibility

artifacts is the canonical model.

layers, outputs, and report can remain for compatibility with current frontend and API consumers.

Consequences

Positive:

one output model
easier frontend integration
easier persistence
easier export handling
plugin outputs become normalized

Negative:

existing direct responses must be migrated
artifact normalizer must be implemented and tested