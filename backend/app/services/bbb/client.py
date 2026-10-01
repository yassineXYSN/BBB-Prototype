"""BigBlueButton REST API client.

Checksum algorithm (per BBB docs): sha1(callName + queryString + sharedSecret)
where queryString is the exact query string sent (without the checksum param).
"""

import hashlib
import json
import logging
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger("app.bbb")


class BbbError(Exception):
    def __init__(self, call: str, returncode: str, message: str = "", message_key: str = ""):
        self.call = call
        self.returncode = returncode
        self.message = message
        self.message_key = message_key
        super().__init__(f"BBB {call} failed: {returncode} {message_key} {message}")


def compute_checksum(call: str, query: str, secret: str) -> str:
    return hashlib.sha1(f"{call}{query}{secret}".encode()).hexdigest()


class BbbClient:
    def __init__(
        self,
        base_url: str | None = None,
        secret: str | None = None,
        timeout: float = 20.0,
        public_base_url: str | None = None,
    ):
        settings = get_settings()
        self.base_url = (base_url if base_url is not None else settings.bbb_url).rstrip("/")
        self.public_base_url = (
            public_base_url if public_base_url is not None else settings.bbb_public_url
        ).rstrip("/") or self.base_url
        self.secret = secret if secret is not None else settings.bbb_shared_secret
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.secret)

    def _build_url(
        self, call: str, params: dict[str, Any] | None = None, *, base: str | None = None
    ) -> str:
        clean = {k: str(v) for k, v in (params or {}).items() if v is not None}
        query = urllib.parse.urlencode(clean)
        checksum = compute_checksum(call, query, self.secret)
        sep = "&" if query else ""
        root = (base if base is not None else self.base_url) or self.base_url
        return f"{root}/api/{call}?{query}{sep}checksum={checksum}"

    def _get(self, call: str, params: dict[str, Any] | None = None) -> ET.Element:
        url = self._build_url(call, params)
        logger.debug("BBB GET %s", call)
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BbbError(call, "HTTP_ERROR", str(exc)) from exc
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise BbbError(call, "PARSE_ERROR", resp.text[:500]) from exc
        returncode = root.findtext("returncode") or ""
        if returncode != "SUCCESS":
            raise BbbError(
                call,
                returncode or "UNKNOWN",
                root.findtext("message") or "",
                root.findtext("messageKey") or "",
            )
        return root

    def _get_text(self, call: str, params: dict[str, Any] | None = None) -> str:
        url = self._build_url(call, params)
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BbbError(call, "HTTP_ERROR", str(exc)) from exc
        return resp.text

    # --- meetings ---------------------------------------------------------

    def create_meeting(
        self,
        *,
        name: str,
        meeting_id: str,
        attendee_pw: str,
        moderator_pw: str,
        record: bool = True,
        duration: int = 60,
        welcome: str = "",
        meta: dict[str, str] | None = None,
        auto_start_recording: bool = True,
        allow_start_stop_recording: bool = True,
        end_when_no_moderator: bool = False,
        logout_url: str = "",
    ) -> dict[str, str]:
        params: dict[str, Any] = {
            "name": name,
            "meetingID": meeting_id,
            "attendeePW": attendee_pw,
            "moderatorPW": moderator_pw,
            "record": "true" if record else "false",
            "duration": duration,
            "welcome": welcome,
            "autoStartRecording": "true" if auto_start_recording else "false",
            "allowStartStopRecording": "true" if allow_start_stop_recording else "false",
            "endWhenNoModerator": "true" if end_when_no_moderator else "false",
            "logoutURL": logout_url or None,
        }
        for key, value in (meta or {}).items():
            params[f"meta_{key}"] = value
        root = self._get("create", params)
        return {
            "meeting_id": root.findtext("meetingID") or meeting_id,
            "internal_meeting_id": root.findtext("internalMeetingID") or "",
            "attendee_pw": root.findtext("attendeePW") or attendee_pw,
            "moderator_pw": root.findtext("moderatorPW") or moderator_pw,
            "message_key": root.findtext("messageKey") or "",
        }

    def join_url(
        self,
        *,
        meeting_id: str,
        full_name: str,
        password: str,
        role: str = "VIEWER",
        user_id: str = "",
        redirect: str = "false",
        logout_url: str = "",
    ) -> str:
        params: dict[str, Any] = {
            "meetingID": meeting_id,
            "fullName": full_name,
            "password": password,
            "role": role.upper(),
            "userID": user_id or None,
            "redirect": redirect,
            "logoutURL": logout_url or None,
        }
        return self._build_url("join", params, base=self.public_base_url)

    def end_meeting(self, meeting_id: str, password: str) -> None:
        self._get("end", {"meetingID": meeting_id, "password": password})

    def is_meeting_running(self, meeting_id: str) -> bool:
        root = self._get("isMeetingRunning", {"meetingID": meeting_id})
        return (root.findtext("running") or "false").lower() == "true"

    def get_meeting_info(self, meeting_id: str) -> dict[str, str]:
        root = self._get("getMeetingInfo", {"meetingID": meeting_id})
        return {child.tag: (child.text or "") for child in root}

    def get_meetings(self) -> ET.Element:
        return self._get("getMeetings")

    # --- recordings -------------------------------------------------------

    def get_recordings(
        self,
        *,
        meeting_id: str | None = None,
        record_id: str | None = None,
        state: str | None = None,
        meta: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "meetingID": meeting_id,
            "recordID": record_id,
            "state": state,
        }
        for key, value in (meta or {}).items():
            params[f"meta_{key}"] = value
        root = self._get("getRecordings", {k: v for k, v in params.items() if v})
        recordings = []
        for rec in root.iter("recording"):
            entry: dict[str, Any] = {
                "record_id": rec.findtext("recordID") or "",
                "meeting_id": rec.findtext("meetingID") or "",
                "name": rec.findtext("name") or "",
                "state": rec.findtext("state") or "",
                "playback": {},
                "meta": {},
            }
            playback = rec.find("playback")
            if playback is not None:
                entry["playback"] = {
                    "format": playback.findtext("type") or "",
                    "link": playback.findtext("link") or "",
                    "duration": playback.findtext("length") or 0,
                }
            meta_el = rec.find("meta")
            if meta_el is not None:
                entry["meta"] = {c.tag: (c.text or "") for c in meta_el}
            recordings.append(entry)
        return recordings

    def delete_recordings(self, record_ids: list[str]) -> None:
        self._get("deleteRecordings", {"recordID": ",".join(record_ids)})

    def publish_recordings(self, record_ids: list[str], publish: bool = True) -> None:
        self._get(
            "publishRecordings",
            {"recordID": ",".join(record_ids), "publish": "true" if publish else "false"},
        )

    def get_recording_text_tracks(self, record_id: str) -> list[dict[str, Any]]:
        """Returns JSON (not XML) — list of caption/subtitle tracks (WebVTT hrefs)."""
        raw = self._get_text("getRecordingTextTracks", {"recordID": record_id})
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise BbbError("getRecordingTextTracks", "PARSE_ERROR", raw[:500]) from exc
        response = data.get("response", {})
        if response.get("returncode") != "SUCCESS":
            raise BbbError(
                "getRecordingTextTracks",
                response.get("returncode", "UNKNOWN"),
                response.get("message", ""),
                response.get("messageKey", ""),
            )
        return response.get("tracks", [])

    def download_text_track(self, href: str) -> str:
        url = href if href.startswith("http") else f"{self.base_url}{href}"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BbbError("download_text_track", "HTTP_ERROR", str(exc)) from exc
        return resp.text

    # --- webhooks (bbb-webhooks module) -----------------------------------

    def hooks_create(
        self, callback_url: str, meeting_id: str | None = None, events: list[str] | None = None
    ) -> str:
        params: dict[str, Any] = {"callbackURL": callback_url}
        if meeting_id:
            params["meetingID"] = meeting_id
        if events:
            params["eventID"] = ",".join(events)
        root = self._get("hooks/create", params)
        return root.findtext("hookID") or ""

    def hooks_list(self, meeting_id: str | None = None) -> list[dict[str, str]]:
        root = self._get("hooks/list", {"meetingID": meeting_id} if meeting_id else None)
        hooks = []
        for hook in root.iter("hook"):
            hooks.append(
                {
                    "hook_id": hook.findtext("hookID") or "",
                    "callback_url": hook.findtext("callbackURL") or "",
                    "meeting_id": hook.findtext("meetingID") or "",
                }
            )
        return hooks

    def hooks_destroy(self, hook_id: str) -> None:
        self._get("hooks/destroy", {"hookID": hook_id})

    # --- health -----------------------------------------------------------

    def ping(self) -> bool:
        if not self.enabled:
            return False
        try:
            root = self._get("getMeetings")
            return (root.findtext("returncode") or "") == "SUCCESS"
        except Exception as exc:  # noqa: BLE001
            logger.warning("BBB ping failed: %s", exc)
            return False
