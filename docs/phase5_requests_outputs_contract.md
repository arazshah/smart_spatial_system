# Phase 5 — Requests, Map Layers, Outputs, and Documents API Contract

## Status

This document defines the current frontend-facing contract for request history,
map layers, persisted outputs, output files, and downloadable documents.

These endpoints are the main post-query retrieval layer for frontend clients.

After a successful `/query`, frontend should use `request_id` to retrieve:

- request detail
- map layers
- output manifest
- downloadable output files
- generated documents/reports

---

# Endpoint Summary

| Method | Path | Purpose | Frontend Priority |
|---|---|---|---|
| GET | `/requests` | List previous requests | High |
| GET | `/requests/{request_id}` | Get request detail/audit record | High |
| GET | `/requests/{request_id}/map-layers` | Get Leaflet-ready map layers | Critical |
| GET | `/requests/{request_id}/outputs` | Get persisted output manifest | High |
| POST | `/requests/{request_id}/outputs/save` | Persist outputs again | Medium/Admin |
| GET | `/requests/{request_id}/outputs/files` | List generated output files | High |
| GET | `/requests/{request_id}/outputs/files/{filename}` | Download generated output file | High |
| GET | `/requests/{request_id}/documents/{filename}` | Download generated document/report | Medium |

---

# Request History API

## List Requests

```http
GET /requests


Returns stored request records.

Success Response
json
[
  {
    "request_id": "req-api-requests-001",
    "query": "calculate NDVI...",
    "status": "success",
    "production_response": {
      "status": "success",
      "request_id": "req-api-requests-001"
    }
  }
]


Exact record fields may include execution metadata, production response,
audit information, inputs summary, output summaries, or planning metadata.

Frontend Usage

Use this endpoint for:

request history panel
recent analyses list
re-opening previous results
linking to map/output pages

Frontend should treat records as flexible objects and rely primarily on:

text
request_id
status
production_response.status
production_response.answer
production_response.metadata

Get Request
http
GET /requests/{request_id}


Returns a single stored request record.

Success Response
json
{
  "request_id": "req-api-requests-001",
  "query": "calculate NDVI...",
  "status": "success",
  "production_response": {
    "status": "success",
    "request_id": "req-api-requests-001",
    "answer": "..."
  }
}

Error Response

Unknown request:

http
404 Not Found

json
{
  "detail": "Unknown request_id: req-missing"
}

Frontend Usage

Use this endpoint for:

result detail page
debug/inspection drawer
restoring previous query result
retrieving production response if the original /query response is not in memory
Map Layers API
Get Request Map Layers
http
GET /requests/{request_id}/map-layers


Returns Leaflet-ready map layers for a previous request.

Success Response

Example from current tests:

json
{
  "request_id": "req-api-map-layers-001",
  "layer_count": 1,
  "layers": [
    {
      "name": "vegetation_polygons",
      "kind": "vector",
      "crs": "EPSG:4326",
      "feature_count": 3,
      "geojson": {
        "type": "FeatureCollection",
        "features": [
          {
            "type": "Feature",
            "properties": {},
            "geometry": {
              "type": "Polygon",
              "coordinates": []
            }
          }
        ]
      }
    }
  ]
}

Stable Fields

Top-level:

Field	Type	Description
request_id	string	Request id.
layer_count	number	Number of available map layers.
layers	array	List of map layer objects.

Layer object:

Field	Type	Description
name	string	Internal layer/output name.
kind	string	Usually vector, possibly raster/other in future.
crs	string/null	CRS for frontend display. Current vector output uses EPSG:4326.
feature_count	number/null	Number of vector features when available.
geojson	object/null	GeoJSON FeatureCollection for vector rendering.
Frontend Usage

Use this endpoint immediately after successful /query if map visualization is
needed.

Recommended flow:

text
POST /query
if status is success/succeeded:
    GET /requests/{request_id}/map-layers
    render layers on Leaflet map

Error Response

Unknown request:

http
404 Not Found

json
{
  "detail": "Unknown request_id: ..."
}


Some map-building failures may return:

http
400 Bad Request


with:

json
{
  "detail": "..."
}

Outputs API
Get Output Manifest
http
GET /requests/{request_id}/outputs


Returns persisted output manifest for a request.

This requires output persistence to be enabled in service configuration.

Success Response

Example:

json
{
  "schema_version": "1.0.0",
  "request_id": "req-api-outputs-001",
  "files": [
    {
      "filename": "manifest.json",
      "kind": "manifest"
    },
    {
      "filename": "production_response.json",
      "kind": "production_response"
    },
    {
      "filename": "audit_record.json",
      "kind": "audit_record"
    },
    {
      "filename": "outputs_summary.json",
      "kind": "outputs_summary"
    },
    {
      "filename": "map_layers.json",
      "kind": "map_layers"
    },
    {
      "filename": "vegetation_polygons.geojson",
      "kind": "vector"
    }
  ]
}

Currently Tested Files

The current tests expect these files after a persisted NDVI/vector output flow:

text
manifest.json
production_response.json
audit_record.json
outputs_summary.json
map_layers.json
vegetation_polygons.geojson

Stable Fields
Field	Type	Description
schema_version	string	Manifest schema version. Currently 1.0.0.
request_id	string	Request id.
files	array	List of persisted output files.

File object commonly includes:

Field	Type	Description
filename	string	Downloadable filename.
kind	string	File/output kind.

Additional file metadata may be present.

Error Response

Unknown request or missing manifest:

http
404 Not Found

json
{
  "detail": "..."
}

Save Request Outputs
http
POST /requests/{request_id}/outputs/save


Persists outputs for a request again.

Success Response
json
{
  "request_id": "req-api-outputs-save-001",
  "schema_version": "1.0.0",
  "files": []
}


Exact response depends on output storage implementation.

Error Responses

Unknown request:

http
404 Not Found


Other output save failures:

http
400 Bad Request

List Request Output Files
http
GET /requests/{request_id}/outputs/files


Lists persisted output files for a request.

Success Response
json
[
  {
    "filename": "manifest.json",
    "kind": "manifest"
  },
  {
    "filename": "vegetation_polygons.geojson",
    "kind": "vector"
  }
]

Error Response

Unknown request or output directory:

http
404 Not Found

Download Request Output File
http
GET /requests/{request_id}/outputs/files/{filename}


Downloads a persisted output file.

Example
http
GET /requests/req-api-outputs-geojson-001/outputs/files/vegetation_polygons.geojson

GeoJSON Success Response

For .geojson output files:

json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {},
      "geometry": {
        "type": "Polygon",
        "coordinates": []
      }
    }
  ]
}

Error Response

Missing file:

http
404 Not Found

json
{
  "detail": "..."
}

Documents API
Download Request Document
http
GET /requests/{request_id}/documents/{filename}


Downloads a generated document for a request.

Current implementation is intentionally restrictive.

Current Security Policy

The endpoint:

serves only files from artifacts/reports
blocks path traversal
currently allows only the real-estate ranking PDF generated for the same request_id
expects filename format:
text
real_estate_ranking_{request_id}.pdf

Example
http
GET /requests/req-api-report-doc-001/documents/real_estate_ranking_req-api-report-doc-001.pdf

Success Response

Returns a PDF file response:

http
200 OK
Content-Type: application/pdf


Body:

text
PDF bytes

Error Responses

Wrong filename:

http
404 Not Found

json
{
  "detail": "Unknown document file."
}


Path traversal attempt:

http
404 Not Found

json
{
  "detail": "Unknown document file."
}


Missing file:

http
404 Not Found

json
{
  "detail": "Unknown document file."
}

Recommended Frontend Result Flow
Normal Query Result Flow
text
1. POST /query
2. Read response.status and request_id
3. If success/succeeded:
   3.1 GET /requests/{request_id}/map-layers
   3.2 GET /requests/{request_id}/outputs
   3.3 GET /requests/{request_id}/outputs/files
4. Render:
   - answer panel
   - map layers
   - output/download list
   - warnings/next actions

Reopening Previous Result
text
1. GET /requests
2. User selects a request
3. GET /requests/{request_id}
4. GET /requests/{request_id}/map-layers
5. GET /requests/{request_id}/outputs

Downloading Output
text
1. GET /requests/{request_id}/outputs/files
2. User selects filename
3. GET /requests/{request_id}/outputs/files/{filename}

Downloading Document
text
1. Read document metadata from query/result outputs if available
2. Use document download_url when present
3. GET /requests/{request_id}/documents/{filename}

Frontend Error Handling

Frontend should handle:

Unknown Request
http
404 Not Found

json
{
  "detail": "Unknown request_id: ..."
}


Recommended UI:

text
Result not found or expired.

Missing Output File
http
404 Not Found


Recommended UI:

text
The requested output file is not available.

Map Layer Build Error
http
400 Bad Request


Recommended UI:

text
The analysis completed, but map layers could not be generated.

Current Test Coverage

Relevant tests:

text
tests/test_api_map_layers.py
tests/test_api_outputs.py
tests/test_output_storage.py


Covered cases:

successful map layer retrieval
Leaflet-ready GeoJSON response
unknown request returns 404 for map layers
output manifest retrieval
output file list retrieval
GeoJSON output download
save outputs endpoint
unknown request returns 404
missing file returns 404
real-estate report PDF download
wrong report filename returns 404
Current Contract Gaps

The current API is functional and tested, but these gaps remain:

Request record schema is not formally typed.
Map layer schema is not a Pydantic response model.
Output manifest file object fields should be formalized.
Document metadata should be exposed consistently in query/request outputs.
Documents endpoint is currently specific to real-estate ranking PDFs.
Output persistence behavior depends on service config.
HTTP error responses are mostly string-based detail, not fully structured.
Frontend should not assume every successful query has persisted outputs unless persist_outputs is enabled.
Compatibility Rule

Frontend should rely on:

request_id
production_response
layers
layer_count
files
filename
schema_version

and treat additional fields as optional.
