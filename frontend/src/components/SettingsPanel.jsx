function shortId(value) {
  if (!value) return "—";
  const text = String(value);
  if (text.length <= 18) return text;
  return `${text.slice(0, 10)}…${text.slice(-6)}`;
}

function normalizeStatus(value) {
  const status = String(value || "unknown").toLowerCase();

  if (["ok", "ready", "healthy", "online"].includes(status)) {
    return { label: "Operational", value: status, className: "success" };
  }

  if (["failed", "error", "offline", "unavailable"].includes(status)) {
    return { label: "Unavailable", value: status, className: "danger" };
  }

  return { label: "Unknown", value: status, className: "neutral" };
}

function getSelectedDataLabel(selectedUpload) {
  return (
    selectedUpload?.name ||
    selectedUpload?.title ||
    selectedUpload?.filename ||
    selectedUpload?.original_filename ||
    selectedUpload?.upload_id ||
    "None"
  );
}

function getSelectedDataKind(selectedUpload) {
  const raw =
    selectedUpload?.kind ||
    selectedUpload?.type ||
    selectedUpload?.data_type ||
    selectedUpload?.metadata?.kind ||
    "";

  const value = String(raw).toLowerCase();

  if (value.includes("vector")) return "Vector";
  if (value.includes("raster")) return "Raster";
  if (value.includes("table")) return "Table";
  if (value.includes("database")) return "Database";
  if (value.includes("wms")) return "WMS";
  if (value.includes("wfs")) return "WFS";

  return selectedUpload ? "Data Source" : "—";
}

function SettingsCard({ icon, iconClass, title, subtitle, badge, meta, extra, actions }) {
  return (
    <article className="ds-card">
      <div className="ds-card-main">
        <div className={`ds-kind-icon ${iconClass}`}>{icon}</div>

        <div className="ds-card-info">
          <div className="ds-card-title-row">
            <strong title={title}>{title}</strong>
            {badge ? <span className="ds-active-badge">{badge}</span> : null}
          </div>

          {subtitle ? <div className="ds-card-subtitle">{subtitle}</div> : null}

          {meta?.length ? (
            <div className="ds-meta-grid">
              {meta.map((item) => (
                <span key={item.label}>
                  <small>{item.label}</small>
                  <b className={item.statusClass ? `ds-status ${item.statusClass}` : ""}>
                    {item.value}
                  </b>
                </span>
              ))}
            </div>
          ) : null}

          {extra?.length ? (
            <div className="ds-extra-line">
              {extra.map((value, index) => (
                <span key={index}>{value}</span>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      {actions ? <div className="ds-card-actions">{actions}</div> : null}
    </article>
  );
}

export default function SettingsPanel({
  health,
  activeProject,
  selectedUpload,
  requestsCount = 0,
}) {
  const status = normalizeStatus(health?.status);

  const pluginModuleCount = Array.isArray(health?.plugin_modules)
    ? health.plugin_modules.length
    : health?.plugins_count || health?.plugin_count || "—";

  const weightedRouter =
    health?.use_weighted_router === false ? "Disabled" : "Enabled";

  const weightsPersistence =
    health?.weights_persistence_exists === true
      ? "Available"
      : health?.weights_persistence_exists === false
        ? "Not saved"
        : "Unknown";

  const historySize = Number.isFinite(Number(health?.history_size))
    ? Number(health.history_size)
    : requestsCount;

  return (
    <div className="ds-list-pro">
      <SettingsCard
        icon="⚙"
        iconClass="data"
        title="System Runtime"
        subtitle={<><span>Settings</span><span dir="ltr">workspace</span></>}
        badge={status.label}
        meta={[
          { label: "API", value: status.label, statusClass: status.className },
          { label: "Requests", value: requestsCount },
          {
            label: "Project",
            value: activeProject?.name || "None",
          },
          {
            label: "Selected",
            value: getSelectedDataKind(selectedUpload),
          },
        ]}
        extra={[
          activeProject?.project_id
            ? `project: ${shortId(activeProject.project_id)}`
            : "no active project",
          `data: ${getSelectedDataLabel(selectedUpload)}`,
        ]}
      />

      <SettingsCard
        icon="▤"
        iconClass="database"
        title="Execution Environment"
        subtitle={<><span>Runtime engine</span><span dir="ltr">router</span></>}
        meta={[
          {
            label: "Weighted Router",
            value: weightedRouter,
            statusClass: health?.use_weighted_router === false ? "running" : "success",
          },
          {
            label: "Weights",
            value: weightsPersistence,
            statusClass: health?.weights_persistence_exists ? "success" : "running",
          },
          { label: "Plugin Modules", value: pluginModuleCount },
          { label: "History", value: historySize },
        ]}
      />

      <SettingsCard
        icon="◎"
        iconClass="online"
        title="LLM & Planning"
        subtitle={<><span>Planning</span><span dir="ltr">next phase</span></>}
        meta={[
          { label: "Provider", value: "—" },
          { label: "Model", value: "—" },
          { label: "Base URL", value: "—" },
          { label: "Status", value: "Planned", statusClass: "neutral" },
        ]}
        extra={["تنظیم provider، model و base URL در فاز بعدی فعال می‌شود."]}
      />

      <SettingsCard
        icon="▦"
        iconClass="raster"
        title="Data & Output Policy"
        subtitle={<><span>Defaults</span><span dir="ltr">policy</span></>}
        meta={[
          { label: "Raster CRS", value: "—" },
          { label: "Vector CRS", value: "—" },
          { label: "Output", value: "—" },
          { label: "Status", value: "Planned", statusClass: "neutral" },
        ]}
        extra={["پیش‌فرض‌های CRS، فرمت خروجی و سیاست نگهداری فایل‌ها بعداً اضافه می‌شود."]}
      />
    </div>
  );
}
