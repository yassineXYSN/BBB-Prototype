import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatDateTime } from "../api";
import type { Interview } from "../api";
import StatusBadge from "../components/StatusBadge";

const FILTERS = [
  { label: "All", value: "" },
  { label: "Upcoming", value: "scheduled,waiting" },
  { label: "Live", value: "live" },
  { label: "Processing", value: "ended,processing" },
  { label: "Done", value: "transcribed,archived" },
  { label: "Issues", value: "failed,cancelled" },
];

export default function Dashboard() {
  const [interviews, setInterviews] = useState<Interview[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, counts] = await Promise.all([
        api.interviews({ status: filter || undefined, search: search || undefined }),
        api.stats(),
      ]);
      setInterviews(list);
      setStats(counts);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    }
  }, [filter, search]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [load]);

  const total = Object.values(stats).reduce((a, b) => a + b, 0);

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Interviews</h1>
          <p className="muted">Plan → room → recording → transcript → only the text is kept.</p>
        </div>
        <Link className="btn btn-primary" to="/schedule">
          + Schedule interview
        </Link>
      </header>

      <div className="stat-row">
        <div className="stat card">
          <span>{total}</span>
          <label>Total</label>
        </div>
        <div className="stat card">
          <span>{(stats.scheduled ?? 0) + (stats.waiting ?? 0)}</span>
          <label>Upcoming</label>
        </div>
        <div className="stat card">
          <span>{stats.live ?? 0}</span>
          <label>Live now</label>
        </div>
        <div className="stat card">
          <span>{(stats.processing ?? 0) + (stats.ended ?? 0)}</span>
          <label>Awaiting transcript</label>
        </div>
        <div className="stat card">
          <span>{stats.transcribed ?? 0}</span>
          <label>Transcribed</label>
        </div>
        <div className="stat card">
          <span>{stats.failed ?? 0}</span>
          <label>Failed</label>
        </div>
      </div>

      <div className="toolbar">
        <div className="chips">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              className={`chip ${filter === f.value ? "chip-active" : ""}`}
              onClick={() => setFilter(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <input
          className="search"
          placeholder="Search title or candidate…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="card table-card">
        <table>
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Interview</th>
              <th>When</th>
              <th>Status</th>
              <th>Recording</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {interviews.map((i) => (
              <tr key={i.public_id}>
                <td>
                  <strong>{i.candidate?.full_name ?? "—"}</strong>
                  <div className="muted small">{i.candidate?.email}</div>
                </td>
                <td>
                  {i.title}
                  <div className="muted small">{i.duration_minutes} min</div>
                </td>
                <td>{formatDateTime(i.scheduled_start, i.timezone)}</td>
                <td>
                  <StatusBadge status={i.status} />
                </td>
                <td>
                  {i.recordings?.length
                    ? i.recordings.map((r) => (
                        <span key={r.record_id} className="muted small">
                          {r.purged_at ? "purged ✓" : r.state}
                        </span>
                      ))
                    : <span className="muted small">—</span>}
                </td>
                <td className="right">
                  <Link className="btn btn-small" to={`/interviews/${i.public_id}`}>
                    Open
                  </Link>
                </td>
              </tr>
            ))}
            {!interviews.length && (
              <tr>
                <td colSpan={6} className="empty">
                  No interviews match this view.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
