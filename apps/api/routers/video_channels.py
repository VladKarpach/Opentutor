"""Video channels and the videos drawn from them.

The learner names the sources; the system only chooses out of them. Every link
that reaches a topic was reported by YouTube itself — from a channel's public
feed or from the Data API — because a model asked for a video URL returns a
well-formed address for a video that does not exist.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from libs.exceptions import NotFoundError, ValidationError
from models.course import Course
from models.learning_goal import LearningGoal, LearningTopic, TopicResource
from models.user import User
from models.video_channel import VideoChannel
from schemas.video import (
    VideoCandidate,
    VideoChannelCreate,
    VideoChannelResponse,
    VideoChannelUpdate,
    VideoDiscoveryResponse,
)
from services.auth.dependency import get_current_user
from services.video import channels as yt
from services.video import discovery

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_response(row: VideoChannel, course_name: str | None = None) -> VideoChannelResponse:
    return VideoChannelResponse(
        id=str(row.id),
        channel_id=row.channel_id,
        handle=row.handle,
        title=row.title,
        url=row.url,
        enabled=row.enabled,
        is_global=row.course_id is None,
        course_id=str(row.course_id) if row.course_id else None,
        course_name=course_name,
        refresh_hours=row.refresh_hours,
        last_fetched_at=row.last_fetched_at.isoformat() if row.last_fetched_at else None,
    )


@router.get(
    "/video/status",
    summary="What video search can currently do",
    description="Whether a Data API key is configured, and what that means for how deep the search goes.",
)
async def video_status(user: User = Depends(get_current_user)):
    """Saying which mode is in use, rather than let a thin result look thorough."""
    available = yt.api_available()
    return {
        "api_search": available,
        # Without a key only the newest 15 videos per channel are reachable, so
        # the UI has to explain a shorter list instead of implying it is all.
        "max_videos_per_channel": None if available else 15,
        "hint_ru": (
            "Поиск по архиву каналов включён."
            if available
            else "Ключ YouTube не задан: доступны только 15 последних видео с каждого канала. "
            "Добавьте YOUTUBE_API_KEY, чтобы искать по всему архиву."
        ),
    }


@router.get(
    "/video/channels",
    response_model=list[VideoChannelResponse],
    summary="Channels lesson videos may come from",
)
async def list_video_channels(
    course_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The shared channels plus, when a course is named, that course's own."""
    result = await db.execute(
        select(VideoChannel).where(VideoChannel.user_id == user.id)
    )
    rows = list(result.scalars().all())

    names: dict[uuid.UUID, str] = {}
    if any(row.course_id for row in rows):
        courses = await db.execute(
            select(Course.id, Course.name).where(
                Course.id.in_([row.course_id for row in rows if row.course_id])
            )
        )
        names = {row[0]: row[1] for row in courses.all()}

    wanted = [
        row
        for row in rows
        if row.course_id is None or (course_id is not None and row.course_id == course_id)
    ]
    wanted.sort(key=lambda row: (row.course_id is not None, row.created_at))
    return [
        _to_response(row, names.get(row.course_id) if row.course_id else None)
        for row in wanted
    ]


@router.post(
    "/video/channels",
    response_model=VideoChannelResponse,
    status_code=201,
    summary="Add a channel",
)
async def add_video_channel(
    body: VideoChannelCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resolve and store a channel.

    The pasted value is resolved against YouTube before it is stored, so every
    row holds a verified channel id rather than a guess.
    """
    if body.course_id is not None:
        owned = await db.execute(
            select(Course.id).where(
                Course.id == body.course_id, Course.user_id == user.id
            )
        )
        if owned.scalar_one_or_none() is None:
            raise NotFoundError("Course", body.course_id)

    try:
        resolved = yt.resolve_channel(body.value)
    except yt.ChannelNotFound as exc:
        raise ValidationError(str(exc)) from exc

    existing = await db.execute(
        select(VideoChannel).where(
            VideoChannel.user_id == user.id,
            VideoChannel.channel_id == resolved.channel_id,
        )
    )
    duplicate = existing.scalars().first()
    if duplicate is not None:
        if duplicate.course_id == body.course_id:
            raise ValidationError("Этот канал уже добавлен")
        # Same channel, different scope. Widening a global channel to a course
        # would duplicate the entry for no gain, so the wider one wins.
        raise ValidationError(
            "Этот канал уже добавлен глобально — он и так доступен этому курсу"
        )

    row = VideoChannel(
        user_id=user.id,
        course_id=body.course_id,
        channel_id=resolved.channel_id,
        handle=resolved.handle,
        title=resolved.title,
        url=resolved.url,
    )
    db.add(row)
    # No refresh after commit. The session is built with expire_on_commit=False,
    # so every attribute is already populated from the INSERT and a refresh would
    # only add a second round-trip. It is also not free of failure: it raises
    # "Could not refresh instance" intermittently, which turned a channel the
    # learner had added into a 503 and a rolled-back insert.
    await db.commit()

    logger.info(
        "User %s added channel %s (%s)", user.id, resolved.title, resolved.channel_id
    )
    return _to_response(row)


@router.patch("/video/channels/{channel_row_id}", response_model=VideoChannelResponse)
async def update_video_channel(
    channel_row_id: uuid.UUID,
    body: VideoChannelUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VideoChannel).where(
            VideoChannel.id == channel_row_id, VideoChannel.user_id == user.id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("VideoChannel", channel_row_id)

    if body.enabled is not None:
        row.enabled = body.enabled
    if body.refresh_hours is not None:
        row.refresh_hours = body.refresh_hours
    await db.commit()
    # Attributes set here are already current — see the note in add_video_channel
    # about why nothing is refreshed after a commit.
    return _to_response(row)


@router.delete("/video/channels/{channel_row_id}", status_code=204)
async def delete_video_channel(
    channel_row_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VideoChannel).where(
            VideoChannel.id == channel_row_id, VideoChannel.user_id == user.id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("VideoChannel", channel_row_id)
    await db.delete(row)
    await db.commit()
    return None


@router.post(
    "/video/channels/{channel_row_id}/refresh",
    response_model=VideoChannelResponse,
    summary="Re-read the channel now",
)
async def refresh_video_channel(
    channel_row_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Touch the feed so a newly added channel is known to be readable.

    Runs on add rather than lazily later: a channel that cannot be read should
    say so while the learner is still looking at it.
    """
    result = await db.execute(
        select(VideoChannel).where(
            VideoChannel.id == channel_row_id, VideoChannel.user_id == user.id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("VideoChannel", channel_row_id)

    try:
        videos = yt.fetch_feed(row.channel_id)
    except yt.ChannelNotFound as exc:
        raise ValidationError(f"Канал не отвечает: {exc}") from exc

    row.last_fetched_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("Channel %s: %d videos in feed", row.channel_id, len(videos))
    return _to_response(row)


@router.get(
    "/learning/topics/{topic_id}/video-candidates",
    response_model=VideoDiscoveryResponse,
    summary="Videos from your channels that suit this topic",
    description="Searches the learner's channels for a lesson on this topic. Links come from YouTube, never from a model.",
)
async def topic_video_candidates(
    topic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Offers videos. It does not attach them.

    Adding one stays a separate, explicit act, the same rule the literature
    grounding follows: the system proposes, the learner decides.
    """
    from libs.exceptions import NotFoundError as _NotFound

    result = await db.execute(
        select(LearningTopic)
        .join(LearningGoal, LearningGoal.id == LearningTopic.goal_id)
        .where(LearningTopic.id == topic_id, LearningGoal.user_id == user.id)
    )
    topic = result.scalar_one_or_none()
    if topic is None:
        raise _NotFound("Topic", topic_id)

    goal = await db.get(LearningGoal, topic.goal_id)
    course_id = goal.course_id if goal else None

    hits, report = await discovery.find_videos_for_topic(
        db,
        user.id,
        topic.title,
        topic.description,
        course_id=course_id,
    )

    existing = await db.execute(
        select(TopicResource.url).where(TopicResource.topic_id == topic_id)
    )
    already = {row[0] for row in existing.all()}

    candidates = [
        VideoCandidate(**hit.to_dict())
        for hit in hits
        if hit.url not in already
    ]
    report["already_added"] = len(hits) - len(candidates)

    return VideoDiscoveryResponse(
        topic_id=str(topic_id),
        candidates=candidates,
        report=report,
    )


@router.post(
    "/video/resolve",
    summary="Check what a pasted value resolves to",
    description="Resolve without storing, so a learner can see a channel's real name before adding it.",
)
async def resolve_video_channel_preview(
    body: VideoChannelCreate,
    user: User = Depends(get_current_user),
):
    """Preview a channel by name.

    Bare names are the ambiguous case: ``@veritasium`` and ``/c/Veritasium``
    resolve to the same channel, but a name that also exists as a user page may
    not. Showing the resolved title first means a wrong guess costs a click
    rather than a channel full of the wrong videos.
    """
    try:
        resolved = yt.resolve_channel(body.value)
    except yt.ChannelNotFound as exc:
        raise ValidationError(str(exc)) from exc
    return {
        "channel_id": resolved.channel_id,
        "handle": resolved.handle,
        "title": resolved.title,
        "url": resolved.url,
    }