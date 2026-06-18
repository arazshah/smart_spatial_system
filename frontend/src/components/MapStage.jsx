import { useEffect, useMemo, useState } from "react";
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

  const sample = features.slice(0, 50).map((feature) => ({
    geometry: feature?.geometry || null,
    id: feature?.id ?? feature?.properties?.id ?? null,
    name: feature?.properties?.name ?? null,
  }));

  return `geojson:${features.length}:${stableStringify(sample)}`;
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
}) {
  const [fitTrigger, setFitTrigger] = useState(0);

  const rawLayers = Array.isArray(mapLayers?.layers)
    ? mapLayers.layers
    : EMPTY_LAYERS;

  const renderableLayers = useMemo(() => {
    if (!rawLayers.length) return [];

    const seenKeys = new Set();
    const seenGeoJson = new Set();
    const output = [];

    rawLayers.forEach((layer, index) => {
      const geojson = extractGeoJsonFromLayer(layer);

      if (!geojson) return;

      const layerKey = getLayerKey(layer, index);
      const signature = mapLayerGeoJsonSignature(geojson);

      if (signature && seenGeoJson.has(signature)) return;
      if (!signature && seenKeys.has(layerKey)) return;

      if (signature) seenGeoJson.add(signature);
      seenKeys.add(layerKey);

      output.push({
        key: layerKey,
        name: getLayerName(layer, index),
        geojson,
        color: COLORS[output.length % COLORS.length],
        source: layer?.source || "workspace",
      });
    });

    return output;
  }, [rawLayers]);

  const collections = useMemo(() => {
    return renderableLayers.map((item) => item.geojson);
  }, [renderableLayers]);

  const fitSignature = useMemo(() => {
    return renderableLayers
      .map((item) => `${item.key}:${item.geojson.features?.length || 0}`)
      .join("|");
  }, [renderableLayers]);

  const totalFeatures = useMemo(() => countFeatures(collections), [collections]);
  const geometries = useMemo(() => geometrySummary(collections), [collections]);

  const geometryText = Object.entries(geometries)
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${value}`)
    .join(" · ");

  const activeDataName = getUploadName(selectedUpload);
  const activeDataKind = getUploadKind(selectedUpload);

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
            value={renderableLayers.length}
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

            {renderableLayers.map((item, index) => (
              <LayersControl.Overlay
                key={item.key}
                checked
                name={item.name}
              >
                <GeoJSON
                  key={`${item.key}-${index}`}
                  data={item.geojson}
                  style={() => ({
                    color: item.color,
                    weight: 2,
                    opacity: 0.9,
                    fillColor: item.color,
                    fillOpacity: 0.22,
                  })}
                  pointToLayer={(feature, latlng) =>
                    L.circleMarker(latlng, {
                      radius: 7,
                      color: "#ffffff",
                      weight: 2,
                      fillColor: item.color,
                      fillOpacity: 0.88,
                      opacity: 1,
                    })
                  }
                  onEachFeature={(feature, layer) => {
                    layer.bindPopup(featurePopupHtml(feature), {
                      maxWidth: 360,
                      className: "map-popup-wrapper-pro",
                    });
                  }}
                />
              </LayersControl.Overlay>
            ))}
          </LayersControl>

          <ScaleControl position="bottomleft" />

          <FitToGeoJsonLayers
            collections={collections}
            signature={fitSignature}
            fitTrigger={fitTrigger}
          />
        </MapContainer>

        <div className="map-floating-toolbar-pro">
          <button
            type="button"
            onClick={() => setFitTrigger((value) => value + 1)}
            disabled={!renderableLayers.length}
            title="Zoom to visible layers"
          >
            ⌖
            <span>Zoom</span>
          </button>

          <button
            type="button"
            title="Use the layer control on the map to change basemap or layer visibility"
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

        {renderableLayers.length === 0 && !loading && (
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

        {renderableLayers.length > 0 && (
          <>
            <div className="map-layer-stack-pro leaflet-layer-stack">
              <div className="map-layer-stack-header">
                <span>Visible layers</span>
                <strong>{renderableLayers.length}</strong>
              </div>

              {renderableLayers.slice(0, 5).map((item) => (
                <div key={item.key} className="map-layer-chip-pro">
                  <span
                    className="layer-color-dot-pro"
                    style={{ background: item.color }}
                  />
                  <span title={item.name}>{item.name}</span>
                </div>
              ))}
            </div>

            <div className="map-bottom-status-pro">
              <span>CRS: EPSG:4326</span>
              <span>{totalFeatures} features</span>
              {geometryText ? <span>{geometryText}</span> : null}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
