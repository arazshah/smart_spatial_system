import { useMemo, useState } from "react";

function getUploadLabel(upload) {
  if (!upload) return "No active data";
  return upload.filename || upload.name || upload.upload_id || "Active data";
}

function getUploadKind(upload) {
  const kind = String(upload?.kind || upload?.type || "").toLowerCase();

  if (kind.includes("vector")) return "Vector";
  if (kind.includes("raster")) return "Raster";
  if (kind.includes("table") || kind.includes("csv")) return "Table";

  return upload ? "Data" : "None";
}

function compactCount(count) {
  const number = Number(count || 0);
  if (number > 99) return "99+";
  return String(number);
}

export default function TopQueryBar({
  project,
  uploads,
  selectedUpload,
  onRun,
  loading,
  onOpenProjects,
  onOpenUploads,
}) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  const uploadCount = useMemo(() => {
    if (Array.isArray(project?.uploads)) return project.uploads.length;
    if (Array.isArray(uploads)) return uploads.length;
    return 0;
  }, [project, uploads]);

  const activeDataLabel = getUploadLabel(selectedUpload);
  const activeDataKind = getUploadKind(selectedUpload);

  const canRun = query.trim().length > 0 && !loading;

  function handleRun() {
    setError("");

    const cleaned = query.trim();

    if (!cleaned) {
      setError("لطفاً درخواست مکانی خود را بنویسید.");
      return;
    }

    onRun({
      query: cleaned,
    });
  }

  function handleKeyDown(event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      handleRun();
    }
  }

  function applySuggestion(text) {
    setQuery(text);
    setError("");
  }

  return (
    <section className="command-bar-shell">
      <div className="command-bar-main">
        <div className="command-input-wrap">
          <div className="command-leading-icon">⌘</div>

          <textarea
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              if (error) setError("");
            }}
            onKeyDown={handleKeyDown}
            rows={2}
            placeholder="درخواست مکانی خود را بنویسید؛ مثلاً: نقاط داخل فایل را روی نقشه نمایش بده"
            autoFocus
          />

          <button
            className="command-run-button"
            type="button"
            onClick={handleRun}
            disabled={!canRun}
            title="Ctrl + Enter"
          >
            {loading ? (
              <>
                <span className="command-spinner" />
                <span>Analyzing</span>
              </>
            ) : (
              <>
                <span>Run</span>
                <span className="command-run-arrow">↵</span>
              </>
            )}
          </button>
        </div>

        <div className="command-meta-row">
          <div className="command-context-chips">
            <button
              type="button"
              className="command-chip clickable"
              onClick={onOpenProjects}
              title="Open projects"
            >
              <span className="chip-icon">▣</span>
              <span className="chip-label">
                {project?.name || "No project"}
              </span>
            </button>

            <button
              type="button"
              className="command-chip clickable"
              onClick={onOpenUploads}
              disabled={!project}
              title="Open data sources"
            >
              <span className="chip-icon">◈</span>
              <span className="chip-label">{activeDataLabel}</span>
              {selectedUpload ? (
                <span className="chip-badge">{activeDataKind}</span>
              ) : null}
            </button>

            <span className="command-chip subtle" title="Connected data sources">
              <span className="chip-icon">↳</span>
              <span>{compactCount(uploadCount)} sources</span>
            </span>
          </div>

          <div className="command-shortcut">
            <kbd>Ctrl</kbd>
            <span>+</span>
            <kbd>Enter</kbd>
          </div>
        </div>
      </div>

      {!selectedUpload && (
        <div className="command-warning">
          <span>!</span>
          <p>
            داده فعالی انتخاب نشده است. برای queryهای مکانی، از بخش Data یک منبع داده انتخاب کنید.
          </p>
        </div>
      )}

      {error && (
        <div className="command-error">
          <span>!</span>
          <p>{error}</p>
        </div>
      )}

      {!query.trim() && selectedUpload ? (
        <div className="command-suggestions">
          <button
            type="button"
            onClick={() => applySuggestion("نقاط داخل فایل را روی نقشه نمایش بده")}
          >
            نمایش نقاط فایل
          </button>

          <button
            type="button"
            onClick={() => applySuggestion("لایه وکتور را بررسی کن و تعداد عارضه‌ها را گزارش بده")}
          >
            خلاصه‌سازی وکتور
          </button>

          <button
            type="button"
            onClick={() => applySuggestion("پوشش گیاهی را از تصویر استخراج کن و به پلیگون تبدیل کن")}
          >
            استخراج پوشش گیاهی
          </button>
        </div>
      ) : null}
    </section>
  );
}
