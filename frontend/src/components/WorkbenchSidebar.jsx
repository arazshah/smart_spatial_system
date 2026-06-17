const TOOLS = [
  { id: "projects", label: "Projects", icon: "P" },
  { id: "uploads", label: "Uploads", icon: "U" },
  { id: "history", label: "History", icon: "H" },
  { id: "settings", label: "Settings", icon: "S" },
];

export default function WorkbenchSidebar({ activeTool, onSelectTool }) {
  return (
    <aside className="wb-sidebar">
      <div className="wb-logo">
        <span>GIS</span>
      </div>

      <nav className="wb-tool-nav">
        {TOOLS.map((tool) => (
          <button
            key={tool.id}
            className={`wb-tool-button ${activeTool === tool.id ? "active" : ""}`}
            onClick={() => onSelectTool(activeTool === tool.id ? null : tool.id)}
            title={tool.label}
          >
            <span>{tool.icon}</span>
          </button>
        ))}
      </nav>

      <div className="wb-sidebar-footer">
        <span className="wb-dot" />
      </div>
    </aside>
  );
}
