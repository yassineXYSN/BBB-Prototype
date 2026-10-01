"""BBB connectivity spike — validates config end-to-end before building.

Usage:
    set BBB_URL=http://your-bbb-server/bigbluebutton/
    set BBB_SHARED_SECRET=...
    .venv/Scripts/python scripts/spike_bbb.py

Checks, in order:
  1. ping (getMeetings)
  2. create meeting (record=true)
  3. join URL generation (moderator + viewer)
  4. isMeetingRunning
  5. end meeting
  6. hooks create/list
  7. getRecordings + getRecordingTextTracks (CAPTIONS CHECK - run after a recorded test session)
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import get_settings  # noqa: E402
from app.services.bbb.client import BbbClient  # noqa: E402


def step(label: str, fn):
    print(f"\n[{label}] ...")
    try:
        result = fn()
        print(f"  OK: {result}")
        return result
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        return None


def main() -> int:
    settings = get_settings()
    if not settings.bbb_enabled:
        print("BBB_URL / BBB_SHARED_SECRET not configured. Set them in backend/.env first.")
        return 1

    client = BbbClient()
    step("ping", lambda: client.ping())

    meeting_id = "spike-test-meeting"
    step(
        "create",
        lambda: client.create_meeting(
            name="Spike Test",
            meeting_id=meeting_id,
            attendee_pw="ap123",
            moderator_pw="mp123",
            record=True,
            meta={"interview-id": "spike"},
        ),
    )

    step(
        "join URL (moderator)",
        lambda: client.join_url(
            meeting_id=meeting_id, full_name="Spike Mod", password="mp123", role="MODERATOR"
        ),
    )
    step("isMeetingRunning", lambda: client.is_meeting_running(meeting_id))
    step("end", lambda: client.end_meeting(meeting_id, "mp123"))

    callback = os.environ.get("SPIKE_CALLBACK_URL", "https://webhook.site/your-uuid")
    step("hooks/create", lambda: client.hooks_create(callback_url=callback))
    step("hooks/list", lambda: client.hooks_list())

    recordings = step("getRecordings(state=any)", lambda: client.get_recordings(state="any"))
    if recordings:
        for rec in recordings:
            record_id = rec["record_id"]
            tracks = step(
                f"getRecordingTextTracks({record_id})",
                lambda rid=record_id: client.get_recording_text_tracks(rid),
            )
            if tracks:
                href = tracks[0]["href"]
                vtt = step("download first track", lambda h=href: client.download_text_track(h))
                if vtt:
                    print("  CAPTIONS AVAILABLE — transcription via BBB captions is viable.")
                    print(f"  first 200 chars: {vtt[:200]!r}")
            else:
                print(
                    "  NO CAPTION TRACKS — check BBB live transcription config "
                    "or upload tracks via putRecordingTextTrack."
                )
    else:
        print("\nNo recordings found. Record a short session in the browser, end it, wait for")
        print("processing (~2 min), then re-run this script to check captions (step 7).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
