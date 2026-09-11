import { useMemo, useState } from "react";
import PluginManagerPanel from "./PluginManagerPanel";
import SettingsPanel from "./SettingsPanel";
import {
  getDataSourceCrsLabel,
  getDataSourceFeatureCount,
  getDataSourceGeometryType,
  getDataSourceKind,
  getDataSourceName,
  getDataSourceSize,
  getDataSourceStatus,
  getDataSourceTime,
} from "../lib/dataSources";

function shortId(value) {
  if (!value) return "—";
  const text = String(value);
  if (text.length <= 18) return text;
  return `${text.slice(0, 10)}…${text.slice(-6)}`;
}

function statusMeta(value) {
  const status = String(value || "unknown").toLowerCase();

  if (["succeeded", "success", "completed", "done", "ready", "ok"].includes(status)) {
    return {
      label: status === "ready" ? "Ready" : "Succeeded",
      className: "success",
      icon: "✓",
    };
  }

  if (["failed", "error"].includes(status)) {
    return {
      label: "Failed",
      className: "danger",
      icon: "!",
    };
  }

  if (["running", "pending", "analyzing", "processing", "uploading"].includes(status)) {
    return {
      label: "Running",
      className: "running",
      icon: "●",
    };
  }

  return {
    label: "Unknown",
    className: "neutral",
    icon: "○",
  };
}

function formatDateTime(value) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return "—";

  return new Intl.DateTimeFormat("fa-IR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function formatBytes(value) {
  const bytes = Number(value || 0);

  if (!Number.isFinite(bytes) || bytes <= 0) return "—";

  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unitIndex = 0;

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }

  return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}


function dataKindMeta(kind) {
  const normalized = String(kind || "").toLowerCase();

  if (normalized === "vector") {
    return {
      label: "Vector",
      icon: "◈",
      className: "vector",
    };
  }

  if (normalized === "raster") {
    return {
      label: "Raster",
      icon: "▦",
      className: "raster",
    };
  }

  if (normalized === "table") {
    return {
      label: "Table",
      icon: "≡",
      className: "table",
    };
  }

  if (normalized === "database") {
    return {
      label: "Database",
      icon: "◍",
      className: "database",
    };
  }

  if (normalized === "online") {
    return {
      label: "Online",
      icon: "◎",
      className: "online",
    };
  }

  if (normalized === "api") {
    return {
      label: "API",
      icon: "{ }",
      className: "api",
    };
  }

  return {
    label: "Data",
    icon: "◇",
    className: "data",
  };
}





function getRequestQuery(item) {
  return (
    item?.query ||
    item?.question ||
    item?.metadata?.original_query ||
    item?.request?.query ||
    item?.production_response?.query ||
    "بدون متن درخواست"
  );
}

function getRequestStatus(item) {
  return (
    item?.status ||
    item?.production_response?.status ||
    item?.response?.status ||
    item?.result?.status ||
    "unknown"
  );
}

function getRequestTime(item) {
  return (
    item?.created_at ||
    item?.updated_at ||
    item?.timestamp ||
    item?.metadata?.created_at ||
    item?.production_response?.created_at ||
    null
  );
}

function matchesProjectRequest(item, activeProject) {
  if (!activeProject?.project_id || !item?.request_id) return false;

  const projectRequestIds = Array.isArray(activeProject.requests)
    ? activeProject.requests
    : [];

  return (
    projectRequestIds.includes(item.request_id) ||
    item.project_id === activeProject.project_id ||
    item.metadata?.project_id === activeProject.project_id ||
    item.production_response?.project_id === activeProject.project_id
  );
}

function drawerTitle(activeTool) {
  if (activeTool === "projects") return "Projects";
  if (activeTool === "uploads") return "Data Sources";
  if (activeTool === "history") return "History";
  if (activeTool === "plugins") return "Plugins";
  if (activeTool === "settings") return "Settings";
  return "Workspace";
}

function drawerSubtitle(activeTool) {
  if (activeTool === "projects") return "مدیریت پروژه‌های کاری";
  if (activeTool === "uploads") return "مدیریت داده‌های ورودی پروژه";
  if (activeTool === "history") return "درخواست‌های اجراشده و نتایج قبلی";
  if (activeTool === "plugins") return "مدیریت پلاگین‌ها، قابلیت‌ها و وضعیت افزونه‌ها";
  if (activeTool === "settings") return "تنظیمات عمومی، وضعیت سیستم و پیکربندی runtime";
  return "";
}

function SectionHeader({ title, count }) {
  return (
    <div className="drawer-section-header">
      <span>{title}</span>
      {Number.isFinite(count) ? <strong>{count}</strong> : null}
    </div>
  );
}

function EmptyDrawerState({ icon, title, text }) {
  return (
    <div className="drawer-empty-pro">
      <div className="drawer-empty-icon">{icon}</div>
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}

function DataSourceTypeTile({ icon, title, text, active, disabled, onClick }) {
  return (
    <button
      type="button"
      className={`ds-type-tile ${active ? "active" : ""}`}
      onClick={onClick}
      disabled={disabled}
    >
      <span>{icon}</span>
      <strong>{title}</strong>
      <small>{text}</small>
      {disabled ? <em>Next phase</em> : null}
    </button>
  );
}

function DataSourceCard({
  item,
  selected,
  onSelect,
  onPreview,
  onEdit,
  onDelete,
}) {
  const kind = getDataSourceKind(item);
  const kindMeta = dataKindMeta(kind);
  const status = statusMeta(getDataSourceStatus(item));

  const featureCount = getDataSourceFeatureCount(item);

  const geometryType =
    getDataSourceGeometryType(item) ||
    item?.metadata?.geometry_type ||
    item?.summary?.geometry_type ||
    null;

  const crs = getDataSourceCrsLabel(item);
  const featureCountLabel =
    Number.isFinite(featureCount) || typeof featureCount === "string"
      ? featureCount
      : "—";

  return (
    <article className={`ds-card ${selected ? "active" : ""}`}>
      <div className="ds-card-main">
        <div className={`ds-kind-icon ${kindMeta.className}`}>
          {kindMeta.icon}
        </div>

        <div className="ds-card-info">
          <div className="ds-card-title-row">
            <strong title={getDataSourceName(item)}>{getDataSourceName(item)}</strong>
            {selected ? <span className="ds-active-badge">Active</span> : null}
          </div>

          <div className="ds-card-subtitle">
            <span>{kindMeta.label}</span>
            <span dir="ltr">{shortId(item.upload_id || item.id)}</span>
          </div>

          <div className="ds-meta-grid">
            <span>
              <small>Status</small>
              <b className={`ds-status ${status.className}`}>{status.label}</b>
            </span>

            <span>
              <small>Size</small>
              <b>{formatBytes(getDataSourceSize(item))}</b>
            </span>

            <span>
              <small>CRS</small>
              <b>{crs}</b>
            </span>

            <span>
              <small>Features</small>
              <b>{featureCountLabel}</b>
            </span>
          </div>

          {(geometryType || getDataSourceTime(item)) && (
            <div className="ds-extra-line">
              {geometryType ? <span>{geometryType}</span> : null}
              {getDataSourceTime(item) ? <span>{formatDateTime(getDataSourceTime(item))}</span> : null}
            </div>
          )}
        </div>
      </div>

      <div className="ds-card-actions">
        <button type="button" onClick={() => onSelect(item)}>
          {selected ? "Selected" : "Set active"}
        </button>

        <button type="button" onClick={() => onPreview(item)}>
          Preview
        </button>

        <button type="button" onClick={() => onEdit(item)}>
          Edit
        </button>

        <button type="button" className="danger" onClick={() => onDelete(item)}>
          Delete
        </button>
      </div>
    </article>
  );
}

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
  requests,
  activeRequest,
  onSelectRequest,
  health,
  onPreviewUpload,
  onEditUpload,
  onDeleteUpload,
  onAddExternalSource,
}) {

  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dataSourceMode = "file";
  const [dataSourceQuery, setDataSourceQuery] = useState("");
  const [dataSourceFilter, setDataSourceFilter] = useState("all");
  const [dataSourceSort, setDataSourceSort] = useState("newest");

  const safeProjects = Array.isArray(projects) ? projects : [];
  const safeUploads = Array.isArray(uploads) ? uploads : [];
  const safeRequests = Array.isArray(requests) ? requests : [];

  const projectUploads = useMemo(() => {
    if (!activeProject?.uploads?.length) return [];

    const allowed = new Set(activeProject.uploads);
    return safeUploads.filter((item) => allowed.has(item.upload_id));
  }, [activeProject, safeUploads]);

  const projectRequests = useMemo(() => {
    if (!activeProject) return [];

    const matched = safeRequests.filter((item) =>
      matchesProjectRequest(item, activeProject),
    );

    const finalList = matched.length ? matched : safeRequests;

    return [...finalList].sort((a, b) => {
      const at = new Date(getRequestTime(a) || 0).getTime();
      const bt = new Date(getRequestTime(b) || 0).getTime();
      return bt - at;
    });
  }, [activeProject, safeRequests]);

  const dataSourceCounts = useMemo(() => {
    const counts = {
      vector: 0,
      raster: 0,
      table: 0,
      database: 0,
      online: 0,
      api: 0,
      data: 0,
    };

    for (const item of projectUploads) {
      const kind = getDataSourceKind(item);
      counts[kind] = (counts[kind] || 0) + 1;
    }

    return counts;
  }, [projectUploads]);

  const filteredProjectUploads = useMemo(() => {
    const query = dataSourceQuery.trim().toLowerCase();

    let items = [...projectUploads];

    if (dataSourceFilter !== "all") {
      items = items.filter(
        (item) => String(getDataSourceKind(item) || "").toLowerCase() === dataSourceFilter,
      );
    }

    if (query) {
      items = items.filter((item) => {
        const tags = Array.isArray(item?.tags) ? item.tags.join(" ") : "";
        const haystack = [
          getDataSourceName(item),
          item?.filename,
          item?.original_filename,
          item?.upload_id,
          item?.id,
          tags,
          getDataSourceKind(item),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();

        return haystack.includes(query);
      });
    }

    items.sort((a, b) => {
      if (dataSourceSort === "name") {
        return String(getDataSourceName(a) || "").localeCompare(
          String(getDataSourceName(b) || ""),
          "fa",
          { sensitivity: "base" },
        );
      }

      const at = new Date(getDataSourceTime(a) || 0).getTime();
      const bt = new Date(getDataSourceTime(b) || 0).getTime();

      if (dataSourceSort === "oldest") return at - bt;
      return bt - at;
    });

    return items;
  }, [projectUploads, dataSourceFilter, dataSourceQuery, dataSourceSort]);

  if (!activeTool) return null;

  async function handlePreviewDataSource(item) {
    setError("");

    if (!onPreviewUpload) return;

    try {
      await onPreviewUpload(item);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleEditDataSource(item) {
    setError("");

    if (!onEditUpload) return;

    try {
      await onEditUpload(item);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleDeleteDataSource(item) {
    setError("");

    if (!onDeleteUpload) return;

    try {
      await onDeleteUpload(item);
    } catch (err) {
      setError(err.message);
    }
  }

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

  return (
    <section className="wb-drawer wb-drawer-pro">
      <div className="wb-drawer-header">
        <div>
          <h2>{drawerTitle(activeTool)}</h2>
          <p>{drawerSubtitle(activeTool)}</p>
        </div>

        <button type="button" className="icon-button" onClick={onClose}>
          ×
        </button>
      </div>

      {error && <div className="alert error">{error}</div>}

      {activeTool === "projects" && (
        <div className="drawer-content">
          <form className="compact-form drawer-form-pro" onSubmit={handleCreateProject}>
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

          <SectionHeader title="پروژه‌ها" count={safeProjects.length} />

          <div className="drawer-list">
            {safeProjects.length === 0 ? (
              <EmptyDrawerState
                icon="▣"
                title="هنوز پروژه‌ای وجود ندارد"
                text="برای شروع، یک پروژه کاری بسازید."
              />
            ) : (
              safeProjects.map((project) => (
                <button
                  key={project.project_id}
                  type="button"
                  className={`drawer-list-item drawer-card-item ${
                    activeProject?.project_id === project.project_id ? "active" : ""
                  }`}
                  onClick={() => onSelectProject(project)}
                >
                  <strong>{project.name}</strong>
                  <span dir="ltr">{shortId(project.project_id)}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}

      {activeTool === "uploads" && (
        <div className="drawer-content ds-drawer-content">
          <div className="ds-summary-card">
            <div>
              <span>Project Data Catalog</span>
              <strong>{projectUploads.length}</strong>
            </div>

            <p>
              {activeProject
                ? `داده‌های ورودی پروژه ${activeProject.name}`
                : "برای مدیریت داده‌ها ابتدا یک پروژه فعال انتخاب کنید."}
            </p>

            <div className="ds-count-row">
              <span>Vector: {dataSourceCounts.vector || 0}</span>
              <span>Raster: {dataSourceCounts.raster || 0}</span>
              <span>Table: {dataSourceCounts.table || 0}</span>
            </div>
          </div>

          <SectionHeader title="افزودن منبع داده" />

          <div className="ds-type-grid">
            <DataSourceTypeTile
              icon="⇧"
              title="File Upload"
              text="Raster / Vector"
              active={dataSourceMode === "file"}
              onClick={() => onAddExternalSource && onAddExternalSource("file")}
            />

            <DataSourceTypeTile
              icon="◍"
              title="PostGIS"
              text="Spatial Database"
              active={false}
              onClick={() => onAddExternalSource && onAddExternalSource("postgis")}
            />

            <DataSourceTypeTile
              icon="◎"
              title="WFS"
              text="Online Vector"
              active={false}
              onClick={() => onAddExternalSource && onAddExternalSource("wfs")}
            />

            <DataSourceTypeTile
              icon="↗"
              title="URL / API"
              text="REST / GeoJSON"
              active={false}
              onClick={() => onAddExternalSource && onAddExternalSource("url")}
            />

            <DataSourceTypeTile
              icon="≡"
              title="CSV/Table"
              text="Tabular / XY"
              active={false}
              onClick={() => onAddExternalSource && onAddExternalSource("csv")}
            />

            <DataSourceTypeTile
              icon="▦"
              title="WMS"
              text="Map Service"
              active={false}
              onClick={() => onAddExternalSource && onAddExternalSource("wms")}
            />
          </div>
<SectionHeader title="داده‌های پروژه فعال" count={filteredProjectUploads.length} />

          <div className="ds-toolbar">
            <input
              type="search"
              value={dataSourceQuery}
              onChange={(event) => setDataSourceQuery(event.target.value)}
              placeholder="جستجو در نام، فایل، تگ یا شناسه..."
            />

            <select
              value={dataSourceFilter}
              onChange={(event) => setDataSourceFilter(event.target.value)}
            >
              <option value="all">All types</option>
              <option value="vector">Vector</option>
              <option value="raster">Raster</option>
              <option value="table">Table</option>
              <option value="database">Database</option>
              <option value="online">Online</option>
              <option value="api">API</option>
            </select>

            <select
              value={dataSourceSort}
              onChange={(event) => setDataSourceSort(event.target.value)}
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="name">Name A-Z</option>
            </select>
          </div>

          <div className="ds-list">
            {!activeProject ? (
              <EmptyDrawerState
                icon="◈"
                title="پروژه‌ای انتخاب نشده است"
                text="ابتدا از بخش Projects یک پروژه فعال انتخاب کنید."
              />
            ) : projectUploads.length === 0 ? (
              <EmptyDrawerState
                icon="◈"
                title="هنوز داده‌ای در پروژه نیست"
                text="یک فایل raster یا vector آپلود کنید تا در catalog پروژه ثبت شود."
              />
            ) : filteredProjectUploads.length === 0 ? (
              <EmptyDrawerState
                icon="⌕"
                title="موردی پیدا نشد"
                text="عبارت جستجو یا فیلتر انتخابی را تغییر دهید."
              />
            ) : (
              filteredProjectUploads.map((item) => (
                <DataSourceCard
                  key={item.upload_id || item.id}
                  item={item}
                  selected={selectedUpload?.upload_id === item.upload_id}
                  onSelect={onSelectUpload}
                  onPreview={handlePreviewDataSource}
                  onEdit={handleEditDataSource}
                  onDelete={handleDeleteDataSource}
                />
              ))
            )}
          </div>
        </div>
      )}

      {activeTool === "history" && (
        <div className="drawer-content history-drawer-content">
          <div className="history-summary-card">
            <div>
              <span>Request history</span>
              <strong>{projectRequests.length}</strong>
            </div>
            <p>
              {activeProject
                ? `پروژه فعال: ${activeProject.name}`
                : "برای مشاهده history ابتدا پروژه انتخاب کنید."}
            </p>
          </div>

          <div className="history-list">
            {!activeProject ? (
              <EmptyDrawerState
                icon="◷"
                title="پروژه‌ای انتخاب نشده است"
                text="برای مشاهده درخواست‌های قبلی، ابتدا یک پروژه فعال انتخاب کنید."
              />
            ) : projectRequests.length === 0 ? (
              <EmptyDrawerState
                icon="◷"
                title="هنوز درخواستی ثبت نشده است"
                text="بعد از اجرای اولین query، درخواست‌ها در این بخش نمایش داده می‌شوند."
              />
            ) : (
              projectRequests.map((item) => {
                const meta = statusMeta(getRequestStatus(item));
                const isActive =
                  activeRequest?.request_id === item.request_id;

                return (
                  <button
                    key={item.request_id}
                    type="button"
                    className={`history-card ${isActive ? "active" : ""}`}
                    onClick={() => onSelectRequest(item)}
                  >
                    <div className="history-card-top">
                      <span className={`history-status ${meta.className}`}>
                        <i>{meta.icon}</i>
                        {meta.label}
                      </span>
                      <span className="history-time">
                        {formatDateTime(getRequestTime(item))}
                      </span>
                    </div>

                    <div className="history-query">
                      {getRequestQuery(item)}
                    </div>

                    <div className="history-meta-row">
                      <span dir="ltr">{shortId(item.request_id)}</span>
                      {item.project_id ? (
                        <span dir="ltr">project: {shortId(item.project_id)}</span>
                      ) : null}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
      {activeTool === "settings" && (
        <div className="drawer-content">
          <SettingsPanel
            health={health}
            activeProject={activeProject}
            selectedUpload={selectedUpload}
            requestsCount={safeRequests.length}
          />
        </div>
      )}

      {activeTool === "plugins" && (
        <div className="drawer-content">
          <PluginManagerPanel />
        </div>
      )}
    </section>
  );
}
