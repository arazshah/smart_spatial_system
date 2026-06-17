import { useEffect, useMemo, useState } from "react";

function createRequestId() {
  const suffix = Math.random().toString(16).slice(2, 8);
  return `req-ui-${Date.now()}-${suffix}`;
}

const QUERY_PRESETS = [
  {
    id: "ndvi",
    title: "NDVI Analysis",
    subtitle: "محاسبه شاخص NDVI از تصویر raster",
    recommendedInput: "raster",
    query:
      "از تصویر ورودی NDVI محاسبه کن و نتیجه را به همراه لایه قابل نمایش برگردان.",
    bandMap: {
      red: 1,
      nir: 2,
    },
  },
  {
    id: "vegetation_mask",
    title: "Vegetation Mask",
    subtitle: "استخراج ماسک پوشش گیاهی",
    recommendedInput: "raster",
    query:
      "با استفاده از NDVI پوشش گیاهی را استخراج کن و ماسک vegetation تولید کن.",
    bandMap: {
      red: 1,
      nir: 2,
    },
  },
  {
    id: "vegetation_polygons",
    title: "Vegetation Polygons",
    subtitle: "تبدیل ماسک پوشش گیاهی به پلیگون",
    recommendedInput: "raster",
    query:
      "پوشش گیاهی را از raster استخراج کن و نواحی vegetation را به polygon تبدیل کن.",
    bandMap: {
      red: 1,
      nir: 2,
    },
  },
  {
    id: "raster_to_vector",
    title: "Raster to Vector",
    subtitle: "تبدیل raster طبقه‌بندی‌شده به vector",
    recommendedInput: "raster",
    query:
      "raster ورودی را بر اساس مقدارهای معتبر به vector polygon تبدیل کن.",
    bandMap: {},
  },
  {
    id: "vector_inspection",
    title: "Vector Inspection",
    subtitle: "بررسی و خلاصه‌سازی داده vector",
    recommendedInput: "vector",
    query:
      "لایه vector ورودی را بررسی کن، نوع هندسه‌ها، تعداد featureها و extent را گزارش بده.",
    bandMap: {},
  },
  {
    id: "custom",
    title: "Custom Query",
    subtitle: "درخواست سفارشی",
    recommendedInput: "any",
    query: "",
    bandMap: {},
  },
];

function normalizeUploadKind(kind) {
  const value = String(kind || "").toLowerCase();

  if (value.includes("vector")) return "vector";
  if (value.includes("raster")) return "raster";

  return value || "unknown";
}

export default function QueryPanel({
  project,
  uploads,
  onSubmit,
  loading,
  upload,
  selectedUpload,
}) {
  const [presetId, setPresetId] = useState("ndvi");
  const [query, setQuery] = useState(QUERY_PRESETS[0].query);
  const [requestId, setRequestId] = useState(() => createRequestId());
  const [inputMode, setInputMode] = useState("selected_upload");
  const [uploadRef, setUploadRef] = useState("");
  const [inputsText, setInputsText] = useState(
    JSON.stringify(
      {
        raster: {
          data: [
            [
              [1, 1, 1],
              [1, 1, 1],
            ],
            [
              [2, 1, 4],
              [1, 3, 0.5],
            ],
          ],
          metadata: {
            crs: "EPSG:3857",
            transform: [10, 0, 100, 0, -10, 200],
            nodata: -9999,
          },
        },
      },
      null,
      2
    )
  );
  const [bandMapText, setBandMapText] = useState(
    JSON.stringify(QUERY_PRESETS[0].bandMap, null, 2)
  );
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [error, setError] = useState("");

  const activePreset = useMemo(() => {
    return QUERY_PRESETS.find((item) => item.id === presetId) || QUERY_PRESETS[0];
  }, [presetId]);

  const safeUploads = useMemo(() => {
    return Array.isArray(uploads) ? uploads : [];
  }, [uploads]);

  const projectUploads = useMemo(() => {
    if (!project?.uploads?.length) return [];

    const allowedIds = new Set(project.uploads);

    return safeUploads.filter((item) => allowedIds.has(item.upload_id));
  }, [project, safeUploads]);

  const activeUpload = useMemo(() => {
    if (selectedUpload?.upload_id) return selectedUpload;

    if (uploadRef) {
      return projectUploads.find((item) => item.upload_id === uploadRef) || null;
    }

    return null;
  }, [selectedUpload, uploadRef, projectUploads]);

  const selectedUploadKind = normalizeUploadKind(activeUpload?.kind);

  useEffect(() => {
    if (upload?.upload_id) {
      setUploadRef(upload.upload_id);
      setInputMode("selected_upload");
    }
  }, [upload]);

  useEffect(() => {
    if (selectedUpload?.upload_id) {
      setUploadRef(selectedUpload.upload_id);
      setInputMode("selected_upload");
    }
  }, [selectedUpload]);

  function handlePresetChange(nextPresetId) {
    const nextPreset =
      QUERY_PRESETS.find((item) => item.id === nextPresetId) ||
      QUERY_PRESETS[0];

    setPresetId(nextPreset.id);

    if (nextPreset.query) {
      setQuery(nextPreset.query);
    }

    setBandMapText(JSON.stringify(nextPreset.bandMap || {}, null, 2));

    if (nextPreset.recommendedInput === "raster") {
      setInputMode("selected_upload");
    } else if (nextPreset.recommendedInput === "vector") {
      setInputMode("selected_upload");
    }

    setError("");
  }

  function buildInputs() {
    if (inputMode === "selected_upload") {
      if (!uploadRef.trim()) {
        throw new Error("ابتدا یک upload از پروژه انتخاب کنید.");
      }

      const item =
        projectUploads.find((entry) => entry.upload_id === uploadRef.trim()) ||
        activeUpload;

      const kind = normalizeUploadKind(item?.kind);

      if (kind === "vector") {
        return {
          vector_ref: uploadRef.trim(),
        };
      }

      return {
        raster_ref: uploadRef.trim(),
      };
    }

    if (inputMode === "raster_ref") {
      if (!uploadRef.trim()) {
        throw new Error("raster_ref خالی است.");
      }

      return {
        raster_ref: uploadRef.trim(),
      };
    }

    if (inputMode === "vector_ref") {
      if (!uploadRef.trim()) {
        throw new Error("vector_ref خالی است.");
      }

      return {
        vector_ref: uploadRef.trim(),
      };
    }

    if (inputMode === "json") {
      try {
        return JSON.parse(inputsText);
      } catch (err) {
        throw new Error(`JSON ورودی‌ها معتبر نیست: ${err.message}`);
      }
    }

    throw new Error(`input mode نامعتبر است: ${inputMode}`);
  }

  function buildBandMap() {
    try {
      const value = JSON.parse(bandMapText || "{}");

      if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new Error("band_map باید object باشد.");
      }

      return value;
    } catch (err) {
      throw new Error(`JSON band_map معتبر نیست: ${err.message}`);
    }
  }

  function buildPayload() {
    if (!project?.project_id) {
      throw new Error("ابتدا یک پروژه فعال انتخاب کنید.");
    }

    if (!query.trim()) {
      throw new Error("متن درخواست خالی است.");
    }

    const inputs = buildInputs();
    const bandMap = buildBandMap();

    return {
      query: query.trim(),
      request_id: requestId.trim() || undefined,
      inputs,
      band_map: bandMap,
      metadata: {
        project_id: project.project_id,
        query_preset: presetId,
        selected_upload_id: uploadRef || null,
      },
      user_context: {
        source: "frontend-professional-query-builder",
        input_mode: inputMode,
        preset_id: presetId,
      },
    };
  }

  function handleSubmit(event) {
    event.preventDefault();
    setError("");

    try {
      const payload = buildPayload();
      onSubmit(payload);
    } catch (err) {
      setError(err.message);
    }
  }

  let previewPayload = null;

  try {
    previewPayload = buildPayload();
  } catch {
    previewPayload = null;
  }

  const presetWarning =
    activePreset.recommendedInput === "raster" &&
    selectedUploadKind === "vector"
      ? "این preset معمولاً به raster نیاز دارد، اما upload انتخاب‌شده vector است."
      : activePreset.recommendedInput === "vector" &&
        selectedUploadKind === "raster"
      ? "این preset معمولاً به vector نیاز دارد، اما upload انتخاب‌شده raster است."
      : "";

  return (
    <section className="panel-card query-builder-card">
      <div className="panel-card-header">
        <div>
          <h2>Professional Query Builder</h2>
          <p>
            اجرای workflowهای مکانی با preset، ورودی پروژه‌محور و payload قابل
            کنترل.
          </p>
        </div>

        <span className="status-badge">
          {project ? "Project Ready" : "No Project"}
        </span>
      </div>

      <form onSubmit={handleSubmit} className="form">
        <div className="query-section">
          <div className="query-section-title">
            <span>1</span>
            <div>
              <h3>انتخاب نوع تحلیل</h3>
              <p>یک الگوی آماده انتخاب کنید یا query سفارشی بنویسید.</p>
            </div>
          </div>

          <div className="preset-grid">
            {QUERY_PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className={`preset-card ${
                  presetId === preset.id ? "active" : ""
                }`}
                onClick={() => handlePresetChange(preset.id)}
              >
                <strong>{preset.title}</strong>
                <span>{preset.subtitle}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="query-section">
          <div className="query-section-title">
            <span>2</span>
            <div>
              <h3>متن درخواست</h3>
              <p>درخواست مکانی را به زبان طبیعی وارد کنید.</p>
            </div>
          </div>

          <label>
            Query
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              rows={5}
              placeholder="مثلاً: پوشش گیاهی را از تصویر استخراج کن و پلیگون‌های آن را تولید کن"
            />
          </label>
        </div>

        <div className="query-section">
          <div className="query-section-title">
            <span>3</span>
            <div>
              <h3>داده ورودی</h3>
              <p>از uploadهای پروژه استفاده کنید یا ورودی دستی بدهید.</p>
            </div>
          </div>

          <div className="form-grid two">
            <label>
              Input Mode
              <select
                value={inputMode}
                onChange={(event) => setInputMode(event.target.value)}
              >
                <option value="selected_upload">انتخاب upload از پروژه</option>
                <option value="raster_ref">raster_ref دستی</option>
                <option value="vector_ref">vector_ref دستی</option>
                <option value="json">JSON دستی</option>
              </select>
            </label>

            <label>
              Request ID
              <div className="inline-field">
                <input
                  value={requestId}
                  onChange={(event) => setRequestId(event.target.value)}
                  placeholder="اختیاری"
                  dir="ltr"
                />
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => setRequestId(createRequestId())}
                >
                  New
                </button>
              </div>
            </label>
          </div>

          {inputMode !== "json" && (
            <>
              <label>
                Upload / Reference
                <select
                  value={uploadRef}
                  onChange={(event) => setUploadRef(event.target.value)}
                >
                  <option value="">-- انتخاب upload --</option>
                  {projectUploads.map((item) => (
                    <option key={item.upload_id} value={item.upload_id}>
                      {item.kind} | {item.filename} | {item.upload_id}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Reference ID
                <input
                  value={uploadRef}
                  onChange={(event) => setUploadRef(event.target.value)}
                  dir="ltr"
                  placeholder="upl-..."
                />
              </label>
            </>
          )}

          {activeUpload && (
            <div className="selected-data-card">
              <div>
                <span>Selected data</span>
                <strong>{activeUpload.filename || activeUpload.upload_id}</strong>
              </div>
              <div className="browser-item-tags">
                <span className="tag">{activeUpload.kind}</span>
                <span className="tag mono">{activeUpload.upload_id}</span>
              </div>
            </div>
          )}

          {presetWarning && <div className="alert warning">{presetWarning}</div>}

          {inputMode === "json" && (
            <label>
              Inputs JSON
              <textarea
                value={inputsText}
                onChange={(event) => setInputsText(event.target.value)}
                rows={12}
                className="code"
                dir="ltr"
              />
            </label>
          )}
        </div>

        <div className="query-section">
          <button
            type="button"
            className="ghost-button section-toggle"
            onClick={() => setShowAdvanced((value) => !value)}
          >
            {showAdvanced ? "بستن تنظیمات پیشرفته" : "نمایش تنظیمات پیشرفته"}
          </button>

          {showAdvanced && (
            <div className="advanced-panel">
              <label>
                band_map JSON
                <textarea
                  value={bandMapText}
                  onChange={(event) => setBandMapText(event.target.value)}
                  rows={6}
                  className="code"
                  dir="ltr"
                />
              </label>

              <p className="field-hint">
                برای NDVI معمولاً band_map شامل red و nir است. مثال:
                <span dir="ltr"> {"{ red: 1, nir: 2 }"}</span>
              </p>
            </div>
          )}
        </div>

        {error && <div className="alert error">{error}</div>}

        <div className="query-actions">
          <button type="submit" disabled={loading || !project}>
            {loading ? "در حال اجرای Query..." : "اجرای تحلیل مکانی"}
          </button>

          <button
            type="button"
            className="ghost-button"
            onClick={() => setShowPreview((value) => !value)}
          >
            {showPreview ? "بستن Preview" : "نمایش Payload"}
          </button>
        </div>

        {showPreview && (
          <div className="payload-preview">
            {previewPayload ? (
              <pre className="json-box" dir="ltr">
                {JSON.stringify(previewPayload, null, 2)}
              </pre>
            ) : (
              <div className="empty-state compact">
                برای نمایش preview، ابتدا خطاهای فرم را برطرف کنید.
              </div>
            )}
          </div>
        )}
      </form>
    </section>
  );
}
