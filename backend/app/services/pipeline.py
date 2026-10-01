"""Recording -> transcript -> purge pipeline (the core requirement)."""

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    AuditLog,
    Interview,
    InterviewStatus,
    Recording,
    Transcript,
    TranscriptSegment,
)
from app.services.bbb.client import BbbClient, BbbError
from app.services.bbb.vtt import cues_to_full_text, parse_vtt
from app.services.state_machine import transition

logger = logging.getLogger("app.pipeline")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _client() -> BbbClient:
    return BbbClient()


def find_interview_by_meeting_id(db: Session, meeting_id: str) -> Interview | None:
    return db.scalar(select(Interview).where(Interview.meeting_id == meeting_id))


def sync_recordings(db: Session, interview: Interview | None = None) -> list[Recording]:
    """Fetch recordings from BBB and upsert them locally."""
    client = _client()
    if not client.enabled:
        return []

    kwargs: dict = {"state": "any"}
    if interview is not None:
        kwargs = {"meta": {"interview-id": interview.public_id}}
    try:
        remote = client.get_recordings(**kwargs)
    except BbbError as exc:
        logger.warning("getRecordings failed: %s", exc)
        return []

    results: list[Recording] = []
    for entry in remote:
        record_id = entry.get("record_id")
        if not record_id:
            continue
        local = db.scalar(select(Recording).where(Recording.record_id == record_id))
        created = False
        if local is None:
            local = Recording(record_id=record_id, interview_id=0)
            created = True
        local.state = entry.get("state", "unknown")
        local.name = entry.get("name", "")
        local.play_url = entry.get("playback", {}).get("link", "")
        local.raw_meta = json.dumps(entry)
        local.fetched_at = utcnow()
        if created:
            owner = _interview_for_recording(db, entry)
            if owner is None:
                continue
            local.interview_id = owner.id
            db.add(local)
        results.append(local)
    db.flush()
    return results


def _interview_for_recording(db: Session, entry: dict) -> Interview | None:
    meta = entry.get("meta") or {}
    interview_id = meta.get("interview-id") or meta.get("meta_interview-id")
    if interview_id:
        interview = db.scalar(select(Interview).where(Interview.public_id == str(interview_id)))
        if interview:
            return interview
    meeting_id = entry.get("meeting_id")
    if meeting_id:
        return find_interview_by_meeting_id(db, meeting_id)
    return None


def process_interview(db: Session, interview: Interview) -> bool:
    """Fetch captions for all recordings of an interview, store transcript, purge raw media.

    Returns True when the interview reached `transcribed`.
    """
    settings = get_settings()
    client = _client()

    recordings = list(
        db.scalars(select(Recording).where(Recording.interview_id == interview.id)).all()
    )
    if not recordings:
        sync_recordings(db, interview)
        recordings = list(
            db.scalars(select(Recording).where(Recording.interview_id == interview.id)).all()
        )
    if not recordings:
        logger.info("No recordings yet for interview %s", interview.public_id)
        return False

    if interview.status not in (InterviewStatus.processing, InterviewStatus.transcribed):
        mark_processing(db, interview, "pipeline processing started")

    transcript = db.scalar(select(Transcript).where(Transcript.interview_id == interview.id))
    if transcript is None:
        transcript = _build_transcript(db, interview, recordings, client)
        if transcript is None:
            return False

    if interview.status != InterviewStatus.transcribed:
        transition(
            db,
            interview,
            InterviewStatus.transcribed,
            event="transcript:stored",
            detail=f"transcript_id={transcript.id}",
        )

    if not settings.keep_raw_recording:
        _purge_recordings(db, interview, recordings, client)
    return True


def _build_transcript(
    db: Session, interview: Interview, recordings: list[Recording], client: BbbClient
) -> Transcript | None:
    languages = [
        lang.strip() for lang in get_settings().caption_languages.split(",") if lang.strip()
    ]
    for recording in recordings:
        if recording.purged_at is not None:
            continue
        if recording.state not in ("processed", "published", "unpublished"):
            logger.info("Recording %s not ready (state=%s)", recording.record_id, recording.state)
            continue
        if not client.enabled:
            break
        try:
            tracks = client.get_recording_text_tracks(recording.record_id)
        except BbbError as exc:
            logger.warning("text tracks for %s failed: %s", recording.record_id, exc)
            continue
        if not tracks:
            logger.warning("No caption tracks on recording %s", recording.record_id)
            continue

        chosen = _choose_track(tracks, languages)
        vtt = client.download_text_track(chosen["href"])
        cues = parse_vtt(vtt)
        if not cues:
            logger.warning("Empty transcript parsed from %s", recording.record_id)
            continue

        transcript = Transcript(
            interview_id=interview.id,
            recording_id=recording.id,
            language=chosen.get("lang") or interview.language,
            source=chosen.get("source") or "captions",
            raw_vtt=vtt,
            full_text=cues_to_full_text(cues),
        )
        db.add(transcript)
        db.flush()
        for cue in cues:
            db.add(
                TranscriptSegment(
                    transcript_id=transcript.id,
                    idx=cue.idx,
                    start_seconds=cue.start,
                    end_seconds=cue.end,
                    speaker=cue.speaker,
                    text=cue.text,
                )
            )
        db.add(
            AuditLog(
                interview_id=interview.id,
                event="transcript:created",
                detail=f"recording={recording.record_id} cues={len(cues)} "
                f"lang={transcript.language} source={transcript.source}",
            )
        )
        db.flush()
        return transcript
    return None


def _choose_track(tracks: list[dict], preferred: list[str]) -> dict:
    for lang in preferred:
        for track in tracks:
            if str(track.get("lang", "")).lower().startswith(lang.lower()):
                return track
    for track in tracks:
        if track.get("source") in ("live", "automatic"):
            return track
    return tracks[0]


def _purge_recordings(
    db: Session, interview: Interview, recordings: list[Recording], client: BbbClient
) -> None:
    to_delete = [r for r in recordings if r.purged_at is None]
    if not to_delete:
        return
    if not client.enabled:
        logger.info("BBB disabled — skipping purge for %s", interview.public_id)
        return
    record_ids = [r.record_id for r in to_delete]
    try:
        client.delete_recordings(record_ids)
    except BbbError as exc:
        logger.error("deleteRecordings failed for %s: %s", record_ids, exc)
        db.add(
            AuditLog(
                interview_id=interview.id,
                event="purge:failed",
                detail=str(exc),
            )
        )
        return
    for recording in to_delete:
        recording.purged_at = utcnow()
        recording.state = "deleted"
    db.add(
        AuditLog(
            interview_id=interview.id,
            event="purge:done",
            detail=f"deleted={','.join(record_ids)}",
        )
    )
    db.flush()


def mark_processing(db: Session, interview: Interview, detail: str = "") -> None:
    if interview.status in (
        InterviewStatus.ended,
        InterviewStatus.waiting,
        InterviewStatus.live,
        InterviewStatus.scheduled,
        InterviewStatus.failed,
    ):
        transition(
            db,
            interview,
            InterviewStatus.processing,
            event="recording:detected",
            detail=detail,
        )
