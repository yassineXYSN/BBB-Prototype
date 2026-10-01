# Deploy on a VPS — step-by-step checklist

Everything (app, room server, TURN relay) runs with **one `docker compose` command**.
No coding, no external services, no signups. Target: ~15 minutes on a fresh VPS.

---

## Before you start

- [ ] **VPS**: Ubuntu 22.04/24.04, public IP, sudo access (2 vCPU / 2 GB RAM is plenty)
- [ ] **Firewall ports** open — in BOTH the OS firewall (step 4) and your provider's
      firewall/security-group panel (Hetzner, OVH, AWS, Contabo, …):
      `22/tcp` (ssh), `80/tcp` (app), `3478/tcp+udp`, `49160-49200/udp` (media relay),
      `443/tcp` (only if you use the optional HTTPS mode)
- [ ] *(optional)* a domain with an `A` record → VPS IP (only for proper HTTPS, Mode C)

### How video works (read this once)

The app runs on the VPS, but the **video flows directly between the two browsers**.
Because that connection is peer-to-peer, calls across different networks sometimes
need a relay (TURN). **This project ships its own TURN relay as a container — on a
public VPS it just works** (the VPS *is* the public endpoint, so there is nothing to
sign up for and no NAT to traverse). For the very first test you don't even need it.

### Test modes

| Mode | What it proves | Camera | TURN needed | Extra setup |
|------|----------------|--------|-------------|-------------|
| **A** — one laptop, two browser profiles, app via SSH tunnel | full workflow + A/V | ✓ (localhost is a secure context) | no (same machine connects directly) | none |
| **B** — two real devices over the internet | cross-network A/V | needs a Chrome flag (test-only) *or* Mode C | yes — the bundled coturn on the VPS handles it | open relay ports |
| **C** — Mode B behind a real domain + HTTPS | everything, polished | ✓ | yes — bundled coturn | domain + 1 command |

---

## Step 1 — install Docker (once)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# log out and back in, then verify:
docker --version && docker compose version
```

## Step 2 — get the project

```bash
git clone https://github.com/yassineXYSN/BBB-Prototype.git ~/interviewops
cd ~/interviewops
```

*(No git? Download the ZIP from the repo page — green **Code** button →
**Download ZIP** — then `unzip BBB-Prototype.zip -d ~/interviewops` and `cd` in.)*

## Step 3 — configure (edit exactly 2 files, no other changes)

### File 1: `.env` (project root) — replace the whole file with:

```env
API_PORT=8000
WEB_PORT=80
BBB_PORT=9090
API_BASE_URL=http://api:8000
PUBLIC_APP_URL=http://YOUR_VPS_IP
```

> Only if you use **Mode C** (domain + HTTPS), use these two lines instead of the
> last two above: `WEB_PORT=8080` and `HTTPS_SITE=interviews.yourdomain.com`,
> plus `PUBLIC_APP_URL=https://interviews.yourdomain.com`.

### File 2: `backend/.env` — change only these lines:

| Find the line | Change its value to |
|---|---|
| `PUBLIC_APP_URL=…` | `PUBLIC_APP_URL=http://YOUR_VPS_IP` (same as root `.env`) |
| `BBB_PUBLIC_URL=…` | `BBB_PUBLIC_URL=http://YOUR_VPS_IP/bigbluebutton` |
| `SMTP_HOST=smtp.gmail.com` | `SMTP_HOST=` **(leave empty — prints invite links to the logs instead of sending mail; also clear `SMTP_USER=`/`SMTP_PASSWORD=` — those aren't your credentials)** |
| `TURN_URL=turn:localhost:3478` | `TURN_URL=turn:YOUR_VPS_IP:3478` |

Everything else stays as shipped (`BBB_SHARED_SECRET`, webhook token, etc. are
already set for the bundled mock BBB).

> Mode C: `BBB_PUBLIC_URL=https://interviews.yourdomain.com/bigbluebutton`.

## Step 4 — open the firewall

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 3478/tcp
sudo ufw allow 3478/udp
sudo ufw allow 49160:49200/udp
sudo ufw --force enable
```

Also open the same ports in your VPS provider's firewall panel if it has one.

## Step 5 — start it

```bash
cd ~/interviewops
docker compose up -d --build    # first run: 2–4 minutes (downloads + builds)
docker compose ps               # api, mockbbb, turn, web → all "running"
```

The database seeds itself on first start (demo users + a demo interview).

## Step 6 — verify

```bash
curl -s http://localhost/api/v1/health   # {"status":"ok",...}
curl -s http://localhost/api/v1/ready    # "database": true, "bbb": true
```

Open `http://YOUR_VPS_IP` in a browser → log in with `admin@example.com` / `admin123`.

---

## Step 7 — first test

### Mode A (recommended first — proves the whole thing in 5 minutes)

1. Log in → **Schedule** an interview (the demo template is pre-filled).
2. Open the interview → **"Room & invites"** → copy both invite links.
3. On your laptop, tunnel the app through ssh:
   ```bash
   ssh -L 8080:localhost:80 user@YOUR_VPS_IP
   # keep this terminal open
   ```
4. In the copied links, replace `http://YOUR_VPS_IP` with `http://localhost:8080`,
   then open the **candidate** link in one browser profile and the **interviewer**
   link in a second profile (or Chrome + Firefox). Allow camera + mic, tap once
   inside each room page (unlocks sound/video), confirm you can see and hear both.
5. Click **Finish interview** → the interview page transitions
   `live → ended → processing → transcribed` and stores the transcript.

### Mode B (two real devices, e.g. phone + another laptop)

1. Make sure relay ports from step 4 are open, and `TURN_URL=turn:YOUR_VPS_IP:3478`
   is set — the video will relay through the VPS automatically when the two networks
   can't connect directly (watch `docker compose logs -f mockbbb` for
   `pair=relay` in the heartbeat lines).
2. Camera access: browsers only allow it on secure pages, and `http://VPS_IP` is
   not one. Either do Mode C (proper HTTPS), or — **test-only hack** — on each
   device open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add
   `http://YOUR_VPS_IP`, and relaunch the browser.
3. Open both invite links on the two devices and run the same flow as Mode A.

### Mode C (you have a domain → real HTTPS, works on any device, no flags)

```bash
# edit root .env:  WEB_PORT=8080, HTTPS_SITE=interviews.yourdomain.com,
#                  PUBLIC_APP_URL=https://interviews.yourdomain.com
# edit backend/.env: PUBLIC_APP_URL + BBB_PUBLIC_URL to the https:// origin
sudo ufw allow 443/tcp
docker compose --profile https up -d
```

Caddy obtains a Let's Encrypt certificate automatically (no certbot, nothing to
renew). Invite links now use `https://…` and camera/mic work everywhere.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| 502 Bad Gateway | `docker compose restart web` |
| can't log in / empty page | `docker compose logs api` (runs migrations + seed on start) |
| no video / no relay candidates | `docker compose logs turn`, re-check ufw **and** the provider firewall for `3478` + `49160-49200` |
| camera banner "needs a secure page" | Mode A should be on `http://localhost:8080`; for real devices use Mode C or the Chrome flag |
| invite links point somewhere wrong | both `.env` files must have the same origin (step 3) |
| see what's happening | `docker compose logs -f api mockbbb` |
| start over from scratch | `docker compose down -v` (wipes the database) |

Each peer's selected ICE connection type is logged as `pair=host|srflx|relay` —
`relay` means the video is going through the VPS relay.
