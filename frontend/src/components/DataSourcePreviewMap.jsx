import { useEffect, useMemo } from "react";
import {
  GeoJSON,
  MapContainer,
  ScaleControl,
  TileLayer,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

const DEFAULT_CENTER = [35.6892, 51.3890];
const DEFAULT_ZOOM = 10;

function safeJsonParse(value) {
  if (typeof value !== "string") return value;

  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
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

function isFeature(value) {
  return value && value.type === "Feature" && value.geometry;
}

function isFeatureCollection(value) {
  return (
    value &&
    value.type === "FeatureCollection" &&
    Array.isArray(value.features)
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

function collectGeoJsonCandidates(payload) {
  return [
    payload,
    payload?.preview,
    payload?.geojson,
    payload?.geo_json,
    payload?.feature_collection,
    payload?.featureCollection,
    payload?.data,
    payload?.payload,
    payload?.payload?.geojson,
    payload?.payload?.geo_json,
    payload?.payload?.data,
    payload?.preview?.geojson,
    payload?.preview?.geo_json,
    payload?.preview?.feature_collection,
    payload?.preview?.featureCollection,
    payload?.preview?.data,
    payload?.preview?.sample,
    payload?.preview?.sample_geojson,
    payload?.preview?.sample_features,
    payload?.summary?.geojson,
    payload?.summary?.data,
  ];
}

function extractPreviewFeatureCollection(payload) {
  const candidates = collectGeoJsonCandidates(payload);

  for (const candidate of candidates) {
    const fc = toFeatureCollection(candidate);
    if (fc) return fc;

    if (Array.isArray(candidate)) {
      const features = candidate.filter(isFeature);
      if (features.length) {
        return {
          type: "FeatureCollection",
          features,
        };
      }
    }
  }

  return null;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function popupHtml(feature) {
  const props = feature?.properties || {};
  const keys = Object.keys(props).slice(0, 10);
  const geometryType = feature?.geometry?.type || "Geometry";

  const rows = keys.length
    ? keys
        .map((key) => {
          const raw = props[key];
          const value =
            raw === null || raw === undefined
              ? ""
              : typeof raw === "object"
              ? JSON.stringify(raw)
              : String(raw);

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
          <strong>Preview feature</strong>
          <span>${escapeHtml(geometryType)}</span>
        </div>
      </div>
      <div class="map-popup-body">
        ${rows}
      </div>
    </div>
  `;
}

function FitPreviewBounds({ collection, signature }) {
  const map = useMap();

  useEffect(() => {
    setTimeout(() => {
      map.invalidateSize();

      if (!collection?.features?.length) return;

      try {
        const layer = L.geoJSON(collection);
        const bounds = layer.getBounds();

        if (bounds.isValid()) {
          map.fitBounds(bounds.pad(0.18), {
            animate: true,
            duration: 0.35,
            maxZoom: 17,
          });
        }
      } catch {
        // Ignore preview fit errors.
      }
    }, 120);
  }, [map, collection, signature]);

  return null;
}

function geometryCounts(collection) {
  const counts = {};

  for (const feature of collection?.features || []) {
    const type = feature?.geometry?.type || "Unknown";
    counts[type] = (counts[type] || 0) + 1;
  }

  return counts;
}

export default function DataSourcePreviewMap({ payload }) {
  const collection = useMemo(() => {
    return extractPreviewFeatureCollection(payload);
  }, [payload]);

  const signature = useMemo(() => {
    return JSON.stringify({
      count: collection?.features?.length || 0,
      first: collection?.features?.[0]?.geometry?.type || "",
    });
  }, [collection]);

  const counts = useMemo(() => geometryCounts(collection), [collection]);

  if (!collection?.features?.length) {
    return (
      <div className="preview-map-empty">
        <strong>Map preview unavailable</strong>
        <span>در preview فعلی GeoJSON قابل نمایش پیدا نشد.</span>
      </div>
    );
  }

  return (
    <div className="preview-map-card">
      <div className="preview-map-header">
        <div>
          <span>Map Preview</span>
          <strong>{collection.features.length} feature</strong>
        </div>

        <div className="preview-map-geometry-list">
          {Object.entries(counts).map(([key, value]) => (
            <span key={key}>
              {key}: {value}
            </span>
          ))}
        </div>
      </div>

      <div className="preview-map-shell">
        <MapContainer
          center={DEFAULT_CENTER}
          zoom={DEFAULT_ZOOM}
          scrollWheelZoom
          className="preview-leaflet-map"
        >
          <TileLayer
            attribution='&copy; OpenStreetMap contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          <GeoJSON
            key={signature}
            data={collection}
            style={() => ({
              color: "#2563eb",
              weight: 2,
              opacity: 0.95,
              fillColor: "#38bdf8",
              fillOpacity: 0.28,
            })}
            pointToLayer={(feature, latlng) =>
              L.circleMarker(latlng, {
                radius: 7,
                color: "#1d4ed8",
                weight: 2,
                fillColor: "#38bdf8",
                fillOpacity: 0.85,
              })
            }
            onEachFeature={(feature, layer) => {
              layer.bindPopup(popupHtml(feature), {
                maxWidth: 320,
              });
            }}
          />

          <ScaleControl position="bottomleft" />
          <FitPreviewBounds collection={collection} signature={signature} />
        </MapContainer>
      </div>
    </div>
  );
}
