"""User-owned content: theory/documentation links and task files.

Lets a learner pin their own theory sources (documentation URLs) and upload
their own task/assignment files to a course, then surface them in workspace
blocks. Everything is scoped to (user, course) — nothing here is agent-generated.
"""

import asyncio
import hashlib
import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import filetype as ft  # used for magic-byte validation

from config import settings
from database import get_db
from libs.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from models.learning_goal import LearningGoal
from models.user import User
from models.user_content import (
    UserTaskFile,
    UserTaskSubmission,
    UserTheoryLink,
)
from services.auth.dependency import get_current_user
from services.course_access import get_course_or_404
from services.user_content.task_grader import (
    generate_remedial_task,
    grade_submission,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Shared helpers ──


def _validate_course_id(course_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(course_id)
    except ValueError as e:
        raise ValidationError("Invalid course_id") from e


def _safe_filename(filename: str) -> str:
    safe = re.sub(r"[^\w.\-]", "_", os.path.basename(filename)) or "unnamed"
    return safe[:255]


async def _save_uploaded_file(file: UploadFile, subdir: str) -> tuple[str, str, str, int]:
    """Save an uploaded file to disk. Returns (save_path, file_hash, mime, size)."""
    if not file.filename:
        raise ValidationError("No filename provided")

    file_bytes = await file.read()
    if len(file_bytes) > settings.max_upload_size_mb * 1024 * 1024:
        raise ValidationError("File too large")

    detected = ft.guess(file_bytes)
    if detected:
        # Reject executable / script types outright.
        if detected.mime and any(
            bad in detected.mime for bad in ("script", "x-msdownload", "application/octet-stream")
        ):
            raise ValidationError(f"Unsupported file type: {detected.mime}")

    save_dir = os.path.join(settings.upload_dir, subdir)
    os.makedirs(save_dir, exist_ok=True)
    file_hash = hashlib.sha256(file_bytes).hexdigest()[:16]
    safe_name = _safe_filename(file.filename)
    save_path = os.path.join(save_dir, f"{file_hash}_{safe_name}")
    await asyncio.to_thread(Path(save_path).write_bytes, file_bytes)

    mime = detected.mime if detected else "application/octet-stream"
    return save_path, file_hash, mime, len(file_bytes)


# ── Theory / documentation links ──


class TheoryLinkRequest(BaseModel):
    course_id: str
    title: str
    url: str
    description: str | None = None


class TheoryLinkResponse(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    title: str
    url: str
    description: str | None
    source_type: str
    created_at: str | None


@router.post("/theory", status_code=201, summary="Pin a theory / documentation link")
async def add_theory_link(
    body: TheoryLinkRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store a documentation URL the learner wants to read alongside a course."""
    cid = _validate_course_id(body.course_id)
    await get_course_or_404(db, cid, user_id=user.id)

    if not body.title or not body.title.strip():
        raise ValidationError("Title is required")
    if not body.url or not body.url.strip():
        raise ValidationError("URL is required")

    link = UserTheoryLink(
        user_id=user.id,
        course_id=cid,
        title=body.title.strip(),
        url=body.url.strip(),
        description=(body.description or "").strip() or None,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return TheoryLinkResponse(
        id=link.id,
        course_id=link.course_id,
        title=link.title,
        url=link.url,
        description=link.description,
        source_type=link.source_type,
        created_at=link.created_at.isoformat() if link.created_at else None,
    )


@router.get("/theory", summary="List theory links for a course")
async def list_theory_links(
    course_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the learner's pinned theory links for a course, newest first."""
    await get_course_or_404(db, course_id, user_id=user.id)
    result = await db.execute(
        select(UserTheoryLink)
        .where(UserTheoryLink.course_id == course_id, UserTheoryLink.user_id == user.id)
        .order_by(UserTheoryLink.created_at.desc())
    )
    links = result.scalars().all()
    return [
        TheoryLinkResponse(
            id=l.id,
            course_id=l.course_id,
            title=l.title,
            url=l.url,
            description=l.description,
            source_type=l.source_type,
            created_at=l.created_at.isoformat() if l.created_at else None,
        )
        for l in links
    ]


@router.delete("/theory/{theory_id}", summary="Delete a theory link")
async def delete_theory_link(
    theory_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove one of the learner's pinned theory links."""
    result = await db.execute(
        select(UserTheoryLink).where(
            UserTheoryLink.id == theory_id, UserTheoryLink.user_id == user.id
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        raise NotFoundError("Theory link", theory_id)
    await db.delete(link)
    await db.commit()
    return {"ok": True}


# ── Task / assignment files ──


class TaskFileResponse(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID | None
    goal_id: uuid.UUID | None
    filename: str
    mime_type: str | None
    size_bytes: int | None
    created_at: str | None


@router.post("/tasks", status_code=201, summary="Upload a task / assignment file")
async def upload_task_file(
    file: UploadFile = File(...),
    course_id: str | None = Form(None),
    goal_id: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a task file for a course or a learning goal.

    When ``goal_id`` is given but ``course_id`` is not, the course is resolved
    from the goal so a goal without a course can still receive assignments.
    """
    cid: uuid.UUID | None = None
    gid: uuid.UUID | None = None

    if course_id:
        try:
            cid = uuid.UUID(course_id)
        except ValueError as e:
            raise ValidationError("Invalid course_id") from e
    if goal_id:
        try:
            gid = uuid.UUID(goal_id)
        except ValueError as e:
            raise ValidationError("Invalid goal_id") from e

    if cid is None and gid is None:
        raise ValidationError("course_id or goal_id is required")

    if gid is not None:
        result = await db.execute(
            select(LearningGoal).where(
                LearningGoal.id == gid, LearningGoal.user_id == user.id
            )
        )
        goal = result.scalar_one_or_none()
        if not goal:
            raise NotFoundError("Goal", gid)
        cid = goal.course_id

    if cid is not None:
        await get_course_or_404(db, cid, user_id=user.id)

    save_path, file_hash, mime, size = await _save_uploaded_file(file, "tasks")

    try:
        task_file = UserTaskFile(
            user_id=user.id,
            course_id=cid,
            goal_id=gid,
            filename=file.filename or "unnamed",
            file_path=save_path,
            mime_type=mime,
            size_bytes=size,
        )
        db.add(task_file)
        await db.commit()
        await db.refresh(task_file)
    except Exception:
        try:
            os.remove(save_path)
        except OSError:
            logger.warning("Failed to clean up uploaded task file: %s", save_path)
        raise

    return TaskFileResponse(
        id=task_file.id,
        course_id=task_file.course_id,
        goal_id=task_file.goal_id,
        filename=task_file.filename,
        mime_type=task_file.mime_type,
        size_bytes=task_file.size_bytes,
        created_at=task_file.created_at.isoformat() if task_file.created_at else None,
    )


@router.get("/tasks", summary="List task files for a course or a goal")
async def list_task_files(
    course_id: uuid.UUID | None = None,
    goal_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the learner's uploaded task files, filtered by course or goal."""
    if course_id is None and goal_id is None:
        raise ValidationError("course_id or goal_id is required")
    if course_id is not None:
        await get_course_or_404(db, course_id, user_id=user.id)

    query = select(UserTaskFile).where(UserTaskFile.user_id == user.id)
    if course_id is not None:
        query = query.where(UserTaskFile.course_id == course_id)
    if goal_id is not None:
        query = query.where(UserTaskFile.goal_id == goal_id)
    result = await db.execute(query.order_by(UserTaskFile.created_at.desc()))
    files = result.scalars().all()
    return [
        TaskFileResponse(
            id=f.id,
            course_id=f.course_id,
            goal_id=f.goal_id,
            filename=f.filename,
            mime_type=f.mime_type,
            size_bytes=f.size_bytes,
            created_at=f.created_at.isoformat() if f.created_at else None,
        )
        for f in files
    ]


class SubmitTaskAnswerRequest(BaseModel):
    user_answer: str


class TaskSubmissionResponse(BaseModel):
    id: uuid.UUID
    task_file_id: uuid.UUID
    verdict: str
    score: int
    feedback: str | None
    error_category: str | None
    submitted_at: str | None
    remedial_task: dict | None
    block_completed: bool


@router.post(
    "/tasks/{task_id}/submit",
    response_model=TaskSubmissionResponse,
    summary="Submit an answer to a task and get AI grading",
)
async def submit_task_answer(
    task_id: uuid.UUID,
    body: SubmitTaskAnswerRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit an answer to a user-uploaded task.

    The task file is extracted, the answer is graded by an LLM, and when the
    verdict is not fully correct a remedial (similar) task is generated for
    practicing the missed concept.
    """
    result = await db.execute(
        select(UserTaskFile).where(
            UserTaskFile.id == task_id, UserTaskFile.user_id == user.id
        )
    )
    task_file = result.scalar_one_or_none()
    if not task_file:
        raise NotFoundError("Task file", task_id)

    if not body.user_answer or not body.user_answer.strip():
        raise ValidationError("Answer must not be empty")

    # Extract the task text from the uploaded file so the LLM can grade against it.
    from services.ingestion.document_loader import extract_content

    task_content, _title = await extract_content(file_path=task_file.file_path)
    if not task_content.strip():
        task_content = (
            f"Task file: {task_file.filename} "
            f"(could not extract text; grade based on filename and your answer)"
        )

    grading = await grade_submission(task_content, body.user_answer.strip())

    remedial = None
    if str(grading.get("verdict", "incorrect")) != "correct":
        remedial = await generate_remedial_task(
            task_content=task_content,
            user_answer=body.user_answer.strip(),
            feedback=grading.get("feedback") or "",
            error_category=grading.get("error_category"),
        )

    submission = UserTaskSubmission(
        user_id=user.id,
        course_id=task_file.course_id,
        goal_id=task_file.goal_id,
        task_file_id=task_file.id,
        user_answer=body.user_answer.strip(),
        verdict=str(grading.get("verdict", "incorrect")),
        score=int(grading.get("score", 0)),
        feedback=grading.get("feedback"),
        error_category=grading.get("error_category"),
        remedial_task=remedial,
        block_completed=str(grading.get("verdict", "incorrect")) == "correct",
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)

    return TaskSubmissionResponse(
        id=submission.id,
        task_file_id=submission.task_file_id,
        verdict=submission.verdict,
        score=submission.score,
        feedback=submission.feedback,
        error_category=submission.error_category,
        submitted_at=submission.submitted_at.isoformat()
        if submission.submitted_at
        else None,
        remedial_task=submission.remedial_task,
        block_completed=submission.block_completed,
    )


@router.get(
    "/tasks/{task_id}/submissions",
    response_model=list[TaskSubmissionResponse],
    summary="List submissions for a task",
)
async def list_task_submissions(
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the learner's submissions for one task file, newest first."""
    result = await db.execute(
        select(UserTaskFile).where(
            UserTaskFile.id == task_id, UserTaskFile.user_id == user.id
        )
    )
    task_file = result.scalar_one_or_none()
    if not task_file:
        raise NotFoundError("Task file", task_id)

    result = await db.execute(
        select(UserTaskSubmission)
        .where(UserTaskSubmission.task_file_id == task_id)
        .order_by(UserTaskSubmission.submitted_at.desc())
    )
    submissions = result.scalars().all()
    return [
        TaskSubmissionResponse(
            id=s.id,
            task_file_id=s.task_file_id,
            verdict=s.verdict,
            score=s.score,
            feedback=s.feedback,
            error_category=s.error_category,
            submitted_at=s.submitted_at.isoformat() if s.submitted_at else None,
            remedial_task=s.remedial_task,
            block_completed=s.block_completed,
        )
        for s in submissions
    ]


@router.get("/tasks/{task_id}/file", summary="Download an uploaded task file")
async def download_task_file(
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve an uploaded task file for download/preview."""
    result = await db.execute(
        select(UserTaskFile).where(
            UserTaskFile.id == task_id, UserTaskFile.user_id == user.id
        )
    )
    task_file = result.scalar_one_or_none()
    if not task_file or not task_file.file_path:
        raise NotFoundError("Task file", task_id)

    file_path = Path(task_file.file_path).resolve()
    upload_dir = Path(settings.upload_dir).resolve()
    if not file_path.is_relative_to(upload_dir):
        raise PermissionDeniedError("Access denied")
    if not file_path.exists():
        raise NotFoundError("File on disk")

    import mimetypes

    media_type = (
        task_file.mime_type
        or mimetypes.guess_type(task_file.filename or "")[0]
        or "application/octet-stream"
    )
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=task_file.filename,
    )


@router.delete("/tasks/{task_id}", summary="Delete a task file")
async def delete_task_file(
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove one of the learner's uploaded task files (and the file on disk)."""
    result = await db.execute(
        select(UserTaskFile).where(
            UserTaskFile.id == task_id, UserTaskFile.user_id == user.id
        )
    )
    task_file = result.scalar_one_or_none()
    if not task_file:
        raise NotFoundError("Task file", task_id)

    try:
        os.remove(task_file.file_path)
    except OSError:
        logger.warning("Failed to delete task file on disk: %s", task_file.file_path)

    await db.delete(task_file)
    await db.commit()
    return {"ok": True}
