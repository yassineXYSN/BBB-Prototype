from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import InterviewStatus, TokenRole
from app.schemas import JoinPreview, JoinResponse
from app.services import scheduling
from app.services.bbb.client import BbbClient
from app.services.state_machine import InvalidTransition, transition

router = APIRouter(prefix="/public", tags=["public"])


def _load(token_value: str, db: Session, role: TokenRole | None = None):
    try:
        interview, token = scheduling.validate_token(db, token_value, role)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid invite link") from None
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    if interview.status in (InterviewStatus.cancelled, InterviewStatus.failed):
        raise HTTPException(status.HTTP_410_GONE, "This interview was cancelled")
    if interview.status == InterviewStatus.archived:
        raise HTTPException(status.HTTP_410_GONE, "This interview is already finished")
    return interview, token


@router.get("/join/{token}/preview", response_model=JoinPreview)
def preview(token: str, db: Session = Depends(get_db)) -> JoinPreview:
    """Works with BOTH the candidate and the interviewer invite link.

    The returned `role` tells the SPA which kind of participant it is, so the
    same /join/{token} page can host candidates and interviewers alike.
    """
    interview, invite_token = _load(token, db)
    token_role = invite_token.role
    if token_role == TokenRole.candidate and interview.status == InterviewStatus.scheduled:
        try:
            transition(
                db,
                interview,
                InterviewStatus.waiting,
                event="candidate:lobby",
                detail="Candidate opened the invite link",
            )
            db.commit()
        except InvalidTransition:
            db.rollback()
    return JoinPreview(
        interview_title=interview.title,
        scheduled_start=interview.scheduled_start,
        duration_minutes=interview.duration_minutes,
        timezone=interview.timezone,
        role=token_role.value,
        candidate_name=interview.candidate.full_name if interview.candidate else None,
        status=interview.status.value,
    )


@router.post("/join/{token}", response_model=JoinResponse)
def join(token: str, db: Session = Depends(get_db)) -> JoinResponse:
    """Join the room with any valid invite link.

    The token's role decides everything: candidates enter as VIEWER with the
    attendee password (and flip the interview to `live`), interviewers enter
    as MODERATOR with the moderator password.
    """
    interview, invite_token = _load(token, db)
    is_moderator = invite_token.role == TokenRole.interviewer

    scheduling.provision_bbb_meeting(db, interview)
    scheduling.ensure_bbb_meeting(db, interview)
    scheduling.mark_token_used(db, invite_token)
    if not is_moderator:
        try:
            if interview.status in (InterviewStatus.scheduled, InterviewStatus.waiting):
                transition(
                    db,
                    interview,
                    InterviewStatus.live,
                    event="candidate:joined",
                    detail="Candidate entered the meeting room",
                )
        except InvalidTransition:
            pass
    db.commit()

    password = interview.moderator_password if is_moderator else interview.join_password
    if not interview.meeting_id or not password:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Meeting room is not ready yet — please try again in a moment",
        )

    if is_moderator:
        full_name = interview.interviewer.full_name if interview.interviewer else "Interviewer"
    else:
        full_name = interview.candidate.full_name if interview.candidate else "Candidate"

    client = BbbClient()
    url = client.join_url(
        meeting_id=interview.meeting_id,
        full_name=full_name,
        password=password,
        role="MODERATOR" if is_moderator else "VIEWER",
    )
    return JoinResponse(
        join_url=url,
        meeting_id=interview.meeting_id,
        role="MODERATOR" if is_moderator else "VIEWER",
    )


@router.post("/join-interviewer/{token}", response_model=JoinResponse)
def join_interviewer(token: str, db: Session = Depends(get_db)) -> JoinResponse:
    """Legacy endpoint kept for compatibility — /join/{token} handles both roles now."""
    interview, invite_token = _load(token, db, TokenRole.interviewer)

    scheduling.provision_bbb_meeting(db, interview)
    scheduling.ensure_bbb_meeting(db, interview)
    scheduling.mark_token_used(db, invite_token)
    db.commit()

    if not interview.meeting_id or not interview.moderator_password:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Meeting room is not ready yet — please try again in a moment",
        )
    client = BbbClient()
    url = client.join_url(
        meeting_id=interview.meeting_id,
        full_name=interview.interviewer.full_name if interview.interviewer else "Interviewer",
        password=interview.moderator_password,
        role="MODERATOR",
    )
    return JoinResponse(join_url=url, meeting_id=interview.meeting_id, role="MODERATOR")
