import { useEffect, useMemo, useState } from "react";
import {
  getRuntimeSettings,
  listPlugins,
  updatePluginState,
} from "../api/client";

function safeList(value) {
  return Array.isArray(value) ? value : [];
}

function normalizePlugins(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.plugins)) return payload.plugins;
  return [];
}

function pluginStatus(plugin) {
  if (plugin?.skipped) return "skipped";
  return plugin?.enabled === false ? "disabled" : "enabled";
}

function PluginBadge({ children, tone = "neutral" }) {
  return <span className={`pm-badge pm-${tone}`}>{children}</span>;
}

function SummaryStat({ label, value, tone = "neutral" }) {
  return (
    <div className={`pm-stat pm-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function CapabilityDetails({ capabilities }) {
  const items = safeList(capabilities);

  if (!items.length) {
    return <div className="pm-empty">No registered capabilities</div>;
  }

  return (
    <div className="pm-capability-list">
      {items.map((capability, index) => (
        <div
          key={capability?.name || `capability-${index}`}
          className="pm-capability-item"
        >
          <div className="pm-capability-head">
            <strong dir="ltr">{capability?.name || "unknown_capability"}</strong>
            <PluginBadge tone="info">{capability?.output_kind || "json"}</PluginBadge>
          </div>

          <div className="pm-capability-meta">
            {!!safeList(capability?.required_inputs).length && (
              <div className="pm-capability-line">
                <span>Required</span>
                <code dir="ltr">{safeList(capability.required_inputs).join(", ")}</code>
              </div>
            )}

            {!!safeList(capability?.optional_inputs).length && (
              <div className="pm-capability-line">
                <span>Optional</span>
                <code dir="ltr">{safeList(capability.optional_inputs).join(", ")}</code>
              </div>
            )}
          </div>

          {!!safeList(capability?.keywords).length && (
            <div className="pm-keywords">
              {safeList(capability.keywords).slice(0, 8).map((keyword, keywordIndex) => (
                <span
                  key={`${capability?.name || "cap"}-${keyword}-${keywordIndex}`}
                  className="pm-keyword"
                  dir="ltr"
                >
                  {keyword}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function PluginManagerPanel() {
  const [plugins, setPlugins] = useState([]);
  const [runtime, setRuntime] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [busyId, setBusyId] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function loadAll({ silent = false } = {}) {
    if (!silent) {
      setLoading(true);
    } else {
      setRefreshing(true);
    }

    setError("");

    try {
      const [pluginsRes, runtimeRes] = await Promise.all([
        listPlugins(),
        getRuntimeSettings(),
      ]);

      setPlugins(normalizePlugins(pluginsRes));
      setRuntime(runtimeRes || null);
    } catch (err) {
      setError(err?.message || "Failed to load plugin manager.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  async function handleToggle(plugin) {
    const pluginId = String(plugin?.plugin_id || "").trim();
    if (!pluginId || plugin?.skipped) return;

    const nextEnabled = !(plugin?.enabled !== false);

    setBusyId(pluginId);
    setError("");

    try {
      await updatePluginState(pluginId, nextEnabled);
      await loadAll({ silent: true });
    } catch (err) {
      setError(err?.message || "Plugin state update failed.");
    } finally {
      setBusyId("");
    }
  }

  const summary = useMemo(() => {
    const items = safeList(plugins);
    const total = items.length;
    const enabled = items.filter((item) => pluginStatus(item) === "enabled").length;
    const disabled = items.filter((item) => pluginStatus(item) === "disabled").length;
    const skipped = items.filter((item) => pluginStatus(item) === "skipped").length;
    return { total, enabled, disabled, skipped };
  }, [plugins]);

  const disabledPluginIds = safeList(runtime?.plugins?.disabled_plugin_ids);
  const enabledCapabilities = safeList(runtime?.plugins?.enabled_capabilities);

  return (
    <div className="settings-card settings-card-pro pm-panel">
      <div className="pm-header">
        <div>
          <div className="pm-title">Plugin Manager</div>
          <p className="pm-subtitle">مدیریت وضعیت پلاگین‌ها و قابلیت‌های سیستم</p>
        </div>

        <button
          type="button"
          className="pm-refresh-btn"
          onClick={() => loadAll({ silent: true })}
          disabled={loading || refreshing}
        >
          {refreshing ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {error ? <div className="pm-error">{error}</div> : null}

      <div className="pm-stats">
        <SummaryStat label="Total" value={summary.total} />
        <SummaryStat label="Enabled" value={summary.enabled} tone="success" />
        <SummaryStat label="Disabled" value={summary.disabled} tone="warning" />
        <SummaryStat label="Skipped" value={summary.skipped} tone="danger" />
      </div>

      <div className="pm-runtime">
        <div className="pm-runtime-item">
          <span>Disabled plugins</span>
          <strong dir="ltr">
            {disabledPluginIds.length ? disabledPluginIds.join(", ") : "—"}
          </strong>
        </div>
        <div className="pm-runtime-item">
          <span>Enabled capabilities</span>
          <strong>{enabledCapabilities.length}</strong>
        </div>
      </div>

      {loading ? (
        <div className="pm-empty">Loading plugin inventory...</div>
      ) : !plugins.length ? (
        <div className="pm-empty">No plugins found.</div>
      ) : (
        <div className="pm-list">
          {plugins.map((plugin, index) => {
            const id = plugin?.plugin_id || `unknown_plugin_${index}`;
            const status = pluginStatus(plugin);
            const expanded = expandedId === id;
            const isBusy = busyId === id;
            const moduleNames = safeList(plugin?.module_names);

            return (
              <div key={id} className={`pm-row pm-row-${status}`}>
                <div className="pm-row-main">
                  <div className="pm-row-title-wrap">
                    <strong className="pm-row-title" dir="ltr">
                      {id}
                    </strong>

                    <div className="pm-row-meta">
                      <span>State source: {plugin?.state_source || "—"}</span>
                      <span dir="ltr">Config: {plugin?.config_path || "—"}</span>
                      <span>{Number(plugin?.capability_count || 0)} capabilities</span>
                    </div>

                    {!!moduleNames.length && (
                      <div className="pm-row-modules" dir="ltr">
                        {moduleNames.join(", ")}
                      </div>
                    )}
                  </div>

                  <div className="pm-row-side">
                    <div className="pm-row-badges">
                      {status === "enabled" ? (
                        <PluginBadge tone="success">Enabled</PluginBadge>
                      ) : null}

                      {status === "disabled" ? (
                        <PluginBadge tone="warning">Disabled</PluginBadge>
                      ) : null}

                      {status === "skipped" ? (
                        <PluginBadge tone="danger">Skipped</PluginBadge>
                      ) : null}

                      <PluginBadge tone={plugin?.config_exists ? "info" : "neutral"}>
                        {plugin?.config_exists ? "Config ready" : "No config"}
                      </PluginBadge>
                    </div>

                    <div className="pm-row-actions">
                      <button
                        type="button"
                        className="pm-btn"
                        onClick={() => setExpandedId(expanded ? null : id)}
                      >
                        {expanded ? "Hide details" : "Details"}
                      </button>

                      <button
                        type="button"
                        className={`pm-btn pm-btn-primary ${
                          plugin?.enabled === false ? "is-off" : "is-on"
                        }`}
                        onClick={() => handleToggle(plugin)}
                        disabled={isBusy || plugin?.skipped}
                      >
                        {isBusy
                          ? "Saving..."
                          : plugin?.enabled === false
                            ? "Enable"
                            : "Disable"}
                      </button>
                    </div>
                  </div>
                </div>

                {plugin?.skipped && plugin?.skipped_error ? (
                  <div className="pm-skipped">
                    <span>Skipped reason</span>
                    <code dir="ltr">{plugin.skipped_error}</code>
                  </div>
                ) : null}

                {expanded ? (
                  <div className="pm-details">
                    <CapabilityDetails capabilities={plugin?.capabilities} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
