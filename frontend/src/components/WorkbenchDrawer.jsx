import { useState } from "react";

export default function WorkbenchDrawer({
  activeTool,
  onClose,
  projects,
  activeProject,
  onCreateProject,
  onSelectProject,
  uploads,
  selectedUpload,
  onSelectUpload,
  onUploadFile,
  requests,
  activeRequest,
  onSelectRequest,
  health,
}) {
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [uploadKind, setUploadKind] = useState("raster");
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!activeTool) return null;

  const projectUploads = activeProject?.uploads?.length
    ? uploads.filter((item) => activeProject.uploads.includes(item.upload_id))
    : [];

  const projectRequests = activeProject?.requests?.length
    ? requests.filter((item) => activeProject.requests.includes(item.request_id))
    : [];

  async function handleCreateProject(event) {
    event.preventDefault();
    setError("");

    if (!projectName.trim()) {
      setError("نام پروژه الزامی است.");
      return;
    }

    try {
      setBusy(true);
      await onCreateProject({
        name: projectName.trim(),
        description: projectDescription.trim(),
        metadata: {
          source: "frontend-workbench-drawer",
        },
      });
      setProjectName("");
      setProjectDescription("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    setError("");

    if (!activeProject?.project_id) {
      setError("ابتدا یک پروژه فعال انتخاب کنید.");
      return;
    }

    if (!file) {
      setError("ابتدا فایل را انتخاب کنید.");
      return;
    }

    try {
      setBusy(true);
      await onUploadFile(uploadKind, file);
      setFile(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="wb-drawer">
      <div className="wb-drawer-header">
        <div>
          <h2>
            {activeTool === "projects" && "Projects"}
            {activeTool === "uploads" && "Uploads"}
            {activeTool === "history" && "History"}
            {activeTool === "settings" && "Settings"}
          </h2>
          <p>
            {activeTool === "projects" && "مدیریت پروژه‌های کاری"}
            {activeTool === "uploads" && "مدیریت داده‌های ورودی پروژه"}
            {activeTool === "history" && "درخواست‌های اجراشده"}
            {activeTool === "settings" && "وضعیت سیستم و تنظیمات"}
          </p>
        </div>

        <button className="icon-button" onClick={onClose}>
          ×
        </button>
      </div>

      {error && <div className="alert error">{error}</div>}

      {activeTool === "projects" && (
        <div className="drawer-content">
          <form className="compact-form" onSubmit={handleCreateProject}>
            <label>
              نام پروژه
              <input
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="Vegetation Monitoring"
              />
            </label>

            <label>
              توضیح کوتاه
              <textarea
                value={projectDescription}
                onChange={(event) => setProjectDescription(event.target.value)}
                rows={3}
                placeholder="اختیاری"
              />
            </label>

            <button type="submit" disabled={busy}>
              {busy ? "در حال ساخت..." : "ساخت پروژه"}
            </button>
          </form>

          <div className="drawer-list-title">پروژه‌ها</div>

          <div className="drawer-list">
            {projects.length === 0 ? (
              <div className="drawer-empty">هنوز پروژه‌ای وجود ندارد.</div>
            ) : (
              projects.map((project) => (
                <button
                  key={project.project_id}
                  className={`drawer-list-item ${
                    activeProject?.project_id === project.project_id ? "active" : ""
                  }`}
                  onClick={() => onSelectProject(project)}
                >
                  <strong>{project.name}</strong>
                  <span dir="ltr">{project.project_id}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}

      {activeTool === "uploads" && (
        <div className="drawer-content">
          <form className="compact-form" onSubmit={handleUpload}>
            <label>
              نوع داده
              <select
                value={uploadKind}
                onChange={(event) => setUploadKind(event.target.value)}
              >
                <option value="raster">Raster</option>
                <option value="vector">Vector</option>
              </select>
            </label>

            <label>
              فایل
              <input
                type="file"
                onChange={(event) => setFile(event.target.files?.[0] || null)}
              />
            </label>

            <button type="submit" disabled={busy || !activeProject}>
              {busy ? "در حال آپلود..." : "آپلود در پروژه فعال"}
            </button>
          </form>

          <div className="drawer-list-title">Uploadهای پروژه فعال</div>

          <div className="drawer-list">
            {!activeProject ? (
              <div className="drawer-empty">ابتدا پروژه انتخاب کنید.</div>
            ) : projectUploads.length === 0 ? (
              <div className="drawer-empty">هنوز فایلی در پروژه نیست.</div>
            ) : (
              projectUploads.map((item) => (
                <button
                  key={item.upload_id}
                  className={`drawer-list-item ${
                    selectedUpload?.upload_id === item.upload_id ? "active" : ""
                  }`}
                  onClick={() => onSelectUpload(item)}
                >
                  <strong>{item.filename || item.upload_id}</strong>
                  <span>
                    {item.kind} · <span dir="ltr">{item.upload_id}</span>
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      )}

      {activeTool === "history" && (
        <div className="drawer-content">
          <div className="drawer-list">
            {!activeProject ? (
              <div className="drawer-empty">ابتدا پروژه انتخاب کنید.</div>
            ) : projectRequests.length === 0 ? (
              <div className="drawer-empty">هنوز درخواستی ثبت نشده است.</div>
            ) : (
              projectRequests.map((item) => (
                <button
                  key={item.request_id}
                  className={`drawer-list-item ${
                    activeRequest?.request_id === item.request_id ? "active" : ""
                  }`}
                  onClick={() => onSelectRequest(item)}
                >
                  <strong dir="ltr">{item.request_id}</strong>
                  <span>{item.query || item.question || "بدون متن درخواست"}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}

      {activeTool === "settings" && (
        <div className="drawer-content">
          <div className="settings-card">
            <span>API Status</span>
            <strong>{health?.status || "unknown"}</strong>
          </div>

          <div className="settings-card">
            <span>Active Project</span>
            <strong>{activeProject?.name || "None"}</strong>
          </div>

          <div className="settings-card">
            <span>Selected Upload</span>
            <strong>{selectedUpload?.filename || "None"}</strong>
          </div>
        </div>
      )}
    </section>
  );
}
