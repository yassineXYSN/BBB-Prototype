"""Interview lifecycle state machine — single source of truth for status changes."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import TERMINAL_STATUSES, TRANSITIONS, AuditLog, Interview, InterviewStatus


class InvalidTransition(Exception):
    def __init__(self, current: InterviewStatus, target: InterviewStatus):
        self.current = current
        self.target = target
        super().__init__(f"Invalid interview transition: {current.value} -> {target.value}")


def can_transition(current: InterviewStatus, target: InterviewStatus) -> bool:
    if current == target:
        return True
    return target in TRANSITIONS.get(current, set())


def transition(
    db: Session,
    interview: Interview,
    target: InterviewStatus,
    *,
    event: str | None = None,
    detail: str = "",
    reason: str | None = None,
    force: bool = False,
) -> Interview:
    """Move an interview to `target`, audit-log it and persist."""
    current = interview.status
    if not force and not can_transition(current, target):
        raise InvalidTransition(current, target)

    interview.status = target
    if reason:
        interview.failure_reason = reason
    interview.updated_at = datetime.now(UTC).replace(tzinfo=None)

    db.add(
        AuditLog(
            interview_id=interview.id,
            event=event or f"status:{target.value}",
            detail=detail or (f"{current.value} -> {target.value}"),
        )
    )
    db.flush()
    return interview


def is_terminal(status: InterviewStatus) -> bool:
    return status in TERMINAL_STATUSES
