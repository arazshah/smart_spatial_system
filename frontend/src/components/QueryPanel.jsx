import { useEffect, useState } from "react";
import { SAMPLE_BAND_MAP, SAMPLE_INPUTS, SAMPLE_QUERY } from "../sampleData";

export default function QueryPanel({ onSubmit, loading, upload }) {
  const [query, setQuery] = useState(SAMPLE_QUERY);
  const [requestId, setRequestId] = useState("req-frontend-001");
  const [inputMode, setInputMode] = useState("json");
  const [uploadRef, setUploadRef] = useState("");
  const [inputsText, setInputsText] = useState(
    JSON.stringify(SAMPLE_INPUTS, null, 2)
  );
  const [bandMapText, setBandMapText] = useState(
    JSON.stringify(SAMPLE_BAND_MAP, null, 2)
  );
  const [error, setError] = useState("");

  useEffect(() => {
    if (upload?.upload_id) {
      setUploadRef(upload.upload_id);

      if (upload.kind === "vector") {
        setInputMode("vector_ref");
      } else {
        setInputMode("raster_ref");
      }
    }
  }, [upload]);

  function handleSubmit(event) {
    event.preventDefault();
    setError("");

    let inputs;
    let bandMap;

    if (inputMode === "raster_ref") {
      if (!uploadRef.trim()) {
        setError("raster_ref خالی است. ابتدا فایل raster آپلود کنید یا upload_id را وارد کنید.");
        return;
      }

      inputs = {
        raster_ref: uploadRef.trim(),
      };
    } else if (inputMode === "vector_ref") {
      if (!uploadRef.trim()) {
        setError("vector_ref خالی است. ابتدا فایل vector آپلود کنید یا upload_id را وارد کنید.");
        return;
      }

      inputs = {
        vector_ref: uploadRef.trim(),
      };
    } else {
      try {
        inputs = JSON.parse(inputsText);
      } catch (err) {
        setError(`JSON ورودی‌ها معتبر نیست: ${err.message}`);
        return;
      }
    }

    try {
      bandMap = JSON.parse(bandMapText);
    } catch (err) {
      setError(`JSON band_map معتبر نیست: ${err.message}`);
      return;
    }

    onSubmit({
      query,
      request_id: requestId || undefined,
      inputs,
      band_map: bandMap,
      user_context: {
        source: "frontend-mvp",
        input_mode: inputMode,
      },
    });
  }

  return (
    <section className="card">
      <div className="card-header">
        <h2>درخواست مکانی</h2>
        <span className="badge">Query</span>
      </div>

      <form onSubmit={handleSubmit} className="form">
        <label>
          متن درخواست
          <textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            rows={4}
            placeholder="مثلاً: از تصویر NDVI بگیر و پوشش گیاهی را استخراج کن"
          />
        </label>

        <label>
          Request ID
          <input
            value={requestId}
            onChange={(event) => setRequestId(event.target.value)}
            placeholder="اختیاری"
          />
        </label>

        <label>
          نوع ورودی
          <select
            value={inputMode}
            onChange={(event) => setInputMode(event.target.value)}
          >
            <option value="json">JSON دستی</option>
            <option value="raster_ref">raster_ref از آپلود</option>
            <option value="vector_ref">vector_ref از آپلود</option>
          </select>
        </label>

        {inputMode === "raster_ref" && (
          <label>
            raster_ref / upload_id
            <input
              value={uploadRef}
              onChange={(event) => setUploadRef(event.target.value)}
              placeholder="upl-..."
              dir="ltr"
            />
          </label>
        )}

        {inputMode === "vector_ref" && (
          <label>
            vector_ref / upload_id
            <input
              value={uploadRef}
              onChange={(event) => setUploadRef(event.target.value)}
              placeholder="upl-..."
              dir="ltr"
            />
          </label>
        )}

        {inputMode === "json" && (
          <label>
            inputs JSON
            <textarea
              value={inputsText}
              onChange={(event) => setInputsText(event.target.value)}
              rows={12}
              dir="ltr"
              className="code"
            />
          </label>
        )}

        <label>
          band_map JSON
          <textarea
            value={bandMapText}
            onChange={(event) => setBandMapText(event.target.value)}
            rows={5}
            dir="ltr"
            className="code"
          />
        </label>

        {error && <div className="alert error">{error}</div>}

        <button type="submit" disabled={loading}>
          {loading ? "در حال اجرا..." : "اجرای درخواست"}
        </button>
      </form>
    </section>
  );
}
