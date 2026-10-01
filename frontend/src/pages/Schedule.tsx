import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Template } from "../api";

interface FormState {
  title: string;
  scheduled_start: string;
  duration_minutes: number;
  timezone: string;
  language: string;
  candidate_name: string;
  candidate_email: string;
  template_id: string;
  notes: string;
  send_email: boolean;
}

const initialState = (): FormState => {
  const inOneDay = new Date(Date.now() + 24 * 3600 * 1000);
  inOneDay.setMinutes(0, 0, 0);
  const local = new Date(inOneDay.getTime() - inOneDay.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
  return {
    title: "",
    scheduled_start: local,
    duration_minutes: 45,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    language: "en",
    candidate_name: "",
    candidate_email: "",
    template_id: "",
    notes: "",
    send_email: true,
  };
};

export default function Schedule() {
  const [form, setForm] = useState<FormState>(initialState);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    api.templates().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const applyTemplate = (publicId: string) => {
    set("template_id", publicId);
    const tpl = templates.find((t) => t.public_id === publicId);
    if (tpl) {
      setForm((f) => ({
        ...f,
        template_id: publicId,
        title: f.title || tpl.title,
        duration_minutes: tpl.duration_minutes,
        language: tpl.language,
      }));
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload = {
        ...form,
        template_id: form.template_id || null,
        scheduled_start: `${form.scheduled_start}:00`,
      };
      const created = await api.schedule(payload);
      navigate(`/interviews/${created.public_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scheduling failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Schedule an interview</h1>
          <p className="muted">
            A BBB meeting room + invite links are created automatically when you schedule.
          </p>
        </div>
      </header>

      <form className="card form-grid" onSubmit={submit}>
        <label className="span-2">
          Candidate name
          <input
            required
            value={form.candidate_name}
            onChange={(e) => set("candidate_name", e.target.value)}
            placeholder="Cara Candidate"
          />
        </label>
        <label className="span-2">
          Candidate email
          <input
            type="email"
            required
            value={form.candidate_email}
            onChange={(e) => set("candidate_email", e.target.value)}
            placeholder="cara@example.com"
          />
        </label>

        <label className="span-4">
          Interview template (optional)
          <select value={form.template_id} onChange={(e) => applyTemplate(e.target.value)}>
            <option value="">No template — custom interview</option>
            {templates.map((t) => (
              <option key={t.public_id} value={t.public_id}>
                {t.title} · {t.duration_minutes} min
              </option>
            ))}
          </select>
        </label>

        <label className="span-4">
          Title
          <input
            required
            value={form.title}
            onChange={(e) => set("title", e.target.value)}
            placeholder="Backend Engineer Screen"
          />
        </label>

        <label>
          Date &amp; time
          <input
            type="datetime-local"
            required
            value={form.scheduled_start}
            onChange={(e) => set("scheduled_start", e.target.value)}
          />
        </label>
        <label>
          Duration (min)
          <input
            type="number"
            min={5}
            max={480}
            value={form.duration_minutes}
            onChange={(e) => set("duration_minutes", Number(e.target.value))}
          />
        </label>
        <label>
          Timezone
          <input
            value={form.timezone}
            onChange={(e) => set("timezone", e.target.value)}
          />
        </label>
        <label>
          Caption language
          <select value={form.language} onChange={(e) => set("language", e.target.value)}>
            <option value="en">English</option>
            <option value="de">German</option>
            <option value="fr">French</option>
            <option value="es">Spanish</option>
          </select>
        </label>

        <label className="span-4">
          Notes for the interviewer
          <textarea
            rows={3}
            value={form.notes}
            onChange={(e) => set("notes", e.target.value)}
            placeholder="Focus areas, links to take-home tasks…"
          />
        </label>

        <label className="checkbox span-4">
          <input
            type="checkbox"
            checked={form.send_email}
            onChange={(e) => set("send_email", e.target.checked)}
          />
          Send invitation emails now (console mailer in dev)
        </label>

        {error && <div className="alert alert-error span-4">{error}</div>}

        <div className="span-4 actions">
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Scheduling…" : "Schedule interview"}
          </button>
        </div>
      </form>
    </>
  );
}
