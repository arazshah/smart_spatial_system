import { useEffect, useState } from "react";
import { API_BASE_URL, getHealth, runQuery } from "./api/client";
import FeedbackPanel from "./components/FeedbackPanel";
import MapViewer from "./components/MapViewer";
import OutputFilesPanel from "./components/OutputFilesPanel";
import QueryPanel from "./components/QueryPanel";
import UploadPanel from "./components/UploadPanel";
import ResponsePanel from "./components/ResponsePanel";
import WeightsPanel from "./components/WeightsPanel";

export default function App() {
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState("");
  const [response, setResponse] = useState(null);
  const [lastUpload, setLastUpload] = useState(null);
  const [queryError, setQueryError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((err) => setHealthError(err.message));
  }, []);

  async function handleQuery(payload) {
    setLoading(true);
    setQueryError("");

    try {
      const data = await runQuery(payload);
      setResponse(data);
    } catch (err) {
      setQueryError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app" dir="rtl">
      <header className="hero">
        <div>
          <h1>Smart Spatial System</h1>
          <p>
            رابط MVP برای اجرای query مکانی، نمایش پاسخ، نقشه و ارسال feedback
          </p>
        </div>

        <div className="api-status">
          <strong>API</strong>
          <span dir="ltr">{API_BASE_URL}</span>

          {health && <span className="status-dot ok">online</span>}
          {healthError && <span className="status-dot bad">offline</span>}
        </div>
      </header>

      {healthError && (
        <div className="alert error">
          اتصال به API برقرار نشد: {healthError}
        </div>
      )}

      {health && (
        <div className="health-bar">
          <span>Service: {health.service}</span>
          <span>Plugins: {health.plugin_modules?.length || 0}</span>
          <span>
            Weighted Router: {String(health.use_weighted_router ?? false)}
          </span>
        </div>
      )}

      <div className="layout">
        <div className="left-column">
          <UploadPanel onUploaded={setLastUpload} />

          <QueryPanel onSubmit={handleQuery} loading={loading} upload={lastUpload} />

          {queryError && <div className="alert error">{queryError}</div>}

          <FeedbackPanel response={response} />

          <WeightsPanel />
        </div>

        <div className="right-column">
          <ResponsePanel response={response} />
          <MapViewer response={response} />
          <OutputFilesPanel response={response} />
        </div>
      </div>
    </main>
  );
}
