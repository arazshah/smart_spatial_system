import "../styles/query-progress-stepper.css";

const DEFAULT_STEPS = [
  {
    key: "accepted",
    label: "دریافت درخواست",
    hint: "کوئری آماده اجراست",
  },
  {
    key: "planning",
    label: "تحلیل اولیه",
    hint: "درک هدف کاربر",
  },
  {
    key: "resolving_inputs",
    label: "آماده‌سازی داده",
    hint: "بررسی ورودی‌ها",
  },
  {
    key: "routing",
    label: "انتخاب مسیر",
    hint: "یافتن پلاگین مناسب",
  },
  {
    key: "executing",
    label: "اجرای تحلیل",
    hint: "پردازش مکانی",
  },
  {
    key: "building_response",
    label: "آماده‌سازی خروجی",
    hint: "ساخت پاسخ و لایه‌ها",
  },
  {
    key: "completed",
    label: "پایان",
    hint: "نتیجه آماده است",
  },
];

function normalizeStatus(status) {
  if (status === "success") return "done";
  if (status === "completed") return "done";
  if (status === "complete") return "done";
  if (status === "error") return "failed";
  if (status === "fail") return "failed";
  if (status === "active") return "running";
  if (status === "current") return "running";
  if (status === "waiting") return "pending";
  if (status === "done") return "done";
  if (status === "failed") return "failed";
  if (status === "running") return "running";
  return "pending";
}

function statusText(status) {
  const normalized = normalizeStatus(status);

  if (normalized === "done") return "انجام شد";
  if (normalized === "failed") return "ناموفق";
  if (normalized === "running") return "در حال اجرا";
  return "در انتظار";
}

function buildSteps(progress) {
  const incomingSteps = Array.isArray(progress?.steps) ? progress.steps : [];

  if (incomingSteps.length) {
    return DEFAULT_STEPS.map((baseStep) => {
      const found = incomingSteps.find((item) => item?.key === baseStep.key);

      return {
        ...baseStep,
        ...found,
        status: normalizeStatus(found?.status || "pending"),
      };
    });
  }

  const currentStep = progress?.current_step || progress?.currentStep || "";
  const isRunning = progress?.status === "running";
  const isFailed = progress?.status === "failed";
  const isCompleted =
    progress?.status === "completed" ||
    progress?.status === "success" ||
    progress?.status === "done";

  if (!progress) {
    return DEFAULT_STEPS.map((step, index) => ({
      ...step,
      status: index === 0 ? "running" : "pending",
    }));
  }

  if (isCompleted) {
    return DEFAULT_STEPS.map((step) => ({
      ...step,
      status: "done",
    }));
  }

  let reachedCurrent = false;

  return DEFAULT_STEPS.map((step) => {
    if (isFailed && step.key === currentStep) {
      reachedCurrent = true;
      return {
        ...step,
        status: "failed",
      };
    }

    if (step.key === currentStep) {
      reachedCurrent = true;
      return {
        ...step,
        status: isRunning ? "running" : "done",
      };
    }

    if (!reachedCurrent && currentStep) {
      return {
        ...step,
        status: "done",
      };
    }

    return {
      ...step,
      status: "pending",
    };
  });
}

function percentFromSteps(steps, progress) {
  if (typeof progress?.percent === "number") {
    return Math.max(0, Math.min(100, Math.round(progress.percent)));
  }

  if (!steps.length) return 0;

  const doneCount = steps.filter((step) => step.status === "done").length;
  const runningCount = steps.filter((step) => step.status === "running").length;

  return Math.round(((doneCount + runningCount * 0.55) / steps.length) * 100);
}

function StepIcon({ status, index }) {
  if (status === "done") return <span>✓</span>;
  if (status === "failed") return <span>!</span>;
  if (status === "running") return <span>{index + 1}</span>;
  return <span>{index + 1}</span>;
}

export default function QueryProgressStepper({
  progress = null,
  compact = false,
  className = "",
}) {
  const steps = buildSteps(progress);
  const percent = percentFromSteps(steps, progress);

  const overallStatus = normalizeStatus(progress?.status || "running");

  const message =
    progress?.message ||
    (overallStatus === "failed"
      ? "فرآیند با خطا متوقف شد."
      : overallStatus === "done"
        ? "فرآیند با موفقیت کامل شد."
        : "آماده اجرای تحلیل مکانی.");

  return (
    <section
      className={[
        "qps-shell",
        compact ? "qps-shell--compact" : "",
        `qps-shell--${overallStatus}`,
        className,
      ].filter(Boolean).join(" ")}
      dir="rtl"
    >
      <div className="qps-top">
        <div className="qps-title-wrap">
          <div className="qps-kicker">Real-time Progress</div>
          <div className="qps-title">روند اجرای تحلیل</div>
        </div>

        <div className="qps-status-pill">
          <span className="qps-status-dot" />
          <span>{statusText(overallStatus)}</span>
          <b>{percent}%</b>
        </div>
      </div>

      <div className="qps-message">{message}</div>

      <div className="qps-track" aria-hidden="true">
        <div
          className="qps-track-fill"
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="qps-steps">
        {steps.map((step, index) => {
          const status = normalizeStatus(step.status);

          return (
            <div
              key={step.key || index}
              className={`qps-step qps-step--${status}`}
            >
              <div className="qps-node-wrap">
                <div className="qps-node">
                  <StepIcon status={status} index={index} />
                </div>
              </div>

              <div className="qps-step-label">{step.label || step.key}</div>
              <div className="qps-step-hint">{step.hint || statusText(status)}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
