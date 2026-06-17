import { useState } from "react";
import { uploadRaster } from "../api/client";

export default function UploadPanel({ onUploaded }) {
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
      const payload = await uploadRaster(file);
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

  return (
    <section className="card">
      <div className="card-header">
        <h2>آپلود Raster</h2>
        <span className="badge">Upload</span>
      </div>

      <form onSubmit={handleUpload} className="form">
        <label>
          فایل raster
          <input
            type="file"
            accept=".json,.geojson,.tif,.tiff,application/json"
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
    </section>
  );
}
