export default function RequestsPanel({
  project,
  requests,
  activeRequestId,
  onSelectRequest,
}) {
  const projectRequests = project?.requests?.length
    ? requests.filter((item) => project.requests.includes(item.request_id))
    : [];

  return (
    <section className="panel-card">
      <div className="panel-card-header">
        <div>
          <h2>Requests History</h2>
          <p>درخواست‌های قبلی پروژه را انتخاب و دوباره بررسی کنید.</p>
        </div>
      </div>

      {!project ? (
        <div className="empty-state compact">ابتدا یک پروژه فعال انتخاب کنید.</div>
      ) : projectRequests.length === 0 ? (
        <div className="empty-state compact">هنوز درخواستی برای این پروژه ثبت نشده است.</div>
      ) : (
        <div className="request-list">
          {projectRequests.map((item) => {
            const isActive = activeRequestId === item.request_id;

            return (
              <button
                key={item.request_id}
                className={`request-item ${isActive ? "active" : ""}`}
                onClick={() => onSelectRequest?.(item)}
              >
                <div className="request-item-top">
                  <strong dir="ltr">{item.request_id}</strong>
                  <span className={`mini-badge ${item.status || "unknown"}`}>
                    {item.status || "unknown"}
                  </span>
                </div>

                <div className="request-item-query">
                  {item.query || item.question || "بدون متن درخواست"}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
