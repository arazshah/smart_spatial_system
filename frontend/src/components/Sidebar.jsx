export default function Sidebar({
  projects,
  activeProjectId,
  onSelectProject,
  onCreateProjectClick,
}) {
  return (
    <div className="sidebar">
      <div className="brand-block">
        <div className="brand-logo">GIS</div>
        <div>
          <h1 className="brand-title">Smart Spatial System</h1>
          <p className="brand-subtitle">Operational Spatial Workbench</p>
        </div>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-section-header">
          <h2>Projects</h2>
          <button className="ghost-button small" onClick={onCreateProjectClick}>
            + New
          </button>
        </div>

        <div className="project-list">
          {projects.length === 0 ? (
            <div className="empty-state compact">هنوز پروژه‌ای ساخته نشده است.</div>
          ) : (
            projects.map((project) => (
              <button
                key={project.project_id}
                className={`project-item ${
                  activeProjectId === project.project_id ? "active" : ""
                }`}
                onClick={() => onSelectProject(project)}
              >
                <div className="project-item-title">{project.name}</div>
                <div className="project-item-meta" dir="ltr">
                  {project.project_id}
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      <div className="sidebar-section">
        <h2>Workspace</h2>
        <div className="sidebar-info-card">
          <div>Projects</div>
          <strong>{projects.length}</strong>
        </div>
      </div>
    </div>
  );
}
