from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.services.bbb.client import BbbClient

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "env": get_settings().app_env}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        pass
    bbb_ok = BbbClient().ping() if settings.bbb_enabled else None
    return {
        "database": db_ok,
        "bbb": bbb_ok,
        "bbb_configured": settings.bbb_enabled,
        "mailer": "smtp" if settings.smtp_host else "console",
    }
