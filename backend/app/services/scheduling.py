"""Interview scheduling: candidates, invite tokens, BBB meeting provisioning, emails."""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    AuditLog,
    BbbMeeting,
    Candidate,
    Interview,
    InterviewStatus,
    InterviewTemplate,
    InviteToken,
    TokenRole,
    User,
)
from app.security import random_password, random_token
from app.services import mail
from app.services.bbb.client import BbbClient, BbbError
from app.services.state_machine import transition

logger = logging.getLogger("app.scheduling")

HOOK_EVENTS = [
    "user-joined",
    "user-left",
    "meeting-created",
    "meeting-ended",
    "meeting-destroyed",
    "recording-started",
    "recording-stopped",
]


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def candidate_link(token: str) -> str:
    return f"{get_settings().public_app_url}/join/{token}"


def interviewer_link(token: str) -> str:
    return f"{get_settings().public_app_url}/join/{token}"


def get_or_create_candidate(db: Session, *, name: str, email: str) -> Candidate:
    candidate = db.scalar(select(Candidate).where(Candidate.email == email.lower()))
    if candidate is None:
        candidate = Candidate(full_name=name, email=email.lower())
        db.add(candidate)
        db.flush()
    else:
        candidate.full_name = name
    return candidate


def create_tokens(db: Session, interview: Interview) -> dict[str, str]:
    expires = utcnow() + timedelta(days=7)
    links: dict[str, str] = {}
    for role in (TokenRole.candidate, TokenRole.interviewer):
        token = InviteToken(
            token=random_token(), interview_id=interview.id, role=role, expires_at=expires
        )
        db.add(token)
        db.flush()
        links[role.value] = token.token
    return links


def provision_bbb_meeting(db: Session, interview: Interview) -> None:
    """Create the BBB meeting + register a webhook. Idempotent."""
    settings = get_settings()
    if interview.meeting_id:
        return

    meeting_id = f"interview-{interview.public_id}"
    # Keep passwords stable across re-provisions (meeting vanished from BBB and
    # was re-created): browsers may still hold signed join URLs containing them,
    # and rotating would break refreshes of an open room tab.
    attendee_pw = interview.join_password or random_password()
    moderator_pw = interview.moderator_password or random_password()
    interview.meeting_id = meeting_id
    interview.join_password = attendee_pw
    interview.moderator_password = moderator_pw

    end_callback = (
        f"{settings.api_base_url}/api/v1/webhooks/bbb/meeting-ended/"
        f"{settings.bbb_webhook_path_token}"
    )
    recording_ready = (
        f"{settings.api_base_url}/api/v1/webhooks/bbb/recording-ready/"
        f"{settings.bbb_webhook_path_token}"
    )

    client = BbbClient()
    hook_id: str | None = None
    if client.enabled and settings.bbb_auto_join:
        try:
            start_label = format_start(interview)
            client.create_meeting(
                name=interview.title,
                meeting_id=meeting_id,
                attendee_pw=attendee_pw,
                moderator_pw=moderator_pw,
                record=True,
                duration=interview.duration_minutes + 15,
                welcome=(
                    f"Interview: {interview.title}\nStarts: {start_label}\n"
                    "This session is recorded and transcribed."
                ),
                meta={
                    "interview-id": interview.public_id,
                    "bbb-recording-ready-url": recording_ready,
                    "endCallbackUrl": end_callback,
                    "category": "interview",
                },
                auto_start_recording=True,
                allow_start_stop_recording=False,
            )
            hook_id = (
                client.hooks_create(
                    callback_url=f"{settings.api_base_url}/api/v1/webhooks/bbb/events/{settings.bbb_webhook_path_token}",
                    meeting_id=meeting_id,
                    events=HOOK_EVENTS,
                )
                or None
            )
        except BbbError as exc:
            logger.error("BBB provisioning failed for %s: %s", interview.public_id, exc)
            db.add(
                AuditLog(
                    interview_id=interview.id,
                    event="bbb:create-failed",
                    detail=str(exc),
                )
            )

    # Upsert the single BbbMeeting row (interview_id is unique) — also reused
    # when a meeting is re-created after it vanished from the BBB server.
    meeting = db.scalar(select(BbbMeeting).where(BbbMeeting.interview_id == interview.id))
    if meeting is None:
        meeting = BbbMeeting(interview_id=interview.id, record=True)
        db.add(meeting)
    meeting.external_meeting_id = meeting_id
    meeting.hook_id = hook_id


def ensure_bbb_meeting(db: Session, interview: Interview) -> None:
    """Re-create the BBB meeting when it no longer exists on the server.

    Meetings expire on BBB, disappear after a server reset, or were never
    created (provisioning happened while BBB was disabled). Candidates would
    otherwise get an `invalidMeetingIdentifier` error page when joining.
    """
    client = BbbClient()
    if not client.enabled or not interview.meeting_id:
        return
    try:
        client.get_meeting_info(interview.meeting_id)
        return
    except BbbError as exc:
        logger.warning(
            "BBB meeting %s unavailable (%s) — re-provisioning", interview.meeting_id, exc
        )
    interview.meeting_id = None
    provision_bbb_meeting(db, interview)
    db.add(
        AuditLog(
            interview_id=interview.id,
            event="bbb:recreated",
            detail=interview.meeting_id or "",
        )
    )


def format_start(interview: Interview) -> str:
    if not interview.scheduled_start:
        return "unscheduled"
    return f"{interview.scheduled_start.isoformat(sep=' ')} {interview.timezone}"


def send_invites(db: Session, interview: Interview, links: dict[str, str]) -> None:
    assert interview.candidate is not None
    start_label = format_start(interview)
    if interview.candidate:
        mail.send_interview_invite(
            to=interview.candidate.email,
            candidate_name=interview.candidate.full_name,
            title=interview.title,
            start_label=start_label,
            duration_minutes=interview.duration_minutes,
            join_url=candidate_link(links["candidate"]),
            role="candidate",
        )
    if interview.interviewer:
        mail.send_interview_invite(
            to=interview.interviewer.email,
            candidate_name=interview.interviewer.full_name,
            title=interview.title,
            start_label=start_label,
            duration_minutes=interview.duration_minutes,
            join_url=interviewer_link(links["interviewer"]),
            role="interviewer",
        )


def schedule_interview(
    db: Session,
    *,
    title: str,
    scheduled_start: datetime,
    duration_minutes: int,
    timezone: str,
    candidate_name: str,
    candidate_email: str,
    interviewer: User,
    template_id: int | None = None,
    language: str = "en",
    notes: str = "",
    send_email: bool = True,
) -> Interview:
    template: InterviewTemplate | None = None
    if template_id:
        template = db.get(InterviewTemplate, template_id)
        if template:
            duration_minutes = template.duration_minutes
            language = template.language
            title = title or template.title

    candidate = get_or_create_candidate(db, name=candidate_name, email=candidate_email)

    interview = Interview(
        title=title,
        status=InterviewStatus.draft,
        scheduled_start=scheduled_start,
        duration_minutes=duration_minutes,
        timezone=timezone,
        language=language,
        candidate_id=candidate.id,
        interviewer_id=interviewer.id,
        template_id=template.id if template else None,
        notes=notes,
    )
    db.add(interview)
    db.flush()

    transition(
        db,
        interview,
        InterviewStatus.scheduled,
        event="scheduled",
        detail="Interview scheduled",
    )
    links = create_tokens(db, interview)
    provision_bbb_meeting(db, interview)
    db.add(
        AuditLog(
            interview_id=interview.id,
            event="invites:created",
            detail=json.dumps(links),
        )
    )
    db.flush()
    if send_email:
        send_invites(db, interview, links)
    return interview


def reschedule_interview(db: Session, interview: Interview, new_start: datetime) -> Interview:
    interview.scheduled_start = new_start
    for token in interview.tokens:
        token.used_at = None
        token.expires_at = utcnow() + timedelta(days=7)
    db.add(
        AuditLog(
            interview_id=interview.id,
            event="rescheduled",
            detail=f"new start={new_start.isoformat()}",
        )
    )
    links = {t.role.value: t.token for t in interview.tokens}
    if interview.status in (InterviewStatus.scheduled, InterviewStatus.waiting):
        send_invites(db, interview, links)
    return interview


def cancel_interview(db: Session, interview: Interview, reason: str = "") -> Interview:
    transition(
        db,
        interview,
        InterviewStatus.cancelled,
        event="cancelled",
        detail=reason or "Cancelled by user",
        force=interview.status == InterviewStatus.draft,
    )
    meeting = interview.bbb_meeting
    client = BbbClient()
    if client.enabled and interview.meeting_id:
        try:
            if meeting and meeting.hook_id:
                client.hooks_destroy(meeting.hook_id)
            client.end_meeting(interview.meeting_id, interview.moderator_password or "")
        except BbbError as exc:
            logger.info("cleanup on cancel: %s", exc)
    return interview


def validate_token(
    db: Session, token_value: str, role: TokenRole | None = None
) -> tuple[Interview, InviteToken]:
    token = db.scalar(select(InviteToken).where(InviteToken.token == token_value))
    if token is None:
        raise LookupError("Invalid invite link")
    if role is not None and token.role != role:
        raise PermissionError("This link cannot be used for this action")
    if token.expires_at < utcnow():
        raise PermissionError("This invite link has expired")
    return token.interview, token


def mark_token_used(db: Session, token: InviteToken) -> None:
    if token.used_at is None:
        token.used_at = utcnow()
