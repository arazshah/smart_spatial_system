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

export function extractInlineGeoJsonLayers(response) {
  const layers = [];

  const push = (source, name, geojson, extra = {}) => {
    const collection = normalizeGeoJsonToFeatureCollection(geojson);
    if (!collection) return;

    layers.push({
      id:
        extra.id ||
        `${source}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name: name || extra.name || source || "GeoJSON layer",
      type: "vector",
      format: "geojson",
      visible: true,
      crs: extra.crs || "EPSG:4326",
      geojson: collection,
      source,
      summary: extra.summary || null,
      ...extra,
    });
  };

  if (!response || typeof response !== "object") {
    return {
      layers: [],
    };
  }

  if (Array.isArray(response.layers)) {
    response.layers.forEach((layer, index) => {
      push(
        "response.layers",
        layer?.name || layer?.id || `Layer ${index + 1}`,
        layer?.geojson || layer?.payload?.geojson || layer?.result?.geojson,
        {
          id: layer?.id || `inline-layer-${index + 1}`,
          summary: layer?.summary,
        },
      );
    });
  }

  if (Array.isArray(response.outputs?.vectors)) {
    response.outputs.vectors.forEach((vector, index) => {
      push(
        "response.outputs.vectors",
        vector?.name || vector?.id || `Vector ${index + 1}`,
        vector?.geojson || vector?.payload?.geojson || vector?.result?.geojson,
        {
          id: vector?.id || `inline-vector-${index + 1}`,
          summary: vector?.summary,
        },
      );
    });
  }

  if (response.result?.geojson) {
    push(
      "response.result.geojson",
      "Result GeoJSON",
      response.result.geojson,
      {
        id: "inline-result-geojson",
        summary: response.result?.summary,
      },
    );
  }

  if (response.geojson) {
    push("response.geojson", "GeoJSON", response.geojson, {
      id: "inline-geojson",
    });
  }

  return {
    layers,
  };
}

export function mergeMapLayerPayloads(primary, secondary) {
  const primaryLayers = Array.isArray(primary?.layers) ? primary.layers : [];
  const secondaryLayers = Array.isArray(secondary?.layers) ? secondary.layers : [];

  const seen = new Set();
  const merged = [];

  for (const layer of [...primaryLayers, ...secondaryLayers]) {
    if (!layer) continue;

    const key =
      layer.id ||
      `${layer.name || "layer"}:${layer.geojson?.features?.length || 0}`;

    if (seen.has(key)) continue;

    seen.add(key);
    merged.push(layer);
  }

  return {
    ...(secondary || {}),
    ...(primary || {}),
    layers: merged,
  };
}
