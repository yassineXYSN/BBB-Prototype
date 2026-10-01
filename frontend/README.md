# InterviewOps frontend

React + TypeScript (Vite) SPA for the BigBlueButton interview platform.
See the root [README](../README.md) for full setup instructions.

```bash
npm install
cp .env.example .env.local   # VITE_API_URL=http://localhost:8000/api/v1
npm run dev                  # http://localhost:5173
npm run build                # type-check + production bundle
npm run lint
```

Pages: login, dashboard (live status), schedule, interview detail
(lifecycle timeline, invite links, transcript viewer with search/export, review),
templates, and the public candidate join page (`/join/:token`).
