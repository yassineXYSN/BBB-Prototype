# InterviewOps — Interview platform prototype (FastAPI + React + BigBlueButton)

End-to-end interview lifecycle on top of a self-hosted BigBlueButton server:

**plan → schedule → invite → join (BBB room) → record → webhook → transcript → delete raw recording → review**

Only the transcription data is kept: once the caption track is stored in our database,
the raw BBB recording is deleted (`deleteRecordings`). TURN/STUN comes free — BBB's
official installer sets up coturn on the same VPS.

---

## Architecture

```
[React SPA :5173] ── REST ──▶ [FastAPI :8000] ── signed API calls ──▶ [BBB + coturn (VPS)]
                                   │  ▲
                     SQLite (file) ─┘  │  webhooks: events / recording-ready / meeting-ended
                                       └── POST /api/v1/webhooks/bbb/...

Transcript flow:  recording-ready ─▶ getRecordings ─▶ getRecordingTextTracks (WebVTT)
                  ─▶ parse + store segments ─▶ deleteRecordings ─▶ only text remains
```

| Concern | Implementation |
| --- | --- |
| Video / room / recording / captions | BigBlueButton REST API (checksum-signed requests) |
| Lifecycle events | `bbb-webhooks` module + `meta_endCallbackUrl` + `meta_bbb-recording-ready-url` (JWT) |
| Safety net if a webhook is lost | background sweep job polls `getRecordings` / `isMeetingRunning` every 60s |
| Transcription | BBB caption tracks (WebVTT) fetched via `getRecordingTextTracks` |
| Data retention | transcript stored → `deleteRecordings` → `recording.purged_at` set |
| Scheduling/invites | JWT admin auth, magic-link tokens (candidate + interviewer), console/SMTP mailer |
| DB | SQLite via SQLAlchemy 2 + Alembic (swap `DATABASE_URL` for Postgres later) |

## Repository layout

```
backend/            FastAPI app
  app/api/          routers: auth, interviews, templates, public join, webhooks, health
  app/models/       SQLAlchemy models (accounts, interview lifecycle, recordings/transcripts)
  app/services/     bbb/ (client + VTT parser), scheduling, pipeline, jobs, mail, state machine
  alembic/          migrations
  scripts/spike_bbb.py   connectivity + captions check
  tests/            49 tests (checksum vector, state machine, webhooks, pipeline, API flow, invite roles, mail)
frontend/           React + TypeScript (Vite)
mockbbb/            mock BigBlueButton server for localhost development
docker-compose.yml  API + SPA + mock BBB containers
```

## Local development

Backend (Python 3.11+):

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # or pip3/activate on macOS/Linux
cp .env.example .env                  # fill BBB_URL, BBB_SHARED_SECRET, APP_SECRET_KEY
.venv/Scripts/alembic upgrade head    # create schema
.venv/Scripts/python -m app.seed      # demo users + sample interview
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```

Frontend (Node 20.19+):

```bash
cd frontend
npm install
cp .env.example .env.local            # VITE_API_URL=http://localhost:8000/api/v1
npm run dev                           # http://localhost:5173
```

Seeded accounts: `admin@example.com / admin123` · `interviewer@example.com / interviewer123`

## BigBlueButton setup

**Local development needs no BBB server at all:** the compose stack ships a mock
(`mockbbb/`) that speaks the real BBB API — valid checksums, meetings, join pages,
recordings, caption tracks, webhooks — so the whole lifecycle runs on localhost. See
[Mock BBB](#mock-bbb-localhost) below.

For a real server:

1. Install BBB on your VPS with the official script (it also installs coturn — no extra TURN server needed).
2. Get credentials: `bbb-conf --secret` → set `BBB_URL` and `BBB_SHARED_SECRET` in `backend/.env`.
3. Verify connectivity **and caption availability** (the riskiest unknown):

```bash
cd backend
.venv/Scripts/python scripts/spike_bbb.py
```

   The spike creates/ends a meeting, registers a webhook, and — if any recording exists —
   downloads its caption track. If step 7 reports *NO CAPTION TRACKS*, enable BBB's live
   transcription (or upload tracks with `putRecordingTextTrack`) before relying on the pipeline.
4. Webhooks: BBB's `bbb-webhooks` module must be running (part of standard BBB installs).
   The app registers a global hook at startup and re-checks it every minute.

### Webhook endpoints (path token = unguessable secret)

| Endpoint | Source | Purpose |
| --- | --- | --- |
| `POST /api/v1/webhooks/bbb/events/{token}` | bbb-webhooks module | user-joined → `live`, meeting-ended, recording-started |
| `GET  /api/v1/webhooks/bbb/meeting-ended/{token}` | `meta_endCallbackUrl` | end of meeting + `recordingmarks` flag |
| `POST /api/v1/webhooks/bbb/recording-ready/{token}` | `meta_bbb-recording-ready-url` | JWT (`shared secret`) → start transcript pipeline |

`BBB_WEBHOOK_VERIFY=api` additionally checks an API-style checksum on callbacks
(experimental — keep `none` if your bbb-webhooks version signs differently).

## Mock BBB (localhost)

BBB itself only runs on Ubuntu — it cannot run on a Windows dev machine — so the
compose stack includes `mockbbb/`, a small FastAPI service that implements the BBB
REST API with **real checksum validation** (`sha1(call + query + secret)`) plus the
callback behaviour of a real server:

- `create / join / end / isMeetingRunning / getMeetingInfo / getMeetings`
- `getRecordings / deleteRecordings / publishRecordings / getRecordingTextTracks`
  (canned WebVTT captions), `hooks/create|list|destroy`
- on join it emits the `user-joined` webhook event; on end it fires
  `meta_endCallbackUrl` immediately and the signed `meta_bbb-recording-ready-url`
  JWT a couple of seconds later — exactly the callbacks a production BBB sends
- the mock room page (the `join` URL) is a **small WebRTC meeting room**:
  participants register on join and see each other live in the roster, camera
  and audio are shared peer-to-peer (perfect negotiation + Google STUN), tab
  close announces a leave, and a **Finish interview** button ends the meeting
  and fires those callbacks

Configuration (already set for local dev):

| Variable | Value | Used by |
| --- | --- | --- |
| `BBB_URL` | `http://mockbbb:9090/bigbluebutton/` in Docker (compose override), `http://localhost:9090/bigbluebutton/` host-side | backend → BBB API |
| `BBB_PUBLIC_URL` | `http://localhost:5174/bigbluebutton` (web origin — nginx proxies `/bigbluebutton/` to the mock, so SPA + room share one origin) | browser-facing join URLs |
| `BBB_SHARED_SECRET` | `dev-mock-secret-localhost` | checksums + recording-ready JWT |

Quick demo of the full lifecycle:

```bash
docker compose up --build
# 1. log in at http://localhost:5174 (admin@example.com / admin123), schedule an interview
# 2. open the CANDIDATE invite link in one window → "Join interview now"
# 3. open the INTERVIEWER invite link (shown on the interview page) in a second
#    window → "Join as moderator" — both parties now see each other in the room
#    (grant camera access; TAP once inside the room to turn on sound & video;
#    both links are role-aware, either order works)
# 4. click "Finish interview" in the mock room
# 5. within ~5s the interview detail page shows: live → ended → processing → transcribed,
#    with the transcript stored and the raw recording purged
docker compose exec api python scripts/spike_bbb.py   # connectivity check from inside Docker
```

Notes:

- mock state is in-memory: restarting `mockbbb` forgets meetings — schedule a new
  interview after a full restart
- invite links are role-aware: the candidate token enters as **VIEWER**, the
  interviewer token as **MODERATOR**, both through the same `/join/<token>` page
- camera needs a **secure context**: `http://localhost:5174` works out of the box
  on this machine. From another device (phone, LAN IP) the browser blocks
  `getUserMedia` on plain http — run `.\scripts\start-tunnel.ps1` to put a free
  HTTPS tunnel (cloudflared quick tunnel) in front of the stack; it rewrites
  `PUBLIC_APP_URL` + `BBB_PUBLIC_URL` to the tunnel origin, restarts the api,
  and verifies everything end-to-end. `-Url https://…` uses your own tunnel
  (ngrok etc.), `-Stop` reverts to localhost. nginx serves SPA, API and room
  from that one origin, so a single tunnel covers the whole app.
- browsers block unmuted autoplay until a user gesture: the room page shows
  remote video **muted** right away (picture on, silent) with a "Tap anywhere
  to turn on sound & video" hint — the first tap/click/keypress unmutes every
  remote tile for good. Each page posts a heartbeat every 10s, so
  `docker compose logs -f mockbbb` streams `status <pid>(name) … conn/ice/
  tracks/el` lines you can watch to confirm the P2P connection settles
- cross-NAT video relies on Google's public STUN only; symmetric NATs may need a
  TURN server (or a real BBB, which runs its own media server)
- host-side backend (uvicorn outside Docker) also works, but the mock cannot call
  back into `localhost` from its container — the 60s background sweep picks the
  lifecycle up instead (same result, just slower)
- when you get a real BBB server: set `BBB_URL`/`BBB_SHARED_SECRET` in
  `backend/.env` (and `BBB_URL` in the project-root `.env` for Docker), and drop
  the `mockbbb` service from `docker-compose.yml`

## Interview state machine

```
draft → scheduled → waiting → live → ended → processing → transcribed → archived
                       └────────────┴──────────┴→ failed      (retry → processing)
any non-terminal → cancelled
```

Every transition is written to `audit_logs` and shown on the interview detail page.

## Docker Compose

```bash
cp backend/.env.example backend/.env    # configure BBB + secrets
docker compose up --build
# SPA  → http://localhost:5173
# API  → http://localhost:8000/api/v1
```

Host ports are overridable via the project-root `.env` (gitignored). This machine
ships one that avoids ports already in use:

```bash
# .env (project root)
API_PORT=8001   # API  → http://localhost:8001/api/v1
WEB_PORT=5174   # SPA  → http://localhost:5174  (also proxies /api/ and /bigbluebutton/)
BBB_PORT=9090   # mock BBB direct → http://localhost:9090 (join URLs use the web origin)
API_BASE_URL=http://api:8000   # webhook callbacks, reachable from the mock container
```

Startup runs `alembic upgrade head` + seed automatically; data persists in the
`app-data` volume (`sqlite:////data/app.db`).

Point `PUBLIC_APP_URL`/`API_BASE_URL` at your public hostnames when deploying to the VPS,
and put TLS in front (Caddy/nginx) — BBB callbacks require HTTPS in production.
**Step-by-step VPS handover checklist: see [DEPLOY.md](DEPLOY.md)** (includes the
bundled TURN relay and an optional one-command HTTPS front via
`docker compose --profile https up -d`).

## Tests & quality

```bash
cd backend && .venv/Scripts/pytest -q && .venv/Scripts/ruff check .
cd frontend && npm run build && npm run lint
```

- Checksum test uses the official BBB docs vector (`sha1(call + query + secret)`).
- Pipeline tests prove the retention rule: **no transcript → no purge; transcript stored → recording deleted.**

## Security notes

- BBB shared secret never leaves the server; join URLs are signed server-side.
- Invite links are random tokens with expiry (7 days), single-role and revocable on reschedule/cancel.
- Webhook URLs carry a random path token; recording-ready callbacks must be valid JWTs.
- Raw recordings are deleted by default (`KEEP_RAW_RECORDING=false`).

## Roadmap after the prototype

Postgres migration, multi-tenant orgs, Whisper fallback when BBB captions are missing,
AI summaries/scoring over stored transcripts, calendar integrations (ICS invites).
