"""FastAPI application factory for the user-profile service."""
import os
import subprocess
import sys
from contextlib import asynccontextmanager
import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
import app.db as db
from app.cleanup import nightly_cleanup
from app.routers import health, visits, signals, interests, score

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

log = structlog.get_logger()
scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown lifecycle: migrations, DB pool, scheduler."""
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    # Run Alembic migrations
    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True, text=True, cwd="/app"
        )
        if result.returncode != 0:
            log.error("migration_failed", stderr=result.stderr)
            sys.exit(1)
        log.info("migrations_applied")
    except Exception as exc:
        log.error("migration_error", error=str(exc))
        sys.exit(1)

    # Initialize DB pool
    db.init_pool()

    # Start nightly cleanup scheduler
    retention = db.BEHAVIOR_RETENTION_DAYS
    scheduler.add_job(
        nightly_cleanup,
        CronTrigger(hour=3, minute=0, timezone="UTC"),
        args=[db._pool, retention],
        id="nightly_cleanup",
    )
    scheduler.start()
    log.info("scheduler_started")

    yield

    scheduler.shutdown()
    db.close_pool()
    log.info("service_shutdown")


app = FastAPI(title="user-profile", lifespan=lifespan)

app.include_router(health.router)
app.include_router(visits.router)
app.include_router(signals.router)
app.include_router(interests.router)
app.include_router(score.router)
