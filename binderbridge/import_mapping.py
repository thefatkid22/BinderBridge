"""CSV mapping, saved presets, and row normalization for BinderBridge imports."""

import csv
import io
import json
import re
import sqlite3

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
    "condition_notes": ("condition notes", "condition details", "condition description"),
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
    ("condition_notes", "Condition details"),
    ("language", "Language"),
    ("scryfall_id", "Scryfall ID"),
    ("tcgplayer_product_id", "TCGplayer product ID"),
    ("cardmarket_product_id", "Cardmarket product ID"),
    ("cardkingdom_sku", "Card Kingdom SKU"),
    ("notes", "Notes"),
    ("section", "Deck section"),
)

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

def clamp_quantity(value, default=0, max_value=None):
    limit = MAX_CARD_QUANTITY if max_value is None else max_value
    return min(parse_nonnegative_int(value, default), limit)

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

def normalize_csv_rows(csv_bytes, default_game="mtg", default_trade_quantity=0, field_mapping=None, source="auto", target="collection"):
    text = decode_csv(csv_bytes)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file needs a header row.")
    _resolved_source, field_mapping = csv_import_profile_mapping(source, reader.fieldnames, target, field_mapping)
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
            "condition_notes": csv_value(csv_row, "condition_notes", field_mapping)[:1000],
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

__all__ = [
    'CSV_HEADER_ALIASES',
    'CSV_IMPORT_MAPPING_FIELDS',
    'normalize_header',
    'CSV_ALIAS_INDEX',
    'csv_import_mapping_field_keys',
    'normalize_csv_import_target',
    'normalize_csv_import_mapping',
    'csv_import_mapping_json',
    'csv_import_mapping_from_json',
    'csv_row_value_for_header',
    'csv_value',
    'csv_import_preset_rows_for_user',
    'csv_import_preset_for_user',
    'csv_import_mapping_for_user',
    'csv_import_preset_display_name',
    'save_csv_import_mapping_preset',
    'delete_csv_import_mapping_preset',
    'csv_import_mapping_from_form',
    'decode_csv',
    'parse_nonnegative_int',
    'clamp_quantity',
    'normalize_condition',
    'normalize_finish',
    'normalize_language',
    'normalize_game',
    'parse_trade_quantity',
    'normalize_csv_rows',
]
