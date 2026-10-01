"""Mailer with an SMTP backend and a console fallback (used in dev / when unset)."""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger("app.mail")


def _deliver(server: smtplib.SMTP, settings, msg: EmailMessage) -> None:
    if settings.smtp_user:
        server.login(settings.smtp_user, settings.smtp_password)
    server.send_message(msg)


def send_email(to: str, subject: str, body: str) -> None:
    """Send one message. Never raises — a mail problem must not break scheduling."""
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if not settings.smtp_host:
        logger.info("CONSOLE MAIL to=%s subject=%s\n%s", to, subject, body)
        print(f"\n--- CONSOLE MAIL ---\nTo: {to}\nSubject: {subject}\n{body}\n---\n")
        return

    try:
        if settings.smtp_port == 465:
            # Implicit TLS (e.g. smtp.gmail.com:465) — plain SMTP+STARTTLS
            # gets disconnected immediately on this port.
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=20,
                context=ssl.create_default_context(),
            ) as server:
                _deliver(server, settings, msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
                if settings.smtp_starttls:
                    server.starttls(context=ssl.create_default_context())
                _deliver(server, settings, msg)
    except (smtplib.SMTPException, OSError) as exc:
        logger.error(
            "SMTP delivery failed to %s via %s:%s — %s",
            to,
            settings.smtp_host,
            settings.smtp_port,
            exc,
        )


def send_interview_invite(
    *,
    to: str,
    candidate_name: str,
    title: str,
    start_label: str,
    duration_minutes: int,
    join_url: str,
    role: str,
) -> None:
    body = (
        f"Hello {candidate_name},\n\n"
        f"You have been invited to an interview: {title}\n"
        f"When: {start_label}\n"
        f"Duration: ~{duration_minutes} minutes\n\n"
        f"Join here: {join_url}\n\n"
        f"Notes:\n"
        f"- The session is recorded and transcribed.\n"
        f"- Only the transcript is kept after processing; the raw recording is deleted.\n"
        f"- Please test your camera and microphone a few minutes early.\n"
    )
    if role == "interviewer":
        body = (
            f"Hello {candidate_name},\n\n"
            f"You are the interviewer for: {title}\n"
            f"When: {start_label}\n"
            f"Duration: ~{duration_minutes} minutes\n\n"
            f"Join as moderator: {join_url}\n"
        )
    send_email(to, f"Interview invitation: {title}", body)


def send_reminder(*, to: str, title: str, start_label: str, join_url: str, when: str) -> None:
    body = (
        f'Reminder: your interview "{title}" starts {when} ({start_label}).\n\nJoin: {join_url}\n'
    )
    send_email(to, f"Reminder: {title}", body)


def send_transcript_ready(*, to: str, title: str, url: str) -> None:
    body = f'The transcript for "{title}" is ready: {url}\n'
    send_email(to, f"Transcript ready: {title}", body)
