"""Video discovery for YouTube channels.

The learner names the channels; this decides which of their videos suit a given
topic. Nothing here asks a model for a URL — a model produces a well-formed
address for a video that does not exist, and the learner finds that out by
clicking something which looked trustworthy. Every link returned is one YouTube
itself reported.
"""

from .channels import (
    ChannelNotFound,
    ResolvedChannel,
    VideoHit,
    api_available,
    extract_channel_id,
    fetch_feed,
    resolve_channel,
    search_channel,
)

__all__ = [
    "ChannelNotFound",
    "ResolvedChannel",
    "VideoHit",
    "api_available",
    "extract_channel_id",
    "fetch_feed",
    "resolve_channel",
    "search_channel",
]