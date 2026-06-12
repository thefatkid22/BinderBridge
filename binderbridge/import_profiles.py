"""Built-in CSV source profiles and auto-detection helpers."""

import re


def _header_key(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


CSV_IMPORT_PROFILES = {
    "manabox": {
        "label": "ManaBox",
        "targets": ("collection", "deck"),
        "signature": ("scryfall id", "set code", "collector number"),
        "mapping": {
            "name": ("Name",),
            "quantity": ("Quantity",),
            "trade": ("Trade Quantity",),
            "set_name": ("Set name",),
            "set_code": ("Set code",),
            "collector_number": ("Collector number",),
            "finish": ("Foil",),
            "condition": ("Condition",),
            "language": ("Language",),
            "scryfall_id": ("Scryfall ID",),
            "notes": ("Notes",),
        },
    },
    "archidekt": {
        "label": "Archidekt",
        "targets": ("collection", "deck"),
        "signature": ("quantity", "name", "edition", "collector number", "foil"),
        "mapping": {
            "name": ("Name", "Card"),
            "quantity": ("Quantity",),
            "set_name": ("Edition",),
            "set_code": ("Edition Code",),
            "collector_number": ("Collector Number",),
            "finish": ("Foil",),
            "condition": ("Condition",),
            "language": ("Language",),
            "section": ("Categories", "Category"),
        },
    },
    "deckbox": {
        "label": "Deckbox",
        "targets": ("collection", "deck"),
        "signature": ("count", "tradelist count", "name", "edition", "card number"),
        "mapping": {
            "name": ("Name",),
            "quantity": ("Count",),
            "trade": ("Tradelist Count",),
            "set_name": ("Edition",),
            "collector_number": ("Card Number",),
            "finish": ("Foil",),
            "condition": ("Condition",),
            "language": ("Language",),
            "notes": ("Notes",),
        },
    },
    "moxfield": {
        "label": "Moxfield",
        "targets": ("collection", "deck"),
        "signature": ("count", "tradelist count", "name", "edition", "collector number"),
        "mapping": {
            "name": ("Name",),
            "quantity": ("Count", "Quantity"),
            "trade": ("Tradelist Count",),
            "set_name": ("Edition",),
            "set_code": ("Edition Code", "Set Code"),
            "collector_number": ("Collector Number",),
            "finish": ("Foil",),
            "condition": ("Condition",),
            "language": ("Language",),
            "notes": ("Tags", "Notes"),
            "section": ("Board", "Section"),
        },
    },
    "dragonshield": {
        "label": "Dragon Shield",
        "targets": ("collection", "deck"),
        "signature": ("folder name", "quantity", "card name", "set code", "card number"),
        "mapping": {
            "name": ("Card Name",),
            "quantity": ("Quantity",),
            "trade": ("Trade Quantity",),
            "set_name": ("Set Name",),
            "set_code": ("Set Code",),
            "collector_number": ("Card Number",),
            "finish": ("Printing", "Foil"),
            "condition": ("Condition",),
            "language": ("Language",),
            "notes": ("Notes",),
            "section": ("Folder Name",),
        },
    },
    "delver_lens": {
        "label": "Delver Lens",
        "targets": ("collection", "deck"),
        "signature": ("quantity", "name", "edition code", "card number", "scryfall id"),
        "mapping": {
            "name": ("Name", "Card Name"),
            "quantity": ("Quantity", "Count"),
            "set_name": ("Edition", "Set"),
            "set_code": ("Edition Code", "Set Code"),
            "collector_number": ("Card Number", "Collector Number"),
            "finish": ("Foil", "Finish"),
            "condition": ("Condition",),
            "language": ("Language",),
            "scryfall_id": ("Scryfall ID",),
            "notes": ("Notes",),
            "section": ("Folder", "Section"),
        },
    },
    "deckstats": {
        "label": "Deckstats",
        "targets": ("deck",),
        "signature": ("amount", "name", "set code", "is foil"),
        "mapping": {
            "name": ("name",),
            "quantity": ("amount",),
            "set_name": ("set name",),
            "set_code": ("set code",),
            "collector_number": ("collector number",),
            "finish": ("is foil",),
            "section": ("section", "board"),
        },
    },
    "tappedout": {
        "label": "TappedOut",
        "targets": ("deck",),
        "signature": ("board", "qty", "name"),
        "mapping": {
            "name": ("Name", "Card"),
            "quantity": ("Qty", "Quantity"),
            "set_name": ("Set", "Edition"),
            "set_code": ("Set Code",),
            "collector_number": ("Collector Number", "Card Number"),
            "finish": ("Foil",),
            "section": ("Board",),
        },
    },
    "aetherhub": {
        "label": "AetherHub",
        "targets": ("deck",),
        "signature": ("board", "card name", "quantity", "set code"),
        "mapping": {
            "name": ("Card Name",),
            "quantity": ("Quantity",),
            "set_name": ("Set Name",),
            "set_code": ("Set Code",),
            "collector_number": ("Card Number", "Collector Number"),
            "finish": ("Foil", "Printing"),
            "section": ("Board",),
        },
    },
}


def csv_import_profile(source, target="collection"):
    profile = CSV_IMPORT_PROFILES.get(str(source or "").strip().lower())
    if not profile or target not in profile["targets"]:
        return None
    return profile


def detect_csv_import_profile(fieldnames, target="collection"):
    headers = {_header_key(name) for name in fieldnames or [] if _header_key(name)}
    matches = []
    for source, profile in CSV_IMPORT_PROFILES.items():
        if target not in profile["targets"]:
            continue
        signature = {_header_key(name) for name in profile.get("signature", ())}
        if signature and signature.issubset(headers):
            matches.append((len(signature), source))
    return max(matches)[1] if matches else "generic"


def resolve_csv_import_profile(source, fieldnames, target="collection"):
    requested = str(source or "auto").strip().lower()
    resolved = detect_csv_import_profile(fieldnames, target) if requested == "auto" else requested
    profile = csv_import_profile(resolved, target)
    if not profile:
        return "generic", None
    return resolved, profile


def merge_csv_import_mappings(profile_mapping=None, override_mapping=None):
    merged = {}
    for mapping in (profile_mapping or {}, override_mapping or {}):
        for canonical, raw_headers in mapping.items():
            headers = raw_headers if isinstance(raw_headers, (list, tuple)) else (raw_headers,)
            current = merged.setdefault(canonical, [])
            for header in headers:
                text = str(header or "").strip()
                if text and text not in current:
                    current.append(text)
    if override_mapping:
        for canonical, raw_headers in override_mapping.items():
            headers = raw_headers if isinstance(raw_headers, (list, tuple)) else (raw_headers,)
            preferred = [str(header or "").strip() for header in headers if str(header or "").strip()]
            existing = merged.get(canonical, [])
            merged[canonical] = preferred + [header for header in existing if header not in preferred]
    return merged


def csv_import_profile_mapping(source, fieldnames, target="collection", override_mapping=None):
    resolved, profile = resolve_csv_import_profile(source, fieldnames, target)
    mapping = merge_csv_import_mappings(profile.get("mapping", {}) if profile else {}, override_mapping)
    return resolved, mapping


def csv_import_profile_label(source):
    profile = CSV_IMPORT_PROFILES.get(str(source or "").strip().lower())
    return profile["label"] if profile else "Generic CSV"


__all__ = [
    "CSV_IMPORT_PROFILES",
    "csv_import_profile",
    "detect_csv_import_profile",
    "resolve_csv_import_profile",
    "merge_csv_import_mappings",
    "csv_import_profile_mapping",
    "csv_import_profile_label",
]
