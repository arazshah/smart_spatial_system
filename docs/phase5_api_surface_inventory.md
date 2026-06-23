# Phase 5 — API Surface Inventory

## Status

Phase 5 has started.

Step 1 focuses on auditing the current FastAPI surface before formalizing
frontend-facing contracts.

## Purpose

The goal of this inventory is to identify:

- available HTTP endpoints
- endpoint groups
- frontend priority
- current request style
- current response style
- contract stability
- cleanup needs before frontend handoff

## Current API Design Notes

The current API is functional and covered by tests, but most request bodies are
currently accepted as generic dictionaries:

```python
body: dict[str, Any] = Body(...)


This keeps implementation flexible, but OpenAPI schemas are not yet strongly
typed for frontend consumers.

A later Phase 5 step should decide whether to introduce Pydantic request/response
models for public endpoints.

Endpoint Groups
Core
Method	Path	Handler	Purpose	Frontend Priority	Status
GET	/	root	Basic API root metadata	Low	Stable
GET	/health	health	Runtime health check	Medium	Stable
Projects
Method	Path	Handler	Purpose	Frontend Priority	Status
POST	/projects	create_project	Create a project/workspace	High	Stable
GET	/projects	list_projects	List projects	High	Stable
GET	/projects/{project_id}	get_project	Get project details	High	Stable
GET	/projects/{project_id}/data-sources	list_project_data_sources	List data sources attached to project	High	Stable
Data Sources
Method	Path	Handler	Purpose	Frontend Priority	Status
GET	/data-sources/{upload_id}	get_data_source	Get normalized data-source metadata	High	Stable
DELETE	/data-sources/{upload_id}	delete_data_source	Delete a data source/upload	Medium	Stable
PATCH	/data-sources/{upload_id}	update_data_source	Update data-source metadata	Medium	Stable
GET	/data-sources/{upload_id}/preview	preview_data_source	Preview uploaded/external data source	High	Stable
POST	/data-sources/csv-table	register_csv_table_source	Register CSV/table-style external source	Medium	Needs contract examples
POST	/data-sources/wms	register_wms_source	Register WMS source	Medium	Needs contract examples
POST	/data-sources/postgis	register_postgis_source	Register PostGIS source	High	Needs contract examples
POST	/data-sources/wfs	register_wfs_source	Register WFS source	Medium	Needs contract examples
POST	/data-sources/url	register_url_source	Register URL source	Medium	Needs contract examples
Plugins
Method	Path	Handler	Purpose	Frontend Priority	Status
GET	/plugins	list_plugins	List available plugins/capabilities	Medium	Stable
GET	/plugins/{plugin_id}	get_plugin	Get plugin details	Medium	Stable
PATCH	/plugins/{plugin_id}	patch_plugin	Enable/disable plugin	Medium	Stable
GET	/plugins/{plugin_id}/config	get_plugin_config	Read plugin config	Low/Advanced	Internal-ish
PUT	/plugins/{plugin_id}/config	put_plugin_config	Update plugin config	Low/Advanced	Internal-ish
Settings / LLM / Planner
Method	Path	Handler	Purpose	Frontend Priority	Status
GET	/settings/runtime	get_runtime_settings	Return non-sensitive runtime settings	Medium	Stable
POST	/settings/llm/smoke-test	llm_smoke_test	Test LLM connectivity	Medium/Admin	Stable
POST	/planner/intent	plan_intent	LLM intent planning endpoint	Medium	Needs contract examples
Uploads
Method	Path	Handler	Purpose	Frontend Priority	Status
POST	/uploads/raster	upload_raster	Upload raster file	High	Stable
POST	/uploads/vector	upload_vector	Upload vector file	High	Stable
GET	/uploads	list_uploads	List uploaded data	High	Stable
GET	/uploads/{upload_id}	get_upload_metadata	Get upload metadata	High	Stable
GET	/uploads/{upload_id}/file	download_upload_file	Download uploaded file	Medium	Stable
Query and Feedback
Method	Path	Handler	Purpose	Frontend Priority	Status
POST	/query	query_endpoint	Main natural-language query execution	Critical	Stable, needs formal contract
POST	/feedback	feedback_endpoint	Submit feedback for a request	Medium	Stable
Requests / Results / Outputs
Method	Path	Handler	Purpose	Frontend Priority	Status
GET	/requests	list_requests	List previous requests	High	Stable
GET	/requests/{request_id}	get_request	Get stored request details	High	Stable
GET	/requests/{request_id}/map-layers	get_request_map_layers	Get map layers for a request	Critical	Stable
GET	/requests/{request_id}/outputs	get_request_outputs	Get output manifest	High	Stable
POST	/requests/{request_id}/outputs/save	save_request_outputs	Persist outputs for a request	Medium	Stable
GET	/requests/{request_id}/outputs/files	list_request_output_files	List generated output files	Medium	Stable
GET	/requests/{request_id}/outputs/files/{filename}	download_request_output_file	Download generated output file	Medium	Stable
GET	/requests/{request_id}/documents/{filename}	download_request_document	Download generated report/document	Medium	Stable
Weights / Learning
Method	Path	Handler	Purpose	Frontend Priority	Status
GET	/weights	get_weights	Inspect router weights	Low/Admin	Stable
POST	/weights/save	save_weights	Persist weights	Low/Admin	Stable
POST	/weights/reload	reload_weights	Reload weights	Low/Admin	Stable
POST	/weights/proposals/apply	apply_weight_proposal	Apply weight proposal	Low/Admin	Stable
Main Frontend-Critical Flows
1. Project Flow

Primary endpoints:

POST /projects
GET /projects
GET /projects/{project_id}
GET /projects/{project_id}/data-sources

Frontend need:

project selector
project creation
project detail page
project data-source list
2. Upload/Data Source Flow

Primary endpoints:

POST /uploads/raster
POST /uploads/vector
GET /uploads
GET /uploads/{upload_id}
GET /data-sources/{upload_id}
GET /data-sources/{upload_id}/preview
POST /data-sources/postgis
POST /data-sources/wfs
POST /data-sources/wms
POST /data-sources/url
POST /data-sources/csv-table

Frontend need:

upload panel
external data-source registration form
data-source preview
data-source metadata editing
3. Query Execution Flow

Primary endpoint:

POST /query

Current request body:

json
{
  "query": "natural language query",
  "inputs": {},
  "band_map": {},
  "request_id": "optional",
  "user_context": {},
  "metadata": {},
  "min_score": 0.01,
  "project_id": "optional"
}


Important response fields:

status
request_id
answer
message
outputs
layers
artifacts
steps
confidence
audit_ref
warnings
next_actions
metadata
structured_error when failed

This endpoint is the most important contract to formalize in Phase 5.

4. Map/Result Flow

Primary endpoints:

GET /requests/{request_id}
GET /requests/{request_id}/map-layers
GET /requests/{request_id}/outputs
GET /requests/{request_id}/outputs/files
GET /requests/{request_id}/outputs/files/{filename}
GET /requests/{request_id}/documents/{filename}

Frontend need:

result history
map rendering
output downloads
generated reports
5. Feedback/Learning Flow

Primary endpoints:

POST /feedback
GET /weights
POST /weights/proposals/apply

Frontend need:

user feedback form
optional admin learning/weight controls
Error Behavior

The API currently has two error behavior styles:

1. HTTP validation errors

Examples:

missing query
invalid inputs object
invalid band_map
invalid metadata
unknown request_id for detail/download endpoints

These return HTTP 4xx with detail.

2. Query execution failures

POST /query usually returns HTTP 200 with:

json
{
  "status": "failed",
  "request_id": "...",
  "structured_error": {
    "code": "...",
    "category": "...",
    "message": "...",
    "retryable": false,
    "source": "...",
    "details": {}
  }
}


This behavior is important for frontend because query execution failure is a
valid product state, not necessarily an HTTP transport failure.

Current Test Coverage

Existing API test files:

tests/test_api_mvp.py
tests/test_api_projects.py
tests/test_api_uploads.py
tests/test_api_upload_plugin_resolver.py
tests/test_api_map_layers.py
tests/test_api_outputs.py
tests/test_api_structured_error_normalization.py

Covered flows include:

health
query execution
request history
feedback
uploads
upload references
map layers
outputs
projects
structured error exposure
Observations
Strengths
API already exposes most product-critical backend functionality.
Query endpoint is operational and tested.
Upload/data-source flow exists.
Project flow exists.
Request history and map-layer retrieval exist.
Output/document download flow exists.
Structured error behavior is now available after Phase 4.
Gaps
Request/response schemas are not formalized as Pydantic models.
OpenAPI schema is therefore less useful for frontend generation.
Query response contract needs a dedicated document.
Data-source registration payloads need examples.
Some admin endpoints may not be needed in the first frontend.
Several backup .bak files exist in the repository and make audits noisy.
Recommended Phase 5 Next Steps
Formalize the /query request/response contract.
Document successful query response examples.
Document failed query response examples with structured_error.
Document upload/data-source flow examples.
Decide whether to add Pydantic models for public API endpoints.
Add API smoke tests around the finalized contract.
Prepare frontend handoff documentation.
