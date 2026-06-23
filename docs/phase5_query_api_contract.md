# Phase 5 — Query API Contract

## Status

This document defines the current frontend-facing contract for:

```http
POST /query


The endpoint is the primary product API for natural-language geospatial query
execution.

Endpoint
http
POST /query
Content-Type: application/json


Handler:

python
query_endpoint


Implementation location:

text
api/main.py


Service boundary:

python
OrchestratorService.handle_query(...)

Purpose

Execute a natural-language geospatial query using available inputs, uploaded
data sources, project context, routing/planning logic, plugins, providers, and
optional runtime metadata.

The endpoint is designed for product usage by frontend clients.

Request Body

Current request body style:

python
body: dict[str, Any] = Body(...)


The endpoint currently performs manual validation instead of using formal
Pydantic request models.

Request JSON
json
{
  "query": "Find parks near metro stations in Tehran",
  "inputs": {},
  "band_map": {},
  "request_id": "optional-request-id",
  "user_context": {},
  "metadata": {},
  "min_score": 0.01,
  "project_id": "optional-project-id"
}

Fields
Field	Type	Required	Description
query	string	yes	Natural-language user query. Must be a non-empty string.
inputs	object	yes	Input payload. Can include direct data, upload references, provider references, etc.
band_map	object	no	Optional raster band mapping. Defaults to {}. Values are converted to integers.
request_id	string	no	Optional client-provided request id. If omitted, service generates one.
user_context	object	no	Optional contextual metadata about user/session/project. Defaults to {}.
metadata	object	no	Optional runtime metadata for planning/provider/kernel behavior. Defaults to {}.
min_score	number	no	Optional router minimum score override.
project_id	string	no	Optional project id. Links request to project.
HTTP Validation Errors

The endpoint returns HTTP 400 for invalid request shape before execution.

Missing or invalid query

Request:

json
{
  "inputs": {}
}


Response:

http
400 Bad Request

json
{
  "detail": "'query' must be a non-empty string."
}

Invalid inputs
json
{
  "query": "show my data",
  "inputs": "bad"
}


Response:

http
400 Bad Request

json
{
  "detail": "'inputs' must be an object."
}

Invalid band_map
json
{
  "query": "calculate NDVI",
  "inputs": {},
  "band_map": "bad"
}


Response:

http
400 Bad Request

json
{
  "detail": "'band_map' must be an object when provided."
}

Invalid user_context
json
{
  "query": "show data",
  "inputs": {},
  "user_context": "bad"
}


Response:

http
400 Bad Request

json
{
  "detail": "'user_context' must be an object when provided."
}

Invalid metadata
json
{
  "query": "show data",
  "inputs": {},
  "metadata": "bad"
}


Response:

http
400 Bad Request

json
{
  "detail": "'metadata' must be an object when provided."
}

Invalid min_score
json
{
  "query": "show data",
  "inputs": {},
  "min_score": "bad"
}


Response:

http
400 Bad Request

json
{
  "detail": "'min_score' must be numeric when provided."
}

Execution Response Behavior

Execution failures are product-level states, not necessarily HTTP transport
failures.

Therefore, /query usually returns:

http
200 OK


even when the query execution fails.

Frontend must check:

json
{
  "status": "..."
}


not only HTTP status code.

Status Values

Current known status values:

Status	Meaning
success	Query completed successfully through production response builder / classic routing path.
succeeded	Query completed successfully through planning-specific response path.
partial_success	Query partially completed.
failed	Query execution failed but response was generated.
Frontend Recommendation

Frontend should treat both of these as successful execution:

text
success
succeeded


Recommended frontend normalization:

ts
const isSuccess = status === "success" || status === "succeeded";
const isFailed = status === "failed";


A future backend cleanup may normalize succeeded to success, but frontend
should currently support both.

Success Response Shape

A typical successful response from the production response builder:

json
{
  "status": "success",
  "request_id": "req-api-query-001",
  "query_hash": "optional-query-hash",
  "answer": "درخواست با موفقیت انجام شد.",
  "outputs": {},
  "confidence": {
    "level": "high",
    "score": 0.91,
    "llm_action": null,
    "is_ambiguous": false,
    "competitive_gap": null
  },
  "audit_ref": {
    "request_id": "req-api-query-001",
    "query_hash": "optional-query-hash",
    "status": "success",
    "plan_steps": 2
  },
  "warnings": [],
  "next_actions": [],
  "metadata": {
    "builder": "ProductionResponseBuilder",
    "language": "fa",
    "service": "OrchestratorService",
    "weighted_router": true
  }
}

Stable Top-Level Fields

Frontend can rely on these fields being commonly available:

Field	Type	Required-ish	Description
status	string	yes	Execution status.
request_id	string/null	yes	Request id.
query_hash	string/null	yes	Query hash if available.
answer	string	yes	User-facing response text.
outputs	object	yes	Output payload or summaries.
confidence	object	yes	Routing/planning confidence metadata.
audit_ref	object	yes	Lightweight reference to execution audit.
warnings	array	yes	User/developer-facing warnings.
next_actions	array	yes	Suggested next actions.
metadata	object	yes	Runtime/build/planning metadata.
Optional Top-Level Fields

Some response paths, especially planning/direct handlers, may include additional
fields:

Field	Type	Description
message	string	Alias/companion to answer in some paths.
layers	array	Map layers generated by planning/direct paths.
artifacts	array	Generated artifacts/files/reports.
steps	array	Planning/execution trace steps.
kernel_plan	object/null	Kernel plan summary when kernel planning is involved.
kernel_execution	object/null	Kernel execution summary when enabled.
structured_error	object/null	Present when failed and structured error exists.
report	object	Report payload when a report is generated.

Frontend should handle optional fields defensively.

Confidence Object

Typical shape:

json
{
  "level": "high",
  "score": 0.91,
  "llm_action": null,
  "is_ambiguous": false,
  "competitive_gap": null
}


Fields:

Field	Type	Description
level	string/null	Usually high, medium, low, or null.
score	number/null	Numeric confidence score when available.
llm_action	string/null	LLM/routing action if available.
is_ambiguous	boolean	Whether routing was ambiguous.
competitive_gap	number/null	Gap between top candidate routes when available.
Audit Reference

Typical shape:

json
{
  "request_id": "req-api-query-001",
  "query_hash": "optional-query-hash",
  "status": "success",
  "plan_steps": 2
}


Frontend can use request_id to fetch related resources:

http
GET /requests/{request_id}
GET /requests/{request_id}/map-layers
GET /requests/{request_id}/outputs
GET /requests/{request_id}/outputs/files

Failed Execution Response Shape

Execution failure usually returns:

http
200 OK


with:

json
{
  "status": "failed",
  "request_id": "req-api-structured-input-error-001",
  "query_hash": null,
  "answer": "در اجرای درخواست خطا رخ داد: ...",
  "outputs": {},
  "confidence": {
    "level": null,
    "score": null,
    "llm_action": null,
    "is_ambiguous": false,
    "competitive_gap": null
  },
  "audit_ref": {
    "request_id": "req-api-structured-input-error-001",
    "query_hash": null,
    "status": "failed",
    "plan_steps": null
  },
  "warnings": [
    "اجرای درخواست ناموفق بود.",
    "جزئیات خطا: ..."
  ],
  "next_actions": [
    "درخواست را دوباره اجرا کنید یا گزارش خطا را بررسی کنید.",
    "ورودی‌ها، پارامترها و داده‌های مکانی را بررسی کنید.",
    "هشدارهای پاسخ را بررسی کنید."
  ],
  "metadata": {
    "builder": "ProductionResponseBuilder",
    "language": "fa",
    "structured_error": {
      "code": "input.reference_not_found",
      "category": "validation_error",
      "message": "...",
      "retryable": false,
      "source": "input_reference_resolver",
      "details": {}
    },
    "service_structured_error": {
      "code": "input.reference_not_found",
      "category": "validation_error",
      "message": "...",
      "retryable": false,
      "source": "input_reference_resolver",
      "details": {}
    }
  },
  "structured_error": {
    "code": "input.reference_not_found",
    "category": "validation_error",
    "message": "...",
    "retryable": false,
    "source": "input_reference_resolver",
    "details": {}
  }
}

Structured Error Contract

When present, structured_error follows this shape:

json
{
  "code": "input.reference_not_found",
  "category": "validation_error",
  "message": "Upload reference not found.",
  "retryable": false,
  "source": "input_reference_resolver",
  "details": {
    "reference_kind": "raster",
    "upload_id": "upl-missing",
    "stage": "read_upload_metadata"
  }
}

Fields
Field	Type	Description
code	string	Stable-ish machine-readable error code.
category	string	Error category.
message	string	Safe human-readable message.
retryable	boolean	Whether retry may help.
source	string	Backend component that produced the error.
details	object	Debug-safe metadata. Sensitive values are redacted.
Known Categories

Examples:

text
validation_error
configuration_error
provider_error
capability_contract_error
internal_error

Known Sources

Examples:

text
input_reference_resolver
loader_plugin_contract
postgis_connector
capability_registry
dag_executor
kernel_execution_bridge
orchestrator_service

Frontend Handling Recommendation

Frontend should parse query response like this:

ts
type QueryStatus = "success" | "succeeded" | "partial_success" | "failed" | string;

function isQuerySuccess(status: QueryStatus): boolean {
  return status === "success" || status === "succeeded";
}

function isQueryFailed(status: QueryStatus): boolean {
  return status === "failed";
}


Recommended UI behavior:

Response State	UI Behavior
HTTP 400	Show request validation error before execution.
HTTP 200 + status=success/succeeded	Show answer, map layers, outputs, and result actions.
HTTP 200 + status=partial_success	Show partial result with warnings.
HTTP 200 + status=failed	Show user-friendly error panel using structured_error.
Related Follow-Up Endpoints

After /query, frontend usually calls:

http
GET /requests/{request_id}
GET /requests/{request_id}/map-layers
GET /requests/{request_id}/outputs
GET /requests/{request_id}/outputs/files
GET /requests/{request_id}/documents/{filename}


This enables:

request history
map rendering
output inspection
report/download actions
Current Test Coverage

Relevant tests:

text
tests/test_api_mvp.py
tests/test_api_structured_error_normalization.py
tests/test_api_map_layers.py
tests/test_api_outputs.py
tests/test_api_uploads.py
tests/test_api_upload_plugin_resolver.py


Covered examples:

successful /query
invalid /query body
failed /query with structured error
request history lookup
map-layer retrieval
output retrieval
upload reference query flow
Current Contract Gaps

The endpoint is functional and tested, but the following improvements are
recommended for later Phase 5 steps:

Add Pydantic request model for /query.
Add Pydantic response models or documented TypedDict-style schemas.
Normalize success vs succeeded.
Decide whether message should always mirror answer.
Decide whether layers, artifacts, and steps should always exist as empty arrays.
Add OpenAPI examples for:
successful query
failed structured error query
query with upload references
query with project id
query with PostGIS metadata
Add frontend handoff examples.
Compatibility Rule

Any future changes should be additive unless explicitly versioned.

Frontend should depend on the stable core fields and treat additional fields as
optional.
    "source": "input_reference_resolver",
    "details": {
      "reference_kind": "raster",
      "upload_id": "upl-missing",
      "stage": "read_upload_metadata"
    }
  }
}

Data Source API

Data-source endpoints register external sources and usually normalize them into
internal uploads/data sources.

Get Data Source
http
GET /data-sources/{upload_id}


Returns normalized metadata for a registered upload/data source.

Success Response
json
{
  "upload_id": "upl-...",
  "filename": "source.geojson",
  "kind": "vector",
  "project_id": "prj-..."
}

Error Response

Unknown data source:

http
404 Not Found

Delete Data Source
http
DELETE /data-sources/{upload_id}


Deletes or unregisters a data source/upload.

Success Response
json
{
  "status": "deleted",
  "upload_id": "upl-..."
}


Exact response shape may vary based on service implementation.

Update Data Source
http
PATCH /data-sources/{upload_id}
Content-Type: application/json

Request
json
{
  "display_name": "Updated name",
  "metadata": {
    "tag": "important"
  }
}

Success Response

Returns updated data-source metadata.

Preview Data Source
http
GET /data-sources/{upload_id}/preview


Returns a lightweight preview for UI display.

Typical Vector Preview
json
{
  "upload_id": "upl-...",
  "kind": "vector",
  "feature_count": 10,
  "features": [
    {
      "type": "Feature",
      "properties": {},
      "geometry": {
        "type": "Point",
        "coordinates": [51.0, 35.0]
      }
    }
  ]
}

Typical Raster Preview
json
{
  "upload_id": "upl-...",
  "kind": "raster",
  "metadata": {
    "crs": "EPSG:3857"
  },
  "shape": [2, 2]
}


Exact preview fields may vary by loader/source type.

External Data Source Registration
Register PostGIS Source
http
POST /data-sources/postgis
Content-Type: application/json


Connects to PostGIS, fetches a spatial table, converts it to GeoJSON, and saves
it as a vector upload/data source.

Request
json
{
  "project_id": "prj-...",
  "display_name": "Tehran metro stations",
  "table": "planet_osm_point",
  "schema": "public",
  "geom_col": "way",
  "where": "railway = 'station'",
  "limit": 1000,
  "output_srid": 4326,
  "dsn": "postgresql://user:pass@localhost:5432/osm_tehran",
  "host": "localhost",
  "port": 5432,
  "database": "osm_tehran",
  "user": "postgres",
  "password": "secret",
  "profile": "optional-profile",
  "connect_timeout": 10
}

Required Fields
Field	Type	Required	Description
table	string	yes	Source table name. Must be non-empty.
Optional Fields
Field	Type	Description
project_id	string	Attach registered source to project.
display_name	string	UI label.
schema	string	Database schema. Defaults are handled by connector/service.
geom_col	string	Geometry column.
where	string	Optional SQL filter.
limit	number	Max rows/features.
output_srid	number	Output CRS/SRID.
dsn	string	DSN connection string.
host	string	Database host.
port	number/string	Database port.
database	string	Database name.
user	string	Database user.
password	string	Database password.
profile	string	Optional configured connection profile.
connect_timeout	number	Connection timeout.
Success Response
json
{
  "upload_id": "upl-...",
  "filename": "planet_osm_point.geojson",
  "kind": "vector",
  "content_type": "application/geo+json",
  "parsed_json_available": true,
  "project_id": "prj-...",
  "source_type": "postgis",
  "feature_count": 1000,
  "postgis_metadata": {
    "table": "planet_osm_point"
  }
}

Validation Error

Missing table:

http
400 Bad Request

json
{
  "detail": "'table' must be a non-empty string."
}

Fetch Error

PostGIS fetch failures currently return:

http
400 Bad Request

json
{
  "detail": "PostGIS fetch failed: ..."
}


Note: This path is not yet fully normalized to the structured error contract at
the HTTP boundary.

Register WFS Source
http
POST /data-sources/wfs
Content-Type: application/json


Fetches features from a WFS service and saves them as a vector upload/data source.

Request
json
{
  "project_id": "prj-...",
  "display_name": "WFS roads",
  "base_url": "https://example.com/geoserver/wfs",
  "type_name": "workspace:roads",
  "layer": "roads",
  "service": "optional-config-profile",
  "version": "2.0.0",
  "output_format": "application/json",
  "srs_name": "EPSG:4326",
  "bbox": [51.0, 35.0, 52.0, 36.0],
  "max_features": 1000,
  "property_name": null,
  "timeout": 30
}

Required Fields

One of these must be provided:

text
service
or
base_url


If service is not provided, one of these must be provided:

text
type_name
or
layer

Success Response
json
{
  "upload_id": "upl-...",
  "filename": "workspace_roads.geojson",
  "kind": "vector",
  "content_type": "application/geo+json",
  "parsed_json_available": true,
  "project_id": "prj-...",
  "source_type": "wfs",
  "feature_count": 1000,
  "wfs_metadata": {}
}

Validation Errors

Missing base URL/service:

http
400 Bad Request

json
{
  "detail": "'base_url' or 'service' must be provided."
}


Missing type/layer when service is not used:

http
400 Bad Request

json
{
  "detail": "'type_name' or 'layer' must be provided."
}

Fetch Error
http
400 Bad Request

json
{
  "detail": "WFS fetch failed: ..."
}

Register URL Source
http
POST /data-sources/url
Content-Type: application/json


Fetches JSON/GeoJSON from a URL and saves it as an upload/data source.

Request
json
{
  "project_id": "prj-...",
  "display_name": "Remote GeoJSON",
  "url": "https://example.com/data.geojson",
  "kind": "vector",
  "timeout": 30,
  "headers": {
    "Authorization": "Bearer ..."
  }
}

Required Fields
Field	Type	Required	Description
url	string	yes	Must be a non-empty string.
Optional Fields
Field	Type	Description
project_id	string	Attach registered source to project.
display_name	string	UI label.
kind	string	Defaults to vector.
timeout	number	Defaults to 30.
headers	object	Optional request headers.
Validation Error

Missing URL:

http
400 Bad Request

json
{
  "detail": "'url' must be a non-empty string."
}

Fetch Error
http
400 Bad Request

json
{
  "detail": "URL fetch failed: ..."
}

Invalid JSON Error
http
422 Unprocessable Entity

json
{
  "detail": "Response is not valid JSON: ..."
}

Register WMS Source
http
POST /data-sources/wms
Content-Type: application/json


Registers a WMS source. This endpoint delegates to the service layer.

Request

Typical shape:

json
{
  "project_id": "prj-...",
  "display_name": "Base map layer",
  "base_url": "https://example.com/geoserver/wms",
  "layer_name": "workspace:layer",
  "service": "optional-profile",
  "version": "1.3.0",
  "styles": "",
  "format": "image/png",
  "transparent": true
}

Response

Returns service-normalized WMS source metadata.

Exact shape should be confirmed in a later contract-hardening step.

Register CSV/Table Source
http
POST /data-sources/csv-table
Content-Type: application/json


Registers a CSV/table-style data source. This endpoint delegates to the service
layer.

Request

Typical shape:

json
{
  "project_id": "prj-...",
  "display_name": "CSV points",
  "url": "https://example.com/points.csv",
  "table_name": "points",
  "lat_col": "lat",
  "lon_col": "lon",
  "x_col": "x",
  "y_col": "y",
  "crs": "EPSG:4326"
}

Response

Returns service-normalized CSV/table source metadata.

Exact shape should be confirmed in a later contract-hardening step.

Frontend Flow Recommendations
Recommended Project + Upload Flow
Create or select project:
http
POST /projects
GET /projects

Upload local data:
http
POST /uploads/raster
POST /uploads/vector

Preview data:
http
GET /data-sources/{upload_id}/preview

Execute query using upload reference:
http
POST /query


with:

json
{
  "inputs": {
    "raster_ref": "upl-..."
  }
}

Fetch map layers and outputs:
http
GET /requests/{request_id}/map-layers
GET /requests/{request_id}/outputs

Recommended External Source Flow
Create/select project.
Register external source:
http
POST /data-sources/postgis
POST /data-sources/wfs
POST /data-sources/url

Use returned upload_id as a data reference.
Preview source.
Query.
Fetch map layers/outputs.
Error Handling Notes

Current behavior has two styles:

HTTP Errors

Upload/data-source registration validation/fetch failures usually return HTTP
4xx with:

json
{
  "detail": "..."
}

Query Execution Errors

When an upload/source reference is used inside /query, execution failures
usually return HTTP 200 with:

json
{
  "status": "failed",
  "structured_error": {
    "code": "...",
    "category": "...",
    "source": "...",
    "details": {}
  }
}


Frontend should therefore handle both:

HTTP error responses for registration/upload forms
product-level failed query responses for /query
Current Test Coverage

Relevant tests:

text
tests/test_api_projects.py
tests/test_api_uploads.py
tests/test_api_upload_plugin_resolver.py
tests/test_api_structured_error_normalization.py
tests/test_api_map_layers.py
tests/test_api_outputs.py


Covered examples:

create/list/get project
upload raster JSON
upload vector GeoJSON
attach upload to project
list uploads
get upload metadata
download upload file
query using raster upload reference
query using loader plugin for TIFF
missing upload reference returns failed query response
map layers after upload-reference query
Current Contract Gaps

The current API is functional, but these gaps remain:

Upload/data-source response schemas are not formalized as Pydantic models.
External source errors are mostly string-based HTTP details.
PostGIS/WFS/URL registration responses should be standardized.
WMS and CSV/table contracts need concrete tests/examples.
Data-source preview response shape should be formalized.
Secret handling should be documented for external source registration forms.
Frontend should avoid persisting passwords/DSNs unless explicitly required.
.bak files in repository make audit commands noisy.
Compatibility Rule

Frontend should rely on these stable identifiers:

project_id
upload_id
kind
filename
project_id
source_type
feature_count
parsed_json_available

All other metadata should be treated as optional and source-specific.