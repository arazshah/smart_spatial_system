import { useState } from "react";
import { uploadFileByKind } from "../api/client";

export default function UploadPanel({ project, onUploaded }) {
  const [kind, setKind] = useState("raster");
  const [file, setFile] = useState(null);
  const [upload, setUpload] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleUpload(event) {
    event.preventDefault();

    if (!project?.project_id) {
      setError("ابتدا یک پروژه فعال انتخاب یا ایجاد کنید.");
      return;
    }

    if (!file) {
      setError("ابتدا یک فایل انتخاب کنید.");
      return;
    }

    setLoading(true);
    setError("");
    setUpload(null);

    try {
      const payload = await uploadFileByKind(kind, file, project.project_id);
      setUpload(payload);
      onUploaded?.(payload);
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
    <section className="panel-card">
      <div className="panel-card-header">
        <div>
          <h2>Upload Center</h2>
          <p>آپلود فایل‌های Raster و Vector در پروژه فعال</p>
        </div>
      </div>

      <form onSubmit={handleUpload} className="form">
        <label>
          نوع داده
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="raster">Raster</option>
            <option value="vector">Vector</option>
          </select>
        </label>

        <label>
          فایل
          <input
            key={kind}
            type="file"
            accept={accept}
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </label>

        <button type="submit" disabled={loading || !project}>
          {loading ? "در حال آپلود..." : "آپلود در پروژه فعال"}
        </button>
      </form>

      {error && <div className="alert error">{error}</div>}

      {upload && (
        <div className="result-card">
          <div className="result-row"><span>Kind</span><strong>{upload.kind}</strong></div>
          <div className="result-row"><span>Upload ID</span><strong dir="ltr">{upload.upload_id}</strong></div>
          <div className="result-row"><span>Filename</span><strong>{upload.filename}</strong></div>
          <div className="result-row"><span>Project ID</span><strong dir="ltr">{upload.project_id}</strong></div>
        </div>
      )}
    </section>
  );
}
