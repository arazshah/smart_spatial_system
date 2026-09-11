import { useEffect, useMemo, useState } from "react";
import { GeoJSON, MapContainer, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";
import { getMapLayers } from "../api/client";

function FitToLayers({ layers }) {
  const map = useMap();

  useEffect(() => {
    if (!layers.length) return;

    const group = L.featureGroup();

    for (const layer of layers) {
      try {
        const geoJsonLayer = L.geoJSON(layer.geojson);
        group.addLayer(geoJsonLayer);
      } catch {
        // ignore invalid layer
      }
    }

    const bounds = group.getBounds();

    if (bounds.isValid()) {
      map.fitBounds(bounds, {
        padding: [30, 30],
        maxZoom: 18,
      });
    }
  }, [layers, map]);

  return null;
}

function layerStyle(feature) {
  const ndvi = feature?.properties?.ndvi;

  if (typeof ndvi === "number") {
    if (ndvi >= 0.5) {
      return {
        color: "#15803d",
        weight: 2,
        fillColor: "#22c55e",
        fillOpacity: 0.45,
      };
    }

    return {
      color: "#65a30d",
      weight: 2,
      fillColor: "#a3e635",
      fillOpacity: 0.4,
    };
  }

  return {
    color: "#16a34a",
    weight: 2,
    fillColor: "#22c55e",
    fillOpacity: 0.35,
  };
}

function onEachFeature(feature, layer) {
  const props = feature?.properties || {};

  const html = `
    <div style="direction:ltr;text-align:left;font-size:12px">
      <strong>Vegetation Pixel</strong><br/>
      row: ${props.row ?? "-"}<br/>
      col: ${props.col ?? "-"}<br/>
      ndvi: ${props.ndvi ?? "-"}<br/>
      threshold: ${props.threshold ?? "-"}<br/>
      source: ${props.source ?? "-"}
    </div>
  `;

  layer.bindPopup(html);
}

export default function MapViewer({ response }) {
  const [mapLayersPayload, setMapLayersPayload] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const requestId = response?.request_id;

  const [trackedRequestId, setTrackedRequestId] = useState(requestId);
  if (trackedRequestId !== requestId) {
    setTrackedRequestId(requestId);
    setMapLayersPayload(null);
    setError("");
    if (requestId) setLoading(true);
  }

  useEffect(() => {
    if (!requestId) return;

    getMapLayers(requestId)
      .then(setMapLayersPayload)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [requestId]);

  const usableLayers = useMemo(() => {
    const layers = mapLayersPayload?.layers || [];

    return layers.filter((layer) => {
      const crs = String(
        layer.crs ||
          layer.geojson?.crs?.properties?.name ||
          "EPSG:4326",
      ).toUpperCase();

      return layer?.geojson?.type === "FeatureCollection" && crs.includes("4326");
    });
  }, [mapLayersPayload]);

  return (
    <section className="card">
      <div className="card-header">
        <h2>نمایش مکانی</h2>
        <span className="badge">Leaflet</span>
      </div>

      <div className="map-wrapper">
        <MapContainer
          center={[32.4279, 53.688]}
          zoom={5}
          scrollWheelZoom
          className="map"
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          <FitToLayers layers={usableLayers} />

          {usableLayers.map((layer) => (
            <GeoJSON
              key={layer.name}
              data={layer.geojson}
              style={layerStyle}
              onEachFeature={onEachFeature}
            />
          ))}
        </MapContainer>
      </div>

      {!requestId && (
        <p className="muted">
          برای نمایش لایه مکانی ابتدا یک query اجرا کنید.
        </p>
      )}

      {loading && <p className="muted">در حال دریافت لایه‌های مکانی...</p>}

      {error && <div className="alert error">{error}</div>}

      {mapLayersPayload?.warnings?.length > 0 && (
        <div className="alert warning">
          <strong>هشدارهای لایه مکانی</strong>
          <ul>
            {mapLayersPayload.warnings.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {requestId && !loading && !error && usableLayers.length === 0 && (
        <p className="muted">
          لایه قابل نمایش برای Leaflet پیدا نشد.
        </p>
      )}

      {usableLayers.length > 0 && (
        <div className="layer-list">
          <strong>لایه‌های قابل نمایش:</strong>
          <ul>
            {usableLayers.map((layer) => (
              <li key={layer.name}>
                {layer.name} — {layer.feature_count} feature — {layer.source}
              </li>
            ))}
          </ul>
        </div>
      )}

      {mapLayersPayload && (
        <details>
          <summary>JSON لایه‌های نقشه</summary>
          <pre dir="ltr" className="json-box">
            {JSON.stringify(mapLayersPayload, null, 2)}
          </pre>
        </details>
      )}
    </section>
  );
}
