"""Pure helpers for one-way browser-session token storage."""

import hashlib
import string


SESSION_TOKEN_HASH_LENGTH = 64
_HEX_DIGITS = frozenset(string.hexdigits)


def session_token_hash(token):
    """Return the stable SHA-256 digest used to identify a browser session."""
    clean_token = str(token or "").strip()
    if not clean_token or len(clean_token) > 512:
        return ""
    return hashlib.sha256(clean_token.encode("utf-8")).hexdigest()


def is_session_token_hash(value):
    """Identify hashes already stored by the hardened session schema."""
    clean_value = str(value or "").strip()
    return len(clean_value) == SESSION_TOKEN_HASH_LENGTH and all(
        character in _HEX_DIGITS for character in clean_value
    )


__all__ = [
    "SESSION_TOKEN_HASH_LENGTH",
    "session_token_hash",
    "is_session_token_hash",
]
