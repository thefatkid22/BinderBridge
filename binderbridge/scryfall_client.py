"""Scryfall API, cache, local bulk-data, and card-enrichment helpers.

The app facade injects shared database, formatting, pricing, and validation
helpers into this module so the legacy app.py API remains compatible.
"""

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from binderbridge.pricing import (
    SCRYFALL_BULK_ERROR_KEY,
    SCRYFALL_BULK_STATUS_KEY,
    SCRYFALL_BULK_TYPE,
    SCRYFALL_BULK_UPDATED_KEY,
)
from binderbridge.ui_helpers import (
    SCRYFALL_ACCEPT,
    SCRYFALL_DELAY_SECONDS,
    SCRYFALL_SEARCH_LIMIT,
    SCRYFALL_USER_AGENT,
)

SCRYFALL_COLLECTION_FIELDS = [
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
]

SCRYFALL_CARD_RESULT_FIELDS = [
    "scryfall_id",
    "card_name",
    "set_name",
    "set_code",
    "collector_number",
    "image_url",
    "mana_cost",
    "type_line",
    "oracle_text",
    "rarity",
    "colors",
    "color_identity",
    "scryfall_uri",
    "price_usd",
    "tcgplayer_product_id",
    "cardmarket_product_id",
]


class ScryfallError(Exception):
    pass


class ScryfallRateLimitError(ScryfallError):
    def __init__(self, message, retry_after=60):
        super().__init__(message)
        self.retry_after = retry_after


_last_scryfall_request_at = 0.0

def scryfall_cache_key(card_name, set_code="", collector_number="", scryfall_id=""):
    if scryfall_id:
        return f"id:{scryfall_id.lower()}"
    if set_code and collector_number:
        return f"print:{set_code.lower()}:{collector_number.lower()}"
    return f"name:{normalize_header(card_name)}"


def scryfall_search_term(value):
    text = str(value or "").strip()
    if not text:
        return ""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"' if any(ch.isspace() for ch in escaped) else escaped


def throttle_scryfall():
    global _last_scryfall_request_at
    elapsed = time.monotonic() - _last_scryfall_request_at
    if elapsed < SCRYFALL_DELAY_SECONDS:
        time.sleep(SCRYFALL_DELAY_SECONDS - elapsed)
    _last_scryfall_request_at = time.monotonic()


def scryfall_get(path, params=None):
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"https://api.scryfall.com{path}{query}",
        headers={"User-Agent": SCRYFALL_USER_AGENT, "Accept": SCRYFALL_ACCEPT},
    )
    throttle_scryfall()
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code == 429:
            try:
                retry_after = int(exc.headers.get("Retry-After", "60"))
            except (TypeError, ValueError):
                retry_after = 60
            raise ScryfallRateLimitError("Scryfall rate limit reached. Background enrichment will retry later.", retry_after) from exc
        raise ScryfallError(f"Scryfall returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ScryfallError(f"Scryfall lookup failed: {exc}") from exc


def scryfall_card_image(card_json):
    image_uris = card_json.get("image_uris") or {}
    if image_uris:
        return image_uris.get("small") or image_uris.get("normal") or image_uris.get("large") or ""
    for face in card_json.get("card_faces") or []:
        face_images = face.get("image_uris") or {}
        if face_images:
            return face_images.get("small") or face_images.get("normal") or face_images.get("large") or ""
    return ""


def first_face_value(card_json, key):
    if card_json.get(key):
        return card_json.get(key) or ""
    for face in card_json.get("card_faces") or []:
        if face.get(key):
            return face.get(key) or ""
    return ""


def flatten_scryfall_card(card_json):
    prices = card_json.get("prices") or {}
    price_usd = prices.get("usd") or prices.get("usd_foil") or prices.get("usd_etched") or ""
    return {
        "scryfall_id": card_json.get("id", ""),
        "card_name": card_json.get("name", ""),
        "set_name": card_json.get("set_name", ""),
        "set_code": (card_json.get("set") or "").upper(),
        "collector_number": card_json.get("collector_number", ""),
        "image_url": scryfall_card_image(card_json),
        "mana_cost": first_face_value(card_json, "mana_cost"),
        "type_line": card_json.get("type_line", "") or first_face_value(card_json, "type_line"),
        "oracle_text": first_face_value(card_json, "oracle_text"),
        "rarity": card_json.get("rarity", ""),
        "colors": ",".join(card_json.get("colors") or []),
        "color_identity": ",".join(card_json.get("color_identity") or []),
        "scryfall_uri": card_json.get("scryfall_uri", ""),
        "price_usd": normalize_price_usd(price_usd),
        "price_source": "scryfall" if price_usd else "",
        "tcgplayer_product_id": str(card_json.get("tcgplayer_id") or ""),
        "cardmarket_product_id": str(card_json.get("cardmarket_id") or ""),
    }


def cache_scryfall_card(lookup_key, card_data, raw_json):
    execute(
        """
        INSERT OR REPLACE INTO scryfall_cache
            (lookup_key, scryfall_id, card_name, set_name, set_code, collector_number, image_url, mana_cost,
             type_line, oracle_text, rarity, colors, color_identity, scryfall_uri, price_usd,
             tcgplayer_product_id, cardmarket_product_id, raw_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lookup_key,
            card_data.get("scryfall_id", ""),
            card_data.get("card_name", ""),
            card_data.get("set_name", ""),
            card_data.get("set_code", ""),
            card_data.get("collector_number", ""),
            card_data.get("image_url", ""),
            card_data.get("mana_cost", ""),
            card_data.get("type_line", ""),
            card_data.get("oracle_text", ""),
            card_data.get("rarity", ""),
            card_data.get("colors", ""),
            card_data.get("color_identity", ""),
            card_data.get("scryfall_uri", ""),
            card_data.get("price_usd", ""),
            card_data.get("tcgplayer_product_id", ""),
            card_data.get("cardmarket_product_id", ""),
            json.dumps(raw_json),
            now_iso(),
        ),
    )


def cached_scryfall_card(lookup_key):
    cached = row("SELECT * FROM scryfall_cache WHERE lookup_key = ?", (lookup_key,))
    if not cached:
        return None
    return {field: cached[field] for field in SCRYFALL_CARD_RESULT_FIELDS}


def bulk_scryfall_row_to_card(found):
    if not found:
        return None
    return {field: found[field] for field in SCRYFALL_CARD_RESULT_FIELDS}


def bulk_scryfall_card(card_name="", set_code="", collector_number="", scryfall_id=""):
    if scryfall_id:
        found = row("SELECT * FROM scryfall_bulk_cards WHERE scryfall_id = ?", (scryfall_id,))
        if found:
            return bulk_scryfall_row_to_card(found)
    if set_code and collector_number:
        found = row(
            """
            SELECT *
            FROM scryfall_bulk_cards
            WHERE set_code = ? COLLATE NOCASE AND collector_number = ? COLLATE NOCASE
            ORDER BY released_at DESC
            LIMIT 1
            """,
            (set_code.upper(), collector_number),
        )
        if found:
            return bulk_scryfall_row_to_card(found)
    if card_name:
        found = row(
            """
            SELECT *
            FROM scryfall_bulk_cards
            WHERE search_name = ?
            ORDER BY released_at DESC
            LIMIT 1
            """,
            (normalize_header(card_name),),
        )
        if found:
            return bulk_scryfall_row_to_card(found)
    return None


def local_scryfall_card(card_name, set_code="", collector_number="", scryfall_id=""):
    lookup_key = scryfall_cache_key(card_name, set_code, collector_number, scryfall_id)
    cached = cached_scryfall_card(lookup_key)
    if cached:
        return cached
    bulk_card = bulk_scryfall_card(card_name, set_code, collector_number, scryfall_id)
    if bulk_card:
        cache_scryfall_card(lookup_key, bulk_card, bulk_card)
    return bulk_card


def local_scryfall_card_for_item(item, lookup_cache=None):
    lookup_key = scryfall_cache_key(
        row_value(item, "card_name", ""),
        row_value(item, "set_code", ""),
        row_value(item, "collector_number", ""),
        row_value(item, "scryfall_id", ""),
    )
    if lookup_cache is not None and lookup_key in lookup_cache:
        return lookup_cache[lookup_key]
    card_data = local_scryfall_card(
        row_value(item, "card_name", ""),
        row_value(item, "set_code", ""),
        row_value(item, "collector_number", ""),
        row_value(item, "scryfall_id", ""),
    )
    if lookup_cache is not None:
        lookup_cache[lookup_key] = card_data
    return card_data


def apply_local_scryfall_data(item, lookup_cache=None):
    card_data = local_scryfall_card_for_item(item, lookup_cache=lookup_cache)
    return apply_scryfall_data(item, card_data) if card_data else item


def lookup_scryfall_card(card_name, set_code="", collector_number="", scryfall_id=""):
    local_card = local_scryfall_card(card_name, set_code, collector_number, scryfall_id)
    if local_card:
        return local_card
    candidates = []
    if scryfall_id:
        candidates.append((scryfall_cache_key(card_name, scryfall_id=scryfall_id), f"/cards/{scryfall_id}", None))
    if set_code and collector_number:
        candidates.append((scryfall_cache_key(card_name, set_code, collector_number), f"/cards/{set_code.lower()}/{collector_number}", None))
    if card_name:
        candidates.append((scryfall_cache_key(card_name), "/cards/named", {"exact": card_name}))

    for lookup_key, path, params in candidates:
        cached = cached_scryfall_card(lookup_key)
        if cached:
            return cached
        raw_json = scryfall_get(path, params)
        if not raw_json and params and "exact" in params:
            raw_json = scryfall_get(path, {"fuzzy": card_name})
        if raw_json:
            card_data = flatten_scryfall_card(raw_json)
            cache_scryfall_card(lookup_key, card_data, raw_json)
            return card_data
    return None


def store_scryfall_bulk_cards(card_json_list, bulk_updated_at=""):
    imported_at = now_iso()
    records = []
    for raw_card in card_json_list:
        if not isinstance(raw_card, dict) or raw_card.get("object") not in ("card", None):
            continue
        card_data = flatten_scryfall_card(raw_card)
        if not card_data.get("scryfall_id") or not card_data.get("card_name"):
            continue
        records.append(
            (
                card_data["scryfall_id"],
                card_data["card_name"],
                normalize_header(card_data["card_name"]),
                card_data["set_name"],
                card_data["set_code"],
                card_data["collector_number"],
                raw_card.get("released_at", ""),
                card_data["image_url"],
                card_data["mana_cost"],
                card_data["type_line"],
                card_data["oracle_text"],
                card_data["rarity"],
                card_data["colors"],
                card_data["color_identity"],
                card_data["scryfall_uri"],
                card_data["price_usd"],
                card_data["tcgplayer_product_id"],
                card_data["cardmarket_product_id"],
                json.dumps(raw_card.get("finishes") or [], separators=(",", ":")),
                imported_at,
            )
        )
    with db() as conn:
        conn.execute("DELETE FROM scryfall_bulk_cards")
        conn.executemany(
            """
            INSERT OR REPLACE INTO scryfall_bulk_cards
                (scryfall_id, card_name, search_name, set_name, set_code, collector_number, released_at,
                 image_url, mana_cost, type_line, oracle_text, rarity, colors, color_identity,
                 scryfall_uri, price_usd, tcgplayer_product_id, cardmarket_product_id, finishes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (SCRYFALL_BULK_UPDATED_KEY, bulk_updated_at or imported_at),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (SCRYFALL_BULK_ERROR_KEY, ""),
        )
    return len(records)


def fetch_json_url(url):
    request = Request(url, headers={"User-Agent": SCRYFALL_USER_AGENT, "Accept": SCRYFALL_ACCEPT})
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def sync_scryfall_bulk_data(bulk_type=SCRYFALL_BULK_TYPE):
    manifest = scryfall_get("/bulk-data")
    if not manifest or not manifest.get("data"):
        raise ScryfallError("Scryfall bulk data manifest could not be loaded.")
    bulk_item = next((item for item in manifest["data"] if item.get("type") == bulk_type), None)
    if not bulk_item or not bulk_item.get("download_uri"):
        raise ScryfallError(f"Scryfall bulk data type {bulk_type} was not found.")
    cards = fetch_json_url(bulk_item["download_uri"])
    if not isinstance(cards, list):
        raise ScryfallError("Scryfall bulk data download was not a card list.")
    return store_scryfall_bulk_cards(cards, bulk_item.get("updated_at", ""))


def scryfall_bulk_status():
    count = row("SELECT COUNT(*) AS count FROM scryfall_bulk_cards")["count"]
    return {
        "card_count": count,
        "status": get_setting(SCRYFALL_BULK_STATUS_KEY, "idle"),
        "updated_at": get_setting(SCRYFALL_BULK_UPDATED_KEY, ""),
        "error": get_setting(SCRYFALL_BULK_ERROR_KEY, ""),
    }


def local_scryfall_card_from_conn(conn, item):
    scryfall_id = row_value(item, "scryfall_id", "")
    set_code = row_value(item, "set_code", "")
    collector_number = row_value(item, "collector_number", "")
    card_name = row_value(item, "card_name", "")
    found = None
    if scryfall_id:
        found = conn.execute("SELECT * FROM scryfall_bulk_cards WHERE scryfall_id = ?", (scryfall_id,)).fetchone()
    if not found and set_code and collector_number:
        found = conn.execute(
            """
            SELECT *
            FROM scryfall_bulk_cards
            WHERE set_code = ? COLLATE NOCASE AND collector_number = ? COLLATE NOCASE
            ORDER BY released_at DESC
            LIMIT 1
            """,
            (set_code.upper(), collector_number),
        ).fetchone()
    if not found and card_name:
        found = conn.execute(
            """
            SELECT *
            FROM scryfall_bulk_cards
            WHERE search_name = ?
            ORDER BY released_at DESC
            LIMIT 1
            """,
            (normalize_header(card_name),),
        ).fetchone()
    if not found and scryfall_id:
        found = conn.execute("SELECT * FROM scryfall_cache WHERE scryfall_id = ? ORDER BY fetched_at DESC LIMIT 1", (scryfall_id,)).fetchone()
    if not found and set_code and collector_number:
        found = conn.execute(
            """
            SELECT *
            FROM scryfall_cache
            WHERE set_code = ? COLLATE NOCASE AND collector_number = ? COLLATE NOCASE
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (set_code.upper(), collector_number),
        ).fetchone()
    if not found and card_name:
        found = conn.execute(
            """
            SELECT *
            FROM scryfall_cache
            WHERE card_name = ? COLLATE NOCASE
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (card_name,),
        ).fetchone()
    if not found:
        return None
    return {field: row_value(found, field, "") for field in SCRYFALL_CARD_RESULT_FIELDS}

def search_scryfall_cards(card_name, set_code="", limit=SCRYFALL_SEARCH_LIMIT):
    name = str(card_name or "").strip()
    if not name:
        raise ValueError("Card name is required for Scryfall search.")
    query_parts = [f"name:{scryfall_search_term(name)}"]
    if set_code:
        query_parts.append(f"set:{normalize_header(set_code)}")
    raw_json = scryfall_get(
        "/cards/search",
        {
            "q": " ".join(query_parts),
            "unique": "cards",
            "order": "name",
            "dir": "auto",
            "include_extras": "false",
        },
    )
    if not raw_json:
        return []
    matches = []
    seen_ids = set()
    for raw_card in raw_json.get("data", []):
        card_data = flatten_scryfall_card(raw_card)
        scryfall_id = card_data.get("scryfall_id", "")
        if not scryfall_id or scryfall_id in seen_ids:
            continue
        seen_ids.add(scryfall_id)
        cache_scryfall_card(scryfall_cache_key(card_data["card_name"], scryfall_id=scryfall_id), card_data, raw_card)
        matches.append(card_data)
        if len(matches) >= limit:
            break
    return matches


def search_scryfall_prints(card_name, limit=SCRYFALL_SEARCH_LIMIT):
    name = str(card_name or "").strip()
    if not name:
        raise ValueError("Card name is required for Scryfall variation search.")
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    raw_json = scryfall_get(
        "/cards/search",
        {
            "q": f'!"{escaped}"',
            "unique": "prints",
            "order": "released",
            "dir": "desc",
            "include_extras": "false",
        },
    )
    if not raw_json:
        return []
    matches = []
    seen_ids = set()
    for raw_card in raw_json.get("data", []):
        card_data = flatten_scryfall_card(raw_card)
        scryfall_id = card_data.get("scryfall_id", "")
        if not scryfall_id or scryfall_id in seen_ids:
            continue
        seen_ids.add(scryfall_id)
        cache_scryfall_card(scryfall_cache_key(card_data["card_name"], scryfall_id=scryfall_id), card_data, raw_card)
        matches.append(card_data)
        if len(matches) >= limit:
            break
    return matches


def apply_scryfall_data(item, card_data):
    if not card_data:
        return item
    enriched = dict(item)
    if card_data.get("card_name"):
        enriched["card_name"] = card_data["card_name"][:160]
    for field in ["set_name", "set_code", "collector_number"]:
        if card_data.get(field) and not enriched.get(field):
            enriched[field] = card_data[field]
    for field in SCRYFALL_COLLECTION_FIELDS:
        if card_data.get(field):
            enriched[field] = card_data[field]
    if card_data.get("price_usd"):
        enriched["price_usd"] = normalize_price_usd(card_data["price_usd"])
        enriched["price_source"] = "scryfall"
    for field in ("tcgplayer_product_id", "cardmarket_product_id"):
        if card_data.get(field) and not enriched.get(field):
            enriched[field] = card_data[field]
    return enriched


def enrich_collection_data_from_scryfall(data):
    if data.get("game") != "mtg":
        raise ValueError("Scryfall lookup is only available for Magic: The Gathering cards.")
    card_data = lookup_scryfall_card(data["card_name"], data.get("set_code", ""), data.get("collector_number", ""), "")
    if not card_data:
        raise ValueError(f"Scryfall did not find {data['card_name']}.")
    return apply_scryfall_data(data, card_data)


def selected_scryfall_card_data(selected_scryfall_id):
    selected_id = str(selected_scryfall_id or "").strip()
    if not selected_id:
        raise ValueError("Choose a Scryfall match first.")
    card_data = lookup_scryfall_card("", "", "", selected_id)
    if not card_data:
        raise ValueError("The selected Scryfall card could not be loaded.")
    return card_data


def manual_scryfall_candidates(data):
    if data.get("game") != "mtg":
        raise ValueError("Scryfall search is only available for Magic: The Gathering cards.")
    candidates = search_scryfall_cards(data["card_name"], data.get("set_code", ""))
    if not candidates:
        raise ValueError(f"Scryfall did not find any matches for {data['card_name']}.")
    return candidates


def manual_scryfall_variants(selected_scryfall_id):
    selected_card = selected_scryfall_card_data(selected_scryfall_id)
    variants = search_scryfall_prints(selected_card["card_name"])
    if not variants:
        variants = [selected_card]
    return selected_card, variants


def apply_selected_card_name(data, selected_card):
    updated = dict(data)
    if selected_card.get("card_name"):
        updated["card_name"] = selected_card["card_name"]
    updated["lookup_on_save"] = "1"
    return updated


def should_request_scryfall_selection(data):
    return data.get("lookup_on_save") == "1" and data.get("game") == "mtg" and not data.get("scryfall_id")
__all__ = [
    "SCRYFALL_COLLECTION_FIELDS",
    "SCRYFALL_CARD_RESULT_FIELDS",
    "ScryfallError",
    "ScryfallRateLimitError",
    "scryfall_cache_key",
    "scryfall_search_term",
    "throttle_scryfall",
    "scryfall_get",
    "scryfall_card_image",
    "first_face_value",
    "flatten_scryfall_card",
    "cache_scryfall_card",
    "cached_scryfall_card",
    "bulk_scryfall_row_to_card",
    "bulk_scryfall_card",
    "local_scryfall_card",
    "local_scryfall_card_for_item",
    "apply_local_scryfall_data",
    "lookup_scryfall_card",
    "store_scryfall_bulk_cards",
    "fetch_json_url",
    "sync_scryfall_bulk_data",
    "scryfall_bulk_status",
    "local_scryfall_card_from_conn",
    "search_scryfall_cards",
    "search_scryfall_prints",
    "apply_scryfall_data",
    "enrich_collection_data_from_scryfall",
    "selected_scryfall_card_data",
    "manual_scryfall_candidates",
    "manual_scryfall_variants",
    "apply_selected_card_name",
    "should_request_scryfall_selection",
]
