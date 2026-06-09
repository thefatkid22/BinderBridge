import base64
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import json
import os
import re
import secrets
import smtplib
import sys
import sqlite3
import threading
import types
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as email_policy
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, unquote, urlparse
from urllib.request import Request, urlopen

from binderbridge.config import config_bool, config_int, config_str


APP_NAME = "BinderBridge"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(config_str("BINDERBRIDGE_DATA", default=str(BASE_DIR / "data"), section="app", key="data_dir"))
DB_PATH = DATA_DIR / "binderbridge.sqlite3"
STATIC_DIR = BASE_DIR / "static"
HOST = config_str("BINDERBRIDGE_HOST", "HOST", default="127.0.0.1", section="server", key="host")
PORT = config_int("BINDERBRIDGE_PORT", "PORT", default=8000, section="server", key="port")
SESSION_COOKIE = "binderbridge_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14
PBKDF2_ITERATIONS = 310_000
CSRF_FIELD_NAME = "_csrf_token"
CSRF_FORM_RE = re.compile(r"(<form\b(?=[^>]*\bmethod\s*=\s*['\"]?post['\"]?)[^>]*>)", re.IGNORECASE)
RATE_LIMITS = {
    "login": (10, 15 * 60),
    "register": (5, 60 * 60),
    "api_auth_failed": (30, 5 * 60),
    "api_write": (120, 60),
    "scryfall_lookup": (30, 5 * 60),
    "integration_admin": (20, 5 * 60),
}
_rate_limit_lock = threading.Lock()
_rate_limit_state = {}


MAX_REQUEST_BODY_BYTES = max(64_000, config_int("BINDERBRIDGE_MAX_REQUEST_BODY_BYTES", "MAX_REQUEST_BODY_BYTES", default=15 * 1024 * 1024, section="limits", key="max_request_body_bytes"))
MAX_UPLOAD_BYTES = max(64_000, config_int("BINDERBRIDGE_MAX_UPLOAD_BYTES", "MAX_UPLOAD_BYTES", default=10 * 1024 * 1024, section="limits", key="max_upload_bytes"))
MAX_FORM_FIELDS = max(100, config_int("BINDERBRIDGE_MAX_FORM_FIELDS", "MAX_FORM_FIELDS", default=2000, section="limits", key="max_form_fields"))
MAX_FORM_VALUE_LENGTH = max(10_000, config_int("BINDERBRIDGE_MAX_FORM_VALUE_LENGTH", "MAX_FORM_VALUE_LENGTH", default=500_000, section="limits", key="max_form_value_length"))
MAX_CSV_ROWS = max(100, config_int("BINDERBRIDGE_MAX_CSV_ROWS", "MAX_CSV_ROWS", default=25000, section="limits", key="max_csv_rows"))
MAX_CARD_QUANTITY = max(1, config_int("BINDERBRIDGE_MAX_CARD_QUANTITY", "MAX_CARD_QUANTITY", default=100000, section="limits", key="max_card_quantity"))


_FEATURE_MODULES = []


def _feature_module_targets(module):
    return (module, *getattr(module, "__binderbridge_feature_modules__", ()))


def _feature_shared_globals(source_globals):
    return {
        name: value
        for name, value in source_globals.items()
        if not (name.startswith("__") and name.endswith("__"))
    }


def _sync_feature_module(module, source_globals):
    module.__dict__.update(_feature_shared_globals(source_globals))
    for target in getattr(module, "__binderbridge_feature_modules__", ()):
        target.__dict__.update(_feature_shared_globals(source_globals))
        target.__dict__.update(_feature_shared_globals(module.__dict__))


def _install_feature_module(module):
    _sync_feature_module(module, globals())
    for name in module.__all__:
        globals()[name] = getattr(module, name)
    _FEATURE_MODULES.append(module)


def _wire_feature_modules():
    for module in _FEATURE_MODULES:
        _sync_feature_module(module, globals())


class _AppModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for module in self.__dict__.get("_FEATURE_MODULES", []):
            for target in _feature_module_targets(module):
                target.__dict__[name] = value


from binderbridge import ui_helpers as _ui_helpers
_install_feature_module(_ui_helpers)
from binderbridge import accounts as _accounts
_install_feature_module(_accounts)
from binderbridge import groups as _groups
_install_feature_module(_groups)
from binderbridge import maintenance as _maintenance
_install_feature_module(_maintenance)
from binderbridge import cleanup as _cleanup
_install_feature_module(_cleanup)



CSV_HEADER_ALIASES = {
    "game": ("game", "tcg", "card game"),
    "name": ("name", "card name", "card", "cardname"),
    "quantity": ("quantity", "qty", "count", "amount"),
    "trade": ("trade", "for trade", "quantity for trade", "trade quantity", "tradable", "tradeable"),
    "set_name": ("set name", "set", "edition", "expansion"),
    "set_code": ("set code", "setcode", "edition code", "expansion code"),
    "collector_number": ("collector number", "collector no", "collector #", "number", "collector"),
    "finish": ("finish", "foil", "printing", "version"),
    "condition": ("condition", "quality"),
    "language": ("language", "lang"),
    "scryfall_id": ("scryfall id", "scryfall_id"),
    "price_usd": ("price", "price usd", "usd", "market price"),
    "price_source": ("price source", "source"),
    "tcgplayer_product_id": ("tcgplayer product id", "tcgplayer id", "tcgplayer_product_id"),
    "cardmarket_product_id": ("cardmarket product id", "cardmarket id", "cardmarket_product_id"),
    "cardkingdom_sku": ("card kingdom sku", "cardkingdom sku", "cardkingdom_sku"),
    "notes": ("notes", "note"),
}
CSV_IMPORT_MAPPING_FIELDS = (
    ("name", "Card name"),
    ("quantity", "Quantity"),
    ("trade", "For trade quantity"),
    ("game", "Game"),
    ("set_name", "Set name"),
    ("set_code", "Set code"),
    ("collector_number", "Collector number"),
    ("finish", "Finish / foil"),
    ("condition", "Condition / quality"),
    ("language", "Language"),
    ("scryfall_id", "Scryfall ID"),
    ("tcgplayer_product_id", "TCGplayer product ID"),
    ("cardmarket_product_id", "Cardmarket product ID"),
    ("cardkingdom_sku", "Card Kingdom SKU"),
    ("notes", "Notes"),
    ("section", "Deck section"),
)
CSV_ALIAS_INDEX = {
}
class RequestTooLargeError(ValueError):
    pass


class CsrfError(ValueError):
    pass


class RateLimitError(ValueError):
    pass


def csrf_token_for_session(session_token):
    token = str(session_token or "").strip()
    if not token:
        return ""
    return hmac.new(token.encode("utf-8"), b"binderbridge-csrf-v1", hashlib.sha256).hexdigest()


def inject_csrf_tokens(html_body, session_token):
    csrf_token = csrf_token_for_session(session_token)
    if not csrf_token or "<form" not in html_body.lower():
        return html_body
    hidden = f'<input type="hidden" name="{CSRF_FIELD_NAME}" value="{csrf_token}">'
    return CSRF_FORM_RE.sub(lambda match: match.group(1) + hidden, html_body)


def csrf_form_valid(form, session_token):
    provided = ""
    values = form.get(CSRF_FIELD_NAME, []) if form else []
    if values:
        provided = str(values[0] or "")
    expected = csrf_token_for_session(session_token)
    return bool(expected and provided and hmac.compare_digest(provided, expected))


def rate_limit_allowed(bucket, key, limit=None, window_seconds=None):
    if bucket in RATE_LIMITS and (limit is None or window_seconds is None):
        limit, window_seconds = RATE_LIMITS[bucket]
    limit = max(1, int(limit or 1))
    window_seconds = max(1, int(window_seconds or 60))
    now = time.monotonic()
    state_key = (bucket, str(key or "anonymous"))
    with _rate_limit_lock:
        timestamps = [item for item in _rate_limit_state.get(state_key, []) if item > now - window_seconds]
        if len(timestamps) >= limit:
            _rate_limit_state[state_key] = timestamps
            return False
        timestamps.append(now)
        _rate_limit_state[state_key] = timestamps
        return True


def clear_rate_limits():
    with _rate_limit_lock:
        _rate_limit_state.clear()


def request_content_length(headers):
    try:
        length = int(headers.get("Content-Length", "0") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid request body length.") from exc
    if length < 0:
        raise ValueError("Invalid request body length.")
    if length > MAX_REQUEST_BODY_BYTES:
        raise RequestTooLargeError("Request body is too large.")
    return length


def sanitize_text_input(value, max_length=MAX_FORM_VALUE_LENGTH):
    text = "" if value is None else str(value)
    cleaned = []
    for char in text.replace("\x00", ""):
        codepoint = ord(char)
        if codepoint < 32 and char not in ("\n", "\r", "\t"):
            continue
        if codepoint == 127:
            continue
        cleaned.append(char)
        if len(cleaned) >= max_length:
            break
    return "".join(cleaned)


def safe_log_text(value, encoding=None):
    text = "" if value is None else str(value)
    escaped = []
    for char in text:
        codepoint = ord(char)
        if codepoint < 32 or 127 <= codepoint <= 159:
            escaped.append(f"\\x{codepoint:02x}")
        else:
            escaped.append(char)
    clean_text = "".join(escaped)
    encoding = encoding or getattr(sys.stderr, "encoding", None) or "utf-8"
    try:
        return clean_text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
    except LookupError:
        return clean_text.encode("ascii", errors="backslashreplace").decode("ascii")


def write_log_message(message, stream=None):
    stream = stream or sys.stderr
    safe_message = safe_log_text(message, getattr(stream, "encoding", None))
    try:
        stream.write(f"{safe_message}\n")
        stream.flush()
    except (AttributeError, UnicodeEncodeError):
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(f"{safe_message}\n".encode("ascii", errors="backslashreplace"))
            buffer.flush()


def sanitize_form_values(form):
    sanitized = {}
    for key, values in dict(form or {}).items():
        clean_key = sanitize_text_input(key, max_length=200)
        if not clean_key:
            continue
        if not isinstance(values, (list, tuple)):
            values = [values]
        sanitized.setdefault(clean_key, []).extend(
            sanitize_text_input(value) for value in values
        )
    return sanitized


def safe_local_redirect_path(value, default="/", allowed_prefix=None):
    text = sanitize_text_input(value, max_length=2000).strip()
    if not text:
        return default
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc or not text.startswith("/") or text.startswith("//") or "\\" in text:
        return default
    if allowed_prefix and parsed.path != allowed_prefix and not parsed.path.startswith(f"{allowed_prefix}/"):
        return default
    return text


def safe_download_filename(filename, default="download"):
    text = sanitize_text_input(filename, max_length=180).strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-")
    return text or default


def normalize_header(header):
    return re.sub(r"[^a-z0-9]+", " ", str(header or "").strip().lower()).strip()


CSV_ALIAS_INDEX = {
    normalize_header(alias): canonical
    for canonical, aliases in CSV_HEADER_ALIASES.items()
    for alias in aliases
}


def csv_import_mapping_field_keys():
    return {key for key, _label in CSV_IMPORT_MAPPING_FIELDS}


def normalize_csv_import_target(value):
    target = sanitize_text_input(value, max_length=40).strip().lower()
    return target if target in ("collection", "deck") else "collection"


def normalize_csv_import_mapping(mapping):
    allowed = csv_import_mapping_field_keys()
    normalized = {}
    for key, value in dict(mapping or {}).items():
        canonical = sanitize_text_input(key, max_length=80).strip().lower()
        if canonical not in allowed:
            continue
        sources = []
        raw_values = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
        for raw in raw_values:
            source = sanitize_text_input(raw, max_length=120).strip()
            if source and source not in sources:
                sources.append(source)
        if sources:
            normalized[canonical] = sources
    return normalized


def csv_import_mapping_json(mapping):
    return json.dumps(normalize_csv_import_mapping(mapping), ensure_ascii=True, sort_keys=True)


def csv_import_mapping_from_json(value):
    try:
        data = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return normalize_csv_import_mapping(data if isinstance(data, dict) else {})


def csv_row_value_for_header(csv_row, source_header):
    normalized_source = normalize_header(source_header)
    for key, value in csv_row.items():
        if normalize_header(key) == normalized_source:
            return sanitize_text_input(value).strip()
    return ""


def csv_value(csv_row, canonical_name, field_mapping=None):
    for source_header in normalize_csv_import_mapping(field_mapping).get(canonical_name, []):
        value = csv_row_value_for_header(csv_row, source_header)
        if value:
            return value
    for key, value in csv_row.items():
        if CSV_ALIAS_INDEX.get(normalize_header(key)) == canonical_name:
            return sanitize_text_input(value).strip()
    return ""


def csv_import_preset_rows_for_user(user_id, import_target="collection", include_shared=True):
    target = normalize_csv_import_target(import_target)
    where = ["import_target = ?", "(user_id = ?"]
    params = [target, int(user_id)]
    if include_shared:
        where[-1] += " OR is_shared = 1"
    where[-1] += ")"
    return rows(
        f"""
        SELECT csv_import_mapping_presets.*, users.display_name AS owner_name, users.username AS owner_username
        FROM csv_import_mapping_presets
        LEFT JOIN users ON users.id = csv_import_mapping_presets.user_id
        WHERE {' AND '.join(where)}
        ORDER BY is_shared DESC, name COLLATE NOCASE, id
        """,
        params,
    )


def csv_import_preset_for_user(user_id, preset_id, is_admin=False, import_target=""):
    try:
        clean_id = int(preset_id or 0)
    except (TypeError, ValueError):
        return None
    where = ["id = ?", "(user_id = ? OR is_shared = 1"]
    params = [clean_id, int(user_id)]
    if is_admin:
        where[-1] += " OR 1 = 1"
    where[-1] += ")"
    if import_target:
        where.append("import_target = ?")
        params.append(normalize_csv_import_target(import_target))
    return row(
        f"""
        SELECT *
        FROM csv_import_mapping_presets
        WHERE {' AND '.join(where)}
        """,
        params,
    )


def csv_import_mapping_for_user(user_id, preset_id, is_admin=False, import_target=""):
    preset = csv_import_preset_for_user(user_id, preset_id, is_admin=is_admin, import_target=import_target)
    return csv_import_mapping_from_json(row_value(preset, "mapping_json", "")) if preset else {}


def csv_import_preset_display_name(preset):
    if not preset:
        return "None"
    owner = row_value(preset, "owner_name", "") or row_value(preset, "owner_username", "")
    suffix = "Shared" if row_value(preset, "is_shared", 0) else "Mine"
    return f"{preset['name']} ({suffix}{f' - {owner}' if owner and row_value(preset, 'is_shared', 0) else ''})"


def save_csv_import_mapping_preset(user_id, name, mapping, import_target="collection", is_shared=False, is_admin=False):
    clean_name = sanitize_text_input(name, max_length=80).strip()
    if not clean_name:
        raise ValueError("Preset name is required.")
    target = normalize_csv_import_target(import_target)
    normalized = normalize_csv_import_mapping(mapping)
    if "name" not in normalized:
        raise ValueError("Map at least the card name column before saving a preset.")
    shared = 1 if is_shared and is_admin else 0
    timestamp = now_iso()
    try:
        preset_id = execute(
            """
            INSERT INTO csv_import_mapping_presets
                (user_id, name, import_target, mapping_json, is_shared, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, import_target, name) DO UPDATE SET
                mapping_json = excluded.mapping_json,
                is_shared = excluded.is_shared,
                updated_at = excluded.updated_at
            """,
            (
                int(user_id),
                clean_name,
                target,
                csv_import_mapping_json(normalized),
                shared,
                timestamp,
                timestamp,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("A preset with that name already exists.") from exc
    return row(
        """
        SELECT *
        FROM csv_import_mapping_presets
        WHERE id = ? OR (user_id = ? AND import_target = ? AND name = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (preset_id, int(user_id), target, clean_name),
    )


def delete_csv_import_mapping_preset(user_id, preset_id, is_admin=False):
    preset = csv_import_preset_for_user(user_id, preset_id, is_admin=is_admin)
    if not preset:
        raise ValueError("Mapping preset was not found.")
    if int(preset["user_id"]) != int(user_id) and not is_admin:
        raise ValueError("Mapping preset was not found.")
    with db() as conn:
        cursor = conn.execute("DELETE FROM csv_import_mapping_presets WHERE id = ?", (preset["id"],))
        return cursor.rowcount


def csv_import_mapping_from_form(form):
    mapping = {}
    for key, _label in CSV_IMPORT_MAPPING_FIELDS:
        raw_value = form.get(f"map_{key}", [""])[0]
        sources = normalize_csv_import_mapping({key: raw_value}).get(key, [])
        if sources:
            mapping[key] = sources
    return mapping


def decode_csv(csv_bytes):
    if isinstance(csv_bytes, str):
        return csv_bytes
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return csv_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return csv_bytes.decode("utf-8", errors="replace")


def parse_nonnegative_int(value, default=0):
    try:
        number = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return max(0, number)


def clamp_quantity(value, default=0, max_value=MAX_CARD_QUANTITY):
    return min(parse_nonnegative_int(value, default), max_value)


def normalize_condition(value):
    text = str(value or "").strip().lower().replace("-", " ")
    mapping = {
        "near mint": "NM",
        "nm": "NM",
        "lightly played": "LP",
        "light played": "LP",
        "lp": "LP",
        "moderately played": "MP",
        "medium played": "MP",
        "mp": "MP",
        "heavily played": "HP",
        "heavy played": "HP",
        "hp": "HP",
        "damaged": "DMG",
        "dmg": "DMG",
        "poor": "DMG",
    }
    return mapping.get(text, str(value or "NM").strip().upper() if str(value or "").strip().upper() in CONDITION_OPTIONS else "NM")


def normalize_finish(value):
    text = str(value or "").strip().lower()
    if text in ("", "false", "no", "n", "0", "regular", "normal", "nonfoil", "non foil"):
        return "Regular"
    if text in ("true", "yes", "y", "1", "foil"):
        return "Foil"
    if text == "etched":
        return "Etched"
    if text == "showcase":
        return "Showcase"
    for option in FINISH_OPTIONS:
        if text == option.lower():
            return option
    return "Other"


def normalize_language(value):
    text = str(value or "").strip()
    lower = text.lower()
    mapping = {
        "en": "English",
        "eng": "English",
        "english": "English",
        "ja": "Japanese",
        "jp": "Japanese",
        "japanese": "Japanese",
        "de": "German",
        "german": "German",
        "fr": "French",
        "french": "French",
        "es": "Spanish",
        "spanish": "Spanish",
        "it": "Italian",
        "italian": "Italian",
        "pt": "Portuguese",
        "portuguese": "Portuguese",
        "ko": "Korean",
        "korean": "Korean",
        "zh": "Chinese",
        "chinese": "Chinese",
    }
    return mapping.get(lower, text if text in LANGUAGE_OPTIONS else "English")


def normalize_game(value, default_game="mtg"):
    text = str(value or default_game or "mtg").strip().lower()
    aliases = {
        "magic": "mtg",
        "magic the gathering": "mtg",
        "magic: the gathering": "mtg",
        "pokemon": "pokemon",
        "pokÃ©mon": "pokemon",
        "lorcana": "lorcana",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in dict(CARD_GAMES) else default_game


def parse_trade_quantity(raw_value, owned_quantity, default_trade_quantity):
    text = str(raw_value or "").strip()
    if text == "":
        return min(clamp_quantity(default_trade_quantity, 0), owned_quantity)
    lower = text.lower()
    if lower in ("true", "yes", "y", "trade", "for trade"):
        return owned_quantity
    if lower in ("false", "no", "n"):
        return 0
    return min(clamp_quantity(text, 0), owned_quantity)


def normalize_csv_rows(csv_bytes, default_game="mtg", default_trade_quantity=0, field_mapping=None):
    text = decode_csv(csv_bytes)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file needs a header row.")
    field_mapping = normalize_csv_import_mapping(field_mapping)
    items = []
    warnings = []
    for row_index, csv_row in enumerate(reader, start=2):
        if row_index - 1 > MAX_CSV_ROWS:
            raise ValueError(f"CSV imports are limited to {MAX_CSV_ROWS} rows per upload.")
        card_name = csv_value(csv_row, "name", field_mapping)
        if not card_name:
            warnings.append(f"Row {row_index}: missing card name.")
            continue
        quantity = max(1, clamp_quantity(csv_value(csv_row, "quantity", field_mapping), 1))
        item = {
            "game": normalize_game(csv_value(csv_row, "game", field_mapping), default_game),
            "card_name": card_name[:160],
            "set_name": csv_value(csv_row, "set_name", field_mapping)[:120],
            "set_code": csv_value(csv_row, "set_code", field_mapping).upper()[:20],
            "collector_number": csv_value(csv_row, "collector_number", field_mapping).lstrip("#")[:40],
            "finish": normalize_finish(csv_value(csv_row, "finish", field_mapping)),
            "condition": normalize_condition(csv_value(csv_row, "condition", field_mapping)),
            "language": normalize_language(csv_value(csv_row, "language", field_mapping)),
            "quantity": quantity,
            "quantity_for_trade": parse_trade_quantity(csv_value(csv_row, "trade", field_mapping), quantity, default_trade_quantity),
            "notes": csv_value(csv_row, "notes", field_mapping)[:1000],
            "scryfall_id": csv_value(csv_row, "scryfall_id", field_mapping)[:80],
            "image_url": "",
            "mana_cost": "",
            "type_line": "",
            "oracle_text": "",
            "rarity": "",
            "colors": "",
            "color_identity": "",
            "scryfall_uri": "",
            "price_usd": "",
            "price_source": "",
            "tcgplayer_product_id": csv_value(csv_row, "tcgplayer_product_id", field_mapping)[:80],
            "cardmarket_product_id": csv_value(csv_row, "cardmarket_product_id", field_mapping)[:80],
            "cardkingdom_sku": csv_value(csv_row, "cardkingdom_sku", field_mapping)[:80],
        }
        items.append(item)
    if not items:
        raise ValueError("No importable collection rows were found.")
    return items, warnings


from binderbridge import deck_imports as _deck_imports
_install_feature_module(_deck_imports)

from binderbridge import scryfall_client as _scryfall_client
_install_feature_module(_scryfall_client)
from binderbridge import scryfall_jobs as _scryfall_jobs
_install_feature_module(_scryfall_jobs)
from binderbridge import background_jobs as _background_jobs
_install_feature_module(_background_jobs)


def render_scryfall_result_picker(
    scryfall_results,
    button_label="Use selected card",
    intent="use_scryfall",
    title="Scryfall matches",
    multiple=False,
):
    if not scryfall_results:
        return ""
    options = []
    for index, card in enumerate(scryfall_results):
        image = f'<img class="scryfall-result-image" src="{e(card["image_url"])}" alt="">' if card.get("image_url") else '<span class="scryfall-result-image placeholder"></span>'
        price = f' - ${e(card["price_usd"])}' if card.get("price_usd") else ""
        input_type = "checkbox" if multiple else "radio"
        input_name = "selected_scryfall_ids" if multiple else "selected_scryfall_id"
        checked_attr = "" if multiple else (" checked" if index == 0 else "")
        options.append(
            f"""
            <label class="scryfall-result-card">
                <input type="{input_type}" name="{input_name}" value="{e(card["scryfall_id"])}"{checked_attr} data-scryfall-option>
                {image}
                <span>
                    <strong>{e(card["card_name"])}</strong>
                    <small>{e(card["set_name"])} ({e(card["set_code"])}) #{e(card["collector_number"])}{price}</small>
                    <small>{e(card["type_line"] or card["rarity"])}</small>
                </span>
            </label>
            """
        )
    select_all = ""
    script = ""
    if multiple:
        select_all = """
        <label class="checkbox-line scryfall-select-all">
            <input type="checkbox" data-scryfall-select-all>
            Select all shown
        </label>
        """
        script = """
        <script>
            (function () {
                document.querySelectorAll("[data-scryfall-select-all]").forEach(function (toggle) {
                    var section = toggle.closest(".scryfall-results");
                    if (!section) return;
                    var options = Array.prototype.slice.call(section.querySelectorAll("[data-scryfall-option]"));
                    function syncToggle() {
                        toggle.checked = options.length > 0 && options.every(function (option) { return option.checked; });
                        toggle.indeterminate = options.some(function (option) { return option.checked; }) && !toggle.checked;
                    }
                    toggle.addEventListener("change", function () {
                        options.forEach(function (option) { option.checked = toggle.checked; });
                    });
                    options.forEach(function (option) {
                        option.addEventListener("change", syncToggle);
                    });
                    syncToggle();
                });
            })();
        </script>
        """
    return f"""
    <section class="scryfall-results span-2">
        <div class="panel-heading">
            <h2>{e(title)}</h2>
            <span class="muted">{len(scryfall_results)} shown</span>
        </div>
        {select_all}
        <div class="scryfall-result-grid">
            {''.join(options)}
        </div>
        <div class="form-actions">
            <button class="button primary" name="intent" value="{e(intent)}" type="submit">{e(button_label)}</button>
        </div>
    </section>
    {script}
    """


def render_scryfall_preview(item):
    if not (item.get("scryfall_id") or item.get("image_url") or item.get("type_line")):
        return ""
    image = f'<img class="lookup-preview-image" src="{e(item["image_url"])}" alt="">' if item.get("image_url") else '<span class="lookup-preview-image placeholder"></span>'
    scryfall_link = f'<a href="{e(item["scryfall_uri"])}" target="_blank" rel="noreferrer">Open on Scryfall</a>' if item.get("scryfall_uri") else ""
    return f"""
    <div class="lookup-preview span-2">
        {image}
        <div>
            <strong>{e(item["card_name"])}</strong>
            <span>{e(item.get("type_line") or "Scryfall match loaded")}</span>
            <span>{e(item.get("set_code") or "Set")} {e("#" + item["collector_number"] if item.get("collector_number") else "")} {e(item.get("rarity", ""))}</span>
            {scryfall_link}
        </div>
    </div>
    """

from binderbridge import collection_service as _collection_service
_install_feature_module(_collection_service)
from binderbridge import api as _api
_install_feature_module(_api)

def add_import_warning(result, message):
    result["warning_count"] += 1
    if len(result["warnings"]) < 12:
        result["warnings"].append(message)



IMPORT_BATCH_COLLECTION_FIELDS = (
    "game",
    "card_name",
    "set_name",
    "set_code",
    "collector_number",
    "finish",
    "condition",
    "language",
    "quantity",
    "quantity_for_trade",
    "scryfall_id",
    "image_url",
    "mana_cost",
    "type_line",
    "oracle_text",
    "rarity",
    "colors",
    "color_identity",
    "scryfall_uri",
    "price_usd",
    "price_source",
    "tcgplayer_product_id",
    "cardmarket_product_id",
    "cardkingdom_sku",
    "price_refreshed_at",
    "price_status",
    "notes",
    "is_public",
    "created_at",
    "updated_at",
)


def record_state(record):
    if not record:
        return ""
    return json.dumps({key: record[key] for key in record.keys()}, ensure_ascii=True, sort_keys=True)


def load_record_state(value):
    try:
        data = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def import_batch_summary_json(summary):
    return json.dumps(summary or {}, ensure_ascii=True, sort_keys=True)


def import_batch_payload_json(payload):
    return json.dumps(payload or {}, ensure_ascii=True, sort_keys=True)


def create_import_batch(user_id, import_type, source="", status="preview", summary=None, payload=None, group_id=0):
    timestamp = now_iso()
    return execute(
        """
        INSERT INTO import_batches
            (user_id, group_id, import_type, source, status, summary_json, payload_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            int(group_id or 0),
            sanitize_text_input(import_type, max_length=40).strip(),
            sanitize_text_input(source, max_length=40).strip(),
            sanitize_text_input(status, max_length=40).strip(),
            import_batch_summary_json(summary),
            import_batch_payload_json(payload),
            timestamp,
            timestamp,
        ),
    )


def update_import_batch(batch_id, status=None, summary=None, payload=None, undone=False):
    assignments = ["updated_at = ?"]
    params = [now_iso()]
    if status is not None:
        assignments.append("status = ?")
        params.append(sanitize_text_input(status, max_length=40).strip())
    if summary is not None:
        assignments.append("summary_json = ?")
        params.append(import_batch_summary_json(summary))
    if payload is not None:
        assignments.append("payload_json = ?")
        params.append(import_batch_payload_json(payload))
    if undone:
        assignments.append("undone_at = ?")
        params.append(now_iso())
    params.append(batch_id)
    execute(f"UPDATE import_batches SET {', '.join(assignments)} WHERE id = ?", params)


def import_batch_for_user(user_id, batch_id):
    try:
        batch_id = int(batch_id)
    except (TypeError, ValueError):
        return None
    return row("SELECT * FROM import_batches WHERE id = ? AND user_id = ?", (batch_id, user_id))


def import_batch_payload(batch):
    return load_record_state(row_value(batch, "payload_json", ""))


def import_batch_summary(batch):
    return load_record_state(row_value(batch, "summary_json", ""))


def record_import_batch_item(batch_id, item_type, action, target_table, target_id, previous_state=""):
    if not batch_id:
        return 0
    return execute(
        """
        INSERT INTO import_batch_items
            (batch_id, item_type, action, target_table, target_id, previous_state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            sanitize_text_input(item_type, max_length=60).strip(),
            sanitize_text_input(action, max_length=60).strip(),
            sanitize_text_input(target_table, max_length=80).strip(),
            int(target_id or 0),
            previous_state or "",
            now_iso(),
        ),
    )


def recent_import_batches(user_id, import_type=None, group_id=None, limit=6):
    where = ["user_id = ?", "status IN ('applied', 'undone')"]
    params = [user_id]
    if import_type:
        where.append("import_type = ?")
        params.append(import_type)
    if group_id is not None:
        where.append("group_id = ?")
        params.append(int(group_id or 0))
    return rows(
        f"""
        SELECT *
        FROM import_batches
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params + [int(limit or 6)],
    )


def collection_import_existing_match(user_id, data, merge=True):
    if not merge:
        return None
    values = collection_item_values(data)
    return row(
        """
        SELECT *
        FROM collection_items
        WHERE user_id = ?
            AND game = ?
            AND card_name = ? COLLATE NOCASE
            AND set_name = ?
            AND set_code = ?
            AND collector_number = ?
            AND finish = ?
            AND condition = ?
            AND language = ?
        """,
        (
            user_id,
            values["game"],
            values["card_name"],
            values["set_name"],
            values["set_code"],
            values["collector_number"],
            values["finish"],
            values["condition"],
            values["language"],
        ),
    )


def import_preview_row(import_item, action, note=""):
    return {
        "action": action,
        "card_name": import_item.get("card_name", ""),
        "set_name": import_item.get("set_name", ""),
        "set_code": import_item.get("set_code", ""),
        "collector_number": import_item.get("collector_number", ""),
        "quantity": int(import_item.get("quantity") or 0),
        "quantity_for_trade": int(import_item.get("quantity_for_trade") or 0),
        "finish": import_item.get("finish", ""),
        "condition": import_item.get("condition", ""),
        "note": note,
    }


def collection_import_preview_from_items(
    user_id,
    items,
    warnings=None,
    source="auto",
    default_game="mtg",
    default_trade_quantity=0,
    enrich_scryfall=False,
    merge=True,
    allow_scryfall_finish_mismatch=False,
):
    result = {
        "source": source,
        "total_rows": len(items),
        "inserted": 0,
        "updated": 0,
        "enriched": 0,
        "queued": 0,
        "not_found": 0,
        "skipped": 0,
        "warning_count": 0,
        "warnings": [],
        "rows": [],
        "preview": True,
        "default_game": default_game,
        "default_trade_quantity": default_trade_quantity,
        "enrich_scryfall": bool(enrich_scryfall),
        "merge": bool(merge),
        "allow_scryfall_finish_mismatch": bool(allow_scryfall_finish_mismatch),
    }
    for warning in warnings or []:
        add_import_warning(result, warning)

    lookup_cache = {}
    for item in items:
        import_item = item
        queued = False
        if enrich_scryfall and item["game"] == "mtg":
            import_item = apply_local_scryfall_data(item, lookup_cache)
            if import_item is not item:
                result["enriched"] += 1
            else:
                queued = True
                result["queued"] += 1
        mismatch_message = scryfall_finish_check_message(import_item)
        if mismatch_message and not allow_scryfall_finish_mismatch:
            result["skipped"] += 1
            add_import_warning(result, f"{mismatch_message} Row would be skipped. Enable the Scryfall finish override to import it anyway.")
            if len(result["rows"]) < 12:
                result["rows"].append(import_preview_row(import_item, "skipped", "Finish mismatch"))
            continue
        if mismatch_message:
            add_import_warning(result, f"{mismatch_message} Override would allow this row.")
        if collection_import_existing_match(user_id, import_item, merge=merge):
            result["updated"] += 1
            action = "update"
        else:
            result["inserted"] += 1
            action = "insert"
        if len(result["rows"]) < 12:
            result["rows"].append(import_preview_row(import_item, action, "Queued for Scryfall lookup" if queued else ""))
    return result


def preview_collection_import_csv(
    user_id,
    csv_bytes,
    source="auto",
    default_game="mtg",
    default_trade_quantity=0,
    enrich_scryfall=False,
    merge=True,
    allow_scryfall_finish_mismatch=False,
    field_mapping=None,
):
    items, warnings = normalize_csv_rows(csv_bytes, default_game, default_trade_quantity, field_mapping=field_mapping)
    preview = collection_import_preview_from_items(
        user_id,
        items,
        warnings=warnings,
        source=source,
        default_game=default_game,
        default_trade_quantity=default_trade_quantity,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        allow_scryfall_finish_mismatch=allow_scryfall_finish_mismatch,
    )
    payload = {
        "items": items,
        "warnings": warnings,
        "source": source,
        "default_game": default_game,
        "default_trade_quantity": default_trade_quantity,
        "enrich_scryfall": bool(enrich_scryfall),
        "merge": bool(merge),
        "allow_scryfall_finish_mismatch": bool(allow_scryfall_finish_mismatch),
    }
    batch_id = create_import_batch(user_id, "collection_csv", source, "preview", preview, payload)
    preview["batch_id"] = batch_id
    update_import_batch(batch_id, summary=preview)
    return preview


def collection_import_result(source, total_rows, warnings=None):
    result = {
        "source": source,
        "total_rows": total_rows,
        "inserted": 0,
        "updated": 0,
        "enriched": 0,
        "queued": 0,
        "not_found": 0,
        "skipped": 0,
        "warning_count": 0,
        "warnings": [],
    }
    for warning in warnings or []:
        add_import_warning(result, warning)
    return result


def import_collection_items(
    user_id,
    items,
    warnings=None,
    source="auto",
    enrich_scryfall=False,
    merge=True,
    allow_scryfall_finish_mismatch=False,
    batch_id=None,
):
    result = collection_import_result(source, len(items), warnings)
    lookup_cache = {}
    for item in items:
        import_item = item
        queue_item = False
        if enrich_scryfall and item["game"] == "mtg":
            import_item = apply_local_scryfall_data(item, lookup_cache)
            if import_item is not item:
                result["enriched"] += 1
            else:
                queue_item = True
        mismatch_message = scryfall_finish_check_message(import_item)
        if mismatch_message:
            if allow_scryfall_finish_mismatch:
                add_import_warning(result, f"{mismatch_message} Override allowed this row.")
            else:
                result["skipped"] += 1
                add_import_warning(result, f"{mismatch_message} Row skipped. Enable the Scryfall finish override to import it anyway.")
                continue

        existing = collection_import_existing_match(user_id, import_item, merge=merge)
        previous_state = record_state(existing)
        action, item_id = upsert_collection_item(user_id, import_item, merge=merge, return_id=True)
        result["inserted" if action == "inserted" else "updated"] += 1
        record_import_batch_item(batch_id, "collection_item", action, "collection_items", item_id, previous_state)
        if queue_item and enqueue_scryfall_enrichment(item_id, user_id, item):
            result["queued"] += 1
    return result


def commit_collection_import_preview(user_id, batch_id):
    batch = import_batch_for_user(user_id, batch_id)
    if not batch or batch["import_type"] != "collection_csv" or batch["status"] != "preview":
        raise ValueError("Import preview was not found. Please upload the CSV again.")
    payload = import_batch_payload(batch)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Import preview was not found. Please upload the CSV again.")
    result = import_collection_items(
        user_id,
        items[:MAX_CSV_ROWS],
        warnings=payload.get("warnings", []),
        source=payload.get("source", batch["source"] or "auto"),
        enrich_scryfall=bool(payload.get("enrich_scryfall")),
        merge=bool(payload.get("merge")),
        allow_scryfall_finish_mismatch=bool(payload.get("allow_scryfall_finish_mismatch")),
        batch_id=batch["id"],
    )
    result["batch_id"] = batch["id"]
    update_import_batch(batch["id"], status="applied", summary=result, payload={})
    return result


def import_collection_csv(
    user_id,
    csv_bytes,
    source="auto",
    default_game="mtg",
    default_trade_quantity=0,
    enrich_scryfall=False,
    merge=True,
    allow_scryfall_finish_mismatch=False,
    field_mapping=None,
):
    items, warnings = normalize_csv_rows(csv_bytes, default_game, default_trade_quantity, field_mapping=field_mapping)
    batch_id = create_import_batch(
        user_id,
        "collection_csv",
        source,
        "applied",
        {"total_rows": len(items), "source": source},
        {},
    )
    result = import_collection_items(
        user_id,
        items,
        warnings=warnings,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        allow_scryfall_finish_mismatch=allow_scryfall_finish_mismatch,
        batch_id=batch_id,
    )
    result["batch_id"] = batch_id
    update_import_batch(batch_id, summary=result)
    return result


def deck_group_item_row(group_id, collection_item_id):
    return row(
        """
        SELECT *
        FROM group_collection_items
        WHERE group_id = ? AND collection_item_id = ?
        """,
        (group_id, collection_item_id),
    )


def deck_import_result(source, total_rows, warnings=None):
    result = {
        "source": source,
        "total_rows": total_rows,
        "inserted": 0,
        "updated": 0,
        "enriched": 0,
        "queued": 0,
        "not_found": 0,
        "grouped": 0,
        "matched": 0,
        "missing": 0,
        "missing_entries": 0,
        "missing_items": [],
        "warning_count": 0,
        "warnings": [],
    }
    for warning in warnings or []:
        add_import_warning(result, warning)
    return result


def deck_import_preview_from_items(user_id, group_id, items, source="decklist", enrich_scryfall=True, merge=True, warnings=None):
    group = user_group(user_id, group_id)
    if not group or group["group_type"] != "deck":
        raise ValueError("Deck imports are only available for deck groups.")
    result = deck_import_result(source, len(items), warnings)
    result["preview"] = True
    result["merge"] = bool(merge)
    result["rows"] = []
    lookup_cache = {}
    deck_cards = {}
    for item in items:
        import_item = item
        if enrich_scryfall and item["game"] == "mtg":
            import_item = apply_local_scryfall_data(item, lookup_cache)
            if import_item is not item:
                result["enriched"] += 1
            else:
                result["not_found"] += 1

        key = deck_import_collection_key(import_item)
        if key not in deck_cards:
            deck_cards[key] = dict(import_item)
            deck_cards[key]["quantity"] = 0
        deck_cards[key]["quantity"] = min(MAX_CARD_QUANTITY, deck_cards[key]["quantity"] + int(item["quantity"] or 0))

    for deck_item in deck_cards.values():
        remaining = max(0, int(deck_item["quantity"] or 0))
        owned_total = 0
        for collection_item in deck_import_collection_matches(user_id, deck_item):
            if remaining <= 0:
                break
            available = max(0, int(collection_item["quantity"] or 0))
            if available <= 0:
                continue
            group_quantity = min(available, remaining)
            existing_group_item = deck_group_item_row(group_id, collection_item["id"])
            result["grouped"] += group_quantity
            result["matched"] += group_quantity
            owned_total += group_quantity
            remaining -= group_quantity
            if len(result["rows"]) < 12:
                result["rows"].append({
                    "action": "update group" if existing_group_item else "add to group",
                    "card_name": collection_item["card_name"],
                    "set_name": collection_item["set_name"],
                    "set_code": collection_item["set_code"],
                    "collector_number": collection_item["collector_number"],
                    "quantity": group_quantity,
                    "quantity_for_trade": collection_item["quantity_for_trade"],
                    "finish": collection_item["finish"],
                    "condition": collection_item["condition"],
                    "note": "Already in this group" if existing_group_item else "Owned copy matched",
                })
        if remaining > 0:
            missing = deck_missing_item(deck_item, remaining, owned_total)
            result["missing"] += remaining
            result["missing_entries"] += 1
            result["missing_items"].append(missing)
            add_import_warning(result, f"{missing['card_name']}: missing {remaining} from your collection.")
            if len(result["rows"]) < 12:
                result["rows"].append(import_preview_row(missing, "missing", "Can be added to wishlist after import"))
    assign_deck_missing_item_keys(result["missing_items"])
    return result


def preview_deck_group_import(user_id, group_id, section_rows, included_sections=None, source="decklist", enrich_scryfall=True, merge=True, warnings=None):
    included = set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS) | {DECK_IMPORT_MAIN_SECTION}
    import_warnings = list(warnings or [])
    import_warnings.extend(deck_import_exclusion_warnings(section_rows, included))
    items = deck_import_items_from_sections(section_rows, included)
    preview = deck_import_preview_from_items(
        user_id,
        group_id,
        items,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=import_warnings,
    )
    payload = {
        "sections": {
            section: [dict(item) for item in rows_for_section]
            for section, rows_for_section in section_rows.items()
            if rows_for_section
        },
        "included_sections": sorted(included),
        "warnings": list(warnings or []),
        "source": source,
        "enrich_scryfall": bool(enrich_scryfall),
        "merge": bool(merge),
    }
    batch_id = create_import_batch(user_id, "deck_group", source, "preview", preview, payload, group_id=group_id)
    preview["batch_id"] = batch_id
    update_import_batch(batch_id, summary=preview)
    return preview


def commit_deck_import_preview(user_id, group_id, batch_id):
    batch = import_batch_for_user(user_id, batch_id)
    if not batch or batch["import_type"] != "deck_group" or batch["status"] != "preview" or int(batch["group_id"] or 0) != int(group_id):
        raise ValueError("Deck import preview was not found. Please submit the deck list again.")
    payload = import_batch_payload(batch)
    sections = {section: [] for section in DECK_IMPORT_SECTION_LABELS}
    raw_sections = payload.get("sections", {})
    if not isinstance(raw_sections, dict):
        raise ValueError("Deck import preview was not found. Please submit the deck list again.")
    for section, raw_items in raw_sections.items():
        if not isinstance(raw_items, list):
            continue
        sections.setdefault(section, [])
        for item in raw_items:
            if isinstance(item, dict) and item.get("card_name"):
                sections[section].append(deck_import_item(
                    item.get("card_name", ""),
                    item.get("quantity", 1),
                    game=item.get("game", "mtg"),
                    set_name=item.get("set_name", ""),
                    set_code=item.get("set_code", ""),
                    collector_number=item.get("collector_number", ""),
                    finish=item.get("finish", "Regular"),
                    condition=item.get("condition", "NM"),
                    language=item.get("language", "English"),
                    scryfall_id=item.get("scryfall_id", ""),
                    notes=item.get("notes", ""),
                ))
    included_sections = set(payload.get("included_sections") or DECK_IMPORT_DEFAULT_SECTIONS)
    result = import_deck_group_sections(
        user_id,
        group_id,
        sections,
        included_sections=included_sections,
        source=payload.get("source", batch["source"] or "decklist"),
        enrich_scryfall=bool(payload.get("enrich_scryfall")),
        merge=bool(payload.get("merge")),
        warnings=payload.get("warnings", []),
        batch_id=batch["id"],
    )
    result["batch_id"] = batch["id"]
    update_import_batch(batch["id"], status="applied", summary=result, payload={})
    return result


def import_deck_group_items(user_id, group_id, items, source="decklist", enrich_scryfall=True, merge=True, warnings=None, batch_id=None):
    group = user_group(user_id, group_id)
    if not group or group["group_type"] != "deck":
        raise ValueError("Deck imports are only available for deck groups.")
    result = deck_import_result(source, len(items), warnings)

    lookup_cache = {}
    deck_cards = {}
    for item in items:
        import_item = item
        if enrich_scryfall and item["game"] == "mtg":
            import_item = apply_local_scryfall_data(item, lookup_cache)
            if import_item is not item:
                result["enriched"] += 1
            else:
                result["not_found"] += 1

        key = deck_import_collection_key(import_item)
        if key not in deck_cards:
            deck_cards[key] = dict(import_item)
            deck_cards[key]["quantity"] = 0
        deck_cards[key]["quantity"] = min(MAX_CARD_QUANTITY, deck_cards[key]["quantity"] + int(item["quantity"] or 0))

    for deck_item in deck_cards.values():
        remaining = max(0, int(deck_item["quantity"] or 0))
        owned_total = 0
        for collection_item in deck_import_collection_matches(user_id, deck_item):
            if remaining <= 0:
                break
            available = max(0, int(collection_item["quantity"] or 0))
            if available <= 0:
                continue
            group_quantity = min(available, remaining)
            existing_group_item = deck_group_item_row(group_id, collection_item["id"])
            previous_state = record_state(existing_group_item)
            add_collection_item_to_group(user_id, group_id, collection_item["id"], group_quantity)
            updated_group_item = deck_group_item_row(group_id, collection_item["id"])
            if updated_group_item:
                record_import_batch_item(
                    batch_id,
                    "group_collection_item",
                    "updated" if existing_group_item else "inserted",
                    "group_collection_items",
                    updated_group_item["id"],
                    previous_state,
                )
            result["grouped"] += group_quantity
            result["matched"] += group_quantity
            owned_total += group_quantity
            remaining -= group_quantity
        if remaining > 0:
            missing = deck_missing_item(deck_item, remaining, owned_total)
            result["missing"] += remaining
            result["missing_entries"] += 1
            result["missing_items"].append(missing)
            add_import_warning(result, f"{missing['card_name']}: missing {remaining} from your collection.")
    assign_deck_missing_item_keys(result["missing_items"])
    return result


def deck_import_collection_key(item):
    return (
        str(item.get("game") or "mtg").strip().lower(),
        str(item.get("card_name") or "").strip().lower(),
    )


def deck_import_collection_matches(user_id, item):
    return rows(
        """
        SELECT *
        FROM collection_items
        WHERE user_id = ?
            AND game = ?
            AND card_name = ? COLLATE NOCASE
            AND quantity > 0
        ORDER BY
            CASE
                WHEN ? != '' AND scryfall_id = ? THEN 0
                WHEN ? != '' AND collector_number != '' AND set_code = ? COLLATE NOCASE AND collector_number = ? COLLATE NOCASE THEN 1
                ELSE 2
            END,
            quantity DESC,
            set_name COLLATE NOCASE,
            collector_number COLLATE NOCASE
        """,
        (
            user_id,
            item.get("game", "mtg"),
            item.get("card_name", ""),
            item.get("scryfall_id", ""),
            item.get("scryfall_id", ""),
            item.get("set_code", ""),
            item.get("set_code", ""),
            item.get("collector_number", ""),
        ),
    )


def deck_missing_item(item, missing_quantity, owned_quantity=0):
    missing = dict(item)
    missing["quantity"] = int(missing_quantity)
    missing["owned_quantity"] = int(owned_quantity or 0)
    missing["desired_quantity"] = int(missing_quantity)
    return missing


def assign_deck_missing_item_keys(items):
    for index, item in enumerate(items):
        item["key"] = f"{index}:{item.get('game', 'mtg')}:{item.get('card_name', '').strip().lower()}"


def import_deck_group_sections(user_id, group_id, section_rows, included_sections=None, source="decklist", enrich_scryfall=True, merge=True, warnings=None, batch_id=None):
    included = set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS) | {DECK_IMPORT_MAIN_SECTION}
    import_warnings = list(warnings or [])
    import_warnings.extend(deck_import_exclusion_warnings(section_rows, included))
    items = deck_import_items_from_sections(section_rows, included)
    return import_deck_group_items(
        user_id,
        group_id,
        items,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=import_warnings,
        batch_id=batch_id,
    )


def deck_import_preview_payload(source, section_rows, warnings=None, enrich_scryfall=True, merge=True, source_url=""):
    normalized_sections = {
        section: [dict(item) for item in items]
        for section, items in section_rows.items()
        if items
    }
    return {
        "source": source,
        "sections": normalized_sections,
        "warnings": list(warnings or []),
        "enrich_scryfall": bool(enrich_scryfall),
        "merge": bool(merge),
        "source_url": source_url,
    }


def encode_deck_import_payload(payload):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_deck_import_payload(encoded_payload):
    try:
        raw = base64.urlsafe_b64decode(str(encoded_payload or "").encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Deck import review expired. Please submit the deck list again.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("sections"), dict):
        raise ValueError("Deck import review expired. Please submit the deck list again.")
    sections = {section: [] for section in DECK_IMPORT_SECTION_LABELS}
    for section, items in payload.get("sections", {}).items():
        if not isinstance(items, list):
            continue
        sections.setdefault(section, [])
        for item in items:
            if isinstance(item, dict) and item.get("card_name"):
                sections[section].append(deck_import_item(
                    item.get("card_name", ""),
                    item.get("quantity", 1),
                    set_name=item.get("set_name", ""),
                    set_code=item.get("set_code", ""),
                    collector_number=item.get("collector_number", ""),
                    finish=item.get("finish", "Regular"),
                    condition=item.get("condition", "NM"),
                    language=item.get("language", "English"),
                    scryfall_id=item.get("scryfall_id", ""),
                    notes=item.get("notes", ""),
                ))
    return {
        "source": str(payload.get("source") or "decklist"),
        "sections": sections,
        "warnings": [str(warning) for warning in payload.get("warnings", []) if str(warning).strip()],
        "enrich_scryfall": bool(payload.get("enrich_scryfall")),
        "merge": bool(payload.get("merge")),
        "source_url": str(payload.get("source_url") or ""),
    }


def encode_deck_missing_wants_payload(items):
    payload_items = []
    for item in items or []:
        price_usd = normalize_price_usd(item.get("price_usd", ""))
        payload_items.append({
            "key": str(item.get("key") or ""),
            "game": str(item.get("game") or "mtg"),
            "card_name": str(item.get("card_name") or ""),
            "set_name": str(item.get("set_name") or ""),
            "set_code": str(item.get("set_code") or ""),
            "collector_number": str(item.get("collector_number") or ""),
            "finish": str(item.get("finish") or ""),
            "language": str(item.get("language") or ""),
            "scryfall_id": str(item.get("scryfall_id") or ""),
            "image_url": str(item.get("image_url") or ""),
            "mana_cost": str(item.get("mana_cost") or ""),
            "type_line": str(item.get("type_line") or ""),
            "oracle_text": str(item.get("oracle_text") or ""),
            "rarity": str(item.get("rarity") or ""),
            "colors": str(item.get("colors") or ""),
            "color_identity": str(item.get("color_identity") or ""),
            "scryfall_uri": str(item.get("scryfall_uri") or ""),
            "price_usd": price_usd,
            "price_source": "scryfall" if price_usd else "",
            "quantity": max(1, clamp_quantity(item.get("quantity") or item.get("desired_quantity"), 1)),
        })
    return encode_deck_import_payload({"items": payload_items})


def decode_deck_missing_wants_payload(encoded_payload):
    try:
        raw = base64.urlsafe_b64decode(str(encoded_payload or "").encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Missing-card prompt expired. Please import the deck again.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Missing-card prompt expired. Please import the deck again.")
    items = []
    for item in payload["items"][:300]:
        if not isinstance(item, dict) or not str(item.get("card_name") or "").strip():
            continue
        clean = deck_import_item(
            item.get("card_name", ""),
            max(1, clamp_quantity(item.get("quantity"), 1)),
            game=item.get("game", "mtg"),
            set_name=item.get("set_name", ""),
            set_code=item.get("set_code", ""),
            collector_number=item.get("collector_number", ""),
            finish=item.get("finish", "Regular"),
            language=item.get("language", "English"),
            scryfall_id=item.get("scryfall_id", ""),
        )
        for field in SCRYFALL_COLLECTION_FIELDS:
            clean[field] = str(item.get(field) or "")[:5000]
        clean["price_usd"] = normalize_price_usd(item.get("price_usd", ""))
        clean["price_source"] = "scryfall" if clean["price_usd"] else ""
        clean["key"] = str(item.get("key") or "")
        items.append(clean)
    return items


def deck_missing_want_data(deck_group, item):
    data = {field: str(item.get(field) or "") for field in SCRYFALL_COLLECTION_FIELDS}
    price_usd = normalize_price_usd(item.get("price_usd", ""))
    data.update({
        "game": item.get("game", "mtg"),
        "card_name": item.get("card_name", ""),
        "set_name": item.get("set_name", ""),
        "set_code": item.get("set_code", ""),
        "collector_number": item.get("collector_number", ""),
        "desired_quantity": max(1, clamp_quantity(item.get("quantity"), 1)),
        "priority": "normal",
        "budget_cap_usd": "",
        "condition": "",
        "finish": item.get("finish", "") if item.get("finish") not in ("", "Regular") else "",
        "language": "",
        "price_usd": price_usd,
        "price_source": "scryfall" if price_usd else "",
        "preferred_printing_notes": "",
        "notes": f"Missing from deck: {deck_group['name']}",
        "is_public": 1,
        "lookup_on_save": "1" if item.get("scryfall_id") else "",
    })
    return data


def existing_group_want_match(user_id, wishlist_group_id, item):
    return row(
        """
        SELECT want_items.*
        FROM group_want_items
        JOIN want_items ON want_items.id = group_want_items.want_item_id
        WHERE group_want_items.group_id = ?
            AND want_items.user_id = ?
            AND want_items.game = ?
            AND want_items.card_name = ? COLLATE NOCASE
            AND want_items.set_code = ? COLLATE NOCASE
            AND want_items.collector_number = ? COLLATE NOCASE
        LIMIT 1
        """,
        (
            wishlist_group_id,
            user_id,
            item.get("game", "mtg"),
            item.get("card_name", ""),
            item.get("set_code", ""),
            item.get("collector_number", ""),
        ),
    )


def add_deck_missing_items_to_wishlist(user_id, deck_group_id, items, selected_keys, wishlist_group_id=0, new_group_name="", is_public=True):
    deck_group = user_group(user_id, deck_group_id)
    if not deck_group or deck_group["group_type"] != "deck":
        raise ValueError("Deck not found.")
    selected_keys = {str(key) for key in selected_keys if str(key).strip()}
    selected_items = [item for item in items if str(item.get("key") or "") in selected_keys]
    if not selected_items:
        raise ValueError("Choose at least one missing card to add.")
    try:
        wishlist_group_id = int(wishlist_group_id or 0)
    except (TypeError, ValueError):
        wishlist_group_id = 0
    wishlist_group = user_group(user_id, wishlist_group_id) if wishlist_group_id else None
    if wishlist_group and wishlist_group["group_type"] != "wishlist":
        raise ValueError("Choose a wishlist group.")
    if not wishlist_group:
        name = sanitize_text_input(new_group_name, max_length=80).strip() or f"{deck_group['name']} missing cards"
        wishlist_group_id = create_card_group(
            user_id,
            "wishlist",
            name,
            f"Cards missing from deck: {deck_group['name']}",
            is_public=is_public,
        )
        wishlist_group = user_group(user_id, wishlist_group_id)

    added = 0
    updated = 0
    for item in selected_items:
        desired_quantity = max(1, clamp_quantity(item.get("quantity"), 1))
        existing = existing_group_want_match(user_id, wishlist_group_id, item)
        if existing:
            execute(
                """
                UPDATE want_items
                SET desired_quantity = MAX(desired_quantity, ?), updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (desired_quantity, now_iso(), existing["id"], user_id),
            )
            updated += 1
            continue
        want_id = insert_want_item(user_id, deck_missing_want_data(deck_group, item))
        add_want_item_to_group(user_id, wishlist_group_id, want_id)
        added += 1
    return {
        "wishlist_group_id": wishlist_group_id,
        "wishlist_group_name": wishlist_group["name"] if wishlist_group else "",
        "added": added,
        "updated": updated,
        "selected": len(selected_items),
    }


def import_deck_group_csv(user_id, group_id, csv_bytes, source="auto", enrich_scryfall=True, merge=True, field_mapping=None):
    section_rows, warnings = normalize_csv_rows_by_section(
        csv_bytes,
        default_game="mtg",
        default_trade_quantity=0,
        field_mapping=field_mapping,
    )
    return import_deck_group_sections(
        user_id,
        group_id,
        section_rows,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=warnings,
    )


def import_deck_group_text(user_id, group_id, deck_text, source="decklist", enrich_scryfall=True, merge=True):
    section_rows, warnings = deck_import_sections_from_text(deck_text)
    return import_deck_group_sections(
        user_id,
        group_id,
        section_rows,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=warnings,
    )


def import_deck_group_url(user_id, group_id, source_url, enrich_scryfall=True, merge=True):
    _, section_rows, warnings = deck_import_sections_from_url(source_url)
    result = import_deck_group_sections(
        user_id,
        group_id,
        section_rows,
        source="url",
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=warnings,
    )
    result["source_url"] = source_url
    return result


def restore_collection_item_state(item_id, user_id, previous_state):
    state = load_record_state(previous_state)
    if not state:
        return 0
    existing = row("SELECT id FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user_id))
    if not existing:
        return 0
    assignments = []
    params = []
    for field in IMPORT_BATCH_COLLECTION_FIELDS:
        if field in state:
            assignments.append(f"{field} = ?")
            params.append(state[field])
    if not assignments:
        return 0
    params.extend([item_id, user_id])
    execute(
        f"UPDATE collection_items SET {', '.join(assignments)} WHERE id = ? AND user_id = ?",
        params,
    )
    return 1


def undo_collection_import_item(user_id, batch, item):
    target_id = int(item["target_id"] or 0)
    if not target_id:
        return 0
    if item["action"] == "inserted":
        existing = row("SELECT id FROM collection_items WHERE id = ? AND user_id = ?", (target_id, user_id))
        if not existing:
            return 0
        execute("DELETE FROM collection_items WHERE id = ? AND user_id = ?", (target_id, user_id))
        return 1
    if item["action"] == "updated":
        restored = restore_collection_item_state(target_id, user_id, item["previous_state"])
        execute(
            """
            DELETE FROM scryfall_enrichment_jobs
            WHERE collection_item_id = ?
                AND user_id = ?
                AND status IN ('pending', 'processing')
                AND created_at >= ?
            """,
            (target_id, user_id, batch["created_at"]),
        )
        return restored
    return 0


def restore_group_collection_item_state(user_id, previous_state):
    state = load_record_state(previous_state)
    if not state:
        return 0
    group_id = int(state.get("group_id") or 0)
    group = user_group(user_id, group_id)
    if not group:
        return 0
    existing = row("SELECT * FROM group_collection_items WHERE id = ? AND group_id = ?", (state.get("id"), group_id))
    if not existing:
        return 0
    execute(
        """
        UPDATE group_collection_items
        SET collection_item_id = ?, quantity = ?, created_at = ?, updated_at = ?
        WHERE id = ? AND group_id = ?
        """,
        (
            int(state.get("collection_item_id") or 0),
            max(1, int(state.get("quantity") or 1)),
            state.get("created_at") or now_iso(),
            state.get("updated_at") or now_iso(),
            int(state.get("id") or 0),
            group_id,
        ),
    )
    return 1


def undo_group_collection_import_item(user_id, item):
    target_id = int(item["target_id"] or 0)
    if not target_id:
        return 0
    found = row(
        """
        SELECT group_collection_items.*
        FROM group_collection_items
        JOIN card_groups ON card_groups.id = group_collection_items.group_id
        WHERE group_collection_items.id = ? AND card_groups.user_id = ?
        """,
        (target_id, user_id),
    )
    if item["action"] == "inserted":
        if not found:
            return 0
        execute("DELETE FROM group_collection_items WHERE id = ?", (target_id,))
        return 1
    if item["action"] == "updated":
        return restore_group_collection_item_state(user_id, item["previous_state"])
    return 0


def undo_import_batch(user_id, batch_id):
    batch = import_batch_for_user(user_id, batch_id)
    if not batch or batch["status"] != "applied":
        raise ValueError("That import cannot be undone.")
    batch_items = rows(
        """
        SELECT *
        FROM import_batch_items
        WHERE batch_id = ?
        ORDER BY id DESC
        """,
        (batch["id"],),
    )
    changed = 0
    for item in batch_items:
        if item["item_type"] == "collection_item":
            changed += undo_collection_import_item(user_id, batch, item)
        elif item["item_type"] == "group_collection_item":
            changed += undo_group_collection_import_item(user_id, item)
    update_import_batch(batch["id"], status="undone", summary={**import_batch_summary(batch), "undone_items": changed}, payload={}, undone=True)
    return {"batch_id": batch["id"], "undone_items": changed, "import_type": batch["import_type"], "group_id": batch["group_id"]}


from binderbridge import views as _views
_install_feature_module(_views)
from binderbridge import exports as _exports
_install_feature_module(_exports)

# Additional trade service functions live in binderbridge.trade_service.

class App(BaseHTTPRequestHandler):
    server_version = "BinderBridge/0.1"

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        user = None
        self._csrf_required = False
        self._request_path = path
        self._request_method = method
        try:
            query = sanitize_form_values(parse_qs(parsed.query, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS))
            user = self.current_user()
            self._csrf_required = bool(user and method == "POST" and not path.startswith("/api/"))
            if path.startswith("/static/"):
                return self.static_file(path)
            if path.startswith("/api/"):
                return self.api_dispatch(method, path, query)
            if path == "/login":
                return self.login(method, user)
            if path == "/login/2fa":
                return self.login_two_factor(method, user)
            if path == "/login/passkey/options":
                return self.login_passkey_options(method, user, query)
            if path == "/login/passkey":
                return self.login_passkey_complete(method, user)
            if path == "/register":
                return self.register(method, user, query)
            if path == "/logout" and method == "POST":
                if self._csrf_required:
                    self.parse_request_body()
                return self.logout()
            if not user:
                return self.redirect("/login")
            if self._csrf_required:
                self.parse_request_body()
            if path == "/":
                return self.html(render_dashboard(user))
            if path == "/account":
                return self.html(render_account(user))
            if path == "/account/export" and method == "GET":
                return self.account_export(user)
            if path == "/account/profile" and method == "POST":
                return self.account_profile(user)
            if path == "/account/password" and method == "POST":
                return self.account_password(user)
            if path == "/account/2fa/start" and method == "POST":
                return self.account_two_factor_start(user)
            if path == "/account/2fa/enable" and method == "POST":
                return self.account_two_factor_enable(user)
            if path == "/account/2fa/disable" and method == "POST":
                return self.account_two_factor_disable(user)
            if path == "/account/2fa/recovery-codes" and method == "POST":
                return self.account_two_factor_recovery_codes(user)
            if path == "/account/passkeys/register/options" and method == "GET":
                return self.account_passkey_register_options(user)
            if path == "/account/passkeys/register" and method == "POST":
                return self.account_passkey_register(user)
            if path.startswith("/account/passkeys/") and path.endswith("/delete") and method == "POST":
                return self.account_passkey_delete(user, path)
            if path == "/account/api-tokens" and method == "POST":
                return self.account_api_token_create(user)
            if path.startswith("/account/api-tokens/") and path.endswith("/revoke") and method == "POST":
                return self.account_api_token_revoke(user, path)
            if path == "/account/webhooks" and method == "POST":
                return self.account_webhook_create(user)
            if path.startswith("/account/webhooks/") and path.endswith("/delete") and method == "POST":
                return self.account_webhook_delete(user, path)
            if path.startswith("/account/webhooks/") and path.endswith("/test") and method == "POST":
                return self.account_webhook_test(user, path)
            if path == "/cleanup":
                return self.cleanup_page(user)
            if path == "/cleanup/collection" and method == "POST":
                return self.cleanup_collection(user)
            if path == "/cleanup/wants" and method == "POST":
                return self.cleanup_wants(user)
            if path == "/cleanup/audit":
                return self.condition_finish_audit_page(user, query)
            if path == "/cleanup/audit/update" and method == "POST":
                return self.condition_finish_audit_update(user)
            if path == "/cleanup/audit/update-all" and method == "POST":
                return self.condition_finish_audit_update_all(user)
            if path == "/cleanup/audit/normalize" and method == "POST":
                return self.condition_finish_audit_normalize(user)
            if path == "/cleanup/audit/normalize-all" and method == "POST":
                return self.condition_finish_audit_normalize_all(user)
            if path == "/admin":
                return self.admin_page(user)
            if path == "/admin/health":
                return self.admin_health_page(user)
            if path == "/admin/health/jobs/retry" and method == "POST":
                return self.admin_health_retry_jobs(user)
            if path == "/admin/health/notifications/replay" and method == "POST":
                return self.admin_health_replay_notifications(user)
            if path == "/admin/health/backups/check" and method == "POST":
                return self.admin_health_check_backups(user)
            if path == "/admin/health/scryfall/sync" and method == "POST":
                return self.admin_health_scryfall_sync(user)
            if path == "/admin/health/retention" and method == "POST":
                return self.admin_health_retention(user)
            if path == "/admin/jobs":
                return self.admin_jobs_page(user)
            if path == "/admin/logs":
                return self.admin_logs_page(user, query)
            if path == "/admin/disputes":
                return self.admin_disputes_page(user, query)
            if path.startswith("/admin/disputes/") and path.endswith("/update") and method == "POST":
                return self.admin_dispute_update(user, path)
            if path == "/admin/jobs/scryfall/retry" and method == "POST":
                return self.admin_job_retry_scryfall(user)
            if path == "/admin/jobs/prices/retry" and method == "POST":
                return self.admin_job_retry_price(user)
            if path == "/admin/jobs/scryfall-prices/retry" and method == "POST":
                return self.admin_job_retry_scryfall_prices(user)
            if path == "/admin/jobs/notifications/retry" and method == "POST":
                return self.admin_job_retry_notification(user)
            if path.startswith("/admin/jobs/imports/") and path.endswith("/undo") and method == "POST":
                return self.admin_job_undo_import(user, path)
            if path == "/admin/trade-policy" and method == "POST":
                return self.admin_trade_policy_settings(user)
            if path == "/admin/integration-policy" and method == "POST":
                return self.admin_integration_policy_settings(user)
            if path == "/admin/trust-settings" and method == "POST":
                return self.admin_trust_settings(user)
            if path == "/admin/trade-fairness" and method == "POST":
                return self.admin_trade_fairness_settings(user)
            if path == "/admin/registration-settings" and method == "POST":
                return self.admin_registration_settings(user)
            if path == "/admin/invites" and method == "POST":
                return self.admin_invite_create(user)
            if path.startswith("/admin/invites/") and path.endswith("/revoke") and method == "POST":
                return self.admin_invite_revoke(user, path)
            if path == "/admin/backups/create" and method == "POST":
                return self.admin_backup_create(user)
            if path == "/admin/backups/settings" and method == "POST":
                return self.admin_backup_settings(user)
            if path == "/admin/backups/run" and method == "POST":
                return self.admin_backup_run(user)
            if path == "/admin/backups/restore" and method == "POST":
                return self.admin_backup_restore(user)
            if path.startswith("/admin/user/") and method == "POST":
                return self.admin_user_action(user, path)
            if path == "/collection":
                return self.html(render_collection(user, query))
            if path == "/collection/stats":
                return self.html(render_collection_statistics(user))
            if path == "/collection/export" and method == "GET":
                return self.collection_export(user, query)
            if path == "/collection/bulk-update" and method == "POST":
                return self.collection_bulk_update(user)
            if path == "/collection/update-all" and method == "POST":
                return self.collection_update_all(user)
            if path == "/collection/bulk-delete" and method == "POST":
                return self.collection_bulk_delete(user)
            if path == "/collection/delete-all" and method == "POST":
                return self.collection_delete_all(user)
            if path == "/collection/new":
                return self.collection_new(method, user)
            if path.startswith("/collection/"):
                return self.collection_item(method, user, path)
            if path == "/import/scryfall-sync" and method == "POST":
                return self.import_scryfall_sync(user)
            if path == "/prices/refresh" and method == "POST":
                return self.prices_refresh(user)
            if path.startswith("/imports/") and path.endswith("/undo") and method == "POST":
                return self.import_undo(user, path)
            if path == "/import/presets" and method == "POST":
                return self.csv_import_mapping_preset_create(user)
            if path.startswith("/import/presets/") and path.endswith("/delete") and method == "POST":
                return self.csv_import_mapping_preset_delete(user, path)
            if path == "/import":
                return self.collection_import(method, user)
            if path == "/wants":
                return self.html(render_wants(user, query=query))
            if path == "/wants/export" and method == "GET":
                return self.wants_export(user)
            if path == "/wants/new":
                return self.want_new(user) if method == "POST" else self.redirect("/wants")
            if path.startswith("/wants/") and path.endswith("/edit"):
                return self.want_edit(method, user, path)
            if path.startswith("/wants/") and path.endswith("/delete") and method == "POST":
                return self.want_delete(user, path)
            if path == "/groups":
                return self.groups_page(method, user, query)
            if path.startswith("/groups/"):
                return self.group_action(method, user, path, query)
            if path == "/browse":
                return self.html(render_browse(user, query))
            if path == "/members":
                return self.redirect("/browse")
            if path.startswith("/members/"):
                return self.member_detail(user, path, query)
            if path == "/notifications":
                return self.html(render_notifications(user))
            if path == "/notifications/read-all" and method == "POST":
                mark_all_notifications_read(user["id"])
                return self.redirect("/notifications")
            if path == "/notifications/delete-read" and method == "POST":
                delete_read_notifications(user["id"])
                return self.redirect("/notifications")
            if path == "/notifications/delete-all" and method == "POST":
                delete_all_notifications(user["id"])
                return self.redirect("/notifications")
            if path.startswith("/notifications/") and path.endswith("/read") and method == "POST":
                return self.notification_action(user, path)
            if path.startswith("/notifications/") and path.endswith("/delete") and method == "POST":
                return self.notification_action(user, path)
            if path == "/trades":
                return self.html(render_trades(user))
            if path == "/trades/matches":
                return self.html(render_trade_matchmaking(user, query))
            if path == "/trades/new":
                return self.trade_new(method, user, query)
            if path.startswith("/trades/"):
                return self.trade_action(method, user, path)
            return self.not_found(user)
        except RequestTooLargeError as exc:
            content = f"""
            <section class="panel centered-state">
                <h1>Request too large</h1>
                <p class="muted">{e(exc)}</p>
                <a class="button primary" href="/">Go home</a>
            </section>
            """
            return self.html(render_layout(user, "Request too large", content), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except CsrfError as exc:
            content = f"""
            <section class="panel centered-state">
                <h1>Security check failed</h1>
                <p class="muted">{e(exc)}</p>
                <a class="button primary" href="/">Go home</a>
            </section>
            """
            return self.html(render_layout(user, "Security check failed", content), HTTPStatus.FORBIDDEN)
        except RateLimitError as exc:
            content = f"""
            <section class="panel centered-state">
                <h1>Slow down a moment</h1>
                <p class="muted">{e(exc)}</p>
                <a class="button primary" href="/">Go home</a>
            </section>
            """
            return self.html(render_layout(user, "Rate limited", content), HTTPStatus.TOO_MANY_REQUESTS)
        except Exception as exc:
            return self.error_page(user, exc)

    def current_user(self):
        cookie = SimpleCookie(self.headers.get("Cookie"))
        token = cookie.get(SESSION_COOKIE)
        return get_user_by_session(token.value) if token else None

    def current_session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie"))
        token = cookie.get(SESSION_COOKIE)
        return token.value if token else None

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
        try:
            return self.client_address[0]
        except (AttributeError, TypeError, IndexError):
            return ""

    def rate_limit_key(self, bucket, extra=""):
        return f"{self.client_ip()}:{extra}" if extra else self.client_ip()

    def enforce_rate_limit(self, bucket, key=None, message="Too many requests. Try again shortly."):
        if not rate_limit_allowed(bucket, key if key is not None else self.rate_limit_key(bucket)):
            raise RateLimitError(message)

    def validate_csrf_form(self, form):
        if not getattr(self, "_csrf_required", False):
            return
        if not csrf_form_valid(form, self.current_session_token()):
            raise CsrfError("Refresh the page and try again.")
        form.pop(CSRF_FIELD_NAME, None)

    def parse_request_body(self):
        if getattr(self, "_body_parsed", False):
            return
        self._body_parsed = True
        self._cached_form = {}
        self._cached_files = {}
        content_type = self.headers.get("Content-Type", "")
        length = request_content_length(self.headers)
        if "multipart/form-data" in content_type:
            body = self.rfile.read(length)
            message = BytesParser(policy=email_policy).parsebytes(
                b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
            )
            fields = {}
            files = {}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                name = sanitize_text_input(name, max_length=200)
                if not name:
                    continue
                payload = part.get_payload(decode=True) or b""
                filename = part.get_filename()
                if filename:
                    if len(payload) > MAX_UPLOAD_BYTES:
                        raise RequestTooLargeError("Uploaded file is too large.")
                    files[name] = {
                        "filename": safe_download_filename(filename, default="upload"),
                        "content": payload,
                        "content_type": part.get_content_type(),
                    }
                else:
                    charset = part.get_content_charset() or "utf-8"
                    fields.setdefault(name, []).append(payload.decode(charset, errors="replace"))
            form = sanitize_form_values(fields)
            self._cached_files = files
        else:
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            form = sanitize_form_values(parse_qs(raw, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS))
        self.validate_csrf_form(form)
        self._cached_form = form

    def read_form(self):
        self.parse_request_body()
        return self._cached_form

    def read_multipart_form(self):
        self.parse_request_body()
        return self._cached_form, self._cached_files

    def html(self, body, status=HTTPStatus.OK, headers=None):
        body = inject_csrf_tokens(body, self.current_session_token())
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_security_headers()
        if headers:
            for key, value in headers:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def binary(self, data, content_type, filename, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{safe_download_filename(filename)}"')
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", safe_local_redirect_path(location, default="/"))
        self.send_security_headers()
        self.end_headers()

    def send_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data: https://cards.scryfall.io https://*.scryfall.io; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'",
        )

    def static_file(self, path):
        safe_name = unquote(path.removeprefix("/static/")).replace("/", os.sep).replace("\\", os.sep)
        file_path = (STATIC_DIR / safe_name).resolve()
        static_root = STATIC_DIR.resolve()
        try:
            file_path.relative_to(static_root)
        except ValueError:
            return self.send_error(HTTPStatus.NOT_FOUND)
        if not file_path.exists():
            return self.send_error(HTTPStatus.NOT_FOUND)
        content_type = "text/css" if file_path.suffix == ".css" else "text/plain"
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)















































    def not_found(self, user=None):
        content = """
        <section class="panel centered-state">
            <h1>Not found</h1>
            <p class="muted">That page is not available.</p>
            <a class="button primary" href="/">Go home</a>
        </section>
        """
        self.html(render_layout(user, "Not found", content), HTTPStatus.NOT_FOUND)

    def error_page(self, user, exc):
        content = f"""
        <section class="panel centered-state">
            <h1>Something broke</h1>
            <p class="muted">{e(exc)}</p>
            <a class="button primary" href="/">Go home</a>
        </section>
        """
        self.html(render_layout(user, "Error", content, notice="The app hit an unexpected error.", status="error"), HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format, *args):
        write_log_message(f"{self.address_string()} - {format % args}")

from binderbridge import account_routes as _account_routes
_install_feature_module(_account_routes)
for _account_route_name in _account_routes.ACCOUNT_ROUTE_METHODS:
    setattr(App, _account_route_name, globals()[_account_route_name])

from binderbridge import group_routes as _group_routes
_install_feature_module(_group_routes)
for _group_route_name in _group_routes.GROUP_ROUTE_METHODS:
    setattr(App, _group_route_name, globals()[_group_route_name])

from binderbridge import collection_routes as _collection_routes
_install_feature_module(_collection_routes)
for _collection_route_name in _collection_routes.COLLECTION_ROUTE_METHODS:
    setattr(App, _collection_route_name, globals()[_collection_route_name])

for _api_route_name in _api.API_ROUTE_METHODS:
    setattr(App, _api_route_name, globals()[_api_route_name])

from binderbridge import trade_service as _trade_service
_install_feature_module(_trade_service)
from binderbridge import trade_routes as _trade_routes
_install_feature_module(_trade_routes)
for _trade_route_name in _trade_routes.TRADE_ROUTE_METHODS:
    setattr(App, _trade_route_name, globals()[_trade_route_name])

from binderbridge import admin_routes as _admin_routes
_install_feature_module(_admin_routes)
for _admin_route_name in _admin_routes.__all__:
    setattr(App, _admin_route_name, globals()[_admin_route_name])

def seed_demo_data():
    if not config_bool("BINDERBRIDGE_DEMO", default=False, section="app", key="demo"):
        return
    if row("SELECT COUNT(*) AS count FROM users")["count"]:
        return
    alice_id = create_user("alice", "password123", "Alice")
    bob_id = create_user("bob", "password123", "Bob")
    samples = [
        (alice_id, "Sol Ring", "Commander Masters", "703", 4, 2, "NM", "Regular"),
        (alice_id, "Counterspell", "Dominaria Remastered", "45", 3, 1, "LP", "Foil"),
        (bob_id, "Lightning Bolt", "Secret Lair", "182", 4, 2, "NM", "Foil"),
        (bob_id, "Rhystic Study", "Wilds of Eldraine", "63", 1, 1, "LP", "Regular"),
    ]
    for user_id, name, set_name, number, qty, trade_qty, condition, finish in samples:
        execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, collector_number, finish, condition, language, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', ?, ?, ?, ?, ?, 'English', ?, ?, ?, ?)
            """,
            (user_id, name, set_name, number, finish, condition, qty, trade_qty, now_iso(), now_iso()),
        )
    execute(
        """
        INSERT INTO want_items (user_id, game, card_name, desired_quantity, notes, created_at, updated_at)
        VALUES (?, 'mtg', 'Cyclonic Rift', 1, 'Commander copy wanted', ?, ?)
        """,
        (alice_id, now_iso(), now_iso()),
    )
    execute(
        """
        INSERT INTO want_items (user_id, game, card_name, desired_quantity, notes, created_at, updated_at)
        VALUES (?, 'mtg', 'Dockside Extortionist', 1, '', ?, ?)
        """,
        (bob_id, now_iso(), now_iso()),
    )


def main():
    init_db()
    seed_demo_data()
    start_scryfall_enrichment_worker()
    start_scryfall_price_refresh_worker()
    start_automatic_backup_worker()
    start_webhook_delivery_worker()
    start_notification_worker()
    server = ThreadingHTTPServer((HOST, PORT), App)
    write_log_message(f"{APP_NAME} running at http://{HOST}:{PORT}", stream=sys.stdout)
    write_log_message(f"Database: {DB_PATH}", stream=sys.stdout)
    server.serve_forever()


_wire_feature_modules()
sys.modules[__name__].__class__ = _AppModule

if __name__ == "__main__":
    main()

