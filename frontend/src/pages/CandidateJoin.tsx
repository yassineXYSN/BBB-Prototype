import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, formatDateTime } from "../api";
import type { JoinPreview } from "../api";

type Preflight = "idle" | "checking" | "ok" | "error";

export default function CandidateJoin() {
  const { token = "" } = useParams();
  const [preview, setPreview] = useState<JoinPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<Preflight>("idle");
  const [deviceError, setDeviceError] = useState<string | null>(null);
  const [joining, setJoining] = useState(false);

  useEffect(() => {
    api
      .joinPreview(token)
      .then(setPreview)
      .catch((err) => setError(err instanceof Error ? err.message : "Invalid invite link"));
  }, [token]);

  const checkDevices = async () => {
    setPreflight("checking");
    setDeviceError(null);
    if (!navigator.mediaDevices || !window.isSecureContext) {
      setPreflight("error");
      setDeviceError(
        "Camera access needs a secure page — open the invite via http://localhost " +
          "or an HTTPS tunnel. Plain http over a LAN IP is blocked by the browser.",
      );
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
      stream.getTracks().forEach((t) => t.stop());
      setPreflight("ok");
    } catch (err) {
      setPreflight("error");
      setDeviceError(
        err instanceof Error
          ? err.message
          : "Camera/microphone access failed — check browser permissions.",
      );
    }
  };

  const join = async () => {
    setJoining(true);
    setError(null);
    try {
      const { join_url } = await api.joinMeeting(token);
      window.location.href = join_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not join the room");
      setJoining(false);
    }
  };

  if (error && !preview) {
    return (
      <div className="join-page">
        <div className="card auth-card">
          <h1>Invite problem</h1>
          <div className="alert alert-error">{error}</div>
        </div>
      </div>
    );
  }

  if (!preview) {
    return (
      <div className="join-page">
        <p className="muted">Loading your interview…</p>
      </div>
    );
  }

  const isInterviewer = preview.role === "interviewer";

  return (
    <div className="join-page">
      <div className="card auth-card">
        <span className="brand-mark">◆</span>
        <h1>{preview.interview_title}</h1>
        <p className="muted">
          {isInterviewer
            ? "Interviewer link — you will enter as the moderator."
            : preview.candidate_name
              ? `Hello ${preview.candidate_name} — `
              : ""}
          {!isInterviewer && (
            <>
              {formatDateTime(preview.scheduled_start, preview.timezone)} ·{" "}
              {preview.duration_minutes} minutes
            </>
          )}
        </p>
        {isInterviewer && (
          <p className="muted small">
            {formatDateTime(preview.scheduled_start, preview.timezone)} ·{" "}
            {preview.duration_minutes} minutes
          </p>
        )}

        <div className="notice">
          <strong>This session is recorded.</strong> We keep only the transcript afterwards — the
          raw recording is deleted automatically once the text is stored.
        </div>

        <div className="device-check">
          <div>
            <strong>Camera &amp; microphone</strong>
            <div className="muted small">
              {preflight === "idle" && "Run a quick check before joining."}
              {preflight === "checking" && "Checking…"}
              {preflight === "ok" && <span className="ok-text">✓ Everything looks good</span>}
              {preflight === "error" && <span className="bad-text">{deviceError}</span>}
            </div>
          </div>
          <button className="btn" onClick={checkDevices} disabled={preflight === "checking"}>
            Test devices
          </button>
        </div>

        {error && <div className="alert alert-error">{error}</div>}

        <button className="btn btn-primary btn-lg" onClick={join} disabled={joining}>
          {joining ? "Joining…" : isInterviewer ? "Join as moderator" : "Join interview now"}
        </button>
        <p className="muted small center">
          {isInterviewer
            ? "You will enter the room as moderator with full controls."
            : "You will enter as a participant. Your interviewer opens the room as moderator."}
        </p>
      </div>
    </div>
  );
}
