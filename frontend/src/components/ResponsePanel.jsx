function formatValue(value) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function SummaryItem({ name, summary }) {
  return (
    <div className="summary-item">
      <h4>{name}</h4>
      <div className="summary-grid">
        <div>
          <strong>kind:</strong> {summary?.kind || "-"}
        </div>
        {summary?.feature_count !== undefined && (
          <div>
            <strong>feature_count:</strong> {summary.feature_count}
          </div>
        )}
        {summary?.shape && (
          <div>
            <strong>shape:</strong> {JSON.stringify(summary.shape)}
          </div>
        )}
        {summary?.numeric_stats && (
          <div>
            <strong>numeric_stats:</strong>
            <pre>{JSON.stringify(summary.numeric_stats, null, 2)}</pre>
          </div>
        )}
      </div>
    </div>
  );
}

export default function ResponsePanel({ response }) {
  if (!response) {
    return (
      <section className="card muted-card">
        <h2>پاسخ سیستم</h2>
        <p>هنوز درخواستی اجرا نشده است.</p>
      </section>
    );
  }

  const summary = response?.outputs?.summary || {};

  return (
    <section className="card">
      <div className="card-header">
        <h2>پاسخ سیستم</h2>
        <span className={`status status-${response.status}`}>
          {response.status}
        </span>
      </div>

      <div className="answer">{response.answer}</div>

      <div className="info-grid">
        <div>
          <strong>Request ID</strong>
          <span>{response.request_id || "-"}</span>
        </div>

        <div>
          <strong>Query Hash</strong>
          <span className="small-text">{response.query_hash || "-"}</span>
        </div>

        <div>
          <strong>Confidence</strong>
          <span>
            {response.confidence?.level || "-"} /{" "}
            {response.confidence?.score ?? "-"}
          </span>
        </div>

        <div>
          <strong>Ambiguous</strong>
          <span>{String(response.confidence?.is_ambiguous ?? false)}</span>
        </div>

        <div>
          <strong>Plan Steps</strong>
          <span>{response.audit_ref?.plan_steps ?? "-"}</span>
        </div>

        <div>
          <strong>LLM Action</strong>
          <span>{response.confidence?.llm_action || "-"}</span>
        </div>
      </div>

      {response.warnings?.length > 0 && (
        <div className="alert warning">
          <strong>هشدارها</strong>
          <ul>
            {response.warnings.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {response.next_actions?.length > 0 && (
        <div className="alert info">
          <strong>اقدام‌های پیشنهادی</strong>
          <ul>
            {response.next_actions.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      <h3>خلاصه خروجی‌ها</h3>

      {Object.keys(summary).length === 0 ? (
        <p className="muted">خلاصه خروجی موجود نیست.</p>
      ) : (
        <div className="summary-list">
          {Object.entries(summary).map(([name, item]) => (
            <SummaryItem key={name} name={name} summary={item} />
          ))}
        </div>
      )}

      <details>
        <summary>JSON کامل پاسخ</summary>
        <pre dir="ltr" className="json-box">
          {formatValue(response)}
        </pre>
      </details>
    </section>
  );
}
