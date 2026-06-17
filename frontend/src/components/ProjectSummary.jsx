export default function ProjectSummary({ project }) {
  if (!project) {
    return (
      <section className="panel-card">
        <div className="empty-state">
          <h3>پروژه‌ای انتخاب نشده است</h3>
          <p>از سایدبار یک پروژه را انتخاب کنید یا پروژه جدید بسازید.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="panel-card">
      <div className="panel-card-header">
        <div>
          <h2>{project.name}</h2>
          <p>{project.description || "بدون توضیح"}</p>
        </div>
        <span className="status-badge active">Active Project</span>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <span>Project ID</span>
          <strong dir="ltr">{project.project_id}</strong>
        </div>
        <div className="stat-card">
          <span>Uploads</span>
          <strong>{project.uploads?.length || 0}</strong>
        </div>
        <div className="stat-card">
          <span>Requests</span>
          <strong>{project.requests?.length || 0}</strong>
        </div>
        <div className="stat-card">
          <span>Outputs</span>
          <strong>{project.outputs?.length || 0}</strong>
        </div>
      </div>
    </section>
  );
}
