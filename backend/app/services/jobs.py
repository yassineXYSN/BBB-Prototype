"""Background jobs: reminders, no-show detection, recording sweeps, hook re-registration.

Runs in a single asyncio loop inside the FastAPI process — no external worker needed.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    AuditLog,
    Interview,
    InterviewStatus,
    InviteToken,
    TokenRole,
)
from app.services import pipeline, scheduling
from app.services.bbb.client import BbbClient, BbbError
from app.services.state_machine import transition

logger = logging.getLogger("app.jobs")


def _has_audit(db, interview_id: int, event: str) -> bool:
    return (
        db.scalar(
            select(AuditLog.id).where(
                AuditLog.interview_id == interview_id, AuditLog.event == event
            )
        )
        is not None
    )


def send_due_reminders(db) -> int:
    settings = get_settings()
    now = datetime.utcnow()
    sent = 0
    upcoming = db.scalars(
        select(Interview).where(
            Interview.status.in_([InterviewStatus.scheduled, InterviewStatus.waiting]),
            Interview.scheduled_start.is_not(None),
        )
    ).all()
    for interview in upcoming:
        assert interview.scheduled_start is not None
        delta = interview.scheduled_start - now
        checks = [
            ("reminder:24h", timedelta(hours=settings.reminder_hours_before), 5),
            ("reminder:15m", timedelta(minutes=settings.reminder_minutes_before), 2),
        ]
        for event, window, tolerance_minutes in checks:
            if _has_audit(db, interview.id, event):
                continue
            diff = delta - window
            if timedelta(0) <= diff <= timedelta(minutes=tolerance_minutes):
                _send_reminder_for(db, interview, event, when="soon")
                sent += 1
            elif delta < timedelta(0) and event == "reminder:15m":
                # missed the window (server was down) — send once anyway
                _send_reminder_for(db, interview, event, when="now")
                sent += 1
    return sent


def _send_reminder_for(db, interview: Interview, event: str, when: str) -> None:
    from app.services import mail

    token = db.scalar(
        select(InviteToken).where(
            InviteToken.interview_id == interview.id,
            InviteToken.role == TokenRole.candidate,
        )
    )
    if token is None or interview.candidate is None:
        return
    mail.send_reminder(
        to=interview.candidate.email,
        title=interview.title,
        start_label=scheduling.format_start(interview),
        join_url=scheduling.candidate_link(token.token),
        when=when,
    )
    db.add(AuditLog(interview_id=interview.id, event=event, detail="reminder sent"))


def check_no_shows(db) -> int:
    settings = get_settings()
    now = datetime.utcnow()
    handled = 0
    candidates = db.scalars(
        select(Interview).where(Interview.status == InterviewStatus.scheduled)
    ).all()
    for interview in candidates:
        if not interview.scheduled_start:
            continue
        deadline = interview.scheduled_start + timedelta(minutes=settings.no_show_grace_minutes)
        if now < deadline:
            continue
        used = db.scalar(
            select(InviteToken.id).where(
                InviteToken.interview_id == interview.id, InviteToken.used_at.is_not(None)
            )
        )
        if used:
            continue
        transition(
            db,
            interview,
            InterviewStatus.failed,
            event="no-show",
            detail="Candidate never joined within grace period",
            reason="No-show: nobody joined the room",
        )
        handled += 1
    return handled


def sweep_recordings(db) -> int:
    """Safety net for missed webhooks: process any interview waiting on a recording."""
    processed = 0
    statuses = (
        InterviewStatus.ended,
        InterviewStatus.processing,
        InterviewStatus.live,
        InterviewStatus.waiting,
    )
    interviews = db.scalars(select(Interview).where(Interview.status.in_(statuses))).all()
    client = BbbClient()
    for interview in interviews:
        if not interview.meeting_id:
            continue
        # meeting liveness fallback
        if client.enabled and interview.status in (InterviewStatus.waiting, InterviewStatus.live):
            try:
                running = client.is_meeting_running(interview.meeting_id)
            except BbbError:
                running = True  # unknown -> don't fail it
            if not running and interview.status == InterviewStatus.live:
                transition(
                    db,
                    interview,
                    InterviewStatus.ended,
                    event="meeting:ended(sweep)",
                    detail="Meeting no longer running",
                )
            elif not running and interview.status == InterviewStatus.waiting:
                pass  # leave waiting; no-show job handles it
        if interview.status in (InterviewStatus.ended, InterviewStatus.processing):
            recordings = pipeline.sync_recordings(db, interview)
            if recordings:
                pipeline.mark_processing(db, interview, "sweep found recording(s)")
            if interview.status == InterviewStatus.processing and pipeline.process_interview(
                db, interview
            ):
                processed += 1
    return processed


def ensure_global_hook(db) -> None:
    settings = get_settings()
    client = BbbClient()
    if not client.enabled:
        return
    callback = (
        f"{settings.api_base_url}/api/v1/webhooks/bbb/events/{settings.bbb_webhook_path_token}"
    )
    try:
        hooks = client.hooks_list()
    except BbbError as exc:
        logger.warning("hooks/list failed: %s", exc)
        return
    if any(h["callback_url"] == callback for h in hooks):
        return
    try:
        hook_id = client.hooks_create(callback_url=callback)
        logger.info("Registered global BBB hook %s", hook_id)
        db.add(AuditLog(event="hooks:registered", detail=f"global hook {hook_id}"))
        db.commit()
    except BbbError as exc:
        logger.warning("hooks/create failed: %s", exc)


def run_sweep_once() -> dict:
    stats = {"reminders": 0, "no_shows": 0, "processed": 0}
    with SessionLocal() as db:
        try:
            stats["reminders"] = send_due_reminders(db)
            stats["no_shows"] = check_no_shows(db)
            stats["processed"] = sweep_recordings(db)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("sweep failed")
    return stats


async def job_loop() -> None:
    settings = get_settings()
    logger.info("Background job loop started (every %ss)", settings.job_sweep_seconds)
    while True:
        try:
            await asyncio.to_thread(run_sweep_once)
        except Exception:  # noqa: BLE001
            logger.exception("job iteration failed")
        await asyncio.sleep(settings.job_sweep_seconds)


async def startup_hook() -> None:
    await asyncio.to_thread(_startup_sync)


def _startup_sync() -> None:
    with SessionLocal() as db:
        ensure_global_hook(db)
