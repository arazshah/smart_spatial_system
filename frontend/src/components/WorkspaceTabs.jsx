import { useMemo, useState } from "react";
import { outputFileUrl } from "../api/client";

export default function WorkspaceTabs({
  response,
  mapLayers,
  outputFiles,
  project,
  activeRequest,
}) {
  const [tab, setTab] = useState("answer");

  const tabs = useMemo(
    () => [
      { key: "answer", label: "Answer" },
      { key: "map", label: "Map Layers" },
      { key: "outputs", label: "Outputs" },
      { key: "project", label: "Project" },
    ],
    []
  );

  return (
    <section className="panel-card">
      <div className="panel-card-header">
        <div>
          <h2>Workspace</h2>
          <p>پاسخ، لایه‌ها و خروجی‌های request فعال در این بخش نمایش داده می‌شود.</p>
        </div>

        {activeRequest?.request_id && (
          <span className="status-badge" dir="ltr">
            {activeRequest.request_id}
          </span>
        )}
      </div>

      <div className="tabs">
        {tabs.map((item) => (
          <button
            key={item.key}
            className={`tab-button ${tab === item.key ? "active" : ""}`}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="tab-body">
        {tab === "answer" && (
          <div>
            {response ? (
              <div className="result-block">
                <div className="result-row">
                  <span>Status</span>
                  <strong>{response.status}</strong>
                </div>
                <div className="result-row">
                  <span>Request ID</span>
                  <strong dir="ltr">{response.request_id}</strong>
                </div>
                <div className="result-answer">{response.answer}</div>
              </div>
            ) : (
              <div className="empty-state compact">
                هنوز پاسخی ثبت نشده است.
              </div>
            )}
          </div>
        )}

        {tab === "map" && (
          <div>
            {mapLayers?.layers?.length ? (
              <pre className="json-box" dir="ltr">
                {JSON.stringify(mapLayers, null, 2)}
              </pre>
            ) : (
              <div className="empty-state compact">
                هنوز لایه نقشه‌ای موجود نیست.
              </div>
            )}
          </div>
        )}

        {tab === "outputs" && (
          <div>
            {response?.request_id && outputFiles?.length ? (
              <div className="output-list">
                {outputFiles.map((file) => (
                  <a
                    key={file.filename}
                    className="output-item"
                    href={outputFileUrl(response.request_id, file.filename)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <div>{file.filename}</div>
                    <small>{file.media_type || "file"}</small>
                  </a>
                ))}
              </div>
            ) : (
              <div className="empty-state compact">
                هنوز فایل خروجی موجود نیست.
              </div>
            )}
          </div>
        )}

        {tab === "project" && (
          <div>
            {project ? (
              <pre className="json-box" dir="ltr">
                {JSON.stringify(project, null, 2)}
              </pre>
            ) : (
              <div className="empty-state compact">
                پروژه‌ای انتخاب نشده است.
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
