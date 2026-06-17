import { useEffect, useMemo } from "react";
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
  const keys = Object.keys(props).slice(0, 10);

  if (keys.length === 0) {
    return "<div class='leaflet-popup-content-inner'>No properties</div>";
  }

  const rows = keys
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
    .join("");

  return `<div class="leaflet-popup-content-inner">${rows}</div>`;
}

function FitToGeoJsonLayers({ collections, signature }) {
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
          animate: false,
          maxZoom: 16,
        });
      }
    } catch {
      // Ignore fit-bounds errors.
    }
  }, [map, signature]);

  return null;
}

export default function MapStage({
  project,
  selectedUpload,
  mapLayers,
  loading,
}) {
  const rawLayers = Array.isArray(mapLayers?.layers)
    ? mapLayers.layers
    : EMPTY_LAYERS;

  const renderableLayers = useMemo(() => {
    if (!rawLayers.length) return [];

    return rawLayers
      .map((layer, index) => {
        const geojson = extractGeoJsonFromLayer(layer);

        if (!geojson) return null;

        return {
          key: getLayerKey(layer, index),
          name: getLayerName(layer, index),
          geojson,
          color: COLORS[index % COLORS.length],
        };
      })
      .filter(Boolean);
  }, [rawLayers]);

  const collections = useMemo(() => {
    return renderableLayers.map((item) => item.geojson);
  }, [renderableLayers]);

  const fitSignature = useMemo(() => {
    return renderableLayers
      .map((item) => `${item.key}:${item.geojson.features?.length || 0}`)
      .join("|");
  }, [renderableLayers]);

  return (
    <section className="map-stage leaflet-stage">
      <div className="map-toolbar">
        <div>
          <strong>{project?.name || "No project selected"}</strong>
          <span>
            {selectedUpload
              ? `Active data source: ${
                  selectedUpload.filename || selectedUpload.upload_id
                }`
              : project
              ? "Data will be selected intelligently from the project"
              : "Open Projects from the menu to start"}
          </span>
        </div>

        <div className="map-toolbar-actions">
          <span className="map-pill">{rawLayers.length} layers</span>
          <span className="map-pill">{renderableLayers.length} visible</span>
          {loading && <span className="map-pill active">Analyzing</span>}
        </div>
      </div>

      <div className="map-canvas leaflet-canvas">
        <MapContainer
          center={DEFAULT_CENTER}
          zoom={DEFAULT_ZOOM}
          minZoom={2}
          maxZoom={19}
          className="leaflet-map"
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
                      color: item.color,
                      weight: 2,
                      fillColor: item.color,
                      fillOpacity: 0.75,
                    })
                  }
                  onEachFeature={(feature, layer) => {
                    layer.bindPopup(featurePopupHtml(feature), {
                      maxWidth: 340,
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
          />
        </MapContainer>

        {renderableLayers.length === 0 && (
          <div className="map-empty-overlay">
            <div className="map-empty-card">
              <div className="map-icon">⌖</div>
              <h2>Spatial Map Workspace</h2>
              <p>
                نقشه آماده است. بعد از اجرای query، لایه‌های قابل نمایش اینجا
                نشان داده می‌شوند.
              </p>
            </div>
          </div>
        )}

        {renderableLayers.length > 0 && (
          <div className="map-layer-stack leaflet-layer-stack">
            {renderableLayers.slice(0, 5).map((item) => (
              <div key={item.key} className="map-layer-chip">
                <span
                  className="layer-color-dot"
                  style={{ background: item.color }}
                />
                {item.name}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
