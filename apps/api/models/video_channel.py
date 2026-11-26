"""YouTube channels the learner wants lesson videos drawn from.

The product never invents a video link. Every link that reaches a topic came
from YouTube itself: either from a channel's public feed or from the official
Data API. That is why the channel list is stored rather than searched for on the
fly — the learner chooses the sources once, and the system's job is only to pick
out of them.

Scope is in one nullable column rather than two tables: ``course_id is None``
means the channel applies everywhere. A course row plus a global row is the same
row with a different scope, and one query covers both.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.compat import CompatUUID

# Order matters for the composite index below: it is read as "channels of this
# user, optionally of this course, enabled first".
_IDX = Index(
    "ix_video_channel_user_scope",
    "user_id",
    "course_id",
    "enabled",
)


class VideoChannel(Base):
    """One YouTube channel, global or attached to a single course."""

    __tablename__ = "video_channels"
    __table_args__ = (_IDX,)

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # NULL means global. SET NULL rather than CASCADE so deleting a course drops
    # only that course's channels and leaves the shared list intact.
    course_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )

    #: The authoritative identity. Resolved at insert time from whatever the
    #: learner pasted, so a renamed handle cannot silently redirect the source.
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    handle: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    #: Resolved from the page at insert time, or supplied directly in the seed.
    #: Purely for display — nothing matches on it.
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # How often the feed is re-read, in hours. The feed is cheap but not free,
    #: and a stale list is only a missed video rather than a wrong one.
    refresh_hours: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )