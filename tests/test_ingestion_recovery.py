"""Regression tests: ingestion jobs left mid-flight must reach a terminal state.

The embedding stage runs in a fire-and-forget task owned by the API process. If
that process dies, the job row stays at "embedding" / 90 % and the upload screens
poll it forever. These tests pin the recovery behaviour:

* an interrupted embedding is completed work (the content tree was already
  written) with the semantic index missing;
* a job interrupted before dispatch is failed, because nothing reached the tree;
* a live job younger than the staleness window is left alone.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
from models.ingestion import IngestionJob
from services.ingestion.recovery import INTERRUPTED_ERROR, STALE_AFTER, reconcile_ingestion_jobs


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _add_job(factory, **overrides) -> IngestionJob:
    defaults = dict(
        user_id=uuid.uuid4(),
        source_type="file",
        original_filename="Генераторы.pdf",
        status="embedding",
        progress_percent=90,
        phase_label="Building semantic index",
        embedding_status="pending",
        nodes_created=5,
        dispatched=True,
        updated_at=_now_naive(),
    )
    defaults.update(overrides)
    async with factory() as db:
        job = IngestionJob(**defaults)
        db.add(job)
        await db.commit()
        return job


@pytest.mark.asyncio
async def test_interrupted_embedding_is_completed_work(session_factory, monkeypatch):
    monkeypatch.setattr(
        "services.embedding.registry.is_noop_provider", lambda: True
    )
    job = await _add_job(session_factory)

    async with session_factory() as db:
        summary = await reconcile_ingestion_jobs(db)

    async with session_factory() as db:
        refreshed = await db.get(IngestionJob, job.id)

    assert summary == {"completed": 1, "failed": 0, "reembed_course_ids": []}
    assert refreshed.status == "completed"
    assert refreshed.progress_percent == 100
    assert refreshed.embedding_status == "skipped"


@pytest.mark.asyncio
async def test_pre_dispatch_interruption_is_failed(session_factory, monkeypatch):
    monkeypatch.setattr(
        "services.embedding.registry.is_noop_provider", lambda: True
    )
    job = await _add_job(session_factory, status="extracting", dispatched=False, progress_percent=20)

    async with session_factory() as db:
        summary = await reconcile_ingestion_jobs(db)

    async with session_factory() as db:
        refreshed = await db.get(IngestionJob, job.id)

    assert summary["failed"] == 1
    assert refreshed.status == "failed"
    assert refreshed.error_message == INTERRUPTED_ERROR


@pytest.mark.asyncio
async def test_live_job_inside_staleness_window_is_left_alone(session_factory):
    job = await _add_job(session_factory, updated_at=_now_naive())

    async with session_factory() as db:
        summary = await reconcile_ingestion_jobs(db, older_than=STALE_AFTER)

    async with session_factory() as db:
        refreshed = await db.get(IngestionJob, job.id)

    assert summary == {"completed": 0, "failed": 0, "reembed_course_ids": []}
    assert refreshed.status == "embedding"
    assert refreshed.embedding_status == "pending"


@pytest.mark.asyncio
async def test_stale_job_is_reconciled_on_the_lazy_path(session_factory, monkeypatch):
    monkeypatch.setattr(
        "services.embedding.registry.is_noop_provider", lambda: True
    )
    stale_at = _now_naive() - STALE_AFTER - timedelta(minutes=5)
    job = await _add_job(session_factory, updated_at=stale_at)

    async with session_factory() as db:
        summary = await reconcile_ingestion_jobs(db, older_than=STALE_AFTER)

    async with session_factory() as db:
        refreshed = await db.get(IngestionJob, job.id)

    assert summary["completed"] == 1
    assert refreshed.status == "completed"


@pytest.mark.asyncio
async def test_background_embed_failure_still_completes_the_upload(session_factory, monkeypatch):
    """A dead embedding provider must not turn a finished upload into a failure."""
    import routers.upload_processing as upload_processing

    job = await _add_job(session_factory)
    monkeypatch.setattr(upload_processing, "async_session", session_factory)
    monkeypatch.setattr(
        "services.embedding.registry.is_noop_provider", lambda: False
    )

    async def _boom(db, course_id):
        raise RuntimeError("embedding endpoint unreachable")

    monkeypatch.setattr("services.embedding.content.embed_course_content", _boom)

    await upload_processing._background_embed(uuid.uuid4(), job.id, user_id=None)

    async with session_factory() as db:
        refreshed = await db.get(IngestionJob, job.id)

    # The content tree was written before embedding; the upload is usable.
    assert refreshed.status == "completed"
    assert refreshed.progress_percent == 100
    assert refreshed.embedding_status == "failed"


@pytest.mark.asyncio
async def test_embedding_provider_present_requests_reembedding(session_factory, monkeypatch):
    monkeypatch.setattr(
        "services.embedding.registry.is_noop_provider", lambda: False
    )
    course_id = uuid.uuid4()
    await _add_job(session_factory, course_id=course_id)

    async with session_factory() as db:
        summary = await reconcile_ingestion_jobs(db)

    assert summary["reembed_course_ids"] == [course_id]
