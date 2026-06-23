# Phase 5 — Upload, Project, and Data Source API Contract

## Status

This document defines the current frontend-facing contract for project,
upload, and data-source registration flows.

These flows are required before meaningful query execution because users need
to create projects, attach data, preview data, and reference uploaded/external
data sources in `/query`.

---

## Main Concepts

### Project

A project is a workspace/container for uploads, data sources, requests, outputs,
and metadata.

### Upload

An upload is a stored file-based data source. It can be raster or vector.

Examples:

- raster JSON
- GeoTIFF
- GeoJSON
- zipped vector data
- KML/GPKG-like vector files

### External Data Source

An external source is fetched or registered through an API endpoint and then
normalized into the internal upload/data-source model.

Examples:

- PostGIS table
- WFS layer
- WMS layer
- URL-hosted GeoJSON
- CSV/table source

---

# Project API

## Create Project

```http
POST /projects
Content-Type: application/json

Request
json
{
  "name": "Vegetation Project",
  "description": "Test project",
  "metadata": {
    "owner": "tester"
  }
}

Fields
Field	Type	Required	Description
name	string	yes	Project display name.
description	string/null	no	Optional project description.
metadata	object	no	Optional project metadata.
Success Response
json
{
  "project_id": "prj-...",
  "name": "Vegetation Project",
  "description": "Test project",
  "metadata": {
    "owner": "tester"
  },
  "uploads": [],
  "requests": [],
  "outputs": []
}


Exact additional fields may vary depending on the project store implementation.

List Projects
http
GET /projects

Success Response
json
[
  {
    "project_id": "prj-...",
    "name": "Vegetation Project",
    "description": "Test project",
    "metadata": {},
    "uploads": [],
    "requests": [],
    "outputs": []
  }
]

Get Project
http
GET /projects/{project_id}

Success Response
json
{
  "project_id": "prj-...",
  "name": "Vegetation Project",
  "uploads": [
    "upl-..."
  ],
  "requests": [],
  "outputs": []
}

Error Response

Unknown project returns:

http
404 Not Found


with:

json
{
  "detail": "..."
}

List Project Data Sources
http
GET /projects/{project_id}/data-sources


Returns data sources/uploads attached to a project.

Success Response
json
[
  {
    "upload_id": "upl-...",
    "project_id": "prj-...",
    "filename": "sample.geojson",
    "kind": "vector"
  }
]

Upload API
Upload Raster
http
POST /uploads/raster
Content-Type: multipart/form-data

Multipart Fields
Field	Type	Required	Description
file	file	yes	Raster file.
kind	string	no	Defaults to raster.
project_id	string	no	Optional project id to attach the upload.
Example
bash
curl -X POST http://localhost:8000/uploads/raster \
  -F "file=@sample_raster.json;type=application/json" \
  -F "project_id=prj-..."

Success Response

Example:

json
{
  "upload_id": "upl-...",
  "filename": "sample_raster.json",
  "kind": "raster",
  "content_type": "application/json",
  "parsed_json_available": true,
  "project_id": "prj-..."
}


Important fields:

Field	Type	Description
upload_id	string	Internal upload reference. Used later as raster_ref.
filename	string	Original/stored filename.
kind	string	Usually raster.
content_type	string/null	Uploaded content type.
parsed_json_available	boolean	Whether JSON content was parsed and is directly usable.
project_id	string/null	Attached project id if provided.
Upload Vector
http
POST /uploads/vector
Content-Type: multipart/form-data

Multipart Fields
Field	Type	Required	Description
file	file	yes	Vector file.
kind	string	no	Defaults to vector.
project_id	string	no	Optional project id to attach the upload.
Example
bash
curl -X POST http://localhost:8000/uploads/vector \
  -F "file=@points.geojson;type=application/geo+json" \
  -F "project_id=prj-..."

Success Response
json
{
  "upload_id": "upl-...",
  "filename": "points.geojson",
  "kind": "vector",
  "content_type": "application/geo+json",
  "parsed_json_available": true,
  "project_id": "prj-..."
}

List Uploads
http
GET /uploads

Success Response
json
[
  {
    "upload_id": "upl-...",
    "filename": "sample_raster.json",
    "kind": "raster",
    "parsed_json_available": true
  }
]

Get Upload Metadata
http
GET /uploads/{upload_id}

Success Response
json
{
  "upload_id": "upl-...",
  "filename": "sample_raster.json",
  "kind": "raster",
  "metadata": {
    "crs": "EPSG:3857"
  }
}

Error Response

Unknown upload:

http
404 Not Found

json
{
  "detail": "..."
}

Download Upload File
http
GET /uploads/{upload_id}/file


Returns the uploaded file as a file response.

For JSON uploads, tests can read it as JSON.

Error Response

Unknown upload:

http
404 Not Found

Using Uploads in Query

Raster upload reference:

json
{
  "query": "Calculate NDVI and polygonize areas greater than 0.3",
  "inputs": {
    "raster_ref": "upl-..."
  },
  "band_map": {
    "red": 1,
    "nir": 2
  },
  "request_id": "req-upload-ref-001"
}


Vector upload reference:

json
{
  "query": "Analyze uploaded points",
  "inputs": {
    "vector_ref": "upl-..."
  },
  "request_id": "req-vector-ref-001"
}


If the referenced upload does not exist, /query usually returns:

http
200 OK


with:

json
{
  "status": "failed",
  "structured_error": {
    "code": "input.reference_not_found",
    "category": "validation_error",
    "source