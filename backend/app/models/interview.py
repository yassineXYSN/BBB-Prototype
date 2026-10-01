import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.accounts import Candidate, User


class InterviewTemplate(Base):
    __tablename__ = "interview_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(36), unique=True, default=lambda: str(uuid.uuid4()), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=45)
    language: Mapped[str] = mapped_column(String(16), default="en")
    questions: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of strings
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class InterviewStatus(enum.StrEnum):
    draft = "draft"
    scheduled = "scheduled"
    waiting = "waiting"
    live = "live"
    ended = "ended"
    processing = "processing"
    transcribed = "transcribed"
    archived = "archived"
    cancelled = "cancelled"
    failed = "failed"


TERMINAL_STATUSES = {InterviewStatus.archived, InterviewStatus.cancelled, InterviewStatus.failed}

# Allowed transitions for the interview state machine.
TRANSITIONS: dict[InterviewStatus, set[InterviewStatus]] = {
    InterviewStatus.draft: {InterviewStatus.scheduled, InterviewStatus.cancelled},
    InterviewStatus.scheduled: {
        InterviewStatus.waiting,
        InterviewStatus.live,
        InterviewStatus.ended,
        InterviewStatus.failed,
        InterviewStatus.cancelled,
        InterviewStatus.processing,
    },
    InterviewStatus.waiting: {
        InterviewStatus.live,
        InterviewStatus.ended,
        InterviewStatus.failed,
        InterviewStatus.cancelled,
        InterviewStatus.processing,
    },
    InterviewStatus.live: {
        InterviewStatus.ended,
        InterviewStatus.failed,
        InterviewStatus.processing,
        InterviewStatus.cancelled,
    },
    InterviewStatus.ended: {
        InterviewStatus.processing,
        InterviewStatus.failed,
        InterviewStatus.cancelled,
        InterviewStatus.transcribed,
        InterviewStatus.archived,
    },
    InterviewStatus.processing: {
        InterviewStatus.transcribed,
        InterviewStatus.failed,
        InterviewStatus.ended,  # no recording marks -> nothing to process
    },
    InterviewStatus.transcribed: {InterviewStatus.archived},
    InterviewStatus.failed: {InterviewStatus.processing, InterviewStatus.cancelled},
    InterviewStatus.cancelled: set(),
    InterviewStatus.archived: set(),
}


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(36), unique=True, default=lambda: str(uuid.uuid4()), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[InterviewStatus] = mapped_column(
        Enum(InterviewStatus, values_callable=lambda e: [m.value for m in e]),
        default=InterviewStatus.draft,
        index=True,
    )
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=45)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    language: Mapped[str] = mapped_column(String(16), default="en")

    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("interview_templates.id"), nullable=True
    )
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), nullable=True)
    interviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    meeting_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True, index=True
    )
    join_password: Mapped[str | None] = mapped_column(String(64), nullable=True)
    moderator_password: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)

    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    candidate: Mapped[Candidate | None] = relationship(back_populates="interviews")
    interviewer: Mapped[User | None] = relationship(
        back_populates="interviews", foreign_keys=[interviewer_id]
    )
    template: Mapped[InterviewTemplate | None] = relationship()
    tokens: Mapped[list["InviteToken"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan"
    )
    bbb_meeting: Mapped["BbbMeeting | None"] = relationship(  # noqa: F821
        back_populates="interview", cascade="all, delete-orphan", uselist=False
    )
    recordings: Mapped[list["Recording"]] = relationship(  # noqa: F821
        back_populates="interview", cascade="all, delete-orphan"
    )
    transcripts: Mapped[list["Transcript"]] = relationship(  # noqa: F821
        back_populates="interview", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan"
    )


class TokenRole(enum.StrEnum):
    candidate = "candidate"
    interviewer = "interviewer"


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"))
    role: Mapped[TokenRole] = mapped_column(
        Enum(TokenRole, values_callable=lambda e: [m.value for m in e])
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    interview: Mapped[Interview] = relationship(back_populates="tokens")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interview_id: Mapped[int | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    interview: Mapped[Interview | None] = relationship(back_populates="audit_logs")
