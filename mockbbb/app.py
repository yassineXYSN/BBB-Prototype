"""Mock BigBlueButton server for local development (NOT for production).

Implements enough of the BBB REST API — with real SHA-1 checksum
validation (sha1(call + query + sharedSecret)) — to exercise the whole
interview lifecycle on localhost:

  create / join / end / isMeetingRunning / getMeetingInfo / getMeetings
  getRecordings / deleteRecordings / publishRecordings
  getRecordingTextTracks (+ canned WebVTT captions)
  hooks/create / hooks/list / hooks/destroy

The mock room is a small WebRTC meeting room, not a placeholder:

  * participants register on join and appear in each other's roster
  * camera/mic/audio are shared peer-to-peer (perfect negotiation,
    STUN on Google's public servers) between everyone in the room
  * leave is announced on unload so ghosts disappear from the roster

When a meeting ends (API `end` or the button on the mock room page) it
fires the same callbacks a real BBB would:

  * POST registered webhook events (user-joined / meeting-ended)
  * GET  meta_endCallbackUrl?meetingID=..&recordingmarks=..   (immediately)
  * POST meta_bbb-recording-ready-url  signed_parameters=<JWT HS256>
         (after MOCK_CAPTIONS_DELAY seconds, simulating recording processing)

State is in-memory: restarting the container forgets meetings/recordings.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlencode
from xml.sax.saxutils import escape as xml_escape

import httpx
import jwt
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

SECRET = os.environ.get("BBB_SHARED_SECRET") or "dev-mock-secret-localhost"
CAPTIONS_DELAY = float(os.environ.get("MOCK_CAPTIONS_DELAY", "2"))
CAPTION_LANG = os.environ.get("CAPTION_LANG", "en-US")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
log = logging.getLogger("mockbbb")

app = FastAPI(title="Mock BigBlueButton", docs_url=None, redoc_url=None)

_lock = threading.Lock()
meetings: dict[str, dict[str, Any]] = {}
recordings: dict[str, dict[str, Any]] = {}
hooks: dict[str, dict[str, Any]] = {}
signals: dict[str, list[dict[str, Any]]] = {}

CAPTION_VTT = """WEBVTT

00:00:00.000 --> 00:00:05.400
<v Interviewer>Thanks for joining. To start, could you give me a quick overview of your background?

00:00:05.400 --> 00:00:12.800
<v Candidate>Sure. I have spent the last five years building backend services, mostly in Python, with some Go for high-throughput workers.

00:00:12.800 --> 00:00:18.200
<v Interviewer>Tell me about a difficult production incident you handled recently.

00:00:18.200 --> 00:00:27.600
<v Candidate>We had a memory leak in a connection pool that only appeared under peak load. I added structured profiling, bisected the deploy history, and found a stale cache reference.

00:00:27.600 --> 00:00:33.100
<v Interviewer>How did you verify the fix?

00:00:33.100 --> 00:00:41.900
<v Candidate>I replayed the load test against a staging cluster with heap dumps before and after, then watched the resident set size for forty-eight hours in production.

00:00:41.900 --> 00:00:47.300
<v Interviewer>Good. How do you approach code review for a teammate?

00:00:47.300 --> 00:00:56.500
<v Candidate>I look for correctness first, then readability and tests. If I cannot understand a change in a couple of minutes, I ask the author to add a comment or split the pull request.

00:00:56.500 --> 00:01:02.700
<v Interviewer>Where do you see yourself growing over the next couple of years?

00:01:02.700 --> 00:01:11.400
<v Candidate>I want to go deeper on distributed systems and spend more time mentoring, because explaining design decisions is where I learn the most.

00:01:11.400 --> 00:01:16.900
<v Interviewer>That was helpful. We will follow up by email with the next steps. Thanks for your time.

00:01:16.900 --> 00:01:20.500
<v Candidate>Thank you — I enjoyed the conversation.
"""


# --- helpers --------------------------------------------------------------

def _ok(body: str = "") -> Response:
    return Response(
        content=f"<response><returncode>SUCCESS</returncode>{body}</response>",
        media_type="application/xml",
    )


def _fail(message_key: str, message: str) -> Response:
    return Response(
        content=(
            "<response><returncode>FAILED</returncode>"
            f"<message>{xml_escape(message)}</message>"
            f"<messageKey>{xml_escape(message_key)}</messageKey></response>"
        ),
        media_type="application/xml",
    )


def _esc(value: Any) -> str:
    return xml_escape(str(value if value is not None else ""))


def _checksum_ok(call: str, raw_query: str) -> bool:
    received = ""
    kept: list[str] = []
    for part in raw_query.split("&") if raw_query else []:
        if part.startswith("checksum="):
            received = part[len("checksum=") :]
        elif part:
            kept.append(part)
    expected = hashlib.sha1(f"{call}{'&'.join(kept)}{SECRET}".encode()).hexdigest()
    return bool(received) and secrets.compare_digest(received, expected)


# Browsers navigating to a failed API call get a readable HTML page instead of
# raw XML ("The document tree is shown below…" confuses everyone).
_FRIENDLY_ERRORS = {
    "invalidPassword": (
        "This room link is no longer valid",
        "The link in your browser was issued before the room was re-created. "
        "Go back to your invite and click Join again — you will get a fresh link.",
    ),
    "checksumError": (
        "This room link has expired",
        "Signed links can be superseded. Return to your invite page and click "
        "Join again for a fresh link.",
    ),
    "invalidMeetingIdentifier": (
        "Room not found",
        "It does not exist (yet). Return to your invite and join again — the "
        "app re-creates missing rooms automatically.",
    ),
    "meetingEnded": (
        "This meeting has ended",
        "The interview is over and nobody can join anymore.",
    ),
}


def _browser_error(request: Request, response: Response) -> Response:
    if "text/html" not in request.headers.get("accept", ""):
        return response  # API client / test — keep the XML
    body = response.body.decode("utf-8", "replace")
    if "<returncode>FAILED" not in body:
        return response
    key_m = re.search(r"<messageKey>(.*?)</messageKey>", body)
    msg_m = re.search(r"<message>(.*?)</message>", body)
    key = html.unescape(key_m.group(1)) if key_m else ""
    msg = html.unescape(msg_m.group(1)) if msg_m else ""
    title, hint = _FRIENDLY_ERRORS.get(key, ("Something went wrong", msg))
    return HTMLResponse(
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)} — mock room</title>"
        "<style>:root{color-scheme:dark}*{box-sizing:border-box}"
        "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
        "font-family:system-ui,sans-serif;background:#0f1420;color:#e7ecf3}"
        ".card{background:#1a2336;border:1px solid #263048;border-radius:12px;"
        "padding:32px;max-width:460px;text-align:center}"
        "h1{font-size:20px;margin:0 0 12px}p{color:#93a4c3;line-height:1.5;margin:0 0 20px}"
        "button{font:inherit;padding:10px 18px;border-radius:8px;border:1px solid #33415f;"
        "background:#223050;color:#e7ecf3;cursor:pointer}button:hover{background:#2b3b60}"
        "</style></head><body><div class=\"card\">"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(hint)}</p>"
        "<button onclick=\"history.length>1?history.back():location.href='/'\">Go back</button>"
        "</div></body></html>"
    )


def _hook_wants(hook: dict[str, Any], event_id: str, meeting_id: str) -> bool:
    if hook.get("meeting_id") and hook["meeting_id"] != meeting_id:
        return False
    events = hook.get("events") or []
    return not events or event_id in events


def _emit_event(event_id: str, meeting_id: str, extra: dict[str, str] | None = None) -> None:
    with _lock:
        meeting = meetings.get(meeting_id)
        meeting_attrs: dict[str, str] = {
            "external-meeting-id": meeting_id,
            "internal-meeting-id": meeting["internal_id"] if meeting else meeting_id,
        }
        if meeting:
            meeting_attrs["name"] = meeting["name"]
        attributes: dict[str, Any] = {"meeting": meeting_attrs}
        attributes.update(extra or {})
        targets = [h for h in hooks.values() if _hook_wants(h, event_id, meeting_id)]
    if not targets:
        log.info("event %s for %s: no hook registered", event_id, meeting_id)
        return
    ts = int(time.time() * 1000)
    payload = {
        "data": {"type": "event", "id": event_id, "attributes": attributes, "event": {"ts": ts}}
    }
    body = {"event": json.dumps(payload), "timestamp": str(ts)}
    for hook in targets:
        url = hook["callback_url"]

        def _post(url: str = url) -> None:
            try:
                resp = httpx.post(url, data=body, timeout=10.0)
                log.info("event %s -> %s: %s", event_id, url, resp.status_code)
            except httpx.HTTPError as exc:
                log.warning("event %s -> %s failed: %s", event_id, url, exc)

        threading.Thread(target=_post, daemon=True).start()


def _get_callback(url: str, params: dict[str, str]) -> None:
    try:
        resp = httpx.get(url, params=params, timeout=10.0)
        log.info("callback GET %s?%s -> %s", url, urlencode(params), resp.status_code)
    except httpx.HTTPError as exc:
        log.warning("callback GET %s failed: %s", url, exc)


def _post_recording_ready(url: str, meeting_id: str, record_id: str) -> None:
    time.sleep(CAPTIONS_DELAY)
    now = int(time.time())
    claims = {"meeting_id": meeting_id, "record_id": record_id, "iat": now, "exp": now + 600}
    token = jwt.encode(claims, SECRET, algorithm="HS256")
    try:
        resp = httpx.post(url, data={"signed_parameters": token}, timeout=10.0)
        log.info("recording-ready -> %s: %s", url, resp.status_code)
    except httpx.HTTPError as exc:
        log.warning("recording-ready -> %s failed: %s", url, exc)


def _finish_meeting(meeting_id: str) -> str | None:
    """End a meeting: create the recording (if any) and fire all callbacks."""
    with _lock:
        meeting = meetings.get(meeting_id)
        if meeting is None:
            raise KeyError(meeting_id)
        if meeting["ended"]:
            return meeting.get("record_id")
        meeting["ended"] = True
        meeting["running"] = False
        meeting["participants"].clear()
        for key in [k for k in signals if k.startswith(f"{meeting_id}:")]:
            signals.pop(key, None)
        record_id: str | None = None
        if meeting["record"] and meeting["joined"]:
            record_id = f"mock-rec-{uuid.uuid4().hex[:16]}"
            recordings[record_id] = {
                "record_id": record_id,
                "meeting_id": meeting_id,
                "name": f"Recording of {meeting['name']}",
                "state": "published",
                "meta": dict(meeting["meta"]),
                "created_at": time.time(),
            }
            meeting["record_id"] = record_id
        end_callback = meeting["meta"].get("endCallbackUrl")
        ready_callback = meeting["meta"].get("bbb-recording-ready-url")
        marks = record_id is not None

    log.info("meeting %s ended (joined=%s recording=%s)", meeting_id, meeting["joined"], record_id)
    _emit_event(
        "meeting-ended", meeting_id, {"recordingmarks": "true" if marks else "false"}
    )
    if end_callback:
        threading.Thread(
            target=_get_callback,
            args=(
                end_callback,
                {"meetingID": meeting_id, "recordingmarks": "true" if marks else "false"},
            ),
            daemon=True,
        ).start()
    if ready_callback and record_id:
        threading.Thread(
            target=_post_recording_ready,
            args=(ready_callback, meeting_id, record_id),
            daemon=True,
        ).start()
    return record_id


# --- mock room page -------------------------------------------------------
#
# Plain string + .replace() placeholders (NOT an f-string: the room script
# contains hundreds of braces).  Placeholders are substituted with
# json.dumps() so names cannot break out of the JS string context.

ROOM_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE_HTML__ — mock room</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #0f1420; color: #e7ecf3;
         min-height: 100vh; display: flex; flex-direction: column; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 12px;
           padding: 14px 20px; background: #161d2e; border-bottom: 1px solid #263048; }
  header .brand { font-weight: 700; color: #7cc4ff; }
  header .badge { font-size: 12px; background: #23304a; border-radius: 999px; padding: 4px 10px; }
  #banner { display: none; padding: 10px 20px; background: #4a2c16; color: #ffd9a6;
            border-bottom: 1px solid #6b431f; font-size: 14px; }
  main { flex: 1; display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          gap: 16px; padding: 20px; align-content: start; }
  .tile { background: #1a2336; border: 1px solid #263048; border-radius: 12px; overflow: hidden;
          display: flex; flex-direction: column; }
  .tile .head { display: flex; justify-content: space-between; align-items: center; gap: 8px;
                padding: 10px 14px; }
  .tile .who { font-weight: 600; }
  .tile .role { color: #93a4c3; font-size: 12px; white-space: nowrap; }
  .tile video { width: 100%; aspect-ratio: 4 / 3; background: #0b0f18; object-fit: cover;
                min-height: 160px; }
  .tile .placeholder { width: 100%; aspect-ratio: 4 / 3; display: flex; flex-direction: column;
                       gap: 6px; align-items: center; justify-content: center; color: #4d5c7d;
                       font-size: 14px; background: #0b0f18; text-align: center; padding: 12px; }
  .tile .placeholder .big { font-size: 42px; }
  footer { display: flex; align-items: center; justify-content: space-between; gap: 16px;
           padding: 14px 20px; background: #161d2e; border-top: 1px solid #263048; flex-wrap: wrap; }
  footer .actions { display: flex; gap: 8px; }
  button { font: inherit; padding: 10px 16px; border-radius: 8px; border: 1px solid #33415f;
           background: #223050; color: #e7ecf3; cursor: pointer; }
  button:hover { background: #2b3b60; }
  button#finish { background: #b4232c; border-color: #d4383f; }
  button#finish:hover { background: #cf2e36; }
  button:disabled { opacity: .6; cursor: default; }
  #status { color: #9fd49f; font-size: 14px; }
  #status.bad { color: #ff9b9b; }
  .note { color: #93a4c3; font-size: 13px; padding: 0 20px 16px; }
  #tapHint { position: fixed; left: 50%; bottom: 78px; transform: translateX(-50%);
             background: #1d4ed8; color: #fff; padding: 10px 18px; border-radius: 999px;
             font-size: 14px; font-weight: 600; box-shadow: 0 4px 16px rgba(0,0,0,.45);
             display: none; z-index: 10; pointer-events: none; white-space: nowrap; }
  #tapHint.on { display: block; }
</style>
</head>
<body>
<header>
  <span class="brand">Mock BigBlueButton</span>
  <strong>__TITLE_HTML__</strong>
  <span class="badge" id="roleBadge">__ROLE_LABEL_HTML__ · you</span>
</header>
<div id="banner"></div>
<main id="tiles"></main>
<div class="note">Video and audio are shared <strong>peer-to-peer (WebRTC)</strong> with everyone
in this room — open the invite link in a second window or another device to see each other.
When the interview is over, press <strong>Finish interview</strong>: the mock ends the meeting,
records a canned session, and fires the same webhooks (meeting-ended + recording-ready) a
production BigBlueButton sends.</div>
<footer>
  <span id="status">Joining the room…</span>
  <div class="actions">
    <button id="media">Enable camera &amp; mic</button>
    <button id="finish">Finish interview</button>
  </div>
</footer>
<div id="tapHint">Tap anywhere to turn on sound &amp; video</div>
<script>
const MEETING = __MEETING_JSON__;
const PID = __PID_JSON__;
const MY_NAME = __NAME_JSON__;
const MY_ROLE = __ROLE_JSON__;
const ICE = __ICE_JSON__;  // server-injected STUN/TURN (TURN_URL/TURN_USER/TURN_PASS)
// ?ice=relay or #ice=relay forces TURN-only gathering (test/verification mode);
// the fragment form is safe for checksummed BBB join URLs (never sent to server)
{
  const q = new URLSearchParams(location.search);
  const h = new URLSearchParams(location.hash.slice(1));
  if (q.get("ice") === "relay" || h.get("ice") === "relay") ICE.policy = "relay";
}
const BASE = "/bigbluebutton/mock/meetings/" + encodeURIComponent(MEETING);

const statusEl = document.getElementById("status");
const bannerEl = document.getElementById("banner");
const tilesEl = document.getElementById("tiles");

function setStatus(msg, bad) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("bad", !!bad);
}
function showBanner(msg) {
  bannerEl.textContent = msg;
  bannerEl.style.display = "block";
}
function hideBanner() {
  bannerEl.style.display = "none";
}

// ---------- media -------------------------------------------------------
let localStream = null;

// Autoplay unlock: browsers block UNMUTED playback until a user gesture.
// Strategy: play muted first (picture shows, silent) and queue the element;
// the first tap/click/keypress anywhere unmutes everything for good.
const pendingUnmute = new Set();
let playbackUnlocked = false;

function updateTapHint() {
  for (const el of [...pendingUnmute]) if (!el.isConnected) pendingUnmute.delete(el);
  document.getElementById("tapHint").classList.toggle("on", pendingUnmute.size > 0);
}

function tryPlay(el) {
  const remote = el.dataset.remote === "1";
  if (remote && !playbackUnlocked) {
    if (!el.paused) {
      // already playing: unmuted = perfect, muted = waiting for the gesture
      if (el.muted) {
        pendingUnmute.add(el);
        updateTapHint();
      }
      return;
    }
    el.play().catch(() => {
      el.muted = true;
      el.play().then(() => {
        pendingUnmute.add(el);
        updateTapHint();
      }).catch(() => {});
    });
    return;
  }
  if (remote) {
    el.muted = false;
    el.volume = 1;
  }
  el.play().then(() => {
    pendingUnmute.delete(el);
    updateTapHint();
  }).catch((err) => {
    if (err && err.name === "NotAllowedError" && remote) {
      el.muted = true;
      el.play().then(() => {
        pendingUnmute.add(el);
        updateTapHint();
      }).catch(() => {});
    }
  });
}

function unlockPlayback() {
  playbackUnlocked = true;
  for (const el of [...pendingUnmute]) {
    pendingUnmute.delete(el);
    if (!el.isConnected) continue;
    el.muted = false;
    el.volume = 1;
    el.play().catch(() => pendingUnmute.add(el));
  }
  for (const el of document.querySelectorAll('video[data-remote="1"]')) {
    if (el.muted) {
      el.muted = false;
      el.play().catch(() => pendingUnmute.add(el));
    }
  }
  updateTapHint();
}
for (const evt of ["pointerdown", "touchstart", "keydown"]) {
  window.addEventListener(evt, unlockPlayback, { passive: true });
}

async function enableMedia() {
  if (localStream && localStream.getTracks().length > 0 &&
      localStream.getTracks().every((t) => t.readyState === "live")) {
    setStatus("Camera & mic already on." + (roster.length > 1 ? "" : " Waiting for others…"));
    render();
    return localStream;
  }
  if (!navigator.mediaDevices || !window.isSecureContext) {
    showBanner("Camera needs a secure page context: open this room via https:// or " +
      "http://localhost — plain http over a LAN IP blocks getUserMedia. " +
      "An HTTPS tunnel in front of the web container makes it work anywhere (see README).");
    setStatus("Camera unavailable — insecure page context.", true);
    return null;
  }
  try {
    try {
      localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    } catch (err) {
      if (err && (err.name === "NotFoundError" || err.name === "OverconstrainedError")) {
        localStream = await navigator.mediaDevices.getUserMedia({ video: false, audio: true });
      } else {
        throw err;
      }
    }
  } catch (err) {
    const busy = err && (err.name === "NotReadableError" || err.name === "TrackStartError");
    showBanner(busy
      ? "Camera/microphone is in use by another tab, window or app — close the others " +
        "and press 'Enable camera & mic' again."
      : "Camera/microphone unavailable: " + (err && err.message ? err.message : err) +
        " — allow access in the browser prompt and press 'Enable camera & mic' again.");
    setStatus("Running without your camera.", true);
    return null;
  }
  hideBanner();
  for (const st of Object.values(peers)) {
    for (const t of localStream.getTracks()) attachLocal(st, t);
  }
  setStatus("Camera & mic on." + (roster.length > 1 ? "" : " Waiting for others…"));
  render();
  return localStream;
}

// ---------- presence ----------------------------------------------------
let roster = [];   // [{id, name, role, joined_at}]
const peers = {};  // participant id -> peer state

async function refreshRoster() {
  let data;
  try {
    const res = await fetch(BASE + "/participants");
    if (!res.ok) return;
    data = await res.json();
  } catch (err) {
    return;
  }
  roster = (data && data.participants) || [];
  if (data && data.finished) setStatus("This meeting has ended.");
  for (const p of roster) {
    if (p.id === PID || peers[p.id]) continue;
    makePeer(p.id);
  }
  for (const pid of Object.keys(peers)) {
    if (!roster.some((p) => p.id === pid)) dropPeer(pid);
  }
  if (roster.length > 1) setStatus("In the room with " + (roster.length - 1) + " other" +
    (roster.length === 2 ? "" : "s") + (localStream ? "." : " — no camera yet."));
  render();
}

// ---------- WebRTC (perfect negotiation) --------------------------------

function preferVideoCodec(pc, track) {
  if (!track || track.kind !== "video") return;
  try {
    const caps = RTCRtpReceiver.getCapabilities && RTCRtpReceiver.getCapabilities("video");
    if (!caps) return;
    const rank = (m) => (m === "video/VP8" ? 0 : m === "video/VP9" ? 1 : m === "video/H264" ? 2 : 3);
    const codecs = caps.codecs.slice().sort((a, b) => rank(a.mimeType) - rank(b.mimeType));
    for (const tr of pc.getTransceivers()) {
      if (tr.sender && tr.sender.track === track && typeof tr.setCodecPreferences === "function") {
        try { tr.setCodecPreferences(codecs); } catch (err) { /* combo unsupported */ }
      }
    }
  } catch (err) { /* getCapabilities unavailable */ }
}

function attachLocal(st, track) {
  st.pc.addTrack(track, localStream);
  preferVideoCodec(st.pc, track);
}

function makePeer(remoteId) {
  const polite = PID > remoteId;  // deterministic tie-break: exactly one polite side
  const pc = new RTCPeerConnection({
    iceServers: ICE.servers,
    iceTransportPolicy: ICE.policy,
  });
  const st = {
    pc, polite,
    makingOffer: false,
    ignoreOffer: false,
    isSettingAnswer: false,
    queuedCandidates: [],
    stream: null,
  };
  peers[remoteId] = st;

  if (localStream) for (const t of localStream.getTracks()) attachLocal(st, t);

  pc.onnegotiationneeded = async () => {
    try {
      st.makingOffer = true;
      await pc.setLocalDescription();
      await send(remoteId, "offer", pc.localDescription);
    } catch (err) {
      console.warn("negotiation failed", err);
    } finally {
      st.makingOffer = false;
    }
  };
  pc.onicecandidate = (e) => {
    if (e.candidate) send(remoteId, "candidate", e.candidate).catch(() => {});
  };
  pc.onicecandidateerror = (e) => {
    // 701 = unreachable STUN server (common offline, harmless); log the rest
    if (e.errorCode !== 701) {
      console.warn("[room] ice error", e.errorCode, e.errorText, e.url || "");
    }
  };
  pc.ontrack = (e) => {
    // Aggregate every received track into ONE MediaStream. Some browsers
    // deliver audio and video in separate streams (or none at all) — trusting
    // e.streams[0] can leave the element with audio but no video.
    if (!st.stream) st.stream = new MediaStream();
    if (!st.stream.getTracks().some((t) => t.id === e.track.id)) st.stream.addTrack(e.track);
    e.track.onmute = () => render();
    e.track.onunmute = () => render();
    e.track.onended = () => render();
    console.log("[room] ontrack", e.track.kind, "from", remoteId,
                "-> have:", st.stream.getTracks().map((t) => t.kind).join(","));
    render();
  };
  pc.onconnectionstatechange = () => {
    st.conn = pc.connectionState;
    if (pc.connectionState === "failed") dropPeer(remoteId);
    else render();
  };
  return st;
}

function dropPeer(remoteId) {
  const st = peers[remoteId];
  if (!st) return;
  try { st.pc.close(); } catch (err) { /* ignore */ }
  delete peers[remoteId];
  render();
}

async function send(to, kind, payload) {
  await fetch(BASE + "/signal", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from: PID, to: to, kind: kind, payload: payload }),
  });
}

async function drainSignals() {
  let msgs;
  try {
    const res = await fetch(BASE + "/signal/" + encodeURIComponent(PID));
    if (!res.ok) return;
    msgs = await res.json();
  } catch (err) {
    return;
  }
  for (const m of msgs) {
    try {
      await handleSignal(m);
    } catch (err) {
      console.warn("signal handling failed", err);
    }
  }
}

async function flushQueued(st) {
  const q = st.queuedCandidates;
  st.queuedCandidates = [];
  for (const c of q) {
    try { await st.pc.addIceCandidate(c); } catch (err) { console.warn("queued candidate", err); }
  }
}

async function handleSignal(m) {
  let st = peers[m.from];
  if (!st) st = makePeer(m.from);  // signal arrived before the roster poll saw them
  const pc = st.pc;

  if (m.kind === "offer") {
    const readyForOffer = !st.makingOffer &&
      (pc.signalingState === "stable" || st.isSettingAnswer);
    const offerCollision = !readyForOffer;
    st.ignoreOffer = !st.polite && offerCollision;
    if (st.ignoreOffer) return;  // impolite peer: glare, drop the remote offer
    await pc.setRemoteDescription(m.payload);  // polite peer: implicit rollback
    await flushQueued(st);
    await pc.setLocalDescription();
    await send(m.from, "answer", pc.localDescription);
  } else if (m.kind === "answer") {
    if (pc.signalingState !== "have-local-offer") return;
    st.isSettingAnswer = true;
    try {
      await pc.setRemoteDescription(m.payload);
      await flushQueued(st);
    } finally {
      st.isSettingAnswer = false;
    }
  } else if (m.kind === "candidate") {
    if (!pc.remoteDescription || !pc.remoteDescription.type) {
      // candidates routinely beat the offer/answer across the wire — hold
      // them until there is a remote description to attach them to
      st.queuedCandidates.push(m.payload);
      return;
    }
    try {
      await pc.addIceCandidate(m.payload);
    } catch (err) {
      if (!st.ignoreOffer) throw err;
    }
  }
}

// ---------- rendering ---------------------------------------------------

function newVideo(stream, muted) {
  const el = document.createElement("video");
  el.autoplay = true;
  el.playsInline = true;
  el.setAttribute("playsinline", "");
  el.setAttribute("webkit-playsinline", "");
  el.preload = "auto";
  el.dataset.remote = muted ? "0" : "1";  // param `muted` means "this is my own preview"
  if (muted) {
    el.muted = true;  // local preview: blocks echo + satisfies autoplay policy
  } else {
    el.muted = false;
    el.volume = 1;
  }
  if (stream) el.srcObject = stream;
  el.addEventListener("pause", () => {
    if (el.isConnected && el.srcObject) tryPlay(el);
  });
  tryPlay(el);
  return el;
}

function syncVideo(el, stream) {
  if (!stream) {
    el.removeAttribute("srcObject");
    return;
  }
  if (el.srcObject !== stream) el.srcObject = stream;
  tryPlay(el);
}

function placeholderBox(text) {
  const el = document.createElement("div");
  el.className = "placeholder";
  const big = document.createElement("div");
  big.className = "big";
  big.textContent = "\\u{1F4F7}";
  const msg = document.createElement("div");
  msg.className = "msg";
  msg.textContent = text;
  el.append(big, msg);
  return el;
}

function connText(st, name) {
  const s = st && st.conn;
  if (s === "failed") return "Could not reach " + name + " directly (NAT blocked — STUN only, no TURN)";
  if (s === "connected") return name + " is connected but shares no camera yet";
  if (s === "disconnected") return "Connection to " + name + " lost — recovering…";
  if (s === "connecting") return "Connecting to " + name + "…";
  return "Waiting for " + name + "…";
}

function render() {
  const items = [{ id: PID, name: MY_NAME, role: MY_ROLE, self: true }];
  for (const p of roster) {
    if (p.id !== PID) items.push({ id: p.id, name: p.name, role: p.role, self: false });
  }
  const seen = new Set();
  for (const it of items) {
    seen.add(it.id);
    let tile = tilesEl.querySelector('[data-pid="' + CSS.escape(it.id) + '"]');
    if (!tile) {
      tile = document.createElement("div");
      tile.className = "tile";
      tile.dataset.pid = it.id;
      const head = document.createElement("div");
      head.className = "head";
      const who = document.createElement("span");
      who.className = "who";
      const role = document.createElement("span");
      role.className = "role";
      head.append(who, role);
      tile.append(head, document.createElement("div"));
      tile.lastChild.className = "media";
      tilesEl.appendChild(tile);
    }
    const who = tile.querySelector(".who");
    who.textContent = it.name + (it.self ? " (you)" : "");

    const media = tile.querySelector(".media");
    const st = peers[it.id];
    // A/V badge: tracks that arrived (track.muted is unreliable in Chrome —
    // it can stay true while RTP flows, so readyState is the honest signal)
    const trk = it.self ? localStream : st ? st.stream : null;
    const kinds = new Set(
      trk ? trk.getTracks().filter((t) => t.readyState === "live").map((t) => t.kind) : []
    );
    tile.querySelector(".role").textContent =
      (it.role === "MODERATOR" ? "Moderator" : "Participant") +
      " · " + (kinds.has("audio") ? "A✓" : "A✗") +
      " " + (kinds.has("video") ? "V✓" : "V✗");

    const stream = it.self ? localStream : st ? st.stream : null;
    const wantVideo = !!stream;
    const current = media.firstElementChild;
    const currentIsVideo = current && current.tagName === "VIDEO";
    if (wantVideo !== currentIsVideo || !current) {
      media.textContent = "";
      media.appendChild(
        wantVideo
          ? newVideo(stream, it.self)
          : placeholderBox(it.self
              ? "Your camera is off"
              : connText(st, it.name))
      );
    } else if (wantVideo) {
      syncVideo(current, stream);
    } else {
      const msg = current.classList.contains("placeholder") ? current.querySelector(".msg") : null;
      const text = it.self ? "Your camera is off" : connText(st, it.name);
      if (msg && msg.textContent !== text) msg.textContent = text;
    }
  }
  for (const tile of Array.from(tilesEl.children)) {
    if (!seen.has(tile.dataset.pid)) tile.remove();
  }
  updateTapHint();
}

// ---------- lifecycle ---------------------------------------------------

async function selectedPairType(pc) {
  // selected candidate-pair type: host | srflx (STUN) | relay (TURN) | prflx
  try {
    const stats = await pc.getStats();
    let pair = null;
    const cands = new Map();
    stats.forEach((r) => {
      if (r.type === "candidate") cands.set(r.id, r);
      else if (r.type === "candidate-pair" && (r.nominated || r.selected)) pair = r;
    });
    const lc = pair && cands.get(pair.localCandidateId);
    return (lc && lc.candidateType) || "-";
  } catch (err) {
    return "?";
  }
}

async function sendStatus() {
  try {
    const remotes = [];
    for (const [id, st] of Object.entries(peers)) {
      const tracks = st.stream ? st.stream.getTracks() : [];
      const desc = tracks
        .map((t) => t.kind + ":" + t.readyState + (t.muted ? "/muted" : ""))
        .join(",");
      const el = Array.from(document.querySelectorAll("video")).find(
        (v) => v.dataset.remote === "1" && v.srcObject === st.stream,
      );
      remotes.push({
        id,
        conn: st.conn || st.pc.connectionState,
        ice: st.pc.iceConnectionState,
        pair: await selectedPairType(st.pc),
        tracks: desc || "-",
        el: el
          ? (el.paused ? "paused" : "playing") + (el.muted ? "+muted" : "+sound") +
            " t=" + el.currentTime.toFixed(1)
          : "none",
      });
    }
    const local = localStream
      ? localStream.getTracks()
          .map((t) => t.kind + ":" + t.readyState + (t.muted ? "/muted" : ""))
          .join(",")
      : "none";
    await fetch(BASE + "/participants/" + encodeURIComponent(PID) + "/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      body: JSON.stringify({
        name: MY_NAME,
        local,
        pending: pendingUnmute.size,
        remote: remotes,
      }),
    });
  } catch (err) {
    console.warn("[room] status heartbeat failed", err);
  }
}

window.addEventListener("beforeunload", () => {
  fetch(BASE + "/participants/" + encodeURIComponent(PID) + "/leave",
        { method: "POST", keepalive: true }).catch(() => {});
});
window.addEventListener("pagehide", () => {
  fetch(BASE + "/participants/" + encodeURIComponent(PID) + "/leave",
        { method: "POST", keepalive: true }).catch(() => {});
});

document.getElementById("media").addEventListener("click", enableMedia);

const finishBtn = document.getElementById("finish");
finishBtn.addEventListener("click", async () => {
  finishBtn.disabled = true;
  setStatus("Ending meeting…");
  try {
    const res = await fetch(BASE + "/finish", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      setStatus(data.record_id
        ? "Meeting ended — recording + webhooks fired. Transcript arrives in a few seconds."
        : "Meeting ended (no recording).");
    } else {
      setStatus("Error: " + (data.error || "unknown"), true);
      finishBtn.disabled = false;
    }
  } catch (err) {
    setStatus("Error: " + err, true);
    finishBtn.disabled = false;
  }
});

enableMedia();  // prompts right away; roster keeps working even if denied
refreshRoster();
setInterval(refreshRoster, 2000);
setInterval(drainSignals, 500);
setInterval(sendStatus, 10000);  // media/ICE heartbeat for docker logs
</script>
</body>
</html>
"""


def _ice_config() -> dict[str, Any]:
    """ICE servers injected into the room page.

    Public STUN (reachable from anywhere) + a TURN entry when TURN_URL is
    set (see backend/.env — local coturn by default, swap for a hosted TURN
    for cross-network calls behind symmetric/CGNAT NAT).
    """
    servers: list[dict[str, Any]] = [
        {"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]},
        {"urls": "stun:stun.cloudflare.com:3478"},
        {"urls": "stun:stun.miwifi.com:3478"},
        {"urls": "stun:stun.chat.bilibili.com:3478"},
    ]
    turn_url = os.environ.get("TURN_URL", "").strip()
    if turn_url:
        entry: dict[str, Any] = {"urls": [u.strip() for u in turn_url.split(",") if u.strip()]}
        if os.environ.get("TURN_USER"):
            entry["username"] = os.environ["TURN_USER"]
        if os.environ.get("TURN_PASS"):
            entry["credential"] = os.environ["TURN_PASS"]
        servers.append(entry)
    return {"servers": servers, "policy": "all"}


def _join_page(meeting: dict[str, Any], user_name: str, role: str, participant_id: str) -> str:
    role_label = "Moderator" if role == "MODERATOR" else "Participant"
    return (
        ROOM_PAGE.replace("__TITLE_HTML__", html.escape(meeting["name"]))
        .replace("__ROLE_LABEL_HTML__", role_label)
        .replace("__MEETING_JSON__", json.dumps(meeting["meeting_id"]))
        .replace("__PID_JSON__", json.dumps(participant_id))
        .replace("__NAME_JSON__", json.dumps(user_name))
        .replace("__ROLE_JSON__", json.dumps(role))
        .replace("__ICE_JSON__", json.dumps(_ice_config()))
    )


def _handle_create(params: Any) -> Response:
    meeting_id = params.get("meetingID", "")
    if not meeting_id:
        return _fail("missingParam", "meetingID is required")
    with _lock:
        meeting = meetings.get(meeting_id)
        meta = {key[5:]: value for key, value in params.items() if key.startswith("meta_")}
        if meeting is None or meeting["ended"]:
            meeting = {
                "meeting_id": meeting_id,
                "internal_id": f"mock-{uuid.uuid4().hex[:12]}",
                "name": params.get("name") or meeting_id,
                "attendee_pw": params.get("attendeePW", ""),
                "moderator_pw": params.get("moderatorPW", ""),
                "record": str(params.get("record", "false")).lower() == "true",
                "duration": params.get("duration", "60"),
                "welcome": params.get("welcome", ""),
                "meta": meta,
                "running": True,
                "joined": False,
                "ended": False,
                "created_at": time.time(),
                "record_id": None,
                "participants": {},
            }
            meetings[meeting_id] = meeting
            message_key = "created"
        else:
            meeting["running"] = True
            meeting["meta"] = meta
            message_key = "already-running"
    return _ok(
        f"<meetingID>{_esc(meeting['meeting_id'])}</meetingID>"
        f"<internalMeetingID>{_esc(meeting['internal_id'])}</internalMeetingID>"
        "<parentMeetingID/>"
        f"<attendeePW>{_esc(meeting['attendee_pw'])}</attendeePW>"
        f"<moderatorPW>{_esc(meeting['moderator_pw'])}</moderatorPW>"
        f"<createTime>{int(time.time())}</createTime>"
        "<voiceBridge/><dialNumber/>"
        f"<createDate>{time.strftime('%a, %d %b %Y %H:%M:%S %Z')}</createDate>"
        f"<message>Meeting {meeting_id} {message_key}</message>"
        f"<messageKey>{message_key}</messageKey>"
    )


def _handle_join(params: Any) -> Response:
    meeting_id = params.get("meetingID", "")
    full_name = params.get("fullName") or "Guest"
    meeting = meetings.get(meeting_id)
    if meeting is None:
        log.warning("join rejected %s for %s: unknown meeting", meeting_id, full_name)
        return _fail("invalidMeetingIdentifier", f"Meeting {meeting_id} does not exist")
    if meeting["ended"]:
        log.warning("join rejected %s for %s: meeting already ended", meeting_id, full_name)
        return _fail("meetingEnded", f"Meeting {meeting_id} has already ended")
    password = params.get("password", "")
    moderator_pw = meeting["moderator_pw"]
    attendee_pw = meeting["attendee_pw"]
    if moderator_pw and password == moderator_pw:
        role = "MODERATOR"
    elif attendee_pw and password == attendee_pw:
        role = "VIEWER"
    else:
        log.warning("join rejected %s for %s: wrong password", meeting_id, full_name)
        return _fail("invalidPassword", "Wrong password for this meeting")

    participant_id = uuid.uuid4().hex[:12]
    with _lock:
        meeting["joined"] = True
        meeting["running"] = True
        parts = meeting["participants"]
        # A refresh of the same person leaves a ghost — replace it.
        for stale_id in [
            pid
            for pid, p in parts.items()
            if p["name"] == full_name and p["role"] == role
        ]:
            parts.pop(stale_id, None)
            signals.pop(f"{meeting_id}:{stale_id}", None)
        parts[participant_id] = {
            "id": participant_id,
            "name": full_name,
            "role": role,
            "joined_at": time.time(),
        }
    threading.Thread(
        target=_emit_event, args=("user-joined", meeting_id), daemon=True
    ).start()
    log.info("join %s as %s (%s) participant=%s", meeting_id, full_name, role, participant_id)
    return HTMLResponse(_join_page(meeting, full_name, role, participant_id))


def _handle_end(params: Any) -> Response:
    meeting_id = params.get("meetingID", "")
    meeting = meetings.get(meeting_id)
    if meeting is None:
        return _fail("invalidMeetingIdentifier", f"Meeting {meeting_id} does not exist")
    password = params.get("password", "")
    if meeting["moderator_pw"] and password != meeting["moderator_pw"]:
        return _fail("invalidPassword", "You must pass the moderator password to end a meeting")
    _finish_meeting(meeting_id)
    return _ok("<message>The meeting is now ended</message><messageKey>meetingEnd</messageKey>")


def _handle_is_running(params: Any) -> Response:
    meeting_id = params.get("meetingID", "")
    meeting = meetings.get(meeting_id)
    running = bool(meeting and meeting["running"])
    return _ok(f"<running>{str(running).lower()}</running>")


def _meeting_info_body(meeting: dict[str, Any]) -> str:
    meta = "".join(
        f"<{_esc(key)}>{_esc(value)}</{_esc(key)}>" for key, value in meeting["meta"].items()
    )
    return (
        f"<meetingID>{_esc(meeting['meeting_id'])}</meetingID>"
        f"<internalMeetingID>{_esc(meeting['internal_id'])}</internalMeetingID>"
        f"<name>{_esc(meeting['name'])}</name>"
        f"<createTime>{int(meeting['created_at'])}</createTime>"
        "<voiceBridge/><dialNumber/>"
        f"<attendeePW>{_esc(meeting['attendee_pw'])}</attendeePW>"
        f"<moderatorPW>{_esc(meeting['moderator_pw'])}</moderatorPW>"
        f"<running>{str(meeting['running']).lower()}</running>"
        f"<duration>{_esc(meeting['duration'])}</duration>"
        f"<meta>{meta}</meta>"
    )


def _handle_meeting_info(params: Any) -> Response:
    meeting = meetings.get(params.get("meetingID", ""))
    if meeting is None:
        return _fail("invalidMeetingIdentifier", "Meeting does not exist")
    return _ok(_meeting_info_body(meeting))


def _handle_get_meetings(params: Any) -> Response:
    with _lock:
        blocks = "".join(f"<meeting>{_meeting_info_body(m)}</meeting>" for m in meetings.values())
    return _ok(f"<meetings>{blocks}</meetings>")


def _recording_body(rec: dict[str, Any]) -> str:
    meta = "".join(f"<{_esc(k)}>{_esc(v)}</{_esc(k)}>" for k, v in rec["meta"].items())
    playback_url = f"/bigbluebutton/mock/recordings/{rec['record_id']}"
    return (
        f"<recordID>{_esc(rec['record_id'])}</recordID>"
        f"<meetingID>{_esc(rec['meeting_id'])}</meetingID>"
        f"<internalMeetingID>{_esc(rec['meeting_id'])}</internalMeetingID>"
        f"<name>{_esc(rec['name'])}</name>"
        f"<state>{_esc(rec['state'])}</state>"
        f"<published>{'true' if rec['state'] == 'published' else 'false'}</published>"
        f"<startTime>{int(rec['created_at'] * 1000)}</startTime>"
        "<endTime/><duration>2</duration>"
        "<playback><type>video</type><format>presentation</format>"
        f"<link>{_esc(playback_url)}</link><length>2</length></playback>"
        f"<meta>{meta}</meta>"
    )


def _handle_get_recordings(params: Any) -> Response:
    record_ids = [r for r in params.get("recordID", "").split(",") if r]
    meeting_id = params.get("meetingID", "")
    state = params.get("state", "")
    meta_filters = {k[5:]: v for k, v in params.items() if k.startswith("meta_")}
    selected = []
    with _lock:
        for rec in recordings.values():
            if record_ids and rec["record_id"] not in record_ids:
                continue
            if meeting_id and rec["meeting_id"] != meeting_id:
                continue
            if state and state != "any" and state != rec["state"]:
                continue
            if any(rec["meta"].get(k) != v for k, v in meta_filters.items()):
                continue
            selected.append(_recording_body(rec))
    return _ok("<recordings>" + "".join(f"<recording>{b}</recording>" for b in selected) + "</recordings>")


def _handle_delete_recordings(params: Any) -> Response:
    ids = [r for r in params.get("recordID", "").split(",") if r]
    with _lock:
        for record_id in ids:
            recordings.pop(record_id, None)
    return _ok(f"<removed>{_esc(','.join(ids))}</removed>")


def _handle_publish_recordings(params: Any) -> Response:
    publish = str(params.get("publish", "true")).lower() == "true"
    ids = [r for r in params.get("recordID", "").split(",") if r]
    with _lock:
        for record_id in ids:
            rec = recordings.get(record_id)
            if rec:
                rec["state"] = "published" if publish else "unpublished"
    return _ok(f"<published>{str(publish).lower()}</published>")


def _handle_text_tracks(params: Any) -> Response:
    record_id = params.get("recordID", "")
    with _lock:
        rec = recordings.get(record_id)
    if rec is None:
        return JSONResponse(
            content={
                "response": {
                    "returncode": "FAILED",
                    "messageKey": "notFound",
                    "message": f"Recording {record_id} does not exist",
                }
            },
            status_code=200,
        )
    return JSONResponse(
        content={
            "response": {
                "returncode": "SUCCESS",
                "tracks": [
                    {
                        "href": f"/captions/{record_id}/captions_{CAPTION_LANG}.vtt",
                        "kind": "captions",
                        "label": "English (auto-generated)",
                        "lang": CAPTION_LANG,
                        "source": "live",
                    }
                ],
            }
        }
    )


def _handle_hooks_create(params: Any) -> Response:
    callback = params.get("callbackURL", "")
    if not callback:
        return _fail("missingParam", "callbackURL is required")
    hook_id = uuid.uuid4().hex[:12]
    events = [e for e in params.get("eventID", "").split(",") if e]
    with _lock:
        hooks[hook_id] = {
            "hook_id": hook_id,
            "callback_url": callback,
            "meeting_id": params.get("meetingID", ""),
            "events": events,
        }
    log.info("hook created %s -> %s (meeting=%s)", hook_id, callback, params.get("meetingID", ""))
    return _ok(
        f"<hookID>{_esc(hook_id)}</hookID>"
        f"<meetingID>{_esc(params.get('meetingID', ''))}</meetingID>"
        f"<callbackURL>{_esc(callback)}</callbackURL>"
    )


def _handle_hooks_list(params: Any) -> Response:
    meeting_id = params.get("meetingID", "")
    with _lock:
        blocks = []
        for hook in hooks.values():
            if meeting_id and hook["meeting_id"] != meeting_id:
                continue
            blocks.append(
                "<hook>"
                f"<hookID>{_esc(hook['hook_id'])}</hookID>"
                f"<meetingID>{_esc(hook['meeting_id'])}</meetingID>"
                f"<callbackURL>{_esc(hook['callback_url'])}</callbackURL>"
                "</hook>"
            )
    return _ok("<hooks>" + "".join(blocks) + "</hooks>")


def _handle_hooks_destroy(params: Any) -> Response:
    hook_id = params.get("hookID", "")
    with _lock:
        removed = hooks.pop(hook_id, None)
    if removed is None:
        return _fail("invalidHookIdentifier", f"Hook {hook_id} does not exist")
    return _ok(f"<hookID>{_esc(hook_id)}</hookID>")


ROUTES = {
    "create": _handle_create,
    "join": _handle_join,
    "end": _handle_end,
    "isMeetingRunning": _handle_is_running,
    "getMeetingInfo": _handle_meeting_info,
    "getMeetings": _handle_get_meetings,
    "getRecordings": _handle_get_recordings,
    "deleteRecordings": _handle_delete_recordings,
    "publishRecordings": _handle_publish_recordings,
    "getRecordingTextTracks": _handle_text_tracks,
    "hooks/create": _handle_hooks_create,
    "hooks/list": _handle_hooks_list,
    "hooks/destroy": _handle_hooks_destroy,
}


# --- HTTP routes ----------------------------------------------------------

@app.api_route("/bigbluebutton/api/{call:path}", methods=["GET", "POST"])
async def bbb_api(call: str, request: Request) -> Response:
    if not _checksum_ok(call, request.url.query):
        log.warning("checksum rejected for %s?%s", call, request.url.query)
        return _browser_error(
            request,
            _fail("checksumError", "You did not pass the checksum or it is not correct"),
        )
    handler = ROUTES.get(call)
    if handler is None:
        return _browser_error(
            request,
            _fail("unsupportedCall", f"The API call {call} is not supported by this mock"),
        )
    return _browser_error(request, handler(request.query_params))


@app.get("/bigbluebutton/mock/meetings/{meeting_id}/participants")
async def mock_participants(meeting_id: str) -> Response:
    with _lock:
        meeting = meetings.get(meeting_id)
        if meeting is None:
            return JSONResponse({"error": "unknown meeting"}, status_code=404)
        return JSONResponse(
            {
                "meeting_id": meeting_id,
                "finished": meeting["ended"],
                "participants": list(meeting["participants"].values()),
            }
        )


@app.post("/bigbluebutton/mock/meetings/{meeting_id}/participants/{participant_id}/leave")
async def mock_leave(meeting_id: str, participant_id: str) -> Response:
    with _lock:
        meeting = meetings.get(meeting_id)
        removed = False
        if meeting:
            removed = meeting["participants"].pop(participant_id, None) is not None
        signals.pop(f"{meeting_id}:{participant_id}", None)
    if removed:
        log.info("participant %s left %s", participant_id, meeting_id)
    return JSONResponse({"ok": True, "removed": removed})


@app.post("/bigbluebutton/mock/meetings/{meeting_id}/participants/{participant_id}/status")
async def mock_status(meeting_id: str, participant_id: str, request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad payload"}, status_code=400)
    remote = body.get("remote") or []
    r_txt = (
        "; ".join(
            f"{r.get('id')} conn={r.get('conn')} ice={r.get('ice')} "
            f"pair={r.get('pair', '-')} tracks={r.get('tracks')} el={r.get('el')}"
            for r in remote
            if isinstance(r, dict)
        )
        or "-"
    )
    log.info(
        "status %s(%s) local=%s pending=%s remote=[%s]",
        participant_id,
        body.get("name", "?"),
        body.get("local", "?"),
        body.get("pending", 0),
        r_txt,
    )
    return JSONResponse({"ok": True, "meeting_id": meeting_id})


@app.post("/bigbluebutton/mock/meetings/{meeting_id}/signal")
async def mock_signal_post(meeting_id: str, request: Request) -> Response:
    try:
        body = await request.json()
        sender = str(body["from"])
        target = str(body["to"])
        kind = str(body["kind"])
        payload = body.get("payload")
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"ok": False, "error": "bad payload"}, status_code=400)
    if kind not in ("offer", "answer", "candidate"):
        return JSONResponse({"ok": False, "error": "unknown kind"}, status_code=400)
    key = f"{meeting_id}:{target}"
    with _lock:
        mailbox = signals.setdefault(key, [])
        if len(mailbox) >= 500:
            mailbox.pop(0)
        mailbox.append({"from": sender, "kind": kind, "payload": payload})
    if kind in ("offer", "answer"):
        log.info("signal %s %s->%s kind=%s%s", meeting_id, sender, target, kind,
                 _sdp_summary(payload))
    else:
        log.info("signal %s %s->%s kind=%s", meeting_id, sender, target, kind)
    return JSONResponse({"ok": True})


def _sdp_summary(payload: object) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("sdp"), str):
        return ""
    rows: list = []
    media = direction = mid = ufrag = None
    for raw in payload["sdp"].splitlines():
        line = raw.strip()
        if line.startswith("m="):
            if media is not None:
                rows.append(f"{media}/{direction or 'sd'}/{mid or '?'}/{ufrag or '-'}")
            media = line[2:].split(" ", 1)[0]
            direction = mid = ufrag = None
        elif media is not None:
            if line in ("a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"):
                direction = line[2:]
            elif line.startswith("a=mid:"):
                mid = line[6:]
            elif line.startswith("a=ice-ufrag:"):
                ufrag = line[12:][:6]
    if media is not None:
        rows.append(f"{media}/{direction or 'sd'}/{mid or '?'}/{ufrag or '-'}")
    return " [" + " ".join(rows) + "]"


@app.get("/bigbluebutton/mock/meetings/{meeting_id}/signal/{participant_id}")
async def mock_signal_get(meeting_id: str, participant_id: str) -> Response:
    key = f"{meeting_id}:{participant_id}"
    with _lock:
        messages = signals.pop(key, [])
    return JSONResponse(messages)


@app.post("/bigbluebutton/mock/meetings/{meeting_id}/finish")
async def mock_finish(meeting_id: str) -> Response:
    try:
        record_id = _finish_meeting(meeting_id)
    except KeyError:
        return JSONResponse({"ok": False, "error": "unknown meeting"}, status_code=404)
    return JSONResponse({"ok": True, "meeting_id": meeting_id, "record_id": record_id})


@app.get("/bigbluebutton/captions/{record_id}/{filename}.vtt")
async def captions(record_id: str) -> Response:
    with _lock:
        if record_id not in recordings:
            return Response(content="NOT FOUND", status_code=404, media_type="text/plain")
    return Response(content=CAPTION_VTT, media_type="text/vtt")


@app.get("/bigbluebutton/mock/recordings/{record_id}")
async def mock_playback(record_id: str) -> HTMLResponse:
    with _lock:
        rec = recordings.get(record_id)
    if rec is None:
        return HTMLResponse("<h1>Recording not found</h1>", status_code=404)
    caption_url = f"/bigbluebutton/captions/{record_id}/captions_{CAPTION_LANG}.vtt"
    return HTMLResponse(
        f"<h1>{html.escape(rec['name'])}</h1>"
        "<p>Mock recording playback — this development server keeps no media, only the "
        "caption track that the transcription pipeline consumes.</p>"
        f"<p><a href=\"{caption_url}\">Open the WebVTT captions</a></p>"
    )


@app.get("/bigbluebutton/mock")
async def mock_index() -> JSONResponse:
    with _lock:
        return JSONResponse(
            {
                "meetings": list(meetings.values()),
                "recordings": list(recordings.values()),
                "hooks": list(hooks.values()),
            },
        )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "service": "mock-bigbluebutton",
            "api": "/bigbluebutton/api/{create,join,end,...}",
            "state": "/bigbluebutton/mock",
        }
    )
