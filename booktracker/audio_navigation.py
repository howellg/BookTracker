"""Audio navigation helpers: verified per-part timeline and filtered markers.

The timeline is media time, not time spent listening or reading progress.
A global timestamp is only resolvable when EVERY linked part has a positive
duration from metadata or from a loaded media source.
"""
from __future__ import annotations

from bisect import bisect_right

MAX_TOTAL_MS = 365 * 24 * 3600 * 1000


def playlist_duration(files, active_file_id, active_duration_ms=0):
    """Return known duration and timeline boundaries, or an explicit unknown."""
    if not files or len(files) > 500:
        return {"known": False, "total_ms": 0, "parts": [], "missing": len(files)}
    timeline = []
    cumulative = 0
    missing = 0
    for part in files:
        raw = active_duration_ms if part["id"] == active_file_id and active_duration_ms > 0 else part.get("duration_ms") or 0
        try:
            duration = int(raw)
        except (ValueError, TypeError, OverflowError):
            duration = 0
        if not 0 < duration <= 7 * 24 * 3600 * 1000:
            duration = 0
            missing += 1
        timeline.append({"file_id": part["id"], "start_ms": cumulative,
                         "duration_ms": duration, "end_ms": cumulative + duration})
        cumulative += duration
    return {"known": missing == 0 and cumulative <= MAX_TOTAL_MS,
            "total_ms": cumulative, "parts": timeline, "missing": missing}


def global_position(timeline, active_file_id, position_ms):
    """Unknown preceding parts never produce a fake global elapsed timestamp."""
    parts = timeline["parts"]
    index = next((i for i, p in enumerate(parts) if p["file_id"] == active_file_id), None)
    if index is None or not timeline["known"]:
        return None
    entry = parts[index]
    try:
        position = int(position_ms)
    except (ValueError, TypeError):
        return None
    return entry["start_ms"] + min(max(0, position), entry["duration_ms"])


def locate_global_time(timeline, position_ms):
    """Exact boundary starts next part; total-end maps to end of last part."""
    if not timeline["known"] or not timeline["parts"]:
        raise ValueError("All parts need valid duration metadata to seek by book time.")
    try:
        position = int(position_ms)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid book-wide timestamp.") from exc
    if not 0 <= position <= timeline["total_ms"]:
        raise ValueError("Book-wide timestamp is outside the known duration.")
    entries = timeline["parts"]
    for idx, entry in enumerate(entries):
        if position < entry["end_ms"] or idx == len(entries) - 1:
            return idx, min(entry["duration_ms"], position - entry["start_ms"])
    raise ValueError("Cannot find timestamp in the playlist.")


def chapter_at(chapters, position_ms):
    """Return index of chapter in progress, or None before first marker."""
    if not chapters:
        return None
    starts = [int(ch["start_ms"]) for ch in chapters]
    index = bisect_right(starts, max(0, int(position_ms))) - 1
    return index if index >= 0 else None


def valid_chapters(chapters, duration_ms=0):
    """Ignore malformed markers and nonmonotone starts; clamp huge lists."""
    if not isinstance(chapters, list):
        return []
    result = []
    previous = -1
    for record in chapters[:5000]:
        if not isinstance(record, dict):
            continue
        try:
            start = int(record.get("start_ms"))
            end = int(record.get("end_ms"))
        except (ValueError, TypeError, OverflowError):
            continue
        if not 0 <= start < end <= 7 * 24 * 3600 * 1000 or start < previous:
            continue
        if duration_ms and start >= duration_ms:
            continue
        title = str(record.get("title") or "Untitled").strip()[:250]
        result.append({"start_ms": start, "end_ms": end,
                       "title": title or "Untitled"})
        previous = start
    return result
