import { useEffect, useMemo, useState } from "react";
import { getRuntimeSettings, listPlugins, updatePluginState } from "../api/client";
import PluginConfigModal from "./PluginConfigModal";

function safeList(v) { return Array.isArray(v) ? v : []; }

function normalizePlugins(p) {
  if (Array.isArray(p)) return p;
  if (Array.isArray(p?.items)) return p.items;
  if (Array.isArray(p?.plugins)) return p.plugins;
  return [];
}

function pluginStatus(p) {
  if (p?.skipped) return "skipped";
  return p?.enabled === false ? "disabled" : "enabled";
}

function CapabilityRow({ cap }) {
  const required = safeList(cap?.required_inputs);
  const optional = safeList(cap?.optional_inputs);
  return (
    <div className="pm-cap-row">
      <div className="ds-card-title-row">
        <strong style={{ fontSize: 11 }} dir="ltr">{cap?.name || "unknown"}</strong>
        <span className="ds-active-badge">{cap?.output_kind || "json"}</span>
      </div>
      {(required.length || optional.length) ? (
        <div className="ds-meta-grid" style={{ marginTop: 6 }}>
          {required.length ? (
            <span>
              <small>Required</small>
              <b dir="ltr" style={{ whiteSpace:"normal", wordBreak:"break-all" }}>
                {required.join(", ")}
              </b>
            </span>
          ) : null}
          {optional.length ? (
            <span>
              <small>Optional</small>
              <b dir="ltr" style={{ whiteSpace:"normal", wordBreak:"break-all" }}>
                {optional.join(", ")}
              </b>
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function PluginCard({ plugin, onToggleState, onConfigure, isBusy }) {
  const id      = plugin?.plugin_id || "unknown";
  const status  = pluginStatus(plugin);
  const caps    = safeList(plugin?.capabilities);
  const modules = safeList(plugin?.module_names);

  const statusLabel = { enabled:"Enabled", disabled:"Disabled", skipped:"Skipped" }[status] || status;
  const statusCls   = { enabled:"success",  disabled:"running",  skipped:"danger"  }[status] || "neutral";

  return (
    <article className="ds-card">

      {/* ── info block (بدون آیکون) ── */}
      <div className="ds-card-info" style={{ padding: "2px 0" }}>

        <div className="ds-card-title-row">
          <strong dir="ltr">{id}</strong>
          <span className="ds-active-badge">{statusLabel}</span>
        </div>

        <div className="ds-card-subtitle">
          <span dir="ltr">{plugin?.capability_count ?? 0} capabilities</span>
          {plugin?.config_exists
            ? <span>config ✓</span>
            : <span style={{ color:"#94a3b8" }}>no config</span>}
          {plugin?.skipped ? <span style={{ color:"#b91c1c" }}>skipped</span> : null}
        </div>

        {/* meta grid */}
        <div className="ds-meta-grid">
          <span>
            <small>Status</small>
            <b className={`ds-status ${statusCls}`}>{statusLabel}</b>
          </span>
          <span>
            <small>Config path</small>
            <b dir="ltr" style={{ whiteSpace:"normal", wordBreak:"break-all", fontSize:10 }}>
              {plugin?.config_path || "—"}
            </b>
          </span>
          {modules.length ? (
            <span style={{ gridColumn:"1/-1" }}>
              <small>Modules</small>
              <b dir="ltr" style={{ whiteSpace:"normal", wordBreak:"break-all" }}>
                {modules.join(", ")}
              </b>
            </span>
          ) : null}
          {plugin?.skipped && plugin?.skipped_error ? (
            <span style={{ gridColumn:"1/-1" }}>
              <small>Skipped reason</small>
              <b dir="ltr" style={{ color:"#b91c1c", whiteSpace:"normal", wordBreak:"break-all" }}>
                {plugin.skipped_error}
              </b>
            </span>
          ) : null}
        </div>

        {/* capabilities */}
        {caps.length ? (
          <div className="pm-caps-section">
            <div className="pm-caps-label">Capabilities</div>
            <div className="pm-caps-list">
              {caps.map((cap, i) => (
                <CapabilityRow key={cap?.name || i} cap={cap} />
              ))}
            </div>
          </div>
        ) : (
          <div className="ds-extra-line">
            <span style={{ color:"#94a3b8" }}>No capabilities registered.</span>
          </div>
        )}

      </div>

      {/* actions */}
      <div className="ds-card-actions">
        <button type="button" className="configure" onClick={onConfigure}>
          Configure
        </button>
        <button
          type="button"
          onClick={onToggleState}
          disabled={isBusy || plugin?.skipped}
        >
          {isBusy ? "..." : status === "disabled" ? "Enable" : "Disable"}
        </button>
      </div>

    </article>
  );
}

export default function PluginManagerPanel() {
  const [plugins, setPlugins]             = useState([]);
  const [runtime, setRuntime]             = useState(null);
  const [busyId, setBusyId]               = useState("");
  const [loading, setLoading]             = useState(true);
  const [refreshing, setRefreshing]       = useState(false);
  const [error, setError]                 = useState("");
  const [query, setQuery]                 = useState("");
  const [filter, setFilter]               = useState("all");
  const [configModalId, setConfigModalId] = useState(null);

  async function loadAll({ silent = false } = {}) {
    if (!silent) setLoading(true); else setRefreshing(true);
    setError("");
    try {
      const [pr, rr] = await Promise.all([listPlugins(), getRuntimeSettings()]);
      setPlugins(normalizePlugins(pr));
      setRuntime(rr || null);
    } catch (e) {
      setError(e?.message || "Failed to load plugins.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    Promise.all([listPlugins(), getRuntimeSettings()])
      .then(([pr, rr]) => {
        setPlugins(normalizePlugins(pr));
        setRuntime(rr || null);
      })
      .catch((e) => setError(e?.message || "Failed to load plugins."))
      .finally(() => setLoading(false));
  }, []);

  async function handleToggleState(plugin) {
    const id = String(plugin?.plugin_id || "").trim();
    if (!id || plugin?.skipped) return;
    setBusyId(id);
    try {
      await updatePluginState(id, !(plugin?.enabled !== false));
      await loadAll({ silent: true });
    } catch (e) {
      setError(e?.message || "Update failed.");
    } finally {
      setBusyId("");
    }
  }

  const summary = useMemo(() => {
    const all = safeList(plugins);
    return {
      total:    all.length,
      enabled:  all.filter(p => pluginStatus(p) === "enabled").length,
      disabled: all.filter(p => pluginStatus(p) === "disabled").length,
      skipped:  all.filter(p => pluginStatus(p) === "skipped").length,
    };
  }, [plugins]);

  const filtered = useMemo(() => {
    const txt = query.trim().toLowerCase();
    return safeList(plugins)
      .filter(p => {
        if (filter !== "all" && pluginStatus(p) !== filter) return false;
        if (!txt) return true;
        return [
          p?.plugin_id,
          ...safeList(p?.module_names),
          ...safeList(p?.capabilities).map(c => c?.name),
        ].filter(Boolean).join(" ").toLowerCase().includes(txt);
      })
      .sort((a, b) => String(a?.plugin_id||"").localeCompare(String(b?.plugin_id||"")));
  }, [plugins, query, filter]);

  const enabledCaps = safeList(runtime?.plugins?.enabled_capabilities);

  return (
    <>
    <div className="ds-list-pro">

      {/* Summary */}
      <article className="ds-card">
        <div className="ds-card-info" style={{ padding:"2px 0" }}>
          <div className="ds-card-title-row">
            <strong>Plugin Manager</strong>
            <span className="ds-active-badge">{summary.total} plugins</span>
          </div>
          <div className="ds-card-subtitle">
            <span>System plugins</span>
            <span>{enabledCaps.length} capabilities active</span>
          </div>
          <div className="ds-meta-grid">
            <span><small>Enabled</small> <b className="ds-status success">{summary.enabled}</b></span>
            <span><small>Disabled</small><b className="ds-status running">{summary.disabled}</b></span>
            <span><small>Skipped</small> <b className="ds-status danger">{summary.skipped}</b></span>
            <span><small>Capabilities</small><b>{enabledCaps.length}</b></span>
          </div>
        </div>
        <div className="ds-card-actions">
          <button type="button" onClick={() => loadAll({ silent:true })} disabled={loading||refreshing}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </article>

      {error ? (
        <article className="ds-card">
          <div className="ds-card-info" style={{ padding:"2px 0" }}>
            <div className="ds-card-title-row"><strong>Error</strong></div>
            <div className="ds-extra-line"><span style={{ color:"#b91c1c" }}>{error}</span></div>
          </div>
        </article>
      ) : null}

      {/* Toolbar */}
      <div className="pm-toolbar">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search plugins..."
          dir="ltr"
        />
        <div className="pm-filter-chips">
          {[["all","All"],["enabled","Enabled"],["disabled","Disabled"],["skipped","Skipped"]].map(([v,l]) => (
            <button key={v} type="button"
              className={filter===v ? "active" : ""}
              onClick={() => setFilter(v)}
            >{l}</button>
          ))}
        </div>
      </div>

      {/* List */}
      {loading ? (
        <article className="ds-card">
          <div className="ds-card-info" style={{ padding:"2px 0" }}>
            <div className="ds-extra-line"><span>Loading plugin inventory...</span></div>
          </div>
        </article>
      ) : !filtered.length ? (
        <article className="ds-card">
          <div className="ds-card-info" style={{ padding:"2px 0" }}>
            <div className="ds-extra-line"><span>No plugins matched.</span></div>
          </div>
        </article>
      ) : filtered.map((plugin, idx) => {
        const id = plugin?.plugin_id || `unknown_${idx}`;
        return (
          <PluginCard
            key={id}
            plugin={plugin}
            isBusy={busyId === id}
            onToggleState={() => handleToggleState(plugin)}
            onConfigure={() => setConfigModalId(id)}
          />
        );
      })}

    </div>

    {configModalId ? (
      <PluginConfigModal
        pluginId={configModalId}
        onClose={() => setConfigModalId(null)}
      />
    ) : null}
    </>
  );
}
