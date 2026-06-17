export default function Layout({ sidebar, header, children }) {
  return (
    <div className="app-shell">
      <aside className="app-sidebar">{sidebar}</aside>

      <div className="app-main">
        <header className="app-header">{header}</header>
        <main className="app-content">{children}</main>
      </div>
    </div>
  );
}
