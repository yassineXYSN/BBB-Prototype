"""BigBlueButton webhook receivers.

Three entrypoints (path token = unguessable secret in the URL):
  POST /webhooks/bbb/events/{token}          - bbb-webhooks module events (form-encoded)
  POST /webhooks/bbb/recording-ready/{token} - meta_bbb-recording-ready-url (JWT signed)
  GET  /webhooks/bbb/meeting-ended/{token}   - meta_endCallbackUrl (?meetingID=..&recordingmarks=..)
"""

import hashlib
import json
import logging
from datetime import UTC, datetime

import jwt as pyjwt
from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.models import AuditLog, Interview, InterviewStatus
from app.services import pipeline
from app.services.state_machine import transition

logger = logging.getLogger("app.webhooks")

router = APIRouter(prefix="/webhooks/bbb", tags=["webhooks"])


def _check_path_token(token: str) -> None:
    expected = get_settings().bbb_webhook_path_token
    if not expected or token != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bad webhook token")


def _check_optional_checksum(request: Request) -> None:
    if get_settings().bbb_webhook_verify != "api":
        return
    query = request.url.query
    if "checksum=" not in query:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing checksum")
    base, _, checksum = query.partition("checksum=")
    checksum = checksum.split("&")[0]
    expected = hashlib.sha1(
        f"{base.rstrip('&')}{get_settings().bbb_shared_secret}".encode()
    ).hexdigest()
    if checksum != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Checksum mismatch")


def _find_interview(db: Session, meeting_id: str | None) -> Interview | None:
    if not meeting_id:
        return None
    return db.scalar(select(Interview).where(Interview.meeting_id == meeting_id))


def handle_meeting_end(db: Session, interview: Interview, recording_marks: bool) -> None:
    meeting = interview.bbb_meeting
    if meeting:
        meeting.ended_at = datetime.now(UTC).replace(tzinfo=None)
        meeting.recording_marks = meeting.recording_marks or recording_marks

    db.add(
        AuditLog(
            interview_id=interview.id,
            event="webhook:meeting-ended",
            detail=f"recordingmarks={recording_marks}",
        )
    )

    if interview.status in (InterviewStatus.cancelled, InterviewStatus.failed):
        return

    if recording_marks:
        pipeline.mark_processing(db, interview, "meeting ended with recording marks")
    elif interview.status in (
        InterviewStatus.scheduled,
        InterviewStatus.waiting,
        InterviewStatus.live,
    ):
        transition(
            db,
            interview,
            InterviewStatus.ended,
            event="meeting:ended",
            detail="Meeting ended without recording marks",
        )


@router.post("/events/{path_token}")
async def events_webhook(
    path_token: str,
    request: Request,
    event: str = Form(...),
    timestamp: str = Form(""),
) -> dict:
    _check_path_token(path_token)
    _check_optional_checksum(request)

    try:
        payload = json.loads(event)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid event payload") from None

    data = payload.get("data", payload)
    event_id = str(data.get("id") or data.get("type") or "")
    attributes = data.get("attributes") or {}
    meeting_attrs = attributes.get("meeting") or {}
    meeting_id = (
        meeting_attrs.get("external-meeting-id")
        or meeting_attrs.get("externalMeetingId")
        or meeting_attrs.get("internal-meeting-id")
    )

    def work() -> None:
        from app.db import SessionLocal

        with SessionLocal() as db:
            interview = _find_interview(db, meeting_id)
            if interview is None:
                logger.info("Webhook for unknown meeting %s (%s)", meeting_id, event_id)
                return
            db.add(
                AuditLog(
                    interview_id=interview.id,
                    event=f"webhook:{event_id or 'unknown'}",
                    detail=json.dumps(attributes)[:500],
                )
            )
            if event_id in ("user-joined", "user_joined"):
                if interview.status in (InterviewStatus.scheduled, InterviewStatus.waiting):
                    transition(
                        db,
                        interview,
                        InterviewStatus.live,
                        event="participant:joined",
                        detail="First participant joined",
                    )
            elif event_id in ("meeting-ended", "meeting-destroyed", "meeting_ended"):
                marks = (
                    str(
                        attributes.get("recordingmarks")
                        or meeting_attrs.get("recordingmarks")
                        or "false"
                    ).lower()
                    == "true"
                )
                handle_meeting_end(db, interview, marks)
            elif event_id in ("recording-started", "recording_started"):
                meeting = interview.bbb_meeting
                if meeting:
                    meeting.recording_marks = True
                pipeline.mark_processing(db, interview, "recording started in session")
            db.commit()

    await run_in_threadpool(work)
    return {"ok": True}


@router.post("/recording-ready/{path_token}")
async def recording_ready_webhook(
    path_token: str,
    request: Request,
    signed_parameters: str = Form(...),
) -> dict:
    _check_path_token(path_token)
    _check_optional_checksum(request)
    settings = get_settings()

    try:
        claims = pyjwt.decode(signed_parameters, settings.bbb_shared_secret, algorithms=["HS256"])
    except pyjwt.PyJWTError as exc:
        logger.warning("recording-ready JWT rejected: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature") from None

    meeting_id = claims.get("meeting_id")
    record_id = claims.get("record_id")
    logger.info("recording-ready meeting=%s record=%s", meeting_id, record_id)

    def work() -> None:
        from app.db import SessionLocal

        with SessionLocal() as db:
            interview = _find_interview(db, meeting_id)
            if interview is None:
                logger.warning("recording-ready for unknown meeting %s", meeting_id)
                return
            pipeline.mark_processing(
                db, interview, f"recording-ready callback record_id={record_id}"
            )
            db.commit()
            pipeline.sync_recordings(db, interview)
            db.commit()
            if interview.status == InterviewStatus.processing:
                pipeline.process_interview(db, interview)
                db.commit()

    await run_in_threadpool(work)
    return {"ok": True}


@router.get("/meeting-ended/{path_token}")
async def meeting_ended_callback(
    path_token: str,
    meeting_id: str = Query(..., alias="meetingID"),
    recordingmarks: str = Query("false"),
) -> dict:
    _check_path_token(path_token)
    marks = recordingmarks.lower() == "true"

    def work() -> None:
        from app.db import SessionLocal

        with SessionLocal() as db:
            interview = _find_interview(db, meeting_id)
            if interview is None:
                logger.info("meeting-ended callback for unknown meeting %s", meeting_id)
                return
            handle_meeting_end(db, interview, marks)
            db.commit()
            if interview.status == InterviewStatus.processing:
                pipeline.sync_recordings(db, interview)
                db.commit()

    await run_in_threadpool(work)
    return {"ok": True}
