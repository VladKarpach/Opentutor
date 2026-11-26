"""Application startup and shutdown hooks."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx

from config import settings
from database import Base, engine

logger = logging.getLogger(__name__)


async def _maybe_create_tables() -> None:
    if not settings.app_auto_create_tables:
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(add_missing_columns)
    logger.info("Ensured database tables exist via Base.metadata.create_all()")


# Columns added to an already-created table, as (table, column, DDL type).
#
# ``create_all()`` creates missing *tables* but never adds columns to a table that
# already exists, and the SQLite bootstrap path deliberately skips Alembic (see
# alembic/env.py). So a model that grows a column is invisible to existing local
# installs: every query then fails with "no such column". This list closes that
# gap. It only ever adds a column that the model declares but the database lacks,
# so it is a no-op on a fresh install and on PostgreSQL where Alembic ran.
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # Variant A of the course/goal integration: LearningGoal.course_id.
    ("learning_goals", "course_id", "VARCHAR(36)"),
    # The goal "Сегодня" opens. A column, not a new table, because there is one
    # profile row per user and the choice belongs to the learner.
    ("user_profiles", "active_goal_id", "VARCHAR(36)"),
    # Topic difficulty weight (easy | medium | hard). Existing plans get
    # "medium" so their estimates do not jump. NOT NULL + default is required:
    # the model declares a non-optional str, and ALTER TABLE backfills it.
    ("learning_topics", "difficulty", "VARCHAR(20) NOT NULL DEFAULT 'medium'"),
    # FSRS difficulty aggregated onto the topic state, fed by the adaptive
    # planner. Nullable by design — NULL means "no FSRS data yet".
    ("topic_states", "fsrs_difficulty", "FLOAT"),
)


def add_missing_columns(connection) -> None:
    """Add declared-but-absent columns. Additive only; never drops anything.

    Guarded by table and column existence checks, so repeated startups are free.
    A failure here must not stop the app from booting — it logs and continues,
    because a missing optional column is better than a dead service.
    """
    import sqlalchemy as sa

    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())

    for table, column, ddl_type in _ADDITIVE_COLUMNS:
        if table not in tables:
            continue  # create_all will make it with the column
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            continue
        try:
            connection.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            logger.info("Added missing column %s.%s", table, column)
        except Exception as exc:  # noqa: BLE001 - never block startup
            logger.warning("Could not add %s.%s: %s", table, column, exc)
            continue

    _ensure_indexes(connection)


# Indexes for the columns above, created only after the column exists.
_ADDITIVE_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_learning_goal_course", "CREATE INDEX IF NOT EXISTS ix_learning_goal_course ON learning_goals (course_id)"),
)


def _ensure_indexes(connection) -> None:
    import sqlalchemy as sa

    tables = set(sa.inspect(connection).get_table_names())
    for index_name, ddl in _ADDITIVE_INDEXES:
        table = ddl.split(" ON ")[1].split(" ")[0]
        if table not in tables:
            continue
        try:
            connection.execute(sa.text(ddl))
        except Exception as exc:  # noqa: BLE001 - an index is an optimisation only
            logger.warning("Could not create index %s: %s", index_name, exc)


async def _maybe_bootstrap_migration_tracking() -> None:
    # Local SQLite flow uses create_all() for bootstrap and does not require
    # migration stamping during startup.
    return None


async def _maybe_reconcile_ingestion_jobs() -> None:
    """Resolve jobs a previous process left mid-flight.

    The embedding stage runs in a fire-and-forget task, so a restart strands the
    job row at 90% and the upload screens poll it forever. Content is dispatched
    before that stage, so an interrupted embedding is completed work with a
    missing index, not a failed upload.
    """
    from database import async_session
    from services.ingestion.recovery import (
        reconcile_ingestion_jobs,
        schedule_reembedding,
    )

    try:
        async with async_session() as db:
            summary = await reconcile_ingestion_jobs(db)
        schedule_reembedding(summary["reembed_course_ids"])
    except Exception as exc:  # noqa: BLE001 - never block startup; logged below
        logger.warning("Ingestion job reconciliation skipped: %s", exc)


async def _maybe_seed_system_data() -> None:
    if not settings.app_auto_seed_system:
        return

    from database import async_session
    from services.templates.system import seed_builtin_templates
    from services.templates.demo_course import seed_demo_course

    async with async_session() as db:
        await seed_builtin_templates(db)
        created = await seed_demo_course(db)
        await db.commit()
    logger.info("Seeded built-in templates")
    if created:
        logger.info("Created demo course for first-time experience")


def _maybe_start_scheduler() -> None:
    if not settings.app_run_scheduler:
        return

    from services.scheduler.engine import start_scheduler

    start_scheduler()
    logger.info("Scheduler started")


def _maybe_stop_scheduler() -> None:
    if not settings.app_run_scheduler:
        return

    from services.scheduler.engine import stop_scheduler

    stop_scheduler()
    logger.info("Scheduler stopped")


def _should_run_activity_engine() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return settings.app_run_activity_engine


def _maybe_start_activity_engine() -> None:
    if not _should_run_activity_engine():
        return

    from services.activity.engine import start_activity_engine

    start_activity_engine()
    logger.info("Activity engine started")


async def _maybe_stop_activity_engine() -> None:
    if not _should_run_activity_engine():
        return

    from services.activity.engine import stop_activity_engine

    await stop_activity_engine()
    logger.info("Activity engine stopped")




async def _detect_local_llm() -> None:
    """Detect local LLM providers (Ollama, LM Studio) on startup.

    If a provider is found and no explicit LLM is configured,
    auto-configure it so the user doesn't need to edit .env.
    Inspired by OpenClaw's auto-detection and AnythingLLM's provider discovery.
    """
    import asyncio

    detected: list[dict] = []

    async def _probe_ollama() -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{settings.ollama_base_url}/api/tags")
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    return {"provider": "ollama", "url": settings.ollama_base_url, "models": [m.get("name", "?") for m in models]}
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.debug("Ollama probe failed: %s", exc)
        return None

    async def _probe_lmstudio() -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{settings.lmstudio_base_url}/models")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["id"] for m in data.get("data", [])]
                    return {"provider": "lmstudio", "url": settings.lmstudio_base_url, "models": models}
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.debug("LM Studio probe failed: %s", exc)
        return None

    results = await asyncio.gather(_probe_ollama(), _probe_lmstudio(), return_exceptions=True)
    for r in results:
        if isinstance(r, dict) and r is not None:
            detected.append(r)

    if not detected:
        if settings.llm_provider.lower() in ("ollama", "lmstudio"):
            logger.warning(
                "No local LLM detected. Install Ollama (https://ollama.com) "
                "or set LLM_PROVIDER / API keys for a cloud provider."
            )
        return

    for p in detected:
        model_count = len(p["models"])
        names = ", ".join(p["models"][:5])
        if model_count > 0:
            logger.info(
                "%s detected at %s with %d model(s): %s",
                p["provider"].upper(), p["url"], model_count, names,
            )
        else:
            logger.warning(
                "%s is running at %s but has no models loaded.",
                p["provider"].upper(), p["url"],
            )


def _start_health_monitor() -> None:
    """Start background LLM health probe loop (OpenClaw pattern)."""
    from services.llm.router import get_registry

    registry = get_registry()
    registry.start_health_monitor(interval=30.0)


def _should_run_health_monitor() -> bool:
    """Disable health monitor in pytest to avoid cross-loop teardown races."""
    return not os.environ.get("PYTEST_CURRENT_TEST")


async def _stop_health_monitor() -> None:
    from services.llm.router import get_registry

    registry = get_registry()
    try:
        await registry.stop_health_monitor()
    except RuntimeError as exc:
        if "Event loop is closed" in str(exc):
            logger.debug("Skipping health monitor shutdown after loop closed")
            return
        raise


def _print_auth_warning() -> None:
    """Print a visible console warning when authentication is disabled."""
    if settings.auth_enabled:
        return
    logger.warning(
        "\n"
        "╔══════════════════════════════════════════════════════════════╗\n"
        "║  ⚠  AUTHENTICATION IS DISABLED (AUTH_ENABLED=false)        ║\n"
        "║                                                            ║\n"
        "║  This is fine for local single-user use.                   ║\n"
        "║  DO NOT expose this instance to the public internet.       ║\n"
        "║                                                            ║\n"
        "║  To enable auth:                                           ║\n"
        "║    1. Set AUTH_ENABLED=true in .env                        ║\n"
        "║    2. Set JWT_SECRET_KEY to a random string (>=32 chars)   ║\n"
        "╚══════════════════════════════════════════════════════════════╝"
    )


async def run_startup_hooks() -> None:
    _print_auth_warning()
    await _maybe_create_tables()
    await _maybe_reconcile_ingestion_jobs()
    await _maybe_bootstrap_migration_tracking()
    await _maybe_seed_system_data()
    if _should_run_health_monitor():
        await _detect_local_llm()
    _maybe_start_scheduler()
    _maybe_start_activity_engine()
    if _should_run_health_monitor():
        _start_health_monitor()


async def run_shutdown_hooks() -> None:
    from services.agent.background_runtime import wait_for_background_tasks

    if _should_run_health_monitor():
        await _stop_health_monitor()
    await _maybe_stop_activity_engine()
    _maybe_stop_scheduler()
    await wait_for_background_tasks()
    await engine.dispose()


@asynccontextmanager
async def lifespan(_: object):
    os.makedirs(settings.upload_dir, exist_ok=True)
    await run_startup_hooks()
    yield
    await run_shutdown_hooks()
