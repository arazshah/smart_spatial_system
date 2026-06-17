import { useState } from "react";
import { uploadFileByKind } from "../api/client";

export default function UploadPanel({ onUploaded }) {
  const [kind, setKind] = useState("raster");
  const [file, setFile] = useState(null);
  const [upload, setUpload] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleUpload(event) {
    event.preventDefault();

    if (!file) {
      setError("ابتدا یک فایل انتخاب کنید.");
      return;
    }

    setLoading(true);
    setError("");
    setUpload(null);

    try {
      const payload = await uploadFileByKind(kind, file);
      setUpload(payload);

      if (onUploaded) {
        onUploaded(payload);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const accept =
    kind === "raster"
      ? ".json,.geojson,.tif,.tiff,application/json"
      : ".json,.geojson,.gpkg,.zip,.shp,.kml,application/json,application/geo+json";

  return (
    <section className="card">
      <div className="card-header">
        <h2>آپلود فایل مکانی</h2>
        <span className="badge">Raster / Vector</span>
      </div>

      <form onSubmit={handleUpload} className="form">
        <label>
          نوع فایل
          <select
            value={kind}
            onChange={(event) => {
              setKind(event.target.value);
              setFile(null);
              setUpload(null);
              setError("");
            }}
          >
            <option value="raster">Raster</option>
            <option value="vector">Vector</option>
          </select>
        </label>

        <label>
          فایل {kind === "raster" ? "Raster" : "Vector"}
          <input
            key={kind}
            type="file"
            accept={accept}
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </label>

        <button type="submit" disabled={loading}>
          {loading ? "در حال آپلود..." : "آپلود فایل"}
        </button>
      </form>

      {error && <div className="alert error">{error}</div>}

      {upload && (
        <div className="alert info">
          <strong>فایل آپلود شد.</strong>
          <div dir="ltr">kind: {upload.kind}</div>
          <div dir="ltr">upload_id: {upload.upload_id}</div>
          <div>filename: {upload.filename}</div>
          <div>parsed_json_available: {String(upload.parsed_json_available)}</div>
        </div>
      )}

      {upload && (
        <details>
          <summary>JSON آپلود</summary>
          <pre dir="ltr" className="json-box">
            {JSON.stringify(upload, null, 2)}
          </pre>
        </details>
      )}

      <p className="muted">
        در حالت عملیاتی، فایل‌های Raster از مسیر پلاگین local_raster_loader و
        فایل‌های Vector از مسیر پلاگین local_vector_loader resolve می‌شوند.
      </p>
    </section>
  );
}
