import { useState } from "react";
import { getWeights, reloadWeights, saveWeights } from "../api/client";

export default function WeightsPanel() {
  const [weights, setWeights] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function run(action) {
    setLoading(true);
    setError("");

    try {
      const payload = await action();
      setWeights(payload);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card">
      <div className="card-header">
        <h2>وزن‌های Router</h2>
        <span className="badge">Admin MVP</span>
      </div>

      <div className="button-row">
        <button disabled={loading} onClick={() => run(getWeights)}>
          دریافت وزن‌ها
        </button>

        <button disabled={loading} onClick={() => run(saveWeights)}>
          ذخیره وزن‌ها
        </button>

        <button disabled={loading} onClick={() => run(reloadWeights)}>
          بارگذاری مجدد
        </button>
      </div>

      {error && <div className="alert error">{error}</div>}

      {weights && (
        <pre dir="ltr" className="json-box">
          {JSON.stringify(weights, null, 2)}
        </pre>
      )}
    </section>
  );
}
