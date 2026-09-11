import { useMemo, useState } from "react";
import { API_BASE_URL } from "../api/client";

function asArray(value) {
  if (!value) return [];
  return Array.isArray(value) ? value : [value];
}

function shortId(value) {
  if (!value) return "—";
  const text = String(value);
  if (text.length <= 18) return text;
  return `${text.slice(0, 10)}…${text.slice(-6)}`;
}

function formatInspectorValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) {
    return value
      .map((item) =>
        item && typeof item === "object" ? JSON.stringify(item) : String(item),
      )
      .join(", ");
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function buildDocumentHref(value) {
  if (!value) return "";
  const href = String(value);
  if (/^https?:\/\//i.test(href)) return href;
  if (href.startsWith("/")) {
    return `${String(API_BASE_URL || "").replace(/\/$/, "")}${href}`;
  }
  return href;
}

function getLayerKey(layer, index) {
  return String(layer?.id || layer?.name || layer?.title || `layer-${index}`);
}

function normalizeStatus(response, activeRequest, loading, error) {
  if (loading) return "running";
  if (error) return "failed";
  return (
    response?.status ||
    activeRequest?.status ||
    response?.run_result?.status ||
    (response?.ok === true ? "succeeded" : response?.ok === false ? "failed" : "idle")
  );
}

function statusMeta(status) {
  const normalized = String(status || "idle").toLowerCase();

  if (normalized === "succeeded" || normalized === "success" || normalized === "completed") {
    return {
      label: "Succeeded",
      icon: "✓",
      className: "success",
    };
  }

  if (normalized === "failed" || normalized === "error") {
    return {
      label: "Failed",
      icon: "!",
      className: "danger",
    };
  }

  if (normalized === "running" || normalized === "pending" || normalized === "analyzing") {
    return {
      label: "Running",
      icon: "●",
      className: "running",
    };
  }

  return {
    label: "Idle",
    icon: "○",
    className: "neutral",
  };
}

function isFeatureCollection(value) {
  return (
    value &&
    typeof value === "object" &&
    value.type === "FeatureCollection" &&
    Array.isArray(value.features)
  );
}

function featureCountFromLayer(layer) {
  if (Number.isFinite(layer?.summary?.feature_count)) {
    return layer.summary.feature_count;
  }

  if (isFeatureCollection(layer?.geojson)) {
    return layer.geojson.features.length;
  }

  return 0;
}

function geometryCountsFromLayer(layer) {
  if (layer?.summary?.geometry_counts && typeof layer.summary.geometry_counts === "object") {
    return layer.summary.geometry_counts;
  }

  const counts = {};

  if (!isFeatureCollection(layer?.geojson)) return counts;

  for (const feature of layer.geojson.features) {
    const type = feature?.geometry?.type || "Unknown";
    counts[type] = (counts[type] || 0) + 1;
  }

  return counts;
}

function collectLayers(response, mapLayers) {
  const result = [];

  const pushLayer = (layer, source) => {
    if (!layer || typeof layer !== "object") return;

    const geojson =
      layer.geojson ||
      layer.payload?.geojson ||
      layer.result?.geojson ||
      layer.data?.geojson;

    const normalized = {
      ...layer,
      geojson,
      source: layer.source || source,
      id: layer.id || layer.name || `${source}-${result.length + 1}`,
      name: layer.name || layer.id || `Layer ${result.length + 1}`,
      type: layer.type || "vector",
      format: layer.format || "geojson",
      visible: layer.visible !== false,
    };

    if (isFeatureCollection(normalized.geojson)) {
      result.push(normalized);
    }
  };

  for (const layer of asArray(mapLayers?.layers)) {
    pushLayer(layer, "workspace");
  }

  for (const layer of asArray(response?.layers)) {
    pushLayer(layer, "response.layers");
  }

  for (const vector of asArray(response?.outputs?.vectors)) {
    pushLayer(vector, "response.outputs.vectors");
  }

  if (response?.result?.geojson) {
    pushLayer(
      {
        id: "result-geojson",
        name: "Result GeoJSON",
        geojson: response.result.geojson,
        summary: response.result.summary,
      },
      "response.result.geojson",
    );
  }

  const seen = new Set();
  return result.filter((layer) => {
    const key = `${layer.id}:${featureCountFromLayer(layer)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function collectOutputs(response, outputManifest) {
  const files = [];

  const pushFile = (file, source) => {
    if (!file) return;

    if (typeof file === "string") {
      files.push({
        name: file,
        path: file,
        source,
      });
      return;
    }

    files.push({
      ...file,
      name: file.name || file.filename || file.path || file.url || `Output ${files.length + 1}`,
      source: file.source || source,
    });
  };

  for (const file of asArray(outputManifest?.files)) {
    pushFile(file, "manifest.files");
  }

  for (const file of asArray(outputManifest?.outputs)) {
    pushFile(file, "manifest.outputs");
  }

  for (const file of asArray(response?.outputs?.files)) {
    pushFile(file, "response.outputs.files");
  }

  for (const file of asArray(response?.output_files)) {
    pushFile(file, "response.output_files");
  }

  for (const file of asArray(response?.outputs?.documents)) {
    pushFile(file, "response.outputs.documents");
  }

  for (const file of asArray(response?.inspector?.documents)) {
    pushFile(file, "response.inspector.documents");
  }

  const seen = new Set();
  const uniqueFiles = files.filter((file) => {
    const key = String(file.id || file.path || file.file_path || file.url || file.name || "");
    if (!key) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return {
    files: uniqueFiles,
    vectors: asArray(response?.outputs?.vectors),
    rasters: asArray(response?.outputs?.rasters),
    tables: asArray(response?.outputs?.tables),
    documents: asArray(response?.outputs?.documents),
    inspectorItems: asArray(response?.inspector?.outputs),
    primaryActions: asArray(response?.inspector?.primary_actions),
  };
}

function aggregateGeometryCounts(layers) {
  const total = {};

  for (const layer of layers) {
    const counts = geometryCountsFromLayer(layer);

    for (const [key, value] of Object.entries(counts)) {
      total[key] = (total[key] || 0) + Number(value || 0);
    }
  }

  return total;
}

function TabButton({ active, icon, label, count, onClick }) {
  return (
    <button
      type="button"
      className={`inspector-tab ${active ? "active" : ""}`}
      onClick={onClick}
    >
      <span className="inspector-tab-icon">{icon}</span>
      <span>{label}</span>
      {Number.isFinite(count) && count > 0 ? (
        <span className="inspector-tab-count">{count}</span>
      ) : null}
    </button>
  );
}

function MetricCard({ label, value, icon, tone = "default" }) {
  return (
    <div className={`inspector-metric ${tone}`}>
      <div className="inspector-metric-icon">{icon}</div>
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
      </div>
    </div>
  );
}

function LayerCard({
  layer,
  layerKey,
  isHidden,
  isRemoved,
  onZoom,
  onStyle,
  onToggleVisibility,
  onRemove,
}) {
  const featureCount = featureCountFromLayer(layer);
  const geometryCounts = geometryCountsFromLayer(layer);
  const geometryText = Object.entries(geometryCounts)
    .map(([key, value]) => `${key}: ${value}`)
    .join(" · ");

  return (
    <article className={`inspector-layer-card ${isRemoved ? "is-removed" : ""}`}>
      <div className="inspector-layer-main">
        <div className="inspector-layer-icon">◈</div>
        <div className="inspector-layer-info">
          <div className="inspector-layer-title-row">
            <strong>{layer.name || "Unnamed layer"}</strong>
            <span className={`layer-visibility ${isHidden ? "off" : "on"}`}>
              {isRemoved ? "Removed" : isHidden ? "Hidden" : "Visible"}
            </span>
          </div>

          <div className="inspector-layer-meta">
            <span>{layer.type || "vector"}</span>
            <span>{layer.format || "geojson"}</span>
            <span>{featureCount} features</span>
          </div>

          {geometryText ? (
            <div className="inspector-layer-geometry">{geometryText}</div>
          ) : null}
        </div>
      </div>

      <div className="inspector-layer-actions">
        <button type="button" onClick={() => onZoom(layerKey)} disabled={isRemoved}>
          ⌖ Zoom
        </button>
        <button type="button" onClick={() => onStyle(layerKey)} disabled={isRemoved}>
          🎨 Style
        </button>
        <button
          type="button"
          onClick={() => onToggleVisibility(layerKey)}
          disabled={isRemoved}
        >
          {isHidden ? "⊘ Show" : "👁 Hide"}
        </button>
        <button type="button" onClick={() => onRemove(layerKey)} disabled={isRemoved}>
          × Remove
        </button>
      </div>
    </article>
  );
}

function resolveOutputHref(file) {
  const raw =
    file?.download_url ||
    file?.preview_url ||
    file?.url ||
    file?.path ||
    file?.file_path;

  if (!raw) return "";

  const text = String(raw);

  if (
    text.startsWith("http://") ||
    text.startsWith("https://") ||
    text.startsWith("blob:") ||
    text.startsWith("data:")
  ) {
    return text;
  }

  if (text.startsWith("/")) {
    return `${API_BASE_URL}${text}`;
  }

  return text;
}

function OutputCard({ file }) {
  const label = file.label || file.name || file.id || "Output";
  const subtitle = [
    file.type,
    file.format,
    file.role,
    Number.isFinite(file.count) ? `${file.count} item(s)` : null,
    file.source,
  ].filter(Boolean).join(" · ");

  const path = resolveOutputHref(file);

  return (
    <article className="inspector-output-card">
      <div className="inspector-output-icon">
        {file.type === "document" || file.format === "pdf" ? "▣" : "⇩"}
      </div>
      <div className="inspector-output-info">
        <strong>{label}</strong>
        <span>{subtitle || "output"}</span>
        {path ? (
          <a
            className="download-link"
            href={path}
            target="_blank"
            rel="noreferrer"
            dir="ltr"
          >
            {file.format === "pdf" ? "دانلود / مشاهده PDF" : "مشاهده خروجی"}
          </a>
        ) : null}
      </div>
    </article>
  );
}


function EmptyState({ icon, title, text }) {
  return (
    <div className="inspector-empty">
      <div className="inspector-empty-icon">{icon}</div>
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}

export default function InspectorPanel({
  response,
  mapLayers,
  outputManifest,
  activeRequest,
  loading = false,
  error = "",
  layerWorkspace = null,
  onLayerWorkspaceChange = null,
}) {
  const [tab, setTab] = useState("summary");
  const [selectedInspectorTable, setSelectedInspectorTable] = useState(null);

  const status = normalizeStatus(response, activeRequest, loading, error);
  const meta = statusMeta(status);

  const layers = useMemo(
    () => collectLayers(response, mapLayers),
    [response, mapLayers],
  );

  const outputs = useMemo(
    () => collectOutputs(response, outputManifest),
    [response, outputManifest],
  );

  const inspector =
    response?.inspector && typeof response.inspector === "object"
      ? response.inspector
      : null;

  const inspectorSummaryCards = asArray(inspector?.summary_cards);
  const inspectorOutputs = asArray(inspector?.outputs);
  const inspectorTrace = asArray(inspector?.trace).length
    ? asArray(inspector?.trace)
    : asArray(response?.audit_record?.trace || response?.trace);
  const inspectorTables = asArray(inspector?.tables).length
    ? asArray(inspector?.tables)
    : outputs.tables;
  const inspectorDocuments = asArray(inspector?.documents).length
    ? asArray(inspector?.documents)
    : outputs.documents;

  const outputItems = inspectorOutputs.length ? inspectorOutputs : outputs.files;
  const fileCount = outputItems.length;

  const featureCount = layers.reduce((sum, layer) => sum + featureCountFromLayer(layer), 0);
  const geometryCounts = aggregateGeometryCounts(layers);

  const requestId =
    response?.request_id ||
    response?.id ||
    activeRequest?.request_id ||
    activeRequest?.id;

  const query =
    response?.query ||
    response?.metadata?.original_query ||
    activeRequest?.query ||
    "";

  const handler =
    response?.metadata?.direct_handler ||
    response?.direct_handler ||
    response?.audit_record?.direct_handler ||
    response?.run_result?.direct_handler ||
    "—";

  const message =
    error ||
    response?.message ||
    response?.error ||
    response?.detail ||
    "هنوز نتیجه‌ای برای نمایش وجود ندارد.";

  const steps = inspectorTrace.length
    ? inspectorTrace.map((step, index) => ({
        label: step?.label || step?.capability_name || `Step ${index + 1}`,
        step: step?.capability_name,
        message: step?.status || "",
      }))
    : asArray(response?.steps || response?.audit?.steps || response?.run_result?.steps);

  const hiddenLayerKeys = layerWorkspace?.hiddenLayerKeys || new Set();
  const removedLayerKeys = layerWorkspace?.removedLayerKeys || new Set();

  const zoomToLayer = (layerKey) => {
    if (!onLayerWorkspaceChange) return;
    onLayerWorkspaceChange((previous) => ({
      ...previous,
      fitRequest: {
        key: layerKey,
        trigger: Date.now(),
      },
    }));
  };

  const openStyleEditor = (layerKey) => {
    if (!onLayerWorkspaceChange) return;
    onLayerWorkspaceChange((previous) => ({
      ...previous,
      styleLayerKey: layerKey,
    }));
  };

  const toggleLayerVisibility = (layerKey) => {
    if (!onLayerWorkspaceChange) return;
    onLayerWorkspaceChange((previous) => {
      const next = new Set(previous.hiddenLayerKeys || []);
      if (next.has(layerKey)) next.delete(layerKey);
      else next.add(layerKey);

      return {
        ...previous,
        hiddenLayerKeys: next,
      };
    });
  };

  const removeLayerFromMap = (layerKey) => {
    if (!onLayerWorkspaceChange) return;
    onLayerWorkspaceChange((previous) => {
      const removed = new Set(previous.removedLayerKeys || []);
      removed.add(layerKey);

      return {
        ...previous,
        removedLayerKeys: removed,
        styleLayerKey: previous.styleLayerKey === layerKey ? null : previous.styleLayerKey,
      };
    });
  };

  return (
    <aside className="inspector-pro">
      <header className="inspector-pro-header">
        <div>
          <p className="inspector-kicker">Analysis Inspector</p>
          <h2>نتیجه و خروجی‌ها</h2>
        </div>

        <div className={`inspector-status ${meta.className}`}>
          <span>{meta.icon}</span>
          {meta.label}
        </div>
      </header>

      <div className="inspector-request-strip">
        <div>
          <span>Request</span>
          <strong>{shortId(requestId)}</strong>
        </div>
        <div>
          <span>Handler</span>
          <strong>{handler}</strong>
        </div>
      </div>

      <nav className="inspector-tabs" aria-label="Inspector tabs">
        <TabButton
          active={tab === "summary"}
          icon="◷"
          label="Summary"
          onClick={() => setTab("summary")}
        />
        <TabButton
          active={tab === "layers"}
          icon="▧"
          label="Layers"
          count={layers.length}
          onClick={() => setTab("layers")}
        />
        <TabButton
          active={tab === "outputs"}
          icon="⇩"
          label="Outputs"
          count={fileCount}
          onClick={() => setTab("outputs")}
        />
        <TabButton
          active={tab === "tables"}
          icon="▦"
          label="Tables"
          count={inspectorTables.length}
          onClick={() => setTab("tables")}
        />
        <TabButton
          active={tab === "documents"}
          icon="▣"
          label="Documents"
          count={inspectorDocuments.length}
          onClick={() => setTab("documents")}
        />
        <TabButton
          active={tab === "trace"}
          icon="↯"
          label="Trace"
          count={inspectorTrace.length}
          onClick={() => setTab("trace")}
        />
        <TabButton
          active={tab === "raw"}
          icon="{ }"
          label="Raw"
          onClick={() => setTab("raw")}
        />
      </nav>

      <section className="inspector-content">
        {tab === "summary" ? (
          <div className="inspector-section">
            {!response && !loading && !error ? (
              <EmptyState
                icon="◌"
                title="هنوز تحلیلی اجرا نشده است"
                text="پس از اجرای query، خلاصه نتیجه، لایه‌ها و خروجی‌ها در این پنل نمایش داده می‌شود."
              />
            ) : (
              <>
                <div className="inspector-message-card">
                  <div className={`message-dot ${meta.className}`} />
                  <div>
                    <strong>{meta.label}</strong>
                    <p>{message}</p>
                  </div>
                </div>

                {query ? (
                  <div className="inspector-query-card">
                    <span>Query</span>
                    <p>{query}</p>
                  </div>
                ) : null}

                <div className="inspector-metrics-grid">
                  {inspectorSummaryCards.length ? (
                    inspectorSummaryCards.map((card) => (
                      <MetricCard
                        key={card.id || card.label}
                        icon={card.icon || "•"}
                        label={card.label || card.id}
                        value={card.value ?? "—"}
                        tone={card.tone || "default"}
                      />
                    ))
                  ) : (
                    <>
                      <MetricCard icon="▧" label="Layers" value={layers.length} tone="blue" />
                      <MetricCard icon="•" label="Features" value={featureCount} tone="green" />
                      <MetricCard icon="⇩" label="Files" value={fileCount} tone="purple" />
                      <MetricCard icon="⚙" label="Status" value={meta.label} tone={meta.className} />
                    </>
                  )}
                </div>

                {Object.keys(geometryCounts).length ? (
                  <div className="inspector-mini-panel">
                    <div className="inspector-mini-title">Geometry summary</div>
                    <div className="geometry-chip-list">
                      {Object.entries(geometryCounts).map(([key, value]) => (
                        <span key={key} className="geometry-chip">
                          {key}
                          <strong>{value}</strong>
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}

                {steps.length ? (
                  <div className="inspector-mini-panel">
                    <div className="inspector-mini-title">Process steps</div>
                    <ol className="inspector-steps">
                      {steps.map((step, index) => (
                        <li key={`${step?.label || step?.step || index}-${index}`}>
                          <span>✓</span>
                          <div>
                            <strong>{step?.label || step?.step || `Step ${index + 1}`}</strong>
                            {step?.message ? <p>{step.message}</p> : null}
                          </div>
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
              </>
            )}
          </div>
        ) : null}

        {tab === "layers" ? (
          <div className="inspector-section">
            {layers.length ? (
              <div className="inspector-card-list">
                {layers.map((layer, index) => {
                  const layerKey = getLayerKey(layer, index);
                  return (
                    <LayerCard
                      key={layerKey}
                      layer={layer}
                      layerKey={layerKey}
                      isHidden={hiddenLayerKeys.has(layerKey)}
                      isRemoved={removedLayerKeys.has(layerKey)}
                      onZoom={zoomToLayer}
                      onStyle={openStyleEditor}
                      onToggleVisibility={toggleLayerVisibility}
                      onRemove={removeLayerFromMap}
                    />
                  );
                })}
              </div>
            ) : (
              <EmptyState
                icon="▧"
                title="لایه قابل نمایش وجود ندارد"
                text="اگر query فقط گزارش متنی تولید کند، لایه‌ای روی نقشه ساخته نمی‌شود. برای نمایش داده، query نمایشی یا تحلیلی با خروجی مکانی اجرا کنید."
              />
            )}
          </div>
        ) : null}

        {tab === "outputs" ? (
          <div className="inspector-section">
            {fileCount ? (
              <div className="inspector-card-list">
                {outputItems.map((file, index) => (
                  <OutputCard
                    key={`${file.id || file.name || "output"}-${index}`}
                    file={file}
                  />
                ))}
              </div>
            ) : layers.length ? (
              <EmptyState
                icon="▧"
                title="فایل خروجی تولید نشده است"
                text="این query یک لایه inline برای نمایش روی نقشه ساخته است. اگر فایل خروجی می‌خواهید، query را با عباراتی مثل «خروجی GeoJSON بساز» یا «نتیجه را ذخیره کن» اجرا کنید."
              />
            ) : (
              <EmptyState
                icon="⇩"
                title="هنوز خروجی تولید نشده است"
                text="پس از اجرای تحلیل‌هایی مثل استخراج، تبدیل، export یا ذخیره‌سازی، فایل‌های خروجی در این بخش نمایش داده می‌شوند."
              />
            )}
          </div>
        ) : null}

        {tab === "tables" ? (
          <div className="inspector-section">
            {inspectorTables.length ? (
              <div className="inspector-table-list">
                {inspectorTables.map((table, tableIndex) => {
                  const rows = asArray(table?.rows);
                  const columns = asArray(table?.columns).length
                    ? asArray(table.columns)
                    : Object.keys(rows[0] || {});
                  const previewRows = rows.slice(0, 4);

                  return (
                    <article
                      key={`${table?.id || table?.name || "table"}-${tableIndex}`}
                      className="inspector-table-card"
                    >
                      <div className="inspector-table-head">
                        <div>
                          <strong>{table?.name || table?.id || `Table ${tableIndex + 1}`}</strong>
                          {table?.role ? <span>{table.role}</span> : null}
                          <small>{rows.length} row(s)</small>
                        </div>

                        <button
                          type="button"
                          className="inspector-open-table-button"
                          onClick={() => setSelectedInspectorTable(table)}
                        >
                          مشاهده کامل
                        </button>
                      </div>

                      {previewRows.length && columns.length ? (
                        <>
                          <div className="inspector-table-scroll inspector-table-preview-scroll">
                            <table className="inspector-data-table inspector-data-table-preview">
                              <thead>
                                <tr>
                                  {columns.slice(0, 5).map((column) => (
                                    <th key={String(column)}>{String(column)}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {previewRows.map((row, rowIndex) => (
                                  <tr key={`${row?.id || "row"}-${rowIndex}`}>
                                    {columns.slice(0, 5).map((column) => (
                                      <td key={String(column)}>
                                        {formatInspectorValue(row?.[column])}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>

                          <p className="inspector-table-preview-note">
                            پیش‌نمایش محدود است؛ برای مشاهده همه ستون‌ها و ردیف‌ها روی «مشاهده کامل» کلیک کنید.
                          </p>
                        </>
                      ) : (
                        <p className="inspector-muted-text">این جدول ردیفی برای نمایش ندارد.</p>
                      )}
                    </article>
                  );
                })}
              </div>
            ) : (
              <EmptyState
                icon="▦"
                title="جدولی برای نمایش وجود ندارد"
                text="اگر تحلیل خروجی جدولی تولید کند، جدول‌های رتبه‌بندی یا آمار در این بخش نمایش داده می‌شوند."
              />
            )}
          </div>
        ) : null}

        {tab === "documents" ? (
          <div className="inspector-section">
            {inspectorDocuments.length ? (
              <div className="inspector-document-list">
                {inspectorDocuments.map((document, index) => {
                  const rawHref =
                    document?.download_url ||
                    document?.preview_url ||
                    document?.url ||
                    document?.path ||
                    document?.file_path;
                  const href = buildDocumentHref(rawHref);

                  return (
                    <article
                      key={`${document?.id || document?.name || "document"}-${index}`}
                      className="inspector-document-card"
                    >
                      <div className="inspector-document-main">
                        <strong>
                          {document?.name ||
                            document?.label ||
                            document?.filename ||
                            document?.id ||
                            `Document ${index + 1}`}
                        </strong>
                        <span>
                          {document?.format || "document"}
                          {document?.role ? ` · ${document.role}` : ""}
                          {document?.size_bytes ? ` · ${document.size_bytes} bytes` : ""}
                        </span>
                      </div>

                      {href ? (
                        <a
                          className="inspector-document-link"
                          href={href}
                          target="_blank"
                          rel="noreferrer"
                        >
                          دانلود / مشاهده PDF
                        </a>
                      ) : (
                        <p className="inspector-muted-text">لینک دانلود برای این سند موجود نیست.</p>
                      )}
                    </article>
                  );
                })}
              </div>
            ) : (
              <EmptyState
                icon="▣"
                title="سندی برای نمایش وجود ندارد"
                text="اگر گزارش PDF یا HTML تولید شود، لینک دانلود و مشاهده آن در این بخش نمایش داده می‌شود."
              />
            )}
          </div>
        ) : null}

        {tab === "trace" ? (
          <div className="inspector-section">
            {inspectorTrace.length ? (
              <div className="inspector-trace-list">
                {inspectorTrace.map((step, index) => {
                  const rawStatus = String(step?.status || "unknown").toLowerCase();
                  const statusClass =
                    rawStatus === "success" || rawStatus === "succeeded"
                      ? "success"
                      : rawStatus === "failed" || rawStatus === "error"
                        ? "danger"
                        : rawStatus === "warning"
                          ? "warning"
                          : "neutral";

                  return (
                    <article
                      key={`${step?.id || step?.node_id || "trace"}-${index}`}
                      className={`inspector-trace-card ${statusClass}`}
                    >
                      <div className="inspector-trace-index">{step?.order || index + 1}</div>
                      <div className="inspector-trace-body">
                        <div className="inspector-trace-title">
                          <strong>
                            {step?.label ||
                              step?.capability_name ||
                              step?.node_id ||
                              `Step ${index + 1}`}
                          </strong>
                          <span>{step?.status || "unknown"}</span>
                        </div>

                        <div className="inspector-trace-meta">
                          {step?.capability_name ? <span>{step.capability_name}</span> : null}
                          {step?.plugin_id ? <span>{step.plugin_id}</span> : null}
                          {step?.output_kind ? <span>{step.output_kind}</span> : null}
                        </div>

                        {step?.path ? (
                          <code className="inspector-trace-path">{step.path}</code>
                        ) : null}

                        {Array.isArray(step?.errors) && step.errors.length ? (
                          <pre className="inspector-trace-errors">
                            {JSON.stringify(step.errors, null, 2)}
                          </pre>
                        ) : null}
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <EmptyState
                icon="↯"
                title="فرآیندی برای نمایش وجود ندارد"
                text="مراحل اجرای قابلیت‌ها پس از اجرای تحلیل در این بخش نمایش داده می‌شوند."
              />
            )}
          </div>
        ) : null}

        {tab === "raw" ? (
          <div className="inspector-section">
            {response ? (
              <pre className="inspector-raw">
                {JSON.stringify(response, null, 2)}
              </pre>
            ) : (
              <EmptyState
                icon="{ }"
                title="داده خامی وجود ندارد"
                text="پس از اجرای query، response خام backend برای دیباگ در این بخش نمایش داده می‌شود."
              />
            )}
          </div>
        ) : null}
      </section>

      {selectedInspectorTable ? (() => {
        const rows = asArray(selectedInspectorTable?.rows);
        const columns = asArray(selectedInspectorTable?.columns).length
          ? asArray(selectedInspectorTable.columns)
          : Object.keys(rows[0] || {});
        const title =
          selectedInspectorTable?.name ||
          selectedInspectorTable?.id ||
          "جدول تحلیل";

        return (
          <div
            className="app-modal-backdrop inspector-table-modal-backdrop"
            role="presentation"
            onClick={() => setSelectedInspectorTable(null)}
          >
            <div
              className="app-modal app-modal-xl inspector-table-modal"
              role="dialog"
              aria-modal="true"
              aria-label={title}
              onClick={(event) => event.stopPropagation()}
            >
              <header className="app-modal-header inspector-table-modal-header">
                <div>
                  <p className="modal-subtitle">Analysis table</p>
                  <h3>{title}</h3>
                  <p>
                    {rows.length} ردیف
                    {columns.length ? ` · ${columns.length} ستون` : ""}
                  </p>
                </div>

                <button
                  type="button"
                  className="modal-close"
                  aria-label="Close"
                  onClick={() => setSelectedInspectorTable(null)}
                >
                  ×
                </button>
              </header>

              <div className="app-modal-body inspector-table-modal-body">
                {rows.length && columns.length ? (
                  <div className="inspector-table-modal-scroll">
                    <table className="inspector-table-modal-table">
                      <thead>
                        <tr>
                          {columns.map((column) => (
                            <th key={String(column)}>{String(column)}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row, rowIndex) => (
                          <tr key={`${row?.id || "row"}-${rowIndex}`}>
                            {columns.map((column) => (
                              <td key={String(column)}>
                                {formatInspectorValue(row?.[column])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyState
                    icon="▦"
                    title="جدول خالی است"
                    text="ردیفی برای نمایش در این جدول وجود ندارد."
                  />
                )}
              </div>

              <footer className="app-modal-footer inspector-table-modal-footer">
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => setSelectedInspectorTable(null)}
                >
                  بستن
                </button>
              </footer>
            </div>
          </div>
        );
      })() : null}
    </aside>
  );
}
