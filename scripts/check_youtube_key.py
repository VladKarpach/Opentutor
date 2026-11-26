"""Check that the YouTube key is working, end to end.

Run this after putting YOUTUBE_API_KEY in OpenTutor/.env and restarting the API.
It never prints the key itself.

Covers the three things that can each be broken on their own: the key reaching
the container, YouTube accepting it, and a real catalogue search inside a channel
returning videos that a topic's words can pick out of.
"""

import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "http://127.0.0.1:8010/api"
OUT = pathlib.Path(r"C:\Users\User\AppData\Local\Temp\opencode\youtube_key.txt")

# A channel with deep, topic-named back catalogue in both languages, so the
# search has something real to find either way.
CHANNEL = "https://www.youtube.com/@3blue1brown"

lines = []
ok = True


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def call(method, url, payload=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# 1. Did the container get it?
lines.append("=== 1. ключ дошёл до контейнера ===")
try:
    status = get(f"{API}/video/status")
    lines.append(f"GET /video/status -> {status}")
    if status.get("api_search"):
        lines.append("  api_search = True  ключ прочитан")
    else:
        lines.append("  api_search = False  ключ НЕ прочитан (или не задан)")
        lines.append("  проверьте: строка YOUTUBE_API_KEY= в OpenTutor/.env,")
        lines.append("  затем docker compose restart api")
        ok = False
except Exception as exc:  # noqa: BLE001 - a diagnostic script
    lines.append(f"  API недоступен: {exc}")
    ok = False

# 2. Does YouTube accept it, with a real search inside a channel?
lines.append("")
lines.append("=== 2. поиск по архиву канала (живой YouTube) ===")
if ok:
    code, preview = call("POST", f"{API}/video/resolve", {"value": CHANNEL})
    if code != 200:
        lines.append(f"  канал не разрешился: HTTP {code} {str(preview)[:120]}")
        ok = False
    else:
        channel_id = preview.get("channel_id")
        lines.append(f"  канал: {preview.get('title')} ({channel_id})")

        # Search YouTube directly with the same key the API is using, so a
        # rejection from YouTube is visible rather than swallowed by the router.
        from pathlib import Path as _P

        env_file = _P(r"D:\techer\OpenTutor\.env")
        key = ""
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("YOUTUBE_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
        if not key:
            lines.append("  ключ не найден в .env")
        else:
            lines.append(f"  ключ найден, длина {len(key)} символов (сам ключ не печатаю)")
            query = urllib.parse.urlencode(
                {
                    "key": key,
                    "part": "snippet",
                    "channelId": channel_id,
                    "q": "derivative",
                    "type": "video",
                    "maxResults": 3,
                }
            )
            try:
                with urllib.request.urlopen(
                    f"https://www.googleapis.com/youtube/v3/search?{query}", timeout=30
                ) as r:
                    data = json.loads(r.read().decode("utf-8"))
                items = data.get("items") or []
                lines.append(f"  YouTube принял ключ, найдено {len(items)} видео")
                for item in items[:3]:
                    snippet = item.get("snippet") or {}
                    lines.append(f"     - {snippet.get('title', '')[:64]}")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                lines.append(f"  YouTube отклонил ключ: HTTP {e.code}")
                try:
                    parsed = json.loads(body)
                    lines.append(
                        "  причина: "
                        + str((parsed.get("error") or {}).get("message"))[:160]
                    )
                except ValueError:
                    lines.append("  " + body[:160])
                ok = False
            except Exception as exc:  # noqa: BLE001
                lines.append(f"  сеть недоступна: {exc}")

# 3. Does the app pick a lesson out of that channel for a topic?
lines.append("")
lines.append("=== 3. подбор видео для темы ===")
if ok:
    course = get(f"{API}/courses/")[0]
    cid = course["id"]
    goals = get(f"{API}/learning/courses/{cid}/goals")["goals"]
    topic_id = None
    for g in goals:
        topics = get(f"{API}/learning/goals/{g['id']}/topics")
        if topics:
            topic_id = topics[0]["id"]
            title = topics[0]["title"]
            break
    if topic_id:
        lines.append(f"  тема: {title}")
        found = get(f"{API}/learning/topics/{topic_id}/video-candidates")
        report = found.get("report") or {}
        lines.append(f"  каналов: {report.get('channels')}")
        lines.append(f"  api_search: {report.get('api_search')}")
        lines.append(f"  просмотрено видео: {report.get('videos_seen')}")
        lines.append(f"  кандидатов: {len(found.get('candidates') or [])}")
        for c in (found.get("candidates") or [])[:4]:
            lines.append(f"     - {c['title'][:56]}")
            lines.append(f"       канал: {c.get('channel_title')}")
            lines.append(f"       совпало: {c.get('matched_terms')}")
            lines.append(f"       {c['url']}")
    else:
        lines.append("  не нашлось ни одной темы для проверки")

lines.append("")
lines.append("ИТОГ: " + ("всё работает" if ok else "есть проблема, см. выше"))

OUT.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
sys.exit(0 if ok else 1)