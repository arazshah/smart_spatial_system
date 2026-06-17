export default function UploadBrowser({
  project,
  uploads,
  selectedUploadId,
  onSelectUpload,
}) {
  const projectUploads = project?.uploads?.length
    ? uploads.filter((item) => project.uploads.includes(item.upload_id))
    : [];

  return (
    <section className="panel-card">
      <div className="panel-card-header">
        <div>
          <h2>Upload Browser</h2>
          <p>فایل‌های آپلودشده پروژه فعال را مرور و برای query انتخاب کنید.</p>
        </div>
      </div>

      {!project ? (
        <div className="empty-state compact">ابتدا یک پروژه فعال انتخاب کنید.</div>
      ) : projectUploads.length === 0 ? (
        <div className="empty-state compact">هنوز فایلی در این پروژه آپلود نشده است.</div>
      ) : (
        <div className="list-grid">
          {projectUploads.map((item) => {
            const isActive = selectedUploadId === item.upload_id;

            return (
              <button
                key={item.upload_id}
                className={`browser-item ${isActive ? "active" : ""}`}
                onClick={() => onSelectUpload?.(item)}
              >
                <div className="browser-item-title">
                  {item.filename || item.upload_id}
                </div>
                <div className="browser-item-tags">
                  <span className="tag">{item.kind}</span>
                  <span className="tag mono">{item.upload_id}</span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
