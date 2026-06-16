"""SQLite-backed rate limiting helpers."""

import sqlite3
import threading
import time

from binderbridge.config import config_bool, config_int
from binderbridge.db import db


def _bucket_config(key, default_limit, default_window):
    return (
        max(1, config_int(f"BINDERBRIDGE_{key.upper()}_LIMIT", default=default_limit, section="rate_limits", key=f"{key}_limit")),
        max(1, config_int(f"BINDERBRIDGE_{key.upper()}_WINDOW_SECONDS", default=default_window, section="rate_limits", key=f"{key}_window_seconds")),
    )


RATE_LIMITS = {
    "login": _bucket_config("login", 10, 15 * 60),
    "register": _bucket_config("register", 5, 60 * 60),
    "password_recovery": _bucket_config("password_recovery", 5, 60 * 60),
    "password_reset": _bucket_config("password_reset", 10, 15 * 60),
    "api_auth_failed": _bucket_config("api_auth_failed", 30, 5 * 60),
    "api_health": _bucket_config("api_health", 120, 60),
    "api_read": _bucket_config("api_read", 600, 60),
    "api_write": _bucket_config("api_write", 120, 60),
    "scryfall_lookup": _bucket_config("scryfall_lookup", 30, 5 * 60),
    "integration_admin": _bucket_config("integration_admin", 20, 5 * 60),
}
RATE_LIMIT_PERSISTENT = config_bool(
    "BINDERBRIDGE_RATE_LIMIT_PERSISTENT",
    default=True,
    section="rate_limits",
    key="persistent",
)
RATE_LIMIT_CLEANUP_WINDOW_SECONDS = max(60, max(window for _limit, window in RATE_LIMITS.values()) * 2)

_rate_limit_lock = threading.Lock()
_rate_limit_state = {}


def _rate_limit_text(value, max_length=180):
    text = "" if value is None else str(value)
    clean = []
    for char in text.replace("\x00", ""):
        codepoint = ord(char)
        if codepoint < 32 or codepoint == 127:
            continue
        clean.append(char)
        if len(clean) >= max_length:
            break
    return "".join(clean) or "anonymous"


def _rate_limit_values(bucket, limit=None, window_seconds=None):
    if bucket in RATE_LIMITS and (limit is None or window_seconds is None):
        limit, window_seconds = RATE_LIMITS[bucket]
    return max(1, int(limit or 1)), max(1, int(window_seconds or 60))


def _rate_limit_allowed_memory(bucket, key, limit, window_seconds, now=None):
    now = now if now is not None else time.monotonic()
    state_key = (bucket, key)
    with _rate_limit_lock:
        timestamps = [item for item in _rate_limit_state.get(state_key, []) if item > now - window_seconds]
        if len(timestamps) >= limit:
            _rate_limit_state[state_key] = timestamps
            return False
        timestamps.append(now)
        _rate_limit_state[state_key] = timestamps
        return True


def _rate_limit_allowed_persistent(bucket, key, limit, window_seconds):
    now = time.time()
    cutoff = now - window_seconds
    cleanup_cutoff = now - RATE_LIMIT_CLEANUP_WINDOW_SECONDS
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM rate_limit_events WHERE event_at < ?", (cleanup_cutoff,))
        count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM rate_limit_events
            WHERE bucket = ? AND rate_key = ? AND event_at > ?
            """,
            (bucket, key, cutoff),
        ).fetchone()["count"]
        if int(count or 0) >= limit:
            return False
        conn.execute(
            "INSERT INTO rate_limit_events (bucket, rate_key, event_at) VALUES (?, ?, ?)",
            (bucket, key, now),
        )
        return True


def rate_limit_allowed(bucket, key, limit=None, window_seconds=None):
    clean_bucket = _rate_limit_text(bucket, max_length=80)
    clean_key = _rate_limit_text(key)
    limit, window_seconds = _rate_limit_values(clean_bucket, limit, window_seconds)
    if RATE_LIMIT_PERSISTENT:
        try:
            return _rate_limit_allowed_persistent(clean_bucket, clean_key, limit, window_seconds)
        except sqlite3.Error:
            pass
    return _rate_limit_allowed_memory(clean_bucket, clean_key, limit, window_seconds)


def clear_rate_limits():
    with _rate_limit_lock:
        _rate_limit_state.clear()
    try:
        with db() as conn:
            conn.execute("DELETE FROM rate_limit_events")
    except sqlite3.Error:
        pass


__all__ = [
    "RATE_LIMITS",
    "RATE_LIMIT_PERSISTENT",
    "RATE_LIMIT_CLEANUP_WINDOW_SECONDS",
    "rate_limit_allowed",
    "clear_rate_limits",
]
