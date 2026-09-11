import { useEffect, useState } from "react";
import { getOutputManifest, outputFileUrl } from "../api/client";

export default function OutputFilesPanel({ response }) {
  const [manifest, setManifest] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const requestId = response?.request_id;

  const [trackedRequestId, setTrackedRequestId] = useState(requestId);
  if (trackedRequestId !== requestId) {
    setTrackedRequestId(requestId);
    setManifest(null);
    setError("");
    if (requestId) setLoading(true);
  }

  useEffect(() => {
    if (!requestId) return;

    getOutputManifest(requestId)
      .then(setManifest)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [requestId]);

  return (
    <section className="card">
      <div className="card-header">
        <h2>فایل‌های خروجی</h2>
        <span className="badge">Outputs</span>
      </div>

      {!requestId && (
        <p className="muted">برای مشاهده فایل‌ها ابتدا query اجرا کنید.</p>
      )}

      {loading && <p className="muted">در حال دریافت فایل‌های خروجی...</p>}

      {error && <div className="alert error">{error}</div>}

      {manifest && (
        <>
          <div className="info-grid">
            <div>
              <strong>Request ID</strong>
              <span>{manifest.request_id}</span>
            </div>
            <div>
              <strong>Schema</strong>
              <span>{manifest.schema_version}</span>
            </div>
            <div>
              <strong>File Count</strong>
              <span>{manifest.files?.length || 0}</span>
            </div>
            <div>
              <strong>Directory</strong>
              <span className="small-text" dir="ltr">
                {manifest.directory}
              </span>
            </div>
          </div>

          <div className="summary-list">
            {manifest.files?.map((file) => (
              <div key={file.filename} className="summary-item">
                <h4>{file.filename}</h4>
                <div className="summary-grid">
                  <div>
                    <strong>kind:</strong> {file.kind}
                  </div>
                  <div>
                    <strong>size:</strong> {file.size_bytes} bytes
                  </div>
                  <div>
                    <strong>media:</strong> {file.media_type}
                  </div>
                </div>

                <a
                  className="download-link"
                  href={outputFileUrl(manifest.request_id, file.filename)}
                  target="_blank"
                  rel="noreferrer"
                >
                  دانلود / مشاهده فایل
                </a>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
