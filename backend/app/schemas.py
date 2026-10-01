from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    email: EmailStr
    full_name: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class TemplateCreate(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    description: str = ""
    duration_minutes: int = Field(default=45, ge=5, le=480)
    language: str = "en"
    questions: list[str] = []


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    title: str
    description: str
    duration_minutes: int
    language: str
    created_at: datetime


class InterviewScheduleRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    scheduled_start: datetime
    duration_minutes: int = Field(default=45, ge=5, le=480)
    timezone: str = "UTC"
    language: str = "en"
    candidate_name: str = Field(min_length=1, max_length=255)
    candidate_email: EmailStr
    template_id: str | None = None
    notes: str = ""
    send_email: bool = True


class RescheduleRequest(BaseModel):
    scheduled_start: datetime


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    full_name: str
    email: EmailStr


class InterviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    title: str
    status: str
    scheduled_start: datetime | None
    duration_minutes: int
    timezone: str
    language: str
    failure_reason: str | None = None
    rating: int | None = None
    verdict: str | None = None
    candidate: CandidateOut | None = None


class TokenOut(BaseModel):
    role: str
    link: str
    expires_at: datetime


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    record_id: str
    state: str
    name: str
    play_url: str
    purged_at: datetime | None


class TranscriptSegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    idx: int
    start_seconds: float
    end_seconds: float
    speaker: str | None
    text: str


class TranscriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    language: str
    source: str
    full_text: str
    created_at: datetime
    segments: list[TranscriptSegmentOut] = []


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event: str
    detail: str
    created_at: datetime


class InterviewDetail(InterviewOut):
    meeting_id: str | None = None
    notes: str = ""
    template_id: int | None = None
    tokens: list[TokenOut] = []
    recordings: list[RecordingOut] = []
    transcripts: list[TranscriptOut] = []
    audit_logs: list[AuditOut] = []


class JoinPreview(BaseModel):
    interview_title: str
    scheduled_start: datetime | None
    duration_minutes: int
    timezone: str
    role: str
    candidate_name: str | None = None
    status: str


class JoinResponse(BaseModel):
    join_url: str
    meeting_id: str
    role: str
