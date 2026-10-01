import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";

export default function Layout() {
  const { user, logout } = useAuth();

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/" className="brand">
          <span className="brand-mark">◆</span> InterviewOps
        </Link>
        <nav>
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/schedule">Schedule interview</NavLink>
          <NavLink to="/templates">Templates</NavLink>
        </nav>
        <div className="sidebar-footer">
          <div className="user-chip">
            <strong>{user?.full_name}</strong>
            <span>{user?.role}</span>
          </div>
          <button className="btn btn-ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
