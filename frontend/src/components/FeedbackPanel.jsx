import { useState } from "react";
import { submitFeedback } from "../api/client";

export default function FeedbackPanel({ response }) {
  const [rating, setRating] = useState("correct");
  const [issueTypes, setIssueTypes] = useState("route_error");
  const [expectedCapability, setExpectedCapability] = useState("");
  const [comment, setComment] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const requestId = response?.request_id;

  async function handleSubmit(event) {
    event.preventDefault();

    if (!requestId) {
      setError("ابتدا یک درخواست اجرا کنید.");
      return;
    }

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const payload = await submitFeedback({
        request_id: requestId,
        rating,
        issue_types: issueTypes
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        expected_capability: expectedCapability || undefined,
        comment: comment || undefined,
        user_context: {
          source: "frontend-mvp",
        },
      });

      setResult(payload);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card">
      <div className="card-header">
        <h2>بازخورد کاربر</h2>
        <span className="badge">Feedback</span>
      </div>

      {!requestId && (
        <p className="muted">برای ارسال feedback ابتدا یک query اجرا کنید.</p>
      )}

      <form onSubmit={handleSubmit} className="form">
        <label>
          امتیاز
          <select
            value={rating}
            onChange={(event) => setRating(event.target.value)}
          >
            <option value="correct">درست</option>
            <option value="incorrect">اشتباه</option>
            <option value="partial">نیمه‌درست</option>
          </select>
        </label>

        <label>
          issue_types
          <input
            value={issueTypes}
            onChange={(event) => setIssueTypes(event.target.value)}
            placeholder="route_error, plugin_error"
            dir="ltr"
          />
        </label>

        <label>
          expected_capability
          <input
            value={expectedCapability}
            onChange={(event) => setExpectedCapability(event.target.value)}
            placeholder="مثلاً threshold_raster"
            dir="ltr"
          />
        </label>

        <label>
          توضیح
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            rows={3}
          />
        </label>

        {error && <div className="alert error">{error}</div>}

        <button type="submit" disabled={loading || !requestId}>
          {loading ? "در حال ارسال..." : "ارسال بازخورد"}
        </button>
      </form>

      {result && (
        <details open>
          <summary>نتیجه feedback و proposals</summary>
          <pre dir="ltr" className="json-box">
            {JSON.stringify(result, null, 2)}
          </pre>
        </details>
      )}
    </section>
  );
}
