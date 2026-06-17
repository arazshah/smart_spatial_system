import { useState } from "react";
import { outputFileUrl } from "../api/client";

function answerText(response) {
  if (!response) return "";

  return (
    response.answer ||
    response.message ||
    response.summary ||
    response.result?.answer ||
    response.response?.answer ||
    ""
  );
}

export default function InspectorPanel({
  response,
  mapLayers,
  outputFiles,
  activeRequest,
}) {
  const [tab, setTab] = useState("result");

  return (
    <aside className="inspector">
      <div className="inspector-header">
        <div>
          <h2>Inspector</h2>
          <p>{activeRequest?.request_id || response?.request_id || "No request"}</p>
        </div>
      </div>

      <div className="inspector-tabs">
        <button
          className={tab === "result" ? "active" : ""}
          onClick={() => setTab("result")}
        >
          Result
        </button>
        <button
          className={tab === "layers" ? "active" : ""}
          onClick={() => setTab("layers")}
        >
          Layers
        </button>
        <button
          className={tab === "outputs" ? "active" : ""}
          onClick={() => setTab("outputs")}
        >
          Outputs
        </button>
      </div>

      <div className="inspector-body">
        {tab === "result" && (
          <>
            {!response ? (
              <div className="inspector-empty">
                هنوز تحلیلی اجرا نشده است.
              </div>
            ) : (
              <div className="result-panel">
                <div className="mini-row">
                  <span>Status</span>
                  <strong>{response.status || "done"}</strong>
                </div>

                <div className="mini-row">
                  <span>Request</span>
                  <strong dir="ltr">{response.request_id || activeRequest?.request_id}</strong>
                </div>

                <div className="answer-box">
                  {answerText(response) || (
                    <pre dir="ltr">{JSON.stringify(response, null, 2)}</pre>
                  )}
                </div>
              </div>
            )}
          </>
        )}

        {tab === "layers" && (
          <>
            {mapLayers?.layers?.length ? (
              <div className="layer-list">
                {mapLayers.layers.map((layer, index) => (
                  <div key={layer.id || layer.name || index} className="layer-item">
                    <strong>{layer.name || layer.id || `Layer ${index + 1}`}</strong>
                    <span>{layer.type || layer.kind || "map layer"}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="inspector-empty">
                هنوز layer قابل نمایش وجود ندارد.
              </div>
            )}
          </>
        )}

        {tab === "outputs" && (
          <>
            {response?.request_id && outputFiles?.length ? (
              <div className="output-list compact">
                {outputFiles.map((file) => (
                  <a
                    key={file.filename}
                    className="output-item"
                    href={outputFileUrl(response.request_id, file.filename)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <strong>{file.filename}</strong>
                    <span>{file.media_type || "file"}</span>
                  </a>
                ))}
              </div>
            ) : (
              <div className="inspector-empty">
                هنوز فایل خروجی تولید نشده است.
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
