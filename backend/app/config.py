from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    app_secret_key: str = "dev-secret"
    public_app_url: str = "http://localhost:5173"
    api_base_url: str = "http://localhost:8000"

    database_url: str = "sqlite:///./data/app.db"

    bbb_url: str = ""
    bbb_public_url: str = ""  # browser-facing base for join URLs (defaults to bbb_url)
    bbb_shared_secret: str = ""
    bbb_webhook_path_token: str = "dev-webhook-token"
    bbb_webhook_verify: str = "none"
    bbb_auto_join: bool = True

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Interviews <interviews@example.com>"
    smtp_starttls: bool = True

    reminder_hours_before: int = 24
    reminder_minutes_before: int = 15
    no_show_grace_minutes: int = 10
    job_sweep_seconds: int = 60
    keep_raw_recording: bool = False
    caption_languages: str = "en"

    @property
    def bbb_enabled(self) -> bool:
        return bool(self.bbb_url and self.bbb_shared_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
