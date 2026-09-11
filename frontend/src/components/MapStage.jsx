import { useEffect, useMemo, useRef, useState } from "react";
import {
  GeoJSON,
  LayersControl,
  MapContainer,
  ScaleControl,
  TileLayer,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

const DEFAULT_CENTER = [32.4279, 53.6880];
const DEFAULT_ZOOM = 5;
const EMPTY_LAYERS = [];

const COLORS = [
  "#2563eb",
  "#16a34a",
  "#dc2626",
  "#9333ea",
  "#ea580c",
  "#0891b2",
  "#be123c",
];

function safeJsonParse(value) {
  if (typeof value !== "string") return value;

  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function isFeatureCollection(value) {
  return (
    value &&
    value.type === "FeatureCollection" &&
    Array.isArray(value.features)
  );
}

function isFeature(value) {
  return value && value.type === "Feature" && value.geometry;
}

function isGeometry(value) {
  return (
    value &&
    typeof value.type === "string" &&
    [
      "Point",
      "MultiPoint",
      "LineString",
      "MultiLineString",
      "Polygon",
      "MultiPolygon",
      "GeometryCollection",
    ].includes(value.type)
  );
}

function toFeatureCollection(value) {
  const parsed = safeJsonParse(value);

  if (!parsed) return null;

  if (isFeatureCollection(parsed)) {
    return parsed;
  }

  if (isFeature(parsed)) {
    return {
      type: "FeatureCollection",
      features: [parsed],
    };
  }

  if (isGeometry(parsed)) {
    return {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {},
          geometry: parsed,
        },
      ],
    };
  }

  if (Array.isArray(parsed?.features)) {
    return {
      type: "FeatureCollection",
      features: parsed.features,
    };
  }

  return null;
}

function extractGeoJsonFromLayer(layer) {
  if (!layer || typeof layer !== "object") return null;

  const candidates = [
    layer.geojson,
    layer.geo_json,
    layer.feature_collection,
    layer.featureCollection,
    layer.data,
    layer.payload?.geojson,
    layer.payload?.geo_json,
    layer.payload?.data,
    layer.result?.geojson,
    layer.result?.data,
  ];

  for (const candidate of candidates) {
    const fc = toFeatureCollection(candidate);
    if (fc) return fc;
  }

  return toFeatureCollection(layer);
}

function getLayerName(layer, index) {
  return layer?.name || layer?.title || layer?.id || `Layer ${index + 1}`;
}

function getLayerKey(layer, index) {
  return String(layer?.id || layer?.name || layer?.title || `layer-${index}`);
}

function getUploadName(upload) {
  if (!upload) return "No active data";
  return upload.filename || upload.name || upload.upload_id || "Active data";
}

function getUploadKind(upload) {
  const value = String(upload?.kind || upload?.type || "").toLowerCase();

  if (value.includes("vector")) return "Vector";
  if (value.includes("raster")) return "Raster";
  if (value.includes("table") || value.includes("csv")) return "Table";

  return upload ? "Data" : "None";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function featurePopupHtml(feature) {
  const props = feature?.properties || {};
  const keys = Object.keys(props).slice(0, 12);
  const geometryType = feature?.geometry?.type || "Unknown geometry";

  const rows = keys.length
    ? keys
        .map((key) => {
          const rawValue = props[key];
          const value =
            rawValue === null || rawValue === undefined
              ? ""
              : typeof rawValue === "object"
              ? JSON.stringify(rawValue)
              : String(rawValue);

          return `
            <div class="popup-row">
              <span>${escapeHtml(key)}</span>
              <strong>${escapeHtml(value)}</strong>
            </div>
          `;
        })
        .join("")
    : `<div class="popup-empty">No properties</div>`;

  return `
    <div class="map-popup-pro">
      <div class="map-popup-header">
        <div class="map-popup-icon">◈</div>
        <div>
          <strong>Feature</strong>
          <span>${escapeHtml(geometryType)}</span>
        </div>
      </div>
      <div class="map-popup-body">
        ${rows}
      </div>
    </div>
  `;
}

function FitToGeoJsonLayers({ collections, signature, fitTrigger }) {
  const map = useMap();

  useEffect(() => {
    if (!collections.length) return;

    try {
      const group = L.featureGroup();

      collections.forEach((collection) => {
        const geoJsonLayer = L.geoJSON(collection);
        geoJsonLayer.eachLayer((layer) => {
          group.addLayer(layer);
        });
      });

      if (group.getLayers().length === 0) return;

      const bounds = group.getBounds();

      if (bounds.isValid()) {
        map.fitBounds(bounds.pad(0.14), {
          animate: true,
          duration: 0.45,
          maxZoom: 16,
        });
      }
    } catch {
      // Ignore fit-bounds errors.
    }
  }, [map, signature, fitTrigger]);

  return null;
}

function LeafletSafePanel({ className, children, onClick }) {
  const ref = useRef(null);

  useEffect(() => {
    const element = ref.current;

    if (!element) return;

    L.DomEvent.disableClickPropagation(element);
    L.DomEvent.disableScrollPropagation(element);
  }, []);

  return (
    <div ref={ref} className={className} onClick={onClick}>
      {children}
    </div>
  );
}

function countFeatures(collections) {
  return collections.reduce((sum, collection) => {
    return sum + (Array.isArray(collection?.features) ? collection.features.length : 0);
  }, 0);
}

function geometrySummary(collections) {
  const counts = {};

  for (const collection of collections) {
    for (const feature of collection?.features || []) {
      const type = feature?.geometry?.type || "Unknown";
      counts[type] = (counts[type] || 0) + 1;
    }
  }

  return counts;
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

function mapLayerGeoJsonSignature(collection) {
  if (
    !collection ||
    typeof collection !== "object" ||
    collection.type !== "FeatureCollection" ||
    !Array.isArray(collection.features)
  ) {
    return null;
  }

  const features = collection.features;

  const geometries = features
    .map((feature) => stableStringify(feature?.geometry || null))
    .sort();

  return `geojson:${features.length}:${geometries.join("|")}`;
}



function normalizeFeatureId(value) {
  if (value === null || value === undefined || value === "") return "";
  return String(value);
}

function getFeatureIdentity(feature) {
  const props = feature?.properties || {};

  return normalizeFeatureId(
    feature?.id ??
      props.id ??
      props.property_id ??
      props.feature_id ??
      props.uid ??
      props.name ??
      props.title
  );
}

function featureMatchesSelection(feature, selectedFeatureId) {
  const wanted = normalizeFeatureId(selectedFeatureId);
  if (!wanted) return false;

  const current = getFeatureIdentity(feature);
  if (current && current === wanted) return true;

  const props = feature?.properties || {};
  return Object.values(props).some((value) => normalizeFeatureId(value) === wanted);
}

function selectedFeatureStyle(baseStyle) {
  return {
    color: "#f59e0b",
    weight: Math.max(Number(baseStyle?.weight || 2) + 2, 4),
    opacity: 1,
    fillColor: "#fbbf24",
    fillOpacity: Math.max(Number(baseStyle?.fillOpacity || 0.22), 0.58),
  };
}

function selectedPointStyle(baseStyle) {
  return {
    radius: Math.max(Number(baseStyle?.radius || 7) + 3, 10),
    color: "#ffffff",
    weight: 3,
    fillColor: "#f59e0b",
    fillOpacity: 0.92,
    opacity: 1,
  };
}

function featureCollectionForFeature(feature) {
  return {
    type: "FeatureCollection",
    features: [feature],
  };
}

function MapStatusChip({ icon, label, value, tone = "default" }) {
  return (
    <div className={`map-status-chip ${tone}`}>
      <span>{icon}</span>
      <div>
        <small>{label}</small>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

export default function MapStage({
  project,
  selectedUpload,
  mapLayers,
  loading,
  layerWorkspace = null,
  onLayerWorkspaceChange = null,
}) {
  const [internalFitRequest, setInternalFitRequest] = useState({
    key: null,
    featureId: null,
    trigger: 0,
  });
  const [internalHiddenLayerKeys, setInternalHiddenLayerKeys] = useState(() => new Set());
  const [internalRemovedLayerKeys, setInternalRemovedLayerKeys] = useState(() => new Set());
  const [internalLayerStyles, setInternalLayerStyles] = useState({});
  const [internalStyleLayerKey, setInternalStyleLayerKey] = useState(null);

  const fitRequest = layerWorkspace?.fitRequest || internalFitRequest;
  const hiddenLayerKeys = layerWorkspace?.hiddenLayerKeys || internalHiddenLayerKeys;
  const removedLayerKeys = layerWorkspace?.removedLayerKeys || internalRemovedLayerKeys;
  const layerStyles = layerWorkspace?.layerStyles || internalLayerStyles;
  const styleLayerKey =
    layerWorkspace && Object.prototype.hasOwnProperty.call(layerWorkspace, "styleLayerKey")
      ? layerWorkspace.styleLayerKey
      : internalStyleLayerKey;

  const selectedFeatureId =
    layerWorkspace?.selectedFeatureId || fitRequest?.featureId || null;

  const setFitRequest = (value) => {
    if (onLayerWorkspaceChange) {
      onLayerWorkspaceChange((previous) => ({
        ...previous,
        fitRequest: typeof value === "function" ? value(previous.fitRequest) : value,
      }));
      return;
    }
    setInternalFitRequest(value);
  };

  const setHiddenLayerKeys = (value) => {
    if (onLayerWorkspaceChange) {
      onLayerWorkspaceChange((previous) => ({
        ...previous,
        hiddenLayerKeys:
          typeof value === "function" ? value(previous.hiddenLayerKeys) : value,
      }));
      return;
    }
    setInternalHiddenLayerKeys(value);
  };

  const setRemovedLayerKeys = (value) => {
    if (onLayerWorkspaceChange) {
      onLayerWorkspaceChange((previous) => ({
        ...previous,
        removedLayerKeys:
          typeof value === "function" ? value(previous.removedLayerKeys) : value,
      }));
      return;
    }
    setInternalRemovedLayerKeys(value);
  };

  const setLayerStyles = (value) => {
    if (onLayerWorkspaceChange) {
      onLayerWorkspaceChange((previous) => ({
        ...previous,
        layerStyles:
          typeof value === "function" ? value(previous.layerStyles) : value,
      }));
      return;
    }
    setInternalLayerStyles(value);
  };

  const setStyleLayerKey = (value) => {
    if (onLayerWorkspaceChange) {
      onLayerWorkspaceChange((previous) => ({
        ...previous,
        styleLayerKey:
          typeof value === "function" ? value(previous.styleLayerKey) : value,
      }));
      return;
    }
    setInternalStyleLayerKey(value);
  };

  const rawLayers = Array.isArray(mapLayers?.layers)
    ? mapLayers.layers
    : EMPTY_LAYERS;

  const rawLayersSignature = useMemo(() => {
    return rawLayers
      .map((layer, index) => {
        const geojson = extractGeoJsonFromLayer(layer);
        const count = Array.isArray(geojson?.features) ? geojson.features.length : 0;
        return `${getLayerKey(layer, index)}:${count}`;
      })
      .join("|");
  }, [rawLayers]);

  const fitTriggerCounterRef = useRef(0);

  const [trackedLayersSignature, setTrackedLayersSignature] = useState(
    rawLayersSignature
  );
  if (trackedLayersSignature !== rawLayersSignature) {
    setTrackedLayersSignature(rawLayersSignature);
    setHiddenLayerKeys(new Set());
    setRemovedLayerKeys(new Set());
    setStyleLayerKey(null);
    setFitRequest({
      key: null,
      featureId: null,
      trigger: rawLayersSignature,
    });
  }

  const getDefaultStyle = (color) => ({
    color,
    fillColor: color,
    weight: 2,
    opacity: 0.9,
    fillOpacity: 0.22,
    radius: 7,
  });

  const normalizeStyle = (style, color) => {
    const fallback = getDefaultStyle(color);

    return {
      color: style?.color || fallback.color,
      fillColor: style?.fillColor || style?.color || fallback.fillColor,
      weight: Number(style?.weight ?? fallback.weight),
      opacity: Number(style?.opacity ?? fallback.opacity),
      fillOpacity: Number(style?.fillOpacity ?? fallback.fillOpacity),
      radius: Number(style?.radius ?? fallback.radius),
    };
  };

  const allRenderableLayers = useMemo(() => {
    if (!rawLayers.length) return [];

    const seenKeys = new Set();
    const seenGeoJson = new Set();
    const output = [];

    rawLayers.forEach((layer, index) => {
      const geojson = extractGeoJsonFromLayer(layer);

      if (!geojson) return;

      const layerKey = getLayerKey(layer, index);
      const signature = mapLayerGeoJsonSignature(geojson);

      if (removedLayerKeys.has(layerKey)) return;
      if (signature && seenGeoJson.has(signature)) return;
      if (!signature && seenKeys.has(layerKey)) return;

      if (signature) seenGeoJson.add(signature);
      seenKeys.add(layerKey);

      const baseColor = COLORS[output.length % COLORS.length];

      output.push({
        key: layerKey,
        name: getLayerName(layer, index),
        geojson,
        color: baseColor,
        style: normalizeStyle(layerStyles[layerKey], baseColor),
        visible: !hiddenLayerKeys.has(layerKey),
        source: layer?.source || "workspace",
        summary: layer?.summary || null,
      });
    });

    return output;
  }, [rawLayers, hiddenLayerKeys, removedLayerKeys, layerStyles]);

  const visibleRenderableLayers = useMemo(() => {
    return allRenderableLayers.filter((layer) => layer.visible);
  }, [allRenderableLayers]);

  const collections = useMemo(() => {
    return visibleRenderableLayers.map((item) => item.geojson);
  }, [visibleRenderableLayers]);

  const visibleSignature = useMemo(() => {
    return visibleRenderableLayers
      .map((item) => `${item.key}:${item.geojson.features?.length || 0}`)
      .join("|");
  }, [visibleRenderableLayers]);

  const fitCollections = useMemo(() => {
    const requestedFeatureId = fitRequest?.featureId || selectedFeatureId;

    if (requestedFeatureId) {
      const candidateLayers = fitRequest?.key
        ? allRenderableLayers.filter((layer) => layer.key === fitRequest.key)
        : allRenderableLayers;

      for (const layer of candidateLayers) {
        const feature = (layer.geojson?.features || []).find((item) =>
          featureMatchesSelection(item, requestedFeatureId)
        );

        if (feature) {
          return [featureCollectionForFeature(feature)];
        }
      }
    }

    if (!fitRequest.key) return collections;

    const target = allRenderableLayers.find((layer) => layer.key === fitRequest.key);

    return target?.geojson ? [target.geojson] : collections;
  }, [fitRequest, selectedFeatureId, allRenderableLayers, collections]);

  const fitSignature = useMemo(() => {
    return `${visibleSignature}|fit:${fitRequest.key || "all"}:${fitRequest.featureId || "none"}:${fitRequest.trigger}`;
  }, [visibleSignature, fitRequest]);

  const totalFeatures = useMemo(() => countFeatures(collections), [collections]);
  const geometries = useMemo(() => geometrySummary(collections), [collections]);

  const geometryText = Object.entries(geometries)
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${value}`)
    .join(" · ");

  const activeDataName = getUploadName(selectedUpload);
  const activeDataKind = getUploadKind(selectedUpload);

  const styleTargetLayer = useMemo(() => {
    if (!styleLayerKey) return null;
    return allRenderableLayers.find((layer) => layer.key === styleLayerKey) || null;
  }, [styleLayerKey, allRenderableLayers]);

  const updateLayerStyle = (layerKey, patch) => {
    setLayerStyles((previous) => {
      const layer = allRenderableLayers.find((item) => item.key === layerKey);
      const baseColor = layer?.color || COLORS[0];
      const current = normalizeStyle(previous[layerKey], baseColor);

      return {
        ...previous,
        [layerKey]: {
          ...current,
          ...patch,
        },
      };
    });
  };

  const resetLayerStyle = (layerKey) => {
    setLayerStyles((previous) => {
      const next = { ...previous };
      delete next[layerKey];
      return next;
    });
  };

  const toggleLayerVisibility = (layerKey) => {
    setHiddenLayerKeys((previous) => {
      const next = new Set(previous);

      if (next.has(layerKey)) {
        next.delete(layerKey);
      } else {
        next.add(layerKey);
      }

      return next;
    });
  };

  const removeLayerFromMap = (layerKey) => {
    setRemovedLayerKeys((previous) => {
      const next = new Set(previous);
      next.add(layerKey);
      return next;
    });

    setStyleLayerKey((current) => (current === layerKey ? null : current));
  };

  const zoomToAllVisibleLayers = () => {
    fitTriggerCounterRef.current += 1;
    setFitRequest({
      key: null,
      featureId: null,
      trigger: fitTriggerCounterRef.current,
    });
  };

  const zoomToLayer = (layerKey) => {
    fitTriggerCounterRef.current += 1;
    setFitRequest({
      key: layerKey,
      featureId: null,
      trigger: fitTriggerCounterRef.current,
    });
  };

  return (
    <section className="map-stage map-stage-pro leaflet-stage">
      <header className="map-stage-header-pro">
        <div className="map-stage-title-block">
          <p>Map Workspace</p>
          <h2>نقشه تحلیلی</h2>
        </div>

        <div className="map-stage-status-row">
          <MapStatusChip
            icon="▣"
            label="Project"
            value={project?.name || "No project"}
          />

          <MapStatusChip
            icon="◈"
            label={activeDataKind}
            value={activeDataName}
            tone={selectedUpload ? "green" : "muted"}
          />

          <MapStatusChip
            icon="▧"
            label="Layers"
            value={visibleRenderableLayers.length}
            tone="blue"
          />

          <MapStatusChip
            icon="•"
            label="Features"
            value={totalFeatures}
            tone="purple"
          />
        </div>
      </header>

      <div className="map-canvas map-canvas-pro leaflet-canvas">
        <MapContainer
          center={DEFAULT_CENTER}
          zoom={DEFAULT_ZOOM}
          minZoom={2}
          maxZoom={19}
          className="leaflet-map leaflet-map-pro"
          zoomControl={true}
          attributionControl={true}
          preferCanvas={true}
        >
          <LayersControl position="topright">
            <LayersControl.BaseLayer checked name="Light Map">
              <TileLayer
                attribution='&copy; OpenStreetMap &copy; CARTO'
                url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
              />
            </LayersControl.BaseLayer>

            <LayersControl.BaseLayer name="OpenStreetMap">
              <TileLayer
                attribution='&copy; OpenStreetMap'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
            </LayersControl.BaseLayer>

            <LayersControl.BaseLayer name="Dark Map">
              <TileLayer
                attribution='&copy; OpenStreetMap &copy; CARTO'
                url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              />
            </LayersControl.BaseLayer>

            {visibleRenderableLayers.map((item, index) => (
              <LayersControl.Overlay
                key={item.key}
                checked
                name={item.name}
              >
                <GeoJSON
                  key={`${item.key}-${index}-${selectedFeatureId || "none"}-${JSON.stringify(item.style)}`}
                  data={item.geojson}
                  style={(feature) => {
                    const baseStyle = {
                      color: item.style.color,
                      weight: item.style.weight,
                      opacity: item.style.opacity,
                      fillColor: item.style.fillColor,
                      fillOpacity: item.style.fillOpacity,
                    };

                    return featureMatchesSelection(feature, selectedFeatureId)
                      ? selectedFeatureStyle(baseStyle)
                      : baseStyle;
                  }}
                  pointToLayer={(feature, latlng) => {
                    const basePointStyle = {
                      radius: item.style.radius,
                      color: "#ffffff",
                      weight: 2,
                      fillColor: item.style.fillColor || item.style.color,
                      fillOpacity: Math.max(item.style.fillOpacity, 0.35),
                      opacity: 1,
                    };

                    return L.circleMarker(
                      latlng,
                      featureMatchesSelection(feature, selectedFeatureId)
                        ? selectedPointStyle(basePointStyle)
                        : basePointStyle
                    );
                  }}
                  onEachFeature={(feature, layer) => {
                    layer.bindPopup(featurePopupHtml(feature), {
                      maxWidth: 360,
                      className: "map-popup-wrapper-pro",
                    });

                    layer.on("click", () => {
                      const featureId = getFeatureIdentity(feature);

                      if (!featureId || !onLayerWorkspaceChange) return;

                      onLayerWorkspaceChange((previous) => ({
                        ...previous,
                        selectedLayerKey: item.key,
                        selectedFeatureId: featureId,
                        selectedFeatureProperties: feature?.properties || {},
                        selectedTableId: previous?.selectedTableId || null,
                        fitRequest: {
                          key: item.key,
                          featureId,
                          trigger: Date.now(),
                        },
                      }));
                    });
                  }}
                />
              </LayersControl.Overlay>
            ))}
          </LayersControl>

          <ScaleControl position="bottomleft" />

          <FitToGeoJsonLayers
            collections={fitCollections}
            signature={fitSignature}
            fitTrigger={fitRequest.trigger}
          />
        </MapContainer>

        <div className="map-floating-toolbar-pro">
          <button
            type="button"
            onClick={zoomToAllVisibleLayers}
            disabled={!visibleRenderableLayers.length}
            title="Zoom to visible layers"
          >
            ⌖
            <span>Zoom</span>
          </button>

          <button
            type="button"
            title="Use the layer panel or Leaflet layer control to change visibility"
          >
            ▧
            <span>Layers</span>
          </button>

          <button
            type="button"
            title="Basemap switcher is available in Leaflet layer control"
          >
            ◉
            <span>Basemap</span>
          </button>
        </div>

        {loading && (
          <div className="map-analyzing-overlay-pro">
            <div className="map-analyzing-card-pro">
              <div className="map-loader-ring" />
              <div>
                <strong>Analyzing spatial request</strong>
                <p>در حال پردازش query و آماده‌سازی خروجی مکانی...</p>
              </div>
            </div>
          </div>
        )}

        {allRenderableLayers.length === 0 && !loading && (
          <div className="map-empty-overlay-pro">
            <div className="map-empty-card-pro">
              <div className="map-empty-icon-pro">⌖</div>
              <h2>نقشه آماده است</h2>
              <p>
                پس از انتخاب داده و اجرای query، لایه‌های قابل نمایش اینجا ظاهر می‌شوند.
              </p>
            </div>
          </div>
        )}

        {allRenderableLayers.length > 0 && (
          <>
            <LeafletSafePanel className="map-layer-stack-pro leaflet-layer-stack">
              <div className="map-layer-stack-header">
                <span>Layers</span>
                <strong>
                  {visibleRenderableLayers.length}/{allRenderableLayers.length}
                </strong>
              </div>

              <div className="map-layer-stack-list-pro">
                {allRenderableLayers.slice(0, 8).map((item) => (
                  <div
                    key={item.key}
                    className={`map-layer-chip-pro map-layer-chip-advanced-pro ${
                      item.visible ? "" : "is-hidden"
                    }`}
                  >
                    <div className="map-layer-chip-main-pro">
                      <span
                        className="layer-color-dot-pro"
                        style={{ background: item.style.fillColor || item.style.color }}
                      />
                      <div className="map-layer-chip-text-pro">
                        <strong title={item.name}>{item.name}</strong>
                        <small>
                          {item.visible ? "Visible" : "Hidden"} ·{" "}
                          {item.geojson.features?.length || 0} features
                        </small>
                      </div>
                    </div>

                    <div className="map-layer-chip-actions-pro">
                      <button
                        type="button"
                        onClick={() => zoomToLayer(item.key)}
                        title="Zoom to this layer"
                      >
                        ⌖
                      </button>

                      <button
                        type="button"
                        onClick={() => setStyleLayerKey(item.key)}
                        title="Style layer"
                      >
                        🎨
                      </button>

                      <button
                        type="button"
                        onClick={() => toggleLayerVisibility(item.key)}
                        title={item.visible ? "Hide layer" : "Show layer"}
                      >
                        {item.visible ? "👁" : "⊘"}
                      </button>

                      <button
                        type="button"
                        onClick={() => removeLayerFromMap(item.key)}
                        title="Remove from map workspace"
                      >
                        ×
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </LeafletSafePanel>

            <div className="map-bottom-status-pro">
              <span>CRS: EPSG:4326</span>
              <span>{totalFeatures} visible features</span>
              {geometryText ? <span>{geometryText}</span> : null}
            </div>
          </>
        )}

        {styleTargetLayer && (
          <div
            className="map-style-modal-backdrop-pro"
            onClick={() => setStyleLayerKey(null)}
          >
            <div
              className="map-style-modal-pro"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="map-style-modal-header-pro">
                <div>
                  <span>Layer Style</span>
                  <strong>{styleTargetLayer.name}</strong>
                </div>

                <button type="button" onClick={() => setStyleLayerKey(null)}>
                  ×
                </button>
              </div>

              <div className="map-style-grid-pro">
                <label>
                  <span>Stroke color</span>
                  <input
                    type="color"
                    value={styleTargetLayer.style.color}
                    onChange={(event) =>
                      updateLayerStyle(styleTargetLayer.key, {
                        color: event.target.value,
                      })
                    }
                  />
                </label>

                <label>
                  <span>Fill color</span>
                  <input
                    type="color"
                    value={styleTargetLayer.style.fillColor}
                    onChange={(event) =>
                      updateLayerStyle(styleTargetLayer.key, {
                        fillColor: event.target.value,
                      })
                    }
                  />
                </label>

                <label>
                  <span>Stroke weight</span>
                  <input
                    type="range"
                    min="1"
                    max="8"
                    step="1"
                    value={styleTargetLayer.style.weight}
                    onChange={(event) =>
                      updateLayerStyle(styleTargetLayer.key, {
                        weight: Number(event.target.value),
                      })
                    }
                  />
                  <strong>{styleTargetLayer.style.weight}</strong>
                </label>

                <label>
                  <span>Opacity</span>
                  <input
                    type="range"
                    min="0.1"
                    max="1"
                    step="0.05"
                    value={styleTargetLayer.style.opacity}
                    onChange={(event) =>
                      updateLayerStyle(styleTargetLayer.key, {
                        opacity: Number(event.target.value),
                      })
                    }
                  />
                  <strong>{styleTargetLayer.style.opacity.toFixed(2)}</strong>
                </label>

                <label>
                  <span>Fill opacity</span>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={styleTargetLayer.style.fillOpacity}
                    onChange={(event) =>
                      updateLayerStyle(styleTargetLayer.key, {
                        fillOpacity: Number(event.target.value),
                      })
                    }
                  />
                  <strong>{styleTargetLayer.style.fillOpacity.toFixed(2)}</strong>
                </label>

                <label>
                  <span>Point radius</span>
                  <input
                    type="range"
                    min="3"
                    max="18"
                    step="1"
                    value={styleTargetLayer.style.radius}
                    onChange={(event) =>
                      updateLayerStyle(styleTargetLayer.key, {
                        radius: Number(event.target.value),
                      })
                    }
                  />
                  <strong>{styleTargetLayer.style.radius}</strong>
                </label>
              </div>

              <div className="map-style-preview-pro">
                <span
                  style={{
                    borderColor: styleTargetLayer.style.color,
                    background: styleTargetLayer.style.fillColor,
                    opacity: styleTargetLayer.style.opacity,
                  }}
                />
                <div>
                  <strong>Preview</strong>
                  <small>
                    {styleTargetLayer.geojson.features?.length || 0} features
                  </small>
                </div>
              </div>

              <div className="map-style-modal-actions-pro">
                <button
                  type="button"
                  onClick={() => resetLayerStyle(styleTargetLayer.key)}
                >
                  Reset
                </button>

                <button
                  type="button"
                  className="primary"
                  onClick={() => setStyleLayerKey(null)}
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
