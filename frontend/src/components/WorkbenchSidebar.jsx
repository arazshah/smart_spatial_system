const TOOLS = [
  { id: "projects", label: "Projects", icon: "▣" },
  { id: "uploads", label: "Data Sources", icon: "◈" },
  { id: "history", label: "History", icon: "◷" },
  { id: "plugins", label: "Plugins", icon: "⬡" },
  { id: "settings", label: "Settings", icon: "⚙" },
];

export default function WorkbenchSidebar({ activeTool, onSelectTool, health }) {
  const online = String(health?.status || "").toLowerCase() === "ok";

  return (
    <aside className="wb-sidebar wb-sidebar-pro">
      <div className="wb-logo wb-logo-pro">
        <span>GIS</span>
      </div>

      <nav className="wb-tool-nav">
        {TOOLS.map((tool) => (
          <button
            key={tool.id}
            type="button"
            className={`wb-tool-button wb-tool-button-pro ${
              activeTool === tool.id ? "active" : ""
            }`}
            onClick={() => onSelectTool(activeTool === tool.id ? null : tool.id)}
            title={tool.label}
          >
            <span className="wb-tool-icon">{tool.icon}</span>
            <span className="wb-tool-tooltip">{tool.label}</span>
          </button>
        ))}
      </nav>

      <div className="wb-sidebar-footer wb-sidebar-footer-pro">
        <span
          className={`wb-dot wb-dot-pro ${online ? "online" : "offline"}`}
          title={online ? "API Operational" : "API Unavailable"}
        />
      </div>
    </aside>
  );
}
