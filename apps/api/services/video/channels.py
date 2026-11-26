"""Resolving and reading YouTube channels.

Two sources of videos, deliberately layered:

* **The public feed**, ``/feeds/videos.xml?channel_id=...``. No key, no quota, and
  it answers with the channel's newest 15 videos — real ids, titles, links and
  dates. That is enough to offer something sensible and it is what makes the
  feature work before anyone goes to Google Cloud.
* **The Data API**, when ``YOUTUBE_API_KEY`` is set. Costs quota but can search a
  channel's whole back catalogue, which is the difference between "15 videos from
  this channel" and "the video about exactly this topic, from three years ago".

Both are first-party. Nothing here fabricates a URL: every link handed to the
rest of the system was read out of a feed or an API response.

Resolving what a learner pasted is the fiddly part. YouTube's channel page
carries several ``UC…`` ids, and only one of them is the channel the URL points
at — the others belong to related and recommended channels. Taking the first one
found silently resolves to the wrong channel, so the canonical markers are used
instead; they were verified to agree with each other and to be stable across the
``/@handle``, ``/c/Name`` and ``/user/name`` forms of the same channel.
"""

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from config import settings

logger = logging.getLogger(__name__)

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
API_SEARCH = "https://www.googleapis.com/youtube/v3/search"
API_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"

# A real YouTube channel id: "UC" then 22 URL-safe characters.
CHANNEL_ID_RE = re.compile(r"^UC[\w-]{22}$")

# The markers that name the channel the URL actually points at. Verified to
# agree; the bare "channelId":"UC…" that appears first in the HTML does not.
# The last pattern covers a plain URL a learner pasted, which has no quotes
# around the id.
_CANONICAL_PATTERNS = (
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
    r'<meta itemprop="identifier" content="(UC[\w-]{22})"',
    r'<meta property="og:url" content="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
    r'youtube\.com/channel/(UC[\w-]{22})(?:["\'/?#]|$)',
)

_ATOM = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# Some CDNs return HTML to a non-browser agent. Naming a browser is the
# difference between a feed and an error page.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_TIMEOUT = 20

# How much of a description is kept. Long enough to tell a lesson apart from a
# short, short enough that a channel posting walls of text does not dominate.
_MAX_SUMMARY = 400


class ChannelNotFound(Exception):
    """The pasted value could not be resolved to a channel."""


@dataclass
class VideoHit:
    """One video found in a channel."""

    video_id: str
    title: str
    url: str
    channel_id: str
    channel_title: str = ""
    published_at: str | None = None
    description: str = ""
    matched_terms: list[str] = field(default_factory=list)
    #: True when this came out of a YouTube search rather than a feed listing.
    #: It matters: a search result is already something YouTube judged relevant,
    #: and it can be relevant across languages — an English-titled video found
    #: for a Russian query shares no characters with the topic, so any local
    #: word-match test would throw away exactly the hits that were found by
    #: asking in another language.
    from_search: bool = False
    #: Why this video was offered, in the learner's language. Set when the model
    #: chose it out of the shortlist; empty when the word match decided alone.
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
            "channel_id": self.channel_id,
            "channel_title": self.channel_title,
            "published_at": self.published_at,
            "description": self.description,
            "matched_terms": self.matched_terms,
            "reason": self.reason,
        }


@dataclass
class ResolvedChannel:
    channel_id: str
    handle: str | None = None
    title: str | None = None
    url: str | None = None


def _fetch(url: str, *, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "en,ru"}
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read()


def extract_channel_id(value: str) -> str | None:
    """Pull a ``UC…`` id straight out of whatever was pasted."""
    if not value:
        return None
    candidate = value.strip()
    if CHANNEL_ID_RE.match(candidate):
        return candidate
    for pattern in _CANONICAL_PATTERNS:
        found = re.search(pattern, candidate)
        if found:
            return found.group(1)
    return None


def _normalise_pasted(value: str) -> str:
    """Turn a pasted handle, name or URL into a channel page URL."""
    text = value.strip()
    if not text:
        raise ChannelNotFound("Пустое значение")
    if CHANNEL_ID_RE.match(text):
        return f"https://www.youtube.com/channel/{text}"
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("@"):
        return f"https://www.youtube.com/{text}"
    # A bare word: assume it is a handle, which is how YouTube itself reads it.
    return f"https://www.youtube.com/@{text}"


def resolve_channel(value: str) -> ResolvedChannel:
    """Turn what the learner pasted into a verified channel.

    Accepts a channel URL in any of its forms, an ``@handle``, a bare name, or a
    ``UC…`` id. The id is always confirmed against the page rather than assumed,
    because a guess that lands on the wrong channel would quietly offer videos
    from somewhere the learner never chose.
    """
    text = value.strip()

    # An id is already the answer, but the title is still worth one request so
    # the channel list is readable rather than a column of ids.
    if CHANNEL_ID_RE.match(text):
        try:
            html = _fetch(f"https://www.youtube.com/channel/{text}").decode(
                "utf-8", "replace"
            )
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("Channel %s: page unreadable (%s)", text, exc)
            return ResolvedChannel(
                channel_id=text,
                url=f"https://www.youtube.com/channel/{text}",
            )
        return ResolvedChannel(
            channel_id=text,
            handle=_meta(html, 'property="og:title" content="') or None,
            title=_title_from(html) or text,
            url=f"https://www.youtube.com/channel/{text}",
        )

    url = _normalise_pasted(text)
    try:
        html = _fetch(url).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise ChannelNotFound(f"YouTube ответил {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ChannelNotFound(f"Не удалось обратиться к YouTube: {exc}") from exc

    channel_id = extract_channel_id(html)
    if not channel_id:
        raise ChannelNotFound(
            "На этой странице нет канала — проверьте ссылку или @имя"
        )

    parsed = urllib.parse.urlparse(url)
    handle = None
    if parsed.path.startswith("/@"):
        handle = parsed.path[1:]

    return ResolvedChannel(
        channel_id=channel_id,
        handle=handle,
        title=_title_from(html),
        url=f"https://www.youtube.com/channel/{channel_id}",
    )


def _title_from(html: str) -> str | None:
    for pattern in (
        r'<meta property="og:title" content="([^"]*)"',
        r'<title>([^<]*?) - YouTube</title>',
    ):
        found = re.search(pattern, html)
        if found and found.group(1).strip():
            return found.group(1).strip()[:200]
    return None


def _meta(html: str, prefix: str) -> str | None:
    found = re.search(re.escape(prefix) + r'([^"]*)"', html)
    return found.group(1).strip()[:80] if found else None


def fetch_feed(channel_id: str) -> list[VideoHit]:
    """The channel's newest 15 videos. No key, no quota.

    Raises :class:`ChannelNotFound` when the feed cannot be read, so a broken
    source is reported rather than silently contributing nothing.
    """
    url = FEED_URL.format(channel_id=channel_id)
    try:
        raw = _fetch(url, accept="application/rss+xml, application/xml, */*")
    except urllib.error.HTTPError as exc:
        raise ChannelNotFound(f"Лента канала недоступна ({exc.code})") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ChannelNotFound(f"Лента канала недоступна: {exc}") from exc

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ChannelNotFound("Лента пришла не в виде XML") from exc

    channel_title = (root.findtext("atom:title", namespaces=_ATOM) or "").strip()
    hits: list[VideoHit] = []
    for entry in root.findall("atom:entry", _ATOM):
        video_id = entry.findtext("yt:videoId", namespaces=_ATOM)
        if not video_id:
            continue
        link = None
        for candidate in entry.findall("atom:link", _ATOM):
            if candidate.get("rel") == "alternate":
                link = candidate.get("href")
                break
        group = entry.find("media:group", _ATOM)
        description = ""
        if group is not None:
            description = (group.findtext("media:description", namespaces=_ATOM) or "")
        hits.append(
            VideoHit(
                video_id=video_id,
                title=(entry.findtext("atom:title", namespaces=_ATOM) or "").strip(),
                url=link or f"https://www.youtube.com/watch?v={video_id}",
                channel_id=channel_id,
                channel_title=channel_title,
                published_at=entry.findtext("atom:published", namespaces=_ATOM),
                description=description.strip()[:_MAX_SUMMARY],
            )
        )
    return hits


def _quota_hint(exc: urllib.error.HTTPError) -> str:
    """Name the likely reason behind a rejected Data API call, without the key.

    A search costs 100 units against a daily quota of 10 000, so "quota spent" is
    the ordinary failure here and not an edge case worth a paragraph. The body is
    read because YouTube names the reason there; the key never appears in it.
    """
    try:
        body = exc.read().decode("utf-8", "replace")[:200]
    except Exception:  # noqa: BLE001 - a diagnostic must never raise
        body = ""
    if "quotaExceeded" in body or "dailyLimitExceeded" in body:
        return "daily quota spent — search.list costs 100 units, limit is 10000/day"
    if "rateLimitExceeded" in body:
        return "rate limited — too many requests in a short time"
    if "keyInvalid" in body:
        return "key rejected"
    return body or "no reason given"


def search_channel(
    channel_id: str,
    query: str,
    *,
    limit: int = 5,
    channel_title: str = "",
) -> list[VideoHit] | None:
    """Search a channel's whole catalogue through the Data API.

    Returns ``None`` — not an empty list — whenever the search did not happen:
    no key configured, the daily quota spent (HTTP 403/429), the network down.
    Only a completed search that matched nothing returns ``[]``.

    The distinction is the whole point of the signature. A ``search.list`` call
    costs 100 units against a quota of 10 000 a day, so the key runs out during
    ordinary use, and "we could not look" is a different fact from "there is
    nothing there" — it means the learner's channels were not consulted at all,
    and telling them otherwise would make a broken search look like a fact about
    their curriculum.
    """
    key = (settings.youtube_api_key or "").strip()
    if not key:
        return None

    params = urllib.parse.urlencode(
        {
            "key": key,
            "part": "snippet",
            "channelId": channel_id,
            "q": query,
            "type": "video",
            "maxResults": max(1, min(25, limit * 2)),
        }
    )
    try:
        import json

        payload = json.loads(_fetch(f"{API_SEARCH}?{params}").decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 403/429 here is the daily quota, not a bad query. Log the reason so the
        # cause is visible in the log rather than looking like an empty channel.
        logger.warning(
            "YouTube search rejected for %s: HTTP %s (%s)",
            channel_id,
            exc.code,
            _quota_hint(exc),
        )
        return None
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("YouTube search unreachable for %s: %s", channel_id, exc)
        return None
    except ValueError as exc:
        logger.warning("YouTube search returned unparsable JSON: %s", exc)
        return None

    hits: list[VideoHit] = []
    for item in payload.get("items") or []:
        snippet = item.get("snippet") or {}
        video_id = ((item.get("id") or {}).get("videoId")) or ""
        if not video_id:
            continue
        hits.append(
            VideoHit(
                video_id=video_id,
                title=(snippet.get("title") or "").strip(),
                url=f"https://www.youtube.com/watch?v={video_id}",
                channel_id=channel_id,
                channel_title=channel_title,
                published_at=snippet.get("publishedAt"),
                description=(snippet.get("description") or "").strip()[:_MAX_SUMMARY],
                from_search=True,
            )
        )
    return hits[:limit] if hits else []


def api_available() -> bool:
    """Whether a full-catalogue search is possible right now."""
    return bool((settings.youtube_api_key or "").strip())