import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, formatDateTime } from "../api";
import type { Interview } from "../api";
import StatusBadge from "../components/StatusBadge";
import TranscriptViewer from "../components/TranscriptViewer";

export default function InterviewDetail() {
  const { id = "" } = useParams();
  const [interview, setInterview] = useState<Interview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rescheduleAt, setRescheduleAt] = useState("");
  const [review, setReview] = useState({ rating: 0, verdict: "", notes: "" });

  const load = useCallback(async () => {
    try {
      const data = await api.interview(id);
      setInterview(data);
      setReview({
        rating: data.rating ?? 0,
        verdict: data.verdict ?? "",
        notes: data.notes ?? "",
      });
      if (data.scheduled_start && !rescheduleAt) {
        setRescheduleAt(data.scheduled_start.slice(0, 16));
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [load]);

  const run = async (fn: () => Promise<unknown>, message?: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (message) setNotice(message);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const copy = (text: string) => {
    navigator.clipboard.writeText(text);
    setNotice("Link copied to clipboard");
  };

  if (!interview) {
    return error ? <div className="alert alert-error">{error}</div> : <p>Loading…</p>;
  }

  const candidateToken = interview.tokens?.find((t) => t.role === "candidate");
  const interviewerToken = interview.tokens?.find((t) => t.role === "interviewer");
  const transcript = interview.transcripts?.at(-1);
  const terminal = ["cancelled", "failed", "archived"].includes(interview.status);

  return (
    <>
      <header className="page-header">
        <div>
          <Link to="/" className="muted small">
            ← All interviews
          </Link>
          <h1>{interview.title}</h1>
          <p className="muted">
            {interview.candidate?.full_name} · {interview.candidate?.email} ·{" "}
            {formatDateTime(interview.scheduled_start, interview.timezone)} ·{" "}
            {interview.duration_minutes} min
          </p>
        </div>
        <div className="header-actions">
          <StatusBadge status={interview.status} />
          <button
            className="btn btn-primary"
            disabled={busy || terminal}
            onClick={() =>
              run(async () => {
                const { join_url } = await api.moderatorJoin(interview.public_id);
                window.open(join_url, "_blank");
              }, "Opening BigBlueButton as moderator…")
            }
          >
            Join as moderator
          </button>
          {!terminal && (
            <button
              className="btn btn-danger"
              disabled={busy}
              onClick={() => run(() => api.cancel(interview.public_id), "Interview cancelled")}
            >
              Cancel
            </button>
          )}
        </div>
      </header>

      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}
      {interview.failure_reason && (
        <div className="alert alert-error">{interview.failure_reason}</div>
      )}

      <div className="two-col">
        <section className="card">
          <h3>Lifecycle</h3>
          <ol className="timeline">
            {(interview.audit_logs ?? []).map((entry, i) => (
              <li key={i}>
                <span className="tl-event">{entry.event}</span>
                <span className="muted small">{entry.detail}</span>
                <span className="muted small">{formatDateTime(entry.created_at)}</span>
              </li>
            ))}
            {!interview.audit_logs?.length && <li className="empty">No events yet.</li>}
          </ol>
        </section>

        <section className="card">
          <h3>Room &amp; invites</h3>
          <dl className="kv">
            <dt>Meeting ID</dt>
            <dd>
              <code>{interview.meeting_id ?? "not provisioned"}</code>
            </dd>
            <dt>Candidate link</dt>
            <dd className="row">
              <span className="truncate">{candidateToken?.link ?? "—"}</span>
              {candidateToken && (
                <button className="btn btn-small" onClick={() => copy(candidateToken.link)}>
                  Copy
                </button>
              )}
            </dd>
            <dt>Interviewer link</dt>
            <dd className="row">
              <span className="truncate">{interviewerToken?.link ?? "—"}</span>
              {interviewerToken && (
                <button className="btn btn-small" onClick={() => copy(interviewerToken.link)}>
                  Copy
                </button>
              )}
            </dd>
          </dl>

          {(interview.recordings ?? []).length > 0 && (
            <>
              <h3>Recordings</h3>
              <ul className="recordings">
                {(interview.recordings ?? []).map((r) => (
                  <li key={r.record_id}>
                    <code>{r.record_id}</code>
                    <span className={`badge badge-${r.purged_at ? "transcribed" : "processing"}`}>
                      {r.purged_at ? "purged — only transcript kept" : `state: ${r.state}`}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}

          <div className="actions">
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                run(
                  () => api.process(interview.public_id),
                  "Pipeline run finished (see lifecycle for details)",
                )
              }
            >
              Run / retry transcription
            </button>
            {!terminal && (
              <details className="reschedule">
                <summary className="btn btn-small">Reschedule</summary>
                <div className="row gap">
                  <input
                    type="datetime-local"
                    value={rescheduleAt}
                    onChange={(e) => setRescheduleAt(e.target.value)}
                  />
                  <button
                    className="btn btn-small btn-primary"
                    disabled={busy || !rescheduleAt}
                    onClick={() =>
                      run(
                        () =>
                          api.reschedule(
                            interview.public_id,
                            `${rescheduleAt}:00`,
                          ),
                        "Interview rescheduled — invites re-sent",
                      )
                    }
                  >
                    Save
                  </button>
                </div>
              </details>
            )}
          </div>
        </section>
      </div>

      {transcript ? (
        <TranscriptViewer transcript={transcript} />
      ) : (
        <section className="card empty-card">
          <h3>No transcript yet</h3>
          <p className="muted">
            After the session ends, BBB notifies us when the recording is ready. We then fetch the
            caption track, store the transcript here, and delete the raw recording.
            {interview.status === "processing" && " Processing… this page refreshes automatically."}
          </p>
          <button
            className="btn"
            disabled={busy}
            onClick={() => run(() => api.process(interview.public_id), "Pipeline re-run complete")}
          >
            Check now
          </button>
        </section>
      )}

      <section className="card">
        <h3>Review</h3>
        <div className="form-grid">
          <label>
            Rating
            <div className="stars">
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  type="button"
                  key={n}
                  className={`star ${n <= review.rating ? "star-on" : ""}`}
                  onClick={() => setReview((r) => ({ ...r, rating: n }))}
                >
                  ★
                </button>
              ))}
            </div>
          </label>
          <label>
            Verdict
            <select
              value={review.verdict}
              onChange={(e) => setReview((r) => ({ ...r, verdict: e.target.value }))}
            >
              <option value="">No decision yet</option>
              <option value="hire">Hire</option>
              <option value="no_hire">No hire</option>
              <option value="next_round">Next round</option>
            </select>
          </label>
          <label className="span-2">
            Notes
            <textarea
              rows={3}
              value={review.notes}
              onChange={(e) => setReview((r) => ({ ...r, notes: e.target.value }))}
            />
          </label>
          <div className="span-4 actions">
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() =>
                run(
                  () =>
                    api.update(interview.public_id, {
                      rating: review.rating || null,
                      verdict: review.verdict || null,
                      notes: review.notes,
                    }),
                  "Review saved",
                )
              }
            >
              Save review
            </button>
          </div>
        </div>
      </section>
    </>
  );
}
