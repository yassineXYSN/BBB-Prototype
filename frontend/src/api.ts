const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

export interface User {
  public_id: string;
  email: string;
  full_name: string;
  role: string;
}

export interface Candidate {
  public_id: string;
  full_name: string;
  email: string;
}

export interface TokenOut {
  role: string;
  link: string;
  expires_at: string;
}

export interface Recording {
  record_id: string;
  state: string;
  name: string;
  play_url: string;
  purged_at: string | null;
}

export interface TranscriptSegment {
  idx: number;
  start_seconds: number;
  end_seconds: number;
  speaker: string | null;
  text: string;
}

export interface Transcript {
  id: number;
  language: string;
  source: string;
  full_text: string;
  created_at: string;
  segments: TranscriptSegment[];
}

export interface AuditEntry {
  event: string;
  detail: string;
  created_at: string;
}

export interface Interview {
  public_id: string;
  title: string;
  status: string;
  scheduled_start: string | null;
  duration_minutes: number;
  timezone: string;
  language: string;
  failure_reason: string | null;
  rating: number | null;
  verdict: string | null;
  candidate: Candidate | null;
  meeting_id?: string | null;
  notes?: string;
  tokens?: TokenOut[];
  recordings?: Recording[];
  transcripts?: Transcript[];
  audit_logs?: AuditEntry[];
}

export interface Template {
  public_id: string;
  title: string;
  description: string;
  duration_minutes: number;
  language: string;
  created_at: string;
}

export interface JoinPreview {
  interview_title: string;
  scheduled_start: string | null;
  duration_minutes: number;
  timezone: string;
  role: string;
  candidate_name: string | null;
  status: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function getToken(): string | null {
  return localStorage.getItem("token");
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem("token", token);
  else localStorage.removeItem("token");
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!resp.ok) {
    const detail = (data as { detail?: string | { msg?: string } })?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg ?? JSON.stringify(d)).join(", ")
          : `Request failed (${resp.status})`;
    throw new ApiError(resp.status, message);
  }
  return data as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string; user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<User>("/auth/me"),
  interviews: (params: { status?: string; search?: string; upcoming?: boolean } = {}) => {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.search) qs.set("search", params.search);
    if (params.upcoming) qs.set("upcoming", "true");
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<Interview[]>(`/interviews${suffix}`);
  },
  stats: () => request<Record<string, number>>("/interviews/stats"),
  interview: (id: string) => request<Interview>(`/interviews/${id}`),
  schedule: (payload: unknown) =>
    request<Interview>("/interviews", { method: "POST", body: JSON.stringify(payload) }),
  reschedule: (id: string, scheduled_start: string) =>
    request<Interview>(`/interviews/${id}/reschedule`, {
      method: "POST",
      body: JSON.stringify({ scheduled_start }),
    }),
  cancel: (id: string) => request<Interview>(`/interviews/${id}/cancel`, { method: "POST" }),
  update: (id: string, payload: Record<string, unknown>) =>
    request<Interview>(`/interviews/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  moderatorJoin: (id: string) =>
    request<{ join_url: string }>(`/interviews/${id}/moderator-join`, { method: "POST" }),
  process: (id: string) => request<Interview>(`/interviews/${id}/process`, { method: "POST" }),
  transcript: (id: string) => request<Transcript>(`/interviews/${id}/transcript`),
  templates: () => request<Template[]>("/templates"),
  createTemplate: (payload: unknown) =>
    request<Template>("/templates", { method: "POST", body: JSON.stringify(payload) }),
  deleteTemplate: (id: string) => request<void>(`/templates/${id}`, { method: "DELETE" }),
  joinPreview: (token: string) => request<JoinPreview>(`/public/join/${token}/preview`),
  joinMeeting: (token: string) =>
    request<{ join_url: string }>(`/public/join/${token}`, { method: "POST" }),
  health: () =>
    request<{ status: string; env: string }>("/health").catch(() => ({ status: "down", env: "?" })),
};

export function formatDateTime(iso: string | null, timezone?: string): string {
  if (!iso) return "—";
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: timezone && timezone !== "UTC" ? timezone : undefined,
  }).format(date);
}

export function formatClock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
