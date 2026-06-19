export default function RealtimeProgressModal({ open, progress, onClose }) {
  if (!open || !progress) return null;

  const steps = Array.isArray(progress?.steps) ? progress.steps : [];

  function stepText(status) {
    if (status === "done") return "انجام شد";
    if (status === "failed") return "ناموفق";
    if (status === "running") return "در حال اجرا";
    return "در انتظار";
  }

  return (
    <div className="rt-progress-modal-backdrop">
      <div className="rt-progress-modal">
        <div className="rt-progress-head">
          <div>
            <h3>روند اجرای تحلیل</h3>
            <p>{progress?.message || "در حال اجرا..."}</p>
          </div>

          <button
            type="button"
            className="rt-progress-close"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div className="rt-progress-bar-shell">
          <div
            className="rt-progress-bar-fill"
            style={{ width: `${progress?.percent || 0}%` }}
          />
        </div>

        <div className="rt-progress-percent">
          {progress?.percent || 0}%
        </div>

        <div className="rt-progress-steps">
          {steps.map((step) => (
            <div key={step.key} className={`rt-step rt-step--${step.status || "pending"}`}>
              <div className="rt-step-dot" />
              <div className="rt-step-line" />
              <div className="rt-step-content">
                <strong>{step.label || step.key}</strong>
                <span>{stepText(step.status)}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
