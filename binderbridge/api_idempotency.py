"""Persistent idempotency records for authenticated API mutations."""

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from binderbridge.config import config_int
from binderbridge.db import db


IDEMPOTENCY_KEY_TTL_HOURS = max(
    1,
    min(
        168,
        config_int(
            "BINDERBRIDGE_API_IDEMPOTENCY_TTL_HOURS",
            default=24,
            section="api",
            key="idempotency_ttl_hours",
        ),
    ),
)
IDEMPOTENCY_PROCESSING_STALE_SECONDS = max(
    30,
    min(
        3600,
        config_int(
            "BINDERBRIDGE_API_IDEMPOTENCY_PROCESSING_STALE_SECONDS",
            default=300,
            section="api",
            key="idempotency_processing_stale_seconds",
        ),
    ),
)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def idempotency_key_from_headers(headers):
    raw_value = headers.get("Idempotency-Key", "") if headers is not None else ""
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise ValueError(
            "Idempotency-Key must contain 8 to 128 letters, numbers, periods, underscores, colons, or hyphens."
        )
    return value


def idempotency_request_hash(method, path, payload):
    canonical_payload = json.dumps(
        payload if isinstance(payload, dict) else {},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    material = "\n".join(
        (
            str(method or "").strip().upper(),
            str(path or "").strip(),
            canonical_payload,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def begin_idempotent_request(user_id, key, method, path, payload):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_text = now.isoformat()
    stale_before = (now - timedelta(seconds=IDEMPOTENCY_PROCESSING_STALE_SECONDS)).isoformat()
    expires_at = (now + timedelta(hours=IDEMPOTENCY_KEY_TTL_HOURS)).isoformat()
    key_hash = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    clean_method = str(method or "").strip().upper()
    clean_path = str(path or "").strip()
    request_hash = idempotency_request_hash(clean_method, clean_path, payload)

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM api_idempotency_keys WHERE expires_at <= ?", (now_text,))
        found = conn.execute(
            """
            SELECT *
            FROM api_idempotency_keys
            WHERE user_id = ? AND key_hash = ?
            """,
            (int(user_id), key_hash),
        ).fetchone()
        if found:
            if (
                found["method"] != clean_method
                or found["path"] != clean_path
                or found["request_hash"] != request_hash
            ):
                return {"state": "mismatch"}
            if found["state"] == "completed":
                try:
                    response = json.loads(found["response_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError("Stored idempotency response is invalid.") from exc
                if not isinstance(response, dict):
                    raise RuntimeError("Stored idempotency response is invalid.")
                return {
                    "state": "replay",
                    "response": response,
                    "status": int(found["response_status"]),
                }
            if found["updated_at"] > stale_before:
                return {"state": "processing"}
            conn.execute(
                "DELETE FROM api_idempotency_keys WHERE user_id = ? AND key_hash = ?",
                (int(user_id), key_hash),
            )

        try:
            conn.execute(
                """
                INSERT INTO api_idempotency_keys (
                    user_id, key_hash, method, path, request_hash, state,
                    response_status, response_json, created_at, updated_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, 'processing', 0, '', ?, ?, ?)
                """,
                (
                    int(user_id),
                    key_hash,
                    clean_method,
                    clean_path,
                    request_hash,
                    now_text,
                    now_text,
                    expires_at,
                ),
            )
        except sqlite3.IntegrityError:
            return {"state": "processing"}
    return {
        "state": "new",
        "context": {
            "user_id": int(user_id),
            "key_hash": key_hash,
            "request_hash": request_hash,
        },
    }


def finalize_idempotent_request(context, payload, status):
    if not context:
        return
    status_code = int(status)
    user_id = int(context["user_id"])
    key_hash = str(context["key_hash"])
    request_hash = str(context["request_hash"])
    with db() as conn:
        if status_code >= 500:
            conn.execute(
                """
                DELETE FROM api_idempotency_keys
                WHERE user_id = ? AND key_hash = ? AND request_hash = ? AND state = 'processing'
                """,
                (user_id, key_hash, request_hash),
            )
            return
        response_json = json.dumps(
            payload if isinstance(payload, dict) else {},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        cursor = conn.execute(
            """
            UPDATE api_idempotency_keys
            SET state = 'completed',
                response_status = ?,
                response_json = ?,
                updated_at = ?
            WHERE user_id = ? AND key_hash = ? AND request_hash = ? AND state = 'processing'
            """,
            (
                status_code,
                response_json,
                datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                user_id,
                key_hash,
                request_hash,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Could not finalize the API idempotency record.")


__all__ = [
    "IDEMPOTENCY_KEY_TTL_HOURS",
    "IDEMPOTENCY_PROCESSING_STALE_SECONDS",
    "idempotency_key_from_headers",
    "idempotency_request_hash",
    "begin_idempotent_request",
    "finalize_idempotent_request",
]
