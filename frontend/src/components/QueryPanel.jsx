import { useEffect, useState } from "react";
import { SAMPLE_BAND_MAP, SAMPLE_INPUTS, SAMPLE_QUERY } from "../sampleData";

export default function QueryPanel({ onSubmit, loading, upload }) {
  const [query, setQuery] = useState(SAMPLE_QUERY);
  const [requestId, setRequestId] = useState("req-frontend-001");
  const [useUploadRef, setUseUploadRef] = useState(false);
  const [rasterRef, setRasterRef] = useState("");
  const [inputsText, setInputsText] = useState(
    JSON.stringify(SAMPLE_INPUTS, null, 2)
  );
  const [bandMapText, setBandMapText] = useState(
    JSON.stringify(SAMPLE_BAND_MAP, null, 2)
  );
  const [error, setError] = useState("");

  useEffect(() => {
    if (upload?.upload_id) {
      setRasterRef(upload.upload_id);
      setUseUploadRef(true);
    }
  }, [upload]);

  function handleSubmit(event) {
    event.preventDefault();
    setError("");

    let inputs;
    let bandMap;

    if (useUploadRef) {
      if (!rasterRef.trim()) {
        setError("raster_ref خالی است. ابتدا فایل آپلود کنید یا upload_id را وارد کنید.");
        return;
      }

      inputs = {
        raster_ref: rasterRef.trim(),
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
        input_mode: useUploadRef ? "upload_ref" : "json",
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

        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={useUploadRef}
            onChange={(event) => setUseUploadRef(event.target.checked)}
          />
          استفاده از raster_ref آپلودشده
        </label>

        {useUploadRef ? (
          <label>
            raster_ref / upload_id
            <input
              value={rasterRef}
              onChange={(event) => setRasterRef(event.target.value)}
              placeholder="upl-..."
              dir="ltr"
            />
          </label>
        ) : (
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
