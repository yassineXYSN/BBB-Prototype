import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, health, interviews, public, templates, webhooks
from app.config import get_settings
from app.db import init_db
from app.services.jobs import job_loop, startup_hook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    settings = get_settings()
    task = None
    if settings.app_env != "test":
        try:
            await startup_hook()
        except Exception:  # noqa: BLE001
            logging.getLogger("app").exception("startup hook failed")
        task = asyncio.create_task(job_loop())
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Interview Platform (BigBlueButton)",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_app_url, settings.api_base_url, "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    prefix = "/api/v1"
    app.include_router(health.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(interviews.router, prefix=prefix)
    app.include_router(templates.router, prefix=prefix)
    app.include_router(public.router, prefix=prefix)
    app.include_router(webhooks.router, prefix=prefix)
    return app


app = create_app()
