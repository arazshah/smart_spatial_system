export function isFeatureCollection(value) {
  return (
    value &&
    typeof value === "object" &&
    value.type === "FeatureCollection" &&
    Array.isArray(value.features)
  );
}

export function normalizeGeoJsonToFeatureCollection(value) {
  if (!value || typeof value !== "object") return null;

  if (isFeatureCollection(value)) return value;

  if (value.type === "Feature") {
    return {
      type: "FeatureCollection",
      features: [value],
    };
  }

  const geometryTypes = new Set([
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
  ]);

  if (geometryTypes.has(value.type)) {
    return {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {},
          geometry: value,
        },
      ],
    };
  }

  return null;
}

function stableStringify(value) {
  if (value === null || value === undefined) return String(value);

  if (typeof value !== "object") {
    return JSON.stringify(value);
  }

  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }

  const keys = Object.keys(value).sort();

  return `{${keys
    .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
    .join(",")}}`;
}

function getGeoJsonCandidate(value) {
  if (!value || typeof value !== "object") return null;

  const direct = normalizeGeoJsonToFeatureCollection(value);
  if (direct) return direct;

  const candidates = [
    value.geojson,
    value.geo_json,
    value.feature_collection,
    value.featureCollection,
    value.data,
    value.payload?.geojson,
    value.payload?.geo_json,
    value.payload?.feature_collection,
    value.payload?.featureCollection,
    value.payload?.data,
    value.result?.geojson,
    value.result?.geo_json,
    value.result?.feature_collection,
    value.result?.featureCollection,
    value.result?.data,
  ];

  for (const candidate of candidates) {
    const collection = normalizeGeoJsonToFeatureCollection(candidate);
    if (collection) return collection;
  }

  return null;
}

function geoJsonSignature(value) {
  const collection = getGeoJsonCandidate(value);

  if (!collection?.features?.length) return null;

  const features = collection.features;

  /*
    We use geometry + common stable identifiers for duplicate detection.
    This prevents the same GeoJSON from being counted twice when backend
    returns it in response.layers, outputs.vectors and result.geojson.
  */
  const sample = features.slice(0, 50).map((feature) => ({
    geometry: feature?.geometry || null,
    id: feature?.id ?? feature?.properties?.id ?? null,
    name: feature?.properties?.name ?? null,
  }));

  return `geojson:${features.length}:${stableStringify(sample)}`;
}

function dedupeLayers(layers) {
  const seenIds = new Set();
  const seenGeoJson = new Set();
  const output = [];

  for (const layer of layers || []) {
    if (!layer) continue;

    const idKey = layer.id ? String(layer.id) : null;
    const signature = layer._geojsonSignature || geoJsonSignature(layer);

    if (idKey && seenIds.has(idKey)) continue;
    if (signature && seenGeoJson.has(signature)) continue;

    if (idKey) seenIds.add(idKey);
    if (signature) seenGeoJson.add(signature);

    const collection = getGeoJsonCandidate(layer);

    output.push({
      ...layer,
      geojson: collection || layer.geojson,
      _geojsonSignature: signature,
    });
  }

  return output;
}

export function extractInlineGeoJsonLayers(response) {
  const layers = [];
  const seenIds = new Set();
  const seenGeoJson = new Set();

  const push = (source, name, rawLayerOrGeojson, extra = {}) => {
    const collection = getGeoJsonCandidate(rawLayerOrGeojson);
    if (!collection) return;

    const signature = geoJsonSignature(collection);
    const id = extra.id || `${source}-${layers.length + 1}`;

    if (id && seenIds.has(String(id))) return;
    if (signature && seenGeoJson.has(signature)) return;

    if (id) seenIds.add(String(id));
    if (signature) seenGeoJson.add(signature);

    layers.push({
      id,
      name: name || extra.name || source || "GeoJSON layer",
      type: extra.type || "vector",
      format: extra.format || "geojson",
      visible: extra.visible !== false,
      crs: extra.crs || "EPSG:4326",
      geojson: collection,
      source,
      summary: extra.summary || null,
      _geojsonSignature: signature,
      ...extra,
    });
  };

  if (!response || typeof response !== "object") {
    return {
      layers: [],
    };
  }

  /*
    Priority:
    1. response.layers is canonical.
    2. response.outputs.vectors is fallback/additional.
    3. response.result.geojson is fallback.
    4. response.geojson is fallback.

    All paths are deduped by actual GeoJSON content.
  */

  if (Array.isArray(response.layers)) {
    response.layers.forEach((layer, index) => {
      push(
        "response.layers",
        layer?.name || layer?.id || `Layer ${index + 1}`,
        layer,
        {
          id: layer?.id || `inline-layer-${index + 1}`,
          summary: layer?.summary,
          visible: layer?.visible !== false,
          type: layer?.type || "vector",
          format: layer?.format || "geojson",
          crs: layer?.crs || "EPSG:4326",
        },
      );
    });
  }

  if (Array.isArray(response.outputs?.vectors)) {
    response.outputs.vectors.forEach((vector, index) => {
      push(
        "response.outputs.vectors",
        vector?.name || vector?.id || `Vector ${index + 1}`,
        vector,
        {
          id: vector?.id || `inline-vector-${index + 1}`,
          summary: vector?.summary,
          visible: vector?.visible !== false,
          type: "vector",
          format: vector?.format || "geojson",
          crs: vector?.crs || "EPSG:4326",
        },
      );
    });
  }

  if (response.result?.geojson) {
    push(
      "response.result.geojson",
      "Result GeoJSON",
      response.result,
      {
        id: "inline-result-geojson",
        summary: response.result?.summary,
      },
    );
  }

  if (response.geojson) {
    push("response.geojson", "GeoJSON", response, {
      id: "inline-geojson",
    });
  }

  return {
    layers: dedupeLayers(layers),
  };
}

export function mergeMapLayerPayloads(primary, secondary) {
  const primaryLayers = Array.isArray(primary?.layers) ? primary.layers : [];
  const secondaryLayers = Array.isArray(secondary?.layers) ? secondary.layers : [];

  const merged = dedupeLayers([...primaryLayers, ...secondaryLayers]);

  return {
    ...(secondary || {}),
    ...(primary || {}),
    layers: merged,
  };
}
