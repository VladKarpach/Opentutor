"""Resolve ingestion jobs that a previous process left mid-flight.

The pipeline hands its last stage — embedding — to a background asyncio task
created inside the upload request. That task lives only in the process that
created it: a restart, a crash, or a shutdown past the 5-second grace period
kills it, and the job row keeps saying «embedding» at 90 %. The upload screens
poll those rows, so the learner is shown "Processing 1 file... (90%)" forever
for work that is, in fact, already sitting in the content tree.

This module closes that hole. Content is dispatched to the content tree before
the embedding stage starts, so an interrupted embedding is not a failed upload:
the material is usable and only the (optional) semantic index is missing. Jobs
interrupted before dispatch are failed instead — nothing reached the tree.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from models.ingestion import IngestionJob
from services.ingestion.pipeline import _set_job_phase

logger = logging.getLogger(__name__)

# Statuses the pipeline always moves out of. A row still in one of them while
# no process owns it is an orphan.
NON_TERMINAL_STATUSES: tuple[str, ...] = (
    "pending",
    "uploaded",
    "extracting",
    "classifying",
    "dispatching",
    "embedding",
)

INTERRUPTED_ERROR = (
    "Processing was interrupted before it finished (the server restarted). "
    "Upload the source again."
)

# Only used by the lazy path (job listing): a job younger than this may still be
# owned by a live background task, so it is left alone. Startup reconciliation
# does not need the threshold — nothing else can be running at that point.
STALE_AFTER = timedelta(minutes=30)


async def reconcile_ingestion_jobs(
    db: AsyncSession,
    *,
    older_than: timedelta | None = None,
    course_id: uuid.UUID | None = None,
) -> dict:
    """Move orphaned ingestion jobs to a terminal state.

    Returns ``{"completed": n, "failed": n, "reembed_course_ids": [...]}``.
    ``reembed_course_ids`` names courses whose content is live but whose
    embeddings were never computed; the caller may schedule them.
    """
    query = select(IngestionJob).where(
        IngestionJob.status.in_(NON_TERMINAL_STATUSES)
    )
    if older_than is not None:
        # SQLite stores naive UTC strings; bind a naive UTC value so the string
        # comparison matches what server-side ``now()`` wrote.
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - older_than
        query = query.where(IngestionJob.updated_at < cutoff)
    if course_id is not None:
        query = query.where(IngestionJob.course_id == course_id)

    rows = await db.execute(query)
    orphans = rows.scalars().all()
    if not orphans:
        return {"completed": 0, "failed": 0, "reembed_course_ids": []}

    from services.embedding.registry import is_noop_provider

    noop = is_noop_provider()
    completed = 0
    failed = 0
    needs_embedding: set[uuid.UUID] = set()

    for job in orphans:
        if job.status == "embedding" and job.dispatched:
            # The content tree was written before this stage; only the index is
            # missing, so the upload itself is done.
            _set_job_phase(
                job,
                status="completed",
                progress_percent=100,
                embedding_status="skipped" if noop else "failed",
            )
            completed += 1
            if not noop and job.course_id:
                needs_embedding.add(job.course_id)
        else:
            _set_job_phase(
                job,
                status="failed",
                progress_percent=job.progress_percent or 0,
                embedding_status="failed",
                error_message=INTERRUPTED_ERROR,
            )
            failed += 1

    await db.commit()
    logger.warning(
        "Reconciled %d interrupted ingestion job(s): %d completed, %d failed",
        len(orphans),
        completed,
        failed,
    )
    return {
        "completed": completed,
        "failed": failed,
        "reembed_course_ids": sorted(needs_embedding, key=str),
    }


async def _reembed_courses(course_ids: list[uuid.UUID]) -> None:
    """Recompute embeddings for courses whose ingestion was interrupted."""
    from database import async_session
    from services.embedding.content import embed_course_content

    for course_id in course_ids:
        try:
            async with async_session() as db:
                await embed_course_content(db, course_id)
                await db.execute(
                    update(IngestionJob)
                    .where(
                        IngestionJob.course_id == course_id,
                        IngestionJob.status == "completed",
                        IngestionJob.embedding_status == "failed",
                    )
                    .values(embedding_status="completed")
                )
                await db.commit()
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            logger.warning(
                "Re-embedding course %s after restart failed: %s", course_id, exc
            )


def schedule_reembedding(course_ids: list[uuid.UUID]) -> None:
    """Fire-and-forget re-embedding for reconciled courses."""
    if not course_ids:
        return
    from services.agent.background_runtime import track_background_task

    track_background_task(asyncio.create_task(_reembed_courses(course_ids)))
