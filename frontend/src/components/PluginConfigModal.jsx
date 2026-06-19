import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getPluginConfig, updatePluginConfig } from "../api/client";

/* ── helpers ── */
function parseSimpleYaml(raw) {
  const result = [];
  const seen   = new Set();
  for (const line of String(raw || "").split("\n")) {
    if (line.startsWith(" ") || line.startsWith("\t")) continue;
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const ci = t.indexOf(":");
    if (ci === -1) continue;
    const key   = t.slice(0, ci).trim();
    const value = t.slice(ci + 1).trim();
    if (!key || seen.has(key)) continue;
    if (value.startsWith("{") || value.startsWith("[")) continue;
    seen.add(key);
    result.push({ key, value });
  }
  return result;
}

function patchYaml(raw, key, val) {
  const pat = new RegExp(`^(${key.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")}\\s*:\\s*)(.*)$`, "m");
  if (pat.test(raw)) return raw.replace(pat, (_, p) => `${p}${val}`);
  return (raw.endsWith("\n") ? raw : raw + "\n") + `${key}: ${val}\n`;
}

/* ── Modal inner ── */
function ModalContent({ pluginId, onClose }) {
  const [tab,        setTab]        = useState("form");
  const [loading,    setLoading]    = useState(true);
  const [saving,     setSaving]     = useState(false);
  const [error,      setError]      = useState("");
  const [saveResult, setSaveResult] = useState(null);
  const [rawYaml,    setRawYaml]    = useState("");
  const [fields,     setFields]     = useState([]);
  const [exampleYaml,setExampleYaml]= useState(null);
  const [configPath, setConfigPath] = useState("");
  const backdropRef = useRef(null);

  useEffect(() => {
    setLoading(true); setError(""); setSaveResult(null);
    getPluginConfig(pluginId)
      .then(data => {
        const yaml = data?.raw_yaml || "";
        setRawYaml(yaml);
        setFields(parseSimpleYaml(yaml));
        setExampleYaml(data?.example_raw_yaml || null);
        setConfigPath(data?.path || `config/plugins/${pluginId}.yaml`);
      })
      .catch(e => setError(e?.message || "Failed to load config."))
      .finally(() => setLoading(false));
  }, [pluginId]);

  useEffect(() => {
    const fn = e => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [onClose]);

  function switchTab(next) {
    if (tab === "form" && next === "raw") {
      let u = rawYaml;
      for (const f of fields) u = patchYaml(u, f.key, f.value);
      setRawYaml(u);
    } else if (tab === "raw" && next === "form") {
      setFields(parseSimpleYaml(rawYaml));
    }
    setTab(next);
  }

  async function handleSave() {
    setSaving(true); setSaveResult(null);
    let yaml = rawYaml;
    if (tab === "form") for (const f of fields) yaml = patchYaml(yaml, f.key, f.value);
    try {
      const res = await updatePluginConfig(pluginId, { raw_yaml: yaml });
      setSaveResult({ ok: true, backup: res?.backup_path || null });
      setRawYaml(yaml);
      setFields(parseSimpleYaml(yaml));
    } catch (e) {
      setSaveResult({ ok: false, message: e?.message || "Save failed." });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="app-modal-backdrop"
      ref={backdropRef}
      onClick={e => { if (e.target === backdropRef.current) onClose(); }}
    >
      <div className="app-modal">

        {/* ── Header ── */}
        <div className="app-modal-header">
          <div>
            <h3 dir="ltr" style={{ margin:"0 0 4px" }}>{pluginId}</h3>
            <p dir="ltr" style={{ margin:0, fontSize:".8rem" }}>{configPath}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              width:34, height:34, borderRadius:10,
              border:"1px solid var(--border)",
              background:"rgba(255,255,255,.7)",
              cursor:"pointer", fontSize:".8rem",
              color:"#64748b", flexShrink:0,
            }}
          >✕</button>
        </div>

        {/* ── Tabs ── */}
        <div style={{
          display:"flex", gap:6, padding:"12px 22px 0",
          borderBottom:"1px solid var(--border)",
        }}>
          {[["form","Form"],["raw","Raw YAML"]].map(([v,l]) => (
            <button
              key={v} type="button"
              onClick={() => switchTab(v)}
              style={{
                height:34, padding:"0 14px",
                borderRadius:"9px 9px 0 0",
                border:"1px solid transparent",
                borderBottom: v === tab ? "1px solid rgba(255,250,240,.98)" : "1px solid transparent",
                background: v === tab ? "rgba(255,250,240,.98)" : "transparent",
                color: v === tab ? "#0f172a" : "#64748b",
                cursor:"pointer", fontSize:".84rem", fontWeight:750,
                position:"relative", bottom:"-1px",
              }}
            >{l}</button>
          ))}
          {exampleYaml ? (
            <button
              type="button"
              onClick={() => { setRawYaml(exampleYaml); setFields(parseSimpleYaml(exampleYaml)); setSaveResult(null); }}
              style={{
                marginLeft:"auto", marginBottom:10,
                height:28, padding:"0 11px", borderRadius:999,
                border:"1px solid var(--border)",
                background:"#f8fafc",
                color:"#64748b", cursor:"pointer",
                fontSize:".74rem", fontWeight:850,
              }}
            >Load example</button>
          ) : null}
        </div>

        {/* ── Body ── */}
        <div className="app-modal-body">

          {loading ? (
            <div style={{ textAlign:"center", color:"var(--muted)", padding:"24px 0" }}>
              Loading config...
            </div>
          ) : error ? (
            <div style={{
              padding:"12px 14px", borderRadius:14,
              border:"1px solid #fecaca",
              background:"#fef2f2", color:"#b91c1c",
              fontSize:".86rem",
            }}>{error}</div>
          ) : tab === "form" ? (
            fields.length === 0 ? (
              <div style={{ textAlign:"center", color:"var(--muted)", padding:"20px 0" }}>
                No editable fields found.<br />
                <span style={{ fontSize:".8rem" }}>Switch to Raw YAML to edit manually.</span>
              </div>
            ) : (
              <div className="modal-form">
                {fields.map(f => (
                  <label key={f.key}>
                    <span style={{ fontSize:".8rem", fontWeight:850, color:"#475569" }} dir="ltr">
                      {f.key}
                    </span>
                    <input
                      dir="ltr"
                      value={f.value}
                      onChange={e => {
                        setFields(prev => prev.map(x => x.key===f.key ? {...x,value:e.target.value} : x));
                        setSaveResult(null);
                      }}
                      spellCheck={false}
                    />
                  </label>
                ))}
              </div>
            )
          ) : (
            <textarea
              dir="ltr"
              value={rawYaml}
              onChange={e => { setRawYaml(e.target.value); setSaveResult(null); }}
              spellCheck={false}
              placeholder={`# ${pluginId} config\nkey: value\n`}
              style={{
                width:"100%", minHeight:260,
                borderRadius:14, border:"1px solid var(--border)",
                background:"rgba(255,255,255,.78)",
                padding:"12px 14px",
                color:"var(--text)",
                fontFamily:'"JetBrains Mono","Fira Code",monospace',
                fontSize:".82rem", lineHeight:1.75,
                resize:"vertical", outline:"none",
                boxSizing:"border-box",
              }}
            />
          )}

          {/* save result */}
          {saveResult ? (
            <div style={{
              marginTop:12, padding:"11px 14px",
              borderRadius:14,
              border: saveResult.ok ? "1px solid #bbf7d0" : "1px solid #fecaca",
              background: saveResult.ok ? "#f0fdf4" : "#fef2f2",
              color: saveResult.ok ? "#047857" : "#b91c1c",
              fontSize:".84rem", display:"flex",
              flexDirection:"column", gap:4,
            }}>
              <span>{saveResult.ok ? "✓ Saved successfully." : `✗ ${saveResult.message}`}</span>
              {saveResult.ok && saveResult.backup ? (
                <small dir="ltr" style={{ color:"#64748b", fontSize:".74rem" }}>
                  Backup → {saveResult.backup}
                </small>
              ) : null}
              <button
                type="button"
                onClick={() => setSaveResult(null)}
                style={{
                  alignSelf:"flex-start", marginTop:4,
                  height:26, padding:"0 10px", borderRadius:8,
                  border:"1px solid var(--border)",
                  background:"rgba(255,255,255,.7)",
                  color:"#64748b", cursor:"pointer", fontSize:".74rem",
                }}
              >Dismiss</button>
            </div>
          ) : null}

        </div>

        {/* ── Footer ── */}
        <div className="app-modal-footer">
          <button
            type="button"
            onClick={onClose}
            style={{
              height:40, padding:"0 18px", borderRadius:13,
              border:"1px solid var(--border)",
              background:"rgba(255,255,255,.8)",
              color:"#475569", cursor:"pointer",
              fontWeight:850, fontSize:".86rem",
            }}
          >Cancel</button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || loading || !!error}
            style={{
              height:40, padding:"0 22px", borderRadius:13,
              border:"1px solid rgba(37,99,235,.3)",
              background: saving||loading||error ? "#e2e8f0" : "rgba(37,99,235,.12)",
              color: saving||loading||error ? "#94a3b8" : "#1d4ed8",
              cursor: saving||loading||error ? "not-allowed" : "pointer",
              fontWeight:950, fontSize:".86rem",
              transition:"140ms",
            }}
          >
            {saving ? "Saving..." : "Save Config"}
          </button>
        </div>

      </div>
    </div>
  );
}

export default function PluginConfigModal({ pluginId, onClose }) {
  if (!pluginId) return null;
  return createPortal(
    <ModalContent pluginId={pluginId} onClose={onClose} />,
    document.body
  );
}
