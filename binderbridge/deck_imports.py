"""Deck-list import parsing and URL fetching helpers."""

import csv
import html
import io
import ipaddress
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DECK_IMPORT_MAIN_SECTION = "main"
DECK_IMPORT_DEFAULT_SECTIONS = {"main", "sideboard"}
DECK_IMPORT_SECTION_LABELS = {
    "main": "Main deck",
    "sideboard": "Sideboard",
    "maybeboard": "Maybeboard",
    "tokens": "Tokens",
    "considering": "Considering",
}
DECK_IMPORT_SECTION_ALIASES = {
    "deck": "main",
    "main": "main",
    "mainboard": "main",
    "commander": "main",
    "commanders": "main",
    "companion": "main",
    "companions": "main",
    "sideboard": "sideboard",
    "side": "sideboard",
    "sb": "sideboard",
    "maybeboard": "maybeboard",
    "maybe": "maybeboard",
    "considering": "considering",
    "consider": "considering",
    "tokens": "tokens",
    "token": "tokens",
}
DECK_IMPORT_REVIEW_SECTION_OPTIONS = [
    ("sideboard", "Sideboard", True),
    ("maybeboard", "Maybeboard", False),
    ("tokens", "Tokens", False),
    ("considering", "Considering", False),
]
DECK_IMPORT_SECTION_PREFIX_PATTERN = "|".join(
    sorted(
        (
            "Sideboard",
            "Mainboard",
            "Maybeboard",
            "Considering",
            "Commander",
            "Commanders",
            "Companion",
            "Companions",
            "Tokens",
            "Token",
            "Deck",
            "Main",
            "Maybe",
            "Consider",
            "Side",
            "SB",
        ),
        key=len,
        reverse=True,
    )
)


def csv_value_any(csv_row, aliases):
    lookup = {normalize_header(key): value for key, value in csv_row.items()}
    for alias in aliases:
        value = lookup.get(normalize_header(alias))
        if value not in (None, ""):
            return str(value).strip()
    return ""


def csv_row_deck_section(csv_row, field_mapping=None):
    section = csv_value(csv_row, "section", field_mapping) if field_mapping else ""
    if not section:
        section = csv_value_any(csv_row, ("section", "board", "category", "categories", "deck section"))
    return normalize_deck_import_section(section) or DECK_IMPORT_MAIN_SECTION


def normalize_csv_rows_by_section(csv_bytes, default_game="mtg", default_trade_quantity=0, field_mapping=None):
    text = decode_csv(csv_bytes)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file needs a header row.")
    field_mapping = normalize_csv_import_mapping(field_mapping)
    section_rows = {}
    warnings = []
    for row_index, csv_row in enumerate(reader, start=2):
        if row_index - 1 > MAX_CSV_ROWS:
            raise ValueError(f"CSV imports are limited to {MAX_CSV_ROWS} rows per upload.")
        row_buffer = io.StringIO()
        writer = csv.DictWriter(row_buffer, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerow(csv_row)
        try:
            items, row_warnings = normalize_csv_rows(
                row_buffer.getvalue().encode("utf-8"),
                default_game=default_game,
                default_trade_quantity=default_trade_quantity,
                field_mapping=field_mapping,
            )
        except ValueError:
            warnings.append(f"Row {row_index}: missing card name.")
            continue
        warnings.extend(row_warnings)
        section = csv_row_deck_section(csv_row, field_mapping)
        section_rows.setdefault(section, []).extend(items)
    if not section_rows:
        raise ValueError("No importable collection rows were found.")
    return section_rows, warnings


def deck_import_item(
    card_name,
    quantity=1,
    game="mtg",
    set_name="",
    set_code="",
    collector_number="",
    finish="Regular",
    condition="NM",
    language="English",
    scryfall_id="",
    notes="",
):
    normalized_game = sanitize_text_input(game or "mtg", max_length=20).strip()
    return {
        "game": normalized_game if normalized_game in dict(CARD_GAMES) else "other",
        "card_name": sanitize_text_input(card_name, max_length=160).strip(),
        "set_name": sanitize_text_input(set_name, max_length=120).strip(),
        "set_code": sanitize_text_input(set_code, max_length=20).strip().upper(),
        "collector_number": sanitize_text_input(collector_number, max_length=40).strip().lstrip("#"),
        "finish": normalize_finish(finish),
        "condition": normalize_condition(condition),
        "language": normalize_language(language),
        "quantity": max(1, clamp_quantity(quantity, 1)),
        "quantity_for_trade": 0,
        "notes": sanitize_text_input(notes, max_length=1000).strip(),
        "scryfall_id": sanitize_text_input(scryfall_id, max_length=80).strip(),
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
        "tcgplayer_product_id": "",
        "cardmarket_product_id": "",
        "cardkingdom_sku": "",
    }


def decklist_section_key(line):
    text = str(line or "").strip().strip(":")
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\b\d+\b", "", text)
    text = text.strip(" :-#")
    return normalize_header(text)


def normalize_deck_import_section(value):
    return DECK_IMPORT_SECTION_ALIASES.get(decklist_section_key(value), "")


def deck_import_section_label(section):
    return DECK_IMPORT_SECTION_LABELS.get(section, str(section or "").title())


def deck_import_section_count(items):
    return sum(int(item.get("quantity", 0) or 0) for item in items)


def deck_import_section_counts(section_rows):
    return {
        section: deck_import_section_count(items)
        for section, items in section_rows.items()
        if items
    }


def deck_import_review_sections(section_rows):
    counts = deck_import_section_counts(section_rows)
    return [
        (section, label, default_checked, counts.get(section, 0), len(section_rows.get(section, [])))
        for section, label, default_checked in DECK_IMPORT_REVIEW_SECTION_OPTIONS
        if counts.get(section, 0) > 0
    ]


def deck_import_sections_need_review(section_rows):
    return bool(deck_import_review_sections(section_rows))


def deck_import_items_from_sections(section_rows, included_sections=None):
    included = set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS)
    included.add(DECK_IMPORT_MAIN_SECTION)
    items = []
    for section in ("main", "sideboard", "maybeboard", "tokens", "considering"):
        if section in included:
            items.extend(section_rows.get(section, []))
    for section, section_items in section_rows.items():
        if section not in DECK_IMPORT_SECTION_LABELS and section in included:
            items.extend(section_items)
    if not items:
        raise ValueError("Choose at least one import section with cards.")
    return items


def deck_import_exclusion_warnings(section_rows, included_sections):
    included = set(included_sections)
    warnings = []
    for section, label, _, quantity, _ in deck_import_review_sections(section_rows):
        if section not in included:
            warnings.append(f"Excluded {quantity} {label.lower()} card{'s' if quantity != 1 else ''}.")
    return warnings


def decklist_line_section_prefix(line):
    match = re.match(
        rf"^\s*(?P<section>{DECK_IMPORT_SECTION_PREFIX_PATTERN})\s*[:\-]\s*(?=\d)",
        str(line or ""),
        flags=re.IGNORECASE,
    )
    return normalize_deck_import_section(match.group("section")) if match else ""


def clean_decklist_card_text(value):
    text = html.unescape(str(value or "")).strip()
    text = re.sub(r"\s+\^[^^]*\^", "", text)
    text = re.sub(r"\s+\*[^*]+\*", "", text)
    text = re.sub(r"\s+\[[^\]]*(?:category|tag|label)[^\]]*\]\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def parse_decklist_line(line):
    working = html.unescape(str(line or "")).strip()
    if not working:
        return None
    working = re.sub(
        rf"^\s*(?:{DECK_IMPORT_SECTION_PREFIX_PATTERN})\s*[:\-]\s*",
        "",
        working,
        flags=re.IGNORECASE,
    )
    match = re.match(r"^(?P<quantity>\d+)\s*(?:x|X)?\.?\s+(?P<card>.+?)\s*$", working)
    if not match:
        return None
    quantity = clamp_quantity(match.group("quantity"), 1)
    if quantity <= 0:
        return None

    card_text = clean_decklist_card_text(match.group("card"))
    set_name = ""
    set_code = ""
    collector_number = ""

    compact_metadata = re.match(
        r"^(?P<name>.+?)\s+\[(?P<set>[A-Za-z0-9]{2,8})[-: ]#?(?P<number>[A-Za-z0-9\-/.]+)\](?:\s+.*)?$",
        card_text,
    )
    normal_metadata = re.match(
        r"^(?P<name>.+?)\s+[\[(](?P<set>[A-Za-z0-9]{2,8})[\])]\s*#?(?P<number>[A-Za-z0-9\-/.]+)?(?:\s+.*)?$",
        card_text,
    )
    metadata = compact_metadata or normal_metadata
    if metadata:
        card_name = metadata.group("name").strip()
        set_code = metadata.group("set").strip().upper()
        collector_number = (metadata.groupdict().get("number") or "").strip().lstrip("#")
    else:
        card_name = re.sub(r"\s+#(?!\d)\S.*$", "", card_text).strip()

    card_name = re.sub(r"\s+\((?:commander|sideboard|foil|etched)\)\s*$", "", card_name, flags=re.IGNORECASE).strip()
    if not card_name:
        return None
    return deck_import_item(card_name, quantity, set_name=set_name, set_code=set_code, collector_number=collector_number)


def deck_import_sections_from_text(deck_text):
    text = str(deck_text or "")
    if not text.strip():
        raise ValueError("Deck list is empty.")
    section_rows = {section: [] for section in DECK_IMPORT_SECTION_LABELS}
    warnings = []
    current_section = DECK_IMPORT_MAIN_SECTION
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        starts_with_quantity = re.match(
            rf"^\s*(?:(?:{DECK_IMPORT_SECTION_PREFIX_PATTERN})\s*[:\-]\s*)?\d+",
            line,
            flags=re.IGNORECASE,
        )
        if not starts_with_quantity:
            section = normalize_deck_import_section(line)
            if section:
                current_section = section
                continue
            continue
        item = parse_decklist_line(line)
        if item:
            section_rows.setdefault(decklist_line_section_prefix(line) or current_section, []).append(item)
        else:
            warnings.append(f"Line {line_number}: skipped an unrecognized deck-list row.")
    return section_rows, warnings


def normalize_decklist_rows(deck_text, included_sections=None):
    section_rows, warnings = deck_import_sections_from_text(deck_text)
    normalized_rows = deck_import_items_from_sections(section_rows, included_sections)
    for warning in deck_import_exclusion_warnings(section_rows, set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS) | {DECK_IMPORT_MAIN_SECTION}):
        warnings.append(warning)
    if not normalized_rows:
        raise ValueError("No importable deck-list rows were found.")
    return normalized_rows, warnings


def scalar_value(mapping, *keys):
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def nested_mapping(mapping, *keys):
    if not isinstance(mapping, dict):
        return {}
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, dict):
            return value
    return {}


def external_deck_category_names(entry):
    categories = entry.get("categories", []) if isinstance(entry, dict) else []
    if isinstance(categories, dict):
        categories = categories.values()
    if isinstance(categories, str):
        categories = [categories]
    names = []
    for category in categories if isinstance(categories, list) else []:
        if isinstance(category, dict):
            names.append(scalar_value(category, "name", "label", "category"))
        else:
            names.append(str(category or "").strip())
    return [name for name in names if name]


def external_deck_entry_section(entry, board_section="main"):
    if not isinstance(entry, dict):
        return DECK_IMPORT_MAIN_SECTION
    if entry.get("isToken") or entry.get("token"):
        return "tokens"
    if entry.get("maybe") or entry.get("isMaybeboard"):
        return "maybeboard"
    for category_name in external_deck_category_names(entry):
        section = normalize_deck_import_section(category_name)
        if section:
            return section
    if entry.get("isOutOfDeck") or entry.get("outOfDeck"):
        return "maybeboard"
    return normalize_deck_import_section(board_section) or DECK_IMPORT_MAIN_SECTION


def deck_item_from_external_entry(name_hint, entry):
    if not isinstance(entry, dict):
        return None
    card = nested_mapping(entry, "card", "printing", "cardModel")
    oracle = nested_mapping(card, "oracleCard", "oracle", "front")
    edition = nested_mapping(card, "edition", "set", "expansion")
    quantity = clamp_quantity(scalar_value(entry, "quantity", "qty", "count"), 1)
    if quantity <= 0:
        return None
    card_name = (
        str(name_hint or "").strip()
        or scalar_value(entry, "cardName", "name", "displayName")
        or scalar_value(card, "name", "cardName", "displayName")
        or scalar_value(oracle, "name", "cardName")
    )
    if not card_name:
        return None
    set_name = scalar_value(entry, "setName", "set_name") or scalar_value(card, "set_name", "setName") or scalar_value(edition, "name")
    set_code = (
        scalar_value(entry, "setCode", "set_code", "editionCode")
        or scalar_value(card, "set", "setCode", "set_code", "editionCode")
        or scalar_value(edition, "editioncode", "editionCode", "code", "setCode")
    )
    collector_number = (
        scalar_value(entry, "collectorNumber", "collector_number", "cn", "number")
        or scalar_value(card, "collector_number", "collectorNumber", "cn", "number")
    )
    scryfall_id = scalar_value(entry, "scryfall_id", "scryfallId") or scalar_value(card, "scryfall_id", "scryfallId")
    finish = "Foil" if entry.get("foil") or entry.get("isFoil") else "Regular"
    return deck_import_item(
        card_name,
        quantity,
        set_name=set_name,
        set_code=set_code,
        collector_number=collector_number,
        finish=finish,
        scryfall_id=scryfall_id,
    )


def decklist_sections_from_moxfield_json(data):
    section_rows = {section: [] for section in DECK_IMPORT_SECTION_LABELS}
    warnings = []
    section_map = {
        "commanders": "main",
        "companions": "main",
        "mainboard": "main",
        "sideboard": "sideboard",
        "maybeboard": "maybeboard",
        "considering": "considering",
        "tokens": "tokens",
    }
    for board_name, section in section_map.items():
        board = data.get(board_name, {}) if isinstance(data, dict) else {}
        if isinstance(board, dict):
            for card_name, entry in board.items():
                item = deck_item_from_external_entry(card_name, entry)
                if item:
                    section_rows.setdefault(external_deck_entry_section(entry, section), []).append(item)
        elif isinstance(board, list):
            for entry in board:
                item = deck_item_from_external_entry("", entry)
                if item:
                    section_rows.setdefault(external_deck_entry_section(entry, section), []).append(item)
    return section_rows, warnings


def decklist_items_from_moxfield_json(data, included_sections=None):
    section_rows, warnings = decklist_sections_from_moxfield_json(data)
    items = deck_import_items_from_sections(section_rows, included_sections)
    warnings.extend(deck_import_exclusion_warnings(section_rows, set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS) | {DECK_IMPORT_MAIN_SECTION}))
    return items, warnings


def decklist_sections_from_archidekt_json(data):
    section_rows = {section: [] for section in DECK_IMPORT_SECTION_LABELS}
    warnings = []
    cards = []
    if isinstance(data, dict):
        if isinstance(data.get("cards"), list):
            cards = data["cards"]
        elif isinstance(data.get("deck"), dict) and isinstance(data["deck"].get("cards"), list):
            cards = data["deck"]["cards"]
    elif isinstance(data, list):
        cards = data
    for entry in cards:
        item = deck_item_from_external_entry("", entry)
        if item:
            section_rows.setdefault(external_deck_entry_section(entry), []).append(item)
    return section_rows, warnings


def decklist_items_from_archidekt_json(data, included_sections=None):
    section_rows, warnings = decklist_sections_from_archidekt_json(data)
    items = deck_import_items_from_sections(section_rows, included_sections)
    warnings.extend(deck_import_exclusion_warnings(section_rows, set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS) | {DECK_IMPORT_MAIN_SECTION}))
    return items, warnings


def decklist_sections_from_json(data):
    extractors = (decklist_sections_from_moxfield_json, decklist_sections_from_archidekt_json)
    collected_warnings = []
    for extractor in extractors:
        section_rows, warnings = extractor(data)
        collected_warnings.extend(warnings)
        if any(section_rows.values()):
            return section_rows, collected_warnings
    raise ValueError("The deck URL returned JSON, but no supported card list was found.")


def decklist_items_from_json(data, included_sections=None):
    section_rows, warnings = decklist_sections_from_json(data)
    items = deck_import_items_from_sections(section_rows, included_sections)
    warnings.extend(deck_import_exclusion_warnings(section_rows, set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS) | {DECK_IMPORT_MAIN_SECTION}))
    return items, warnings


def deck_text_from_html(html_text):
    text = re.sub(r"(?is)<(script|style).*?</\1>", "\n", str(html_text or ""))
    text = re.sub(r"(?i)<\s*(br|/li|/tr|/p|/div|h[1-6])[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def deck_import_host_is_blocked(host):
    host = str(host or "").strip().strip("[]").lower()
    if not host or host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local or address.is_reserved or address.is_multicast


def validate_deck_import_url(source_url):
    parsed = urlparse(str(source_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter a public http or https deck-list URL.")
    if deck_import_host_is_blocked(parsed.hostname):
        raise ValueError("Deck URL imports only allow public deck-building sites.")
    return parsed


def deck_import_candidate_urls(source_url):
    parsed = validate_deck_import_url(source_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    candidates = []
    if host.endswith("moxfield.com"):
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "decks":
            public_id = parts[1]
            candidates.append(f"https://api2.moxfield.com/v3/decks/all/{public_id}")
            candidates.append(f"https://api.moxfield.com/v2/decks/all/{public_id}")
    if host.endswith("archidekt.com"):
        match = re.search(r"/decks/(?P<id>\d+)", path)
        if match:
            candidates.append(f"https://archidekt.com/api/decks/{match.group('id')}/")
    candidates.append(str(source_url).strip())
    return candidates


def fetch_deck_import_url(source_url):
    validate_deck_import_url(source_url)
    request = Request(
        source_url,
        headers={"User-Agent": SCRYFALL_USER_AGENT, "Accept": DECK_IMPORT_ACCEPT},
    )
    try:
        with urlopen(request, timeout=15) as response:
            content = response.read(DECK_IMPORT_MAX_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        raise ValueError(f"Deck URL returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise ValueError(f"Deck URL could not be fetched: {exc.reason}") from exc
    if len(content) > DECK_IMPORT_MAX_BYTES:
        raise ValueError("Deck URL response was too large to import.")
    return content_type, content


def deck_sections_from_url_content(source_url, content_type, content):
    content_type = str(content_type or "").lower()
    parsed = urlparse(source_url)
    path = (parsed.path or "").lower()
    text = decode_csv(content)
    if "json" in content_type or text.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Deck URL returned invalid JSON.") from exc
        return decklist_sections_from_json(data)
    if "csv" in content_type or path.endswith(".csv"):
        return normalize_csv_rows_by_section(content, default_game="mtg", default_trade_quantity=0)
    if "<html" in text[:1000].lower() or "<!doctype html" in text[:1000].lower():
        text = deck_text_from_html(text)
    return deck_import_sections_from_text(text)


def rows_from_deck_url_content(source_url, content_type, content, included_sections=None):
    section_rows, warnings = deck_sections_from_url_content(source_url, content_type, content)
    items = deck_import_items_from_sections(section_rows, included_sections)
    warnings.extend(deck_import_exclusion_warnings(section_rows, set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS) | {DECK_IMPORT_MAIN_SECTION}))
    return items, warnings


def deck_import_sections_from_url(source_url):
    errors = []
    for candidate_url in deck_import_candidate_urls(source_url):
        try:
            content_type, content = fetch_deck_import_url(candidate_url)
            section_rows, warnings = deck_sections_from_url_content(candidate_url, content_type, content)
            return candidate_url, section_rows, warnings
        except ValueError as exc:
            errors.append(str(exc))
    detail = f" Last error: {errors[-1]}" if errors else ""
    raise ValueError(f"Deck URL could not be imported. Paste a plain text export or upload a CSV if the site blocks automated exports.{detail}")


__all__ = [
    "DECK_IMPORT_MAIN_SECTION",
    "DECK_IMPORT_DEFAULT_SECTIONS",
    "DECK_IMPORT_SECTION_LABELS",
    "DECK_IMPORT_SECTION_ALIASES",
    "DECK_IMPORT_REVIEW_SECTION_OPTIONS",
    "DECK_IMPORT_SECTION_PREFIX_PATTERN",
    "csv_value_any",
    "csv_row_deck_section",
    "normalize_csv_rows_by_section",
    "deck_import_item",
    "decklist_section_key",
    "normalize_deck_import_section",
    "deck_import_section_label",
    "deck_import_section_count",
    "deck_import_section_counts",
    "deck_import_review_sections",
    "deck_import_sections_need_review",
    "deck_import_items_from_sections",
    "deck_import_exclusion_warnings",
    "decklist_line_section_prefix",
    "clean_decklist_card_text",
    "parse_decklist_line",
    "deck_import_sections_from_text",
    "normalize_decklist_rows",
    "scalar_value",
    "nested_mapping",
    "external_deck_category_names",
    "external_deck_entry_section",
    "deck_item_from_external_entry",
    "decklist_sections_from_moxfield_json",
    "decklist_items_from_moxfield_json",
    "decklist_sections_from_archidekt_json",
    "decklist_items_from_archidekt_json",
    "decklist_sections_from_json",
    "decklist_items_from_json",
    "deck_text_from_html",
    "deck_import_host_is_blocked",
    "validate_deck_import_url",
    "deck_import_candidate_urls",
    "fetch_deck_import_url",
    "deck_sections_from_url_content",
    "rows_from_deck_url_content",
    "deck_import_sections_from_url",
]
