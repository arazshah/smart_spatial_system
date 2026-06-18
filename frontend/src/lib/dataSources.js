function toArray(value) {
  if (!value) return [];
  return Array.isArray(value) ? value : [value];
}

function toObject(value) {
  return value && typeof value === "object" ? value : null;
}

function getGeoJsonCandidate(item) {
  return (
    item?.preview?.sample_geojson ||
    item?.preview?.geojson ||
    item?.geojson ||
    item?.data ||
    null
  );
}

function expandBounds(bounds, coord) {
  if (!Array.isArray(coord) || coord.length < 2) return bounds;
  const x = Number(coord[0]);
  const y = Number(coord[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return bounds;

  if (!bounds) return [x, y, x, y];

  return [
    Math.min(bounds[0], x),
    Math.min(bounds[1], y),
    Math.max(bounds[2], x),
    Math.max(bounds[3], y),
  ];
}

function walkGeometryBounds(geometry, bounds = null) {
  if (!geometry || typeof geometry !== "object") return bounds;

  const type = geometry.type;
  const coords = geometry.coordinates;

  if (type === "Point") {
    return expandBounds(bounds, coords);
  }

  if (type === "MultiPoint" || type === "LineString") {
    if (!Array.isArray(coords)) return bounds;
    return coords.reduce((acc, coord) => expandBounds(acc, coord), bounds);
  }

  if (type === "MultiLineString" || type === "Polygon") {
    if (!Array.isArray(coords)) return bounds;
    let next = bounds;
    for (const ring of coords) {
      if (!Array.isArray(ring)) continue;
      next = ring.reduce((acc, coord) => expandBounds(acc, coord), next);
    }
    return next;
  }

  if (type === "MultiPolygon") {
    if (!Array.isArray(coords)) return bounds;
    let next = bounds;
    for (const polygon of coords) {
      if (!Array.isArray(polygon)) continue;
      for (const ring of polygon) {
        if (!Array.isArray(ring)) continue;
        next = ring.reduce((acc, coord) => expandBounds(acc, coord), next);
      }
    }
    return next;
  }

  if (type === "GeometryCollection" && Array.isArray(geometry.geometries)) {
    return geometry.geometries.reduce(
      (acc, child) => walkGeometryBounds(child, acc),
      bounds,
    );
  }

  return bounds;
}

function inferBBoxFromGeoJson(geojson) {
  if (!geojson || typeof geojson !== "object") return null;

  if (Array.isArray(geojson.bbox) && geojson.bbox.length >= 4) {
    return geojson.bbox;
  }

  if (geojson.type === "FeatureCollection" && Array.isArray(geojson.features)) {
    let bounds = null;
    for (const feature of geojson.features) {
      bounds = walkGeometryBounds(feature?.geometry, bounds);
    }
    return bounds;
  }

  if (geojson.type === "Feature") {
    return walkGeometryBounds(geojson.geometry, null);
  }

  if (geojson.type && geojson.coordinates) {
    return walkGeometryBounds(geojson, null);
  }

  return null;
}

export function getDataSourceId(item) {
  return String(item?.upload_id || item?.id || item?.data_source_id || "");
}

export function getDataSourceName(item) {
  return (
    item?.display_name ||
    item?.name ||
    item?.filename ||
    item?.original_filename ||
    item?.title ||
    item?.upload_id ||
    "Unnamed data source"
  );
}

export function getDataSourceKind(item) {
  const raw = String(
    item?.kind ||
      item?.data_kind ||
      item?.type ||
      item?.data_type ||
      ""
  ).toLowerCase();

  if (["vector", "geojson", "shp", "shapefile"].includes(raw)) return "vector";
  if (["raster", "tif", "tiff", "geotiff"].includes(raw)) return "raster";
  if (["table", "csv", "xlsx", "excel"].includes(raw)) return "table";
  if (raw.includes("vector")) return "vector";
  if (raw.includes("raster")) return "raster";
  if (raw.includes("csv") || raw.includes("table")) return "table";
  if (raw.includes("database") || raw.includes("postgis")) return "database";
  if (raw.includes("wms") || raw.includes("wfs") || raw.includes("online")) return "online";
  if (raw.includes("api")) return "api";

  return raw || "unknown";
}

export function getDataSourceSourceType(item) {
  return String(item?.source_type || item?.source || "file").toLowerCase();
}

export function getDataSourceStatus(item) {
  const raw = String(item?.status || item?.state || "").toLowerCase();

  if (raw) return raw;
  if (item?.deleted) return "deleted";
  if (getDataSourceId(item)) return "ready";

  return "unknown";
}

export function getDataSourceSize(item) {
  const value =
    item?.size_bytes ??
    item?.size ??
    item?.file_size ??
    item?.metadata?.size_bytes ??
    item?.storage?.size_bytes ??
    0;

  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

export function getDataSourceTime(item) {
  return (
    item?.updated_at ||
    item?.created_at ||
    item?.stored_at ||
    item?.timestamp ||
    item?.metadata?.created_at ||
    null
  );
}

export function getDataSourceFeatureCount(item) {
  const value =
    item?.feature_count ??
    item?.preview?.feature_count ??
    item?.summary?.feature_count ??
    item?.metadata?.feature_count ??
    item?.metadata?.summary?.feature_count;

  if (value != null && Number.isFinite(Number(value))) {
    return Number(value);
  }

  const geojson = getGeoJsonCandidate(item);
  if (geojson?.type === "FeatureCollection" && Array.isArray(geojson.features)) {
    return geojson.features.length;
  }

  return null;
}

export function getDataSourceBBox(item) {
  const bbox =
    item?.bbox ??
    item?.preview?.bbox ??
    item?.summary?.bbox ??
    item?.metadata?.bbox ??
    item?.metadata?.summary?.bbox;

  if (Array.isArray(bbox)) return bbox;
  if (bbox && typeof bbox === "string") return bbox;

  const geojson = getGeoJsonCandidate(item);
  return inferBBoxFromGeoJson(geojson);
}

export function getDataSourceCrs(item) {
  const direct =
    item?.crs ||
    item?.preview?.crs ||
    item?.summary?.crs ||
    item?.metadata?.crs ||
    item?.metadata?.summary?.crs;

  if (direct) {
    if (typeof direct === "string") return direct;
    if (typeof direct === "object") {
      return direct?.name || direct?.properties?.name || JSON.stringify(direct);
    }
  }

  const geojson = getGeoJsonCandidate(item);
  const crs = geojson?.crs;
  if (crs?.name) return crs.name;
  if (crs?.properties?.name) return crs.properties.name;

  return "";
}

export function getDataSourceCrsLabel(item) {
  const crs = getDataSourceCrs(item);
  if (crs) return crs;

  const extension = String(item?.extension || "").toLowerCase();
  const mediaType = String(item?.media_type || item?.content_type || "").toLowerCase();
  const kind = String(getDataSourceKind(item) || "").toLowerCase();

  if (
    kind === "vector" &&
    (extension === ".geojson" ||
      mediaType.includes("geo+json") ||
      item?.preview?.geojson_type === "FeatureCollection")
  ) {
    return "Implicit GeoJSON (likely WGS84 / EPSG:4326)";
  }

  return "—";
}

export function getDataSourceGeometryType(item) {
  const direct =
    item?.geometry_type ||
    item?.geometry ||
    item?.geom_type ||
    item?.preview?.geometry_type ||
    item?.preview?.geometryType ||
    item?.summary?.geometry_type ||
    item?.metadata?.geometry_type ||
    item?.metadata?.summary?.geometry_type;

  if (direct) return String(direct);

  const geometryTypes = item?.preview?.geometry_types;
  if (Array.isArray(geometryTypes) && geometryTypes.length) {
    if (geometryTypes.length === 1) return String(geometryTypes[0]);
    return "Mixed";
  }

  const counts =
    item?.geometry_counts ||
    item?.summary?.geometry_counts ||
    item?.preview?.geometry_counts ||
    item?.metadata?.geometry_counts;

  if (counts && typeof counts === "object") {
    const keys = Object.keys(counts).filter(Boolean);
    if (keys.length === 1) return keys[0];
    if (keys.length > 1) return "Mixed";
  }

  const geojson = getGeoJsonCandidate(item);
  if (geojson?.type === "FeatureCollection" && Array.isArray(geojson.features)) {
    const set = new Set(
      geojson.features
        .map((feature) => feature?.geometry?.type)
        .filter(Boolean),
    );
    if (set.size === 1) return Array.from(set)[0];
    if (set.size > 1) return "Mixed";
  }

  if (geojson?.type === "Feature") {
    return geojson?.geometry?.type || "";
  }

  return "";
}

export function getDataSourcePropertyKeys(item) {
  const keys =
    item?.property_keys ||
    item?.preview?.property_keys ||
    item?.summary?.property_keys ||
    item?.metadata?.property_keys ||
    item?.metadata?.summary?.property_keys;

  if (Array.isArray(keys) && keys.length) return keys.filter(Boolean);

  const geojson = getGeoJsonCandidate(item);
  if (geojson?.type === "FeatureCollection" && Array.isArray(geojson.features)) {
    const result = new Set();
    for (const feature of geojson.features.slice(0, 100)) {
      const props = toObject(feature?.properties);
      if (!props) continue;
      for (const key of Object.keys(props)) {
        if (key) result.add(key);
      }
    }
    return Array.from(result);
  }

  if (Array.isArray(item?.preview?.keys)) {
    return item.preview.keys.filter(Boolean);
  }

  return [];
}

export function normalizeDataSource(item) {
  const normalized = {
    ...item,
    id: getDataSourceId(item),
    upload_id: getDataSourceId(item),
    name: getDataSourceName(item),
    display_name: item?.display_name || getDataSourceName(item),
    kind: getDataSourceKind(item),
    data_kind: getDataSourceKind(item),
    source_type: getDataSourceSourceType(item),
    status: getDataSourceStatus(item),
    size_bytes: getDataSourceSize(item),
    time: getDataSourceTime(item),
    geometry_type: getDataSourceGeometryType(item),
    feature_count: getDataSourceFeatureCount(item),
    crs: getDataSourceCrs(item),
    bbox: getDataSourceBBox(item),
    property_keys: getDataSourcePropertyKeys(item),
    tags: toArray(item?.tags).filter(Boolean),
    description: item?.description || "",
  };

  return normalized;
}

export function normalizeDataSourceList(items) {
  return toArray(items)
    .filter(Boolean)
    .map((item) => normalizeDataSource(item));
}

export function matchesDataSourceSearch(item, searchText) {
  const search = String(searchText || "").trim().toLowerCase();
  if (!search) return true;

  const haystack = [
    item?.name,
    item?.display_name,
    item?.filename,
    item?.original_filename,
    item?.kind,
    item?.data_kind,
    item?.source_type,
    item?.description,
    item?.geometry_type,
    item?.crs,
    ...(Array.isArray(item?.tags) ? item.tags : []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  return haystack.includes(search);
}
