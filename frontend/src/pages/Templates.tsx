import { useEffect, useState } from "react";
import { api } from "../api";
import type { Template } from "../api";

export default function Templates() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [form, setForm] = useState({ title: "", duration_minutes: 45, description: "" });
  const [error, setError] = useState<string | null>(null);

  const load = () => api.templates().then(setTemplates).catch(() => undefined);
  useEffect(() => {
    load();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.createTemplate({ ...form, language: "en", questions: [] });
      setForm({ title: "", duration_minutes: 45, description: "" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create template");
    }
  };

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Interview templates</h1>
          <p className="muted">Reusable defaults for duration, language and question sets.</p>
        </div>
      </header>

      <form className="card form-grid" onSubmit={submit}>
        <label className="span-2">
          Title
          <input
            required
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            placeholder="Frontend Engineer Screen"
          />
        </label>
        <label>
          Duration (min)
          <input
            type="number"
            min={5}
            max={480}
            value={form.duration_minutes}
            onChange={(e) => setForm((f) => ({ ...f, duration_minutes: Number(e.target.value) }))}
          />
        </label>
        <label className="span-3">
          Description
          <input
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
          />
        </label>
        {error && <div className="alert alert-error span-4">{error}</div>}
        <div className="span-4">
          <button className="btn btn-primary">Create template</button>
        </div>
      </form>

      <div className="card-list">
        {templates.map((t) => (
          <div className="card template-card" key={t.public_id}>
            <div>
              <strong>{t.title}</strong>
              <div className="muted small">{t.description || "No description"}</div>
            </div>
            <div className="row gap">
              <span className="badge badge-scheduled">{t.duration_minutes} min</span>
              <button
                className="btn btn-small btn-danger"
                onClick={() => api.deleteTemplate(t.public_id).then(load)}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
        {!templates.length && <div className="card empty-card">No templates yet.</div>}
      </div>
    </>
  );
}
