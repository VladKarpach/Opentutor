"""API schemas for video channels and per-topic video suggestions."""

import uuid

from pydantic import BaseModel, Field


class VideoChannelCreate(BaseModel):
    """A channel to draw lesson videos from.

    Accepts anything the learner can copy off YouTube: a channel URL in any of
    its forms, an ``@handle``, a bare name, or a ``UC…`` id. It is resolved and
    verified on the way in, because a guess that landed on the wrong channel
    would quietly offer videos from a source they never chose.
    """

    value: str = Field(..., min_length=1, max_length=500)
    #: Null means the channel applies to every course.
    course_id: uuid.UUID | None = None


class VideoChannelUpdate(BaseModel):
    enabled: bool | None = None
    refresh_hours: int | None = Field(default=None, ge=1, le=720)


class VideoChannelResponse(BaseModel):
    id: str
    channel_id: str
    handle: str | None = None
    title: str | None = None
    url: str | None = None
    enabled: bool
    #: True when this row applies to every course rather than one.
    is_global: bool
    course_id: str | None = None
    course_name: str | None = None
    refresh_hours: int
    last_fetched_at: str | None = None


class VideoCandidate(BaseModel):
    """A video from one of the learner's channels, offered for a topic."""

    video_id: str
    title: str
    url: str
    channel_id: str
    channel_title: str
    published_at: str | None = None
    description: str = ""
    #: The words that made this a candidate. Shown so the offer can be judged —
    #: a lesson that shares nothing with the topic is not a lesson about it.
    matched_terms: list[str] = Field(default_factory=list)
    #: Why the model chose this one, in the language of the topic. Empty when the
    #: word match decided on its own. A cross-language pick has no matched words
    #: — an English title cannot contain a Russian query — so this is the only
    #: justification there is, and it is the reason to show it.
    reason: str = ""


class VideoDiscoveryResponse(BaseModel):
    topic_id: str
    candidates: list[VideoCandidate]
    #: How the list was produced, so a thin result is not read as exhaustive.
    report: dict = Field(default_factory=dict)