"""Seed the database with demo users, a template and sample interviews.

Usage:  python -m app.seed
"""

import json
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Interview, InterviewTemplate, User, UserRole
from app.security import hash_password
from app.services.scheduling import schedule_interview


def main() -> None:
    init_db()
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        if admin is None:
            admin = User(
                email="admin@example.com",
                full_name="Ada Admin",
                password_hash=hash_password("admin123"),
                role=UserRole.admin,
            )
            db.add(admin)
        interviewer = db.scalar(select(User).where(User.email == "interviewer@example.com"))
        if interviewer is None:
            interviewer = User(
                email="interviewer@example.com",
                full_name="Ivan Interviewer",
                password_hash=hash_password("interviewer123"),
                role=UserRole.interviewer,
            )
            db.add(interviewer)
        db.flush()

        template = db.scalar(
            select(InterviewTemplate).where(InterviewTemplate.title == "Backend Engineer Screen")
        )
        if template is None:
            template = InterviewTemplate(
                title="Backend Engineer Screen",
                description=(
                    "45-minute technical screen covering APIs, databases and system design."
                ),
                duration_minutes=45,
                language="en",
                questions=json.dumps(
                    [
                        "Walk me through a system you designed.",
                        "How do you approach API versioning?",
                        "Trade-offs between SQL and NoSQL for this use case?",
                        "How would you ensure idempotency in a webhook consumer?",
                    ]
                ),
                created_by_id=admin.id,
            )
            db.add(template)
            db.flush()

        existing = db.scalar(select(User).where(User.email == "candidate@example.com"))
        if existing is None:
            db.add(
                User(
                    email="candidate@example.com",
                    full_name="Cara Candidate",
                    password_hash=hash_password("candidate123"),
                    role=UserRole.interviewer,
                )
            )
        db.flush()

        interviews_count = len(db.scalars(select(Interview)).all())
        if interviews_count == 0:
            schedule_interview(
                db,
                title="Backend Engineer Screen — Demo",
                scheduled_start=datetime.utcnow() + timedelta(days=1),
                duration_minutes=45,
                timezone="UTC",
                candidate_name="Cara Candidate",
                candidate_email="candidate@example.com",
                interviewer=interviewer,
                template_id=template.id,
                send_email=False,
            )
        db.commit()

    print("Seed complete.")
    print("  admin        / admin123")
    print("  interviewer  / interviewer123")


if __name__ == "__main__":
    main()
