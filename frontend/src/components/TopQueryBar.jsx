import { useState } from "react";

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

  const projectUploadCount = project?.uploads?.length || 0;

  function handleRun() {
    setError("");

    if (!query.trim()) {
      setError("لطفاً درخواست مکانی خود را بنویسید.");
      return;
    }

    onRun({
      query: query.trim(),
    });
  }

  function handleKeyDown(event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      handleRun();
    }
  }

  return (
    <section className="top-query-bar minimal">
      <div className="query-context-strip">
        <div className="context-left">
          <span className="context-pill">
            {project ? project.name : "No active project"}
          </span>

          <span className="context-muted">
            {project
              ? `${projectUploadCount} data source${projectUploadCount === 1 ? "" : "s"} connected`
              : "Create or select a project from the menu"}
          </span>
        </div>

        <div className="context-actions">
          <button
            type="button"
            className="text-button"
            onClick={onOpenProjects}
          >
            Projects
          </button>

          <button
            type="button"
            className="text-button"
            onClick={onOpenUploads}
            disabled={!project}
          >
            Data Sources
          </button>
        </div>
      </div>

      <div className="query-command-row minimal">
        <textarea
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleKeyDown}
          rows={2}
          placeholder="مثلاً: پوشش گیاهی این منطقه را استخراج کن و نتیجه را روی نقشه نشان بده..."
          autoFocus
        />

        <button
          className="run-analysis-button"
          type="button"
          onClick={handleRun}
          disabled={loading}
        >
          {loading ? "در حال تحلیل..." : "اجرای تحلیل"}
        </button>
      </div>

      {selectedUpload && (
        <div className="silent-context">
          داده فعال: {selectedUpload.filename || selectedUpload.upload_id}
        </div>
      )}

      {error && <div className="query-error light">{error}</div>}
    </section>
  );
}
