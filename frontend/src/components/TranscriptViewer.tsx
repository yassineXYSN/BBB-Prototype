import { useMemo, useState } from "react";
import { formatClock } from "../api";
import type { Transcript } from "../api";

export default function TranscriptViewer({ transcript }: { transcript: Transcript }) {
  const [query, setQuery] = useState("");
  const [onlyMatches, setOnlyMatches] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return transcript.segments;
    return transcript.segments.filter(
      (s) =>
        s.text.toLowerCase().includes(q) ||
        (s.speaker ?? "").toLowerCase().includes(q) ||
        formatClock(s.start_seconds).includes(q),
    );
  }, [query, transcript.segments]);

  const visible = onlyMatches && query ? filtered : transcript.segments;
  const matchCount = query ? filtered.length : 0;

  const download = (format: "md" | "txt") => {
    let content: string;
    if (format === "txt") {
      content = transcript.full_text;
    } else {
      const lines = transcript.segments.map((s) => {
        const speaker = s.speaker ? `**${s.speaker}**` : `*[${formatClock(s.start_seconds)}]*`;
        return `- ${speaker} (${formatClock(s.start_seconds)}): ${s.text}`;
      });
      content = `# Interview transcript\n\n${lines.join("\n\n")}\n`;
    }
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `transcript.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="card transcript">
      <div className="transcript-head">
        <div>
          <h3>Transcript</h3>
          <p className="muted small">
            {transcript.segments.length} segments · lang {transcript.language} · source{" "}
            {transcript.source} · raw recording deleted after storage
          </p>
        </div>
        <div className="transcript-actions">
          <input
            className="search"
            placeholder="Search transcript…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <label className="checkbox">
            <input
              type="checkbox"
              checked={onlyMatches}
              onChange={(e) => setOnlyMatches(e.target.checked)}
            />
            only matches
          </label>
          <button className="btn btn-small" onClick={() => download("md")}>
            .md
          </button>
          <button className="btn btn-small" onClick={() => download("txt")}>
            .txt
          </button>
          <button
            className="btn btn-small"
            onClick={() => navigator.clipboard.writeText(transcript.full_text)}
          >
            Copy all
          </button>
        </div>
      </div>

      {query && (
        <p className="muted small">
          {matchCount} matching segment{matchCount === 1 ? "" : "s"}
        </p>
      )}

      <ol className="segments">
        {visible.map((s) => {
          const hit =
            query.trim() !== "" &&
            (s.text.toLowerCase().includes(query.trim().toLowerCase()) ||
              (s.speaker ?? "").toLowerCase().includes(query.trim().toLowerCase()));
          return (
            <li key={s.idx} className={hit ? "segment-hit" : ""}>
              <span className="seg-time">{formatClock(s.start_seconds)}</span>
              <div>
                {s.speaker && <strong className="seg-speaker">{s.speaker} </strong>}
                <span>{s.text}</span>
              </div>
            </li>
          );
        })}
        {!visible.length && <li className="empty">No segments match.</li>}
      </ol>
    </section>
  );
}
