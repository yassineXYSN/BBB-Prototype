import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import InterviewTemplate, User
from app.schemas import TemplateCreate, TemplateOut

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateOut])
def list_templates(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> list[TemplateOut]:
    rows = db.scalars(select(InterviewTemplate).order_by(InterviewTemplate.created_at.desc())).all()
    out = []
    for row in rows:
        item = TemplateOut.model_validate(row)
        out.append(item)
    return out


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TemplateOut:
    template = InterviewTemplate(
        title=payload.title,
        description=payload.description,
        duration_minutes=payload.duration_minutes,
        language=payload.language,
        questions=json.dumps(payload.questions, ensure_ascii=False),
        created_by_id=user.id,
    )
    db.add(template)
    db.commit()
    return TemplateOut.model_validate(template)


@router.get("/{template_id}", response_model=TemplateOut)
def get_template(
    template_id: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> TemplateOut:
    template = db.scalar(
        select(InterviewTemplate).where(InterviewTemplate.public_id == template_id)
    )
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    return TemplateOut.model_validate(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> None:
    template = db.scalar(
        select(InterviewTemplate).where(InterviewTemplate.public_id == template_id)
    )
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    db.delete(template)
    db.commit()
