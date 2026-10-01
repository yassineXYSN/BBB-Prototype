from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_admin
from app.config import get_settings
from app.db import get_db
from app.models import (
    Candidate,
    Interview,
    InterviewStatus,
    Transcript,
    User,
)
from app.schemas import (
    AuditOut,
    InterviewDetail,
    InterviewOut,
    InterviewScheduleRequest,
    JoinResponse,
    RecordingOut,
    RescheduleRequest,
    TokenOut,
    TranscriptOut,
)
from app.services import scheduling
from app.services.bbb.client import BbbClient
from app.services.state_machine import InvalidTransition

router = APIRouter(prefix="/interviews", tags=["interviews"])


def _detail(interview: Interview) -> InterviewDetail:
    settings = get_settings()
    base = InterviewOut.model_validate(interview)
    return InterviewDetail(
        **base.model_dump(),
        meeting_id=interview.meeting_id,
        notes=interview.notes,
        template_id=interview.template_id,
        tokens=[
            TokenOut(
                role=t.role.value,
                link=f"{settings.public_app_url}/join/{t.token}",
                expires_at=t.expires_at,
            )
            for t in interview.tokens
        ],
        recordings=[RecordingOut.model_validate(r) for r in interview.recordings],
        transcripts=[TranscriptOut.model_validate(t) for t in interview.transcripts],
        audit_logs=[AuditOut.model_validate(a) for a in interview.audit_logs],
    )


def _get_interview(db: Session, public_id: str) -> Interview:
    interview = db.scalar(
        select(Interview)
        .where(Interview.public_id == public_id)
        .options(
            selectinload(Interview.candidate),
            selectinload(Interview.interviewer),
            selectinload(Interview.tokens),
            selectinload(Interview.recordings),
            selectinload(Interview.transcripts).selectinload(Transcript.segments),
            selectinload(Interview.audit_logs),
            selectinload(Interview.bbb_meeting),
        )
    )
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return interview


@router.get("", response_model=list[InterviewOut])
def list_interviews(
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = None,
    upcoming: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[InterviewOut]:
    query = select(Interview).options(
        selectinload(Interview.candidate), selectinload(Interview.interviewer)
    )
    if status_filter:
        values = [s.strip() for s in status_filter.split(",") if s.strip()]
        query = query.where(Interview.status.in_(values))
    if search:
        like = f"%{search}%"
        query = query.join(Candidate, isouter=True).where(
            or_(
                Interview.title.ilike(like),
                Candidate.full_name.ilike(like),
                Candidate.email.ilike(like),
            )
        )
    if upcoming:
        query = query.where(
            Interview.status.in_([InterviewStatus.scheduled, InterviewStatus.waiting]),
            Interview.scheduled_start >= datetime.utcnow(),
        )
    query = query.order_by(Interview.scheduled_start.asc().nullslast(), Interview.created_at.desc())
    interviews = db.scalars(query).unique().all()
    return [InterviewOut.model_validate(i) for i in interviews]


@router.get("/stats", response_model=dict[str, int])
def stats(db: Session = Depends(get_db), _user: User = Depends(get_current_user)) -> dict:
    rows = db.execute(select(Interview.status, func.count()).group_by(Interview.status)).all()
    return {status.value: count for status, count in rows}


@router.post("", response_model=InterviewDetail, status_code=status.HTTP_201_CREATED)
def schedule(
    payload: InterviewScheduleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InterviewDetail:
    template_id = None
    if payload.template_id:
        from app.models import InterviewTemplate

        template = db.scalar(
            select(InterviewTemplate).where(InterviewTemplate.public_id == payload.template_id)
        )
        if template is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown template")
        template_id = template.id

    interview = scheduling.schedule_interview(
        db,
        title=payload.title,
        scheduled_start=payload.scheduled_start.replace(tzinfo=None),
        duration_minutes=payload.duration_minutes,
        timezone=payload.timezone,
        candidate_name=payload.candidate_name,
        candidate_email=payload.candidate_email,
        interviewer=user,
        template_id=template_id,
        language=payload.language,
        notes=payload.notes,
        send_email=payload.send_email,
    )
    db.commit()
    return _detail(_get_interview(db, interview.public_id))


@router.get("/{public_id}", response_model=InterviewDetail)
def get_interview(
    public_id: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> InterviewDetail:
    return _detail(_get_interview(db, public_id))


@router.patch("/{public_id}", response_model=InterviewDetail)
def update_interview(
    public_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> InterviewDetail:
    interview = _get_interview(db, public_id)
    for field in ("notes", "rating", "verdict", "title"):
        if field in payload and payload[field] is not None:
            setattr(interview, field, payload[field])
    db.commit()
    return _detail(_get_interview(db, public_id))


@router.post("/{public_id}/reschedule", response_model=InterviewDetail)
def reschedule(
    public_id: str,
    payload: RescheduleRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> InterviewDetail:
    interview = _get_interview(db, public_id)
    if interview.status in (
        InterviewStatus.ended,
        InterviewStatus.processing,
        InterviewStatus.transcribed,
        InterviewStatus.cancelled,
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Interview already finished")
    scheduling.reschedule_interview(db, interview, payload.scheduled_start.replace(tzinfo=None))
    db.commit()
    return _detail(_get_interview(db, public_id))


@router.post("/{public_id}/cancel", response_model=InterviewDetail)
def cancel(
    public_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> InterviewDetail:
    interview = _get_interview(db, public_id)
    try:
        scheduling.cancel_interview(db, interview)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return _detail(_get_interview(db, public_id))


@router.post("/{public_id}/moderator-join", response_model=JoinResponse)
def moderator_join(
    public_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JoinResponse:
    interview = _get_interview(db, public_id)
    if not interview.meeting_id or not interview.moderator_password:
        raise HTTPException(status.HTTP_409_CONFLICT, "Meeting not provisioned yet")
    scheduling.provision_bbb_meeting(db, interview)  # idempotent retry if provisioning failed
    scheduling.ensure_bbb_meeting(db, interview)  # re-create if the meeting vanished on BBB
    db.commit()
    client = BbbClient()
    url = client.join_url(
        meeting_id=interview.meeting_id,
        full_name=user.full_name,
        password=interview.moderator_password,
        role="MODERATOR",
    )
    return JoinResponse(join_url=url, meeting_id=interview.meeting_id, role="MODERATOR")


@router.post("/{public_id}/process", response_model=InterviewDetail)
def force_process(
    public_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> InterviewDetail:
    """Manually (re)run the recording -> transcript -> purge pipeline."""
    from app.services import pipeline

    interview = _get_interview(db, public_id)
    pipeline.sync_recordings(db, interview)
    db.commit()
    pipeline.process_interview(db, interview)
    db.commit()
    return _detail(_get_interview(db, public_id))


@router.get("/{public_id}/transcript", response_model=TranscriptOut)
def get_transcript(
    public_id: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> TranscriptOut:
    interview = _get_interview(db, public_id)
    if not interview.transcripts:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No transcript available yet")
    return TranscriptOut.model_validate(interview.transcripts[-1])


@router.delete("/{public_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_interview(
    public_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
) -> None:
    interview = _get_interview(db, public_id)
    db.delete(interview)
    db.commit()
