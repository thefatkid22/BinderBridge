"""Password hashing, sessions, and two-factor authentication helpers for BinderBridge.

The app facade injects shared runtime helpers/constants into this module.
"""

import base64
import binascii
import hashlib
import hmac
import html
import json
import re
import secrets
import smtplib
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from urllib.parse import quote

from binderbridge.config import config_bool, config_float, config_int, config_str
from binderbridge.session_tokens import session_token_hash
from binderbridge.migrations import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_MIGRATIONS,
    SCHEMA_VERSION_KEY,
    db_schema_version,
    migrate_hot_path_indexes,
    run_schema_migrations,
    set_db_schema_version,
)

def hash_password(password):
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"

def verify_password(password, stored_hash):
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def generate_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

def normalize_totp_secret(secret):
    return re.sub(r"[^A-Z2-7]", "", str(secret or "").upper())

def normalize_totp_code(code):
    return re.sub(r"[^0-9]", "", str(code or ""))

def totp_secret_bytes(secret):
    normalized = normalize_totp_secret(secret)
    if not normalized:
        return b""
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        return base64.b32decode(normalized + padding, casefold=True)
    except (binascii.Error, ValueError):
        return b""

def totp_code(secret, for_time=None, timestep=30, digits=6):
    secret_bytes = totp_secret_bytes(secret)
    if not secret_bytes:
        return ""
    timestamp = int(time.time() if for_time is None else for_time)
    counter = max(0, timestamp // timestep)
    digest = hmac.new(secret_bytes, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(truncated % (10 ** digits)).zfill(digits)

def verify_totp_code(secret, code, for_time=None, timestep=30, window=1):
    normalized_code = normalize_totp_code(code)
    if len(normalized_code) != 6:
        return False
    timestamp = int(time.time() if for_time is None else for_time)
    for drift in range(-window, window + 1):
        expected = totp_code(secret, timestamp + drift * timestep, timestep=timestep)
        if expected and hmac.compare_digest(expected, normalized_code):
            return True
    return False

def user_totp_label(user):
    username = row_value(user, "username", "") or row_value(user, "display_name", "") or f"user-{row_value(user, 'id', '')}"
    return f"{APP_NAME}:{username}"

def totp_otpauth_uri(user, secret):
    issuer = APP_NAME
    label = user_totp_label(user)
    return (
        f"otpauth://totp/{quote(label)}"
        f"?secret={quote(normalize_totp_secret(secret))}"
        f"&issuer={quote(issuer)}"
    )

def format_totp_secret(secret):
    normalized = normalize_totp_secret(secret)
    return " ".join(normalized[index:index + 4] for index in range(0, len(normalized), 4))

QR_VERSION = 6

QR_SIZE = 17 + 4 * QR_VERSION

QR_DATA_CODEWORDS = 136

QR_ECC_CODEWORDS_PER_BLOCK = 18

QR_BLOCK_COUNT = 2

QR_DATA_CODEWORDS_PER_BLOCK = QR_DATA_CODEWORDS // QR_BLOCK_COUNT

def qr_gf_tables():
    exp = [0] * 512
    log = [0] * 256
    value = 1
    for index in range(255):
        exp[index] = value
        log[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for index in range(255, 512):
        exp[index] = exp[index - 255]
    return exp, log

QR_GF_EXP, QR_GF_LOG = qr_gf_tables()

def qr_gf_multiply(left, right):
    if not left or not right:
        return 0
    return QR_GF_EXP[QR_GF_LOG[left] + QR_GF_LOG[right]]

def qr_reed_solomon_generator(degree):
    generator = [1]
    root = 1
    for _ in range(degree):
        next_generator = [0] * (len(generator) + 1)
        for index, coefficient in enumerate(generator):
            next_generator[index] ^= coefficient
            next_generator[index + 1] ^= qr_gf_multiply(coefficient, root)
        generator = next_generator
        root = qr_gf_multiply(root, 2)
    return generator

def qr_reed_solomon_remainder(data, degree):
    generator = qr_reed_solomon_generator(degree)
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for index in range(degree):
            remainder[index] ^= qr_gf_multiply(generator[index + 1], factor)
    return remainder

def qr_bit_buffer_for_text(text):
    data = str(text or "").encode("utf-8")
    if len(data) > QR_DATA_CODEWORDS - 2:
        raise ValueError("Two-factor setup URI is too long to encode as a QR code.")
    bits = []

    def append_bits(value, length):
        for shift in range(length - 1, -1, -1):
            bits.append((value >> shift) & 1)

    append_bits(0b0100, 4)
    append_bits(len(data), 8)
    for byte in data:
        append_bits(byte, 8)
    remaining = QR_DATA_CODEWORDS * 8 - len(bits)
    append_bits(0, min(4, remaining))
    while len(bits) % 8:
        bits.append(0)
    pad_bytes = (0xEC, 0x11)
    pad_index = 0
    while len(bits) < QR_DATA_CODEWORDS * 8:
        append_bits(pad_bytes[pad_index % 2], 8)
        pad_index += 1
    return [sum(bits[index + bit] << (7 - bit) for bit in range(8)) for index in range(0, len(bits), 8)]

def qr_codewords_for_text(text):
    data_codewords = qr_bit_buffer_for_text(text)
    blocks = [
        data_codewords[index * QR_DATA_CODEWORDS_PER_BLOCK:(index + 1) * QR_DATA_CODEWORDS_PER_BLOCK]
        for index in range(QR_BLOCK_COUNT)
    ]
    ecc_blocks = [qr_reed_solomon_remainder(block, QR_ECC_CODEWORDS_PER_BLOCK) for block in blocks]
    codewords = []
    for index in range(QR_DATA_CODEWORDS_PER_BLOCK):
        for block in blocks:
            codewords.append(block[index])
    for index in range(QR_ECC_CODEWORDS_PER_BLOCK):
        for block in ecc_blocks:
            codewords.append(block[index])
    return codewords

def qr_empty_matrix():
    return [[False for _ in range(QR_SIZE)] for _ in range(QR_SIZE)], [[False for _ in range(QR_SIZE)] for _ in range(QR_SIZE)]

def qr_set_function(matrix, reserved, x, y, value):
    if 0 <= x < QR_SIZE and 0 <= y < QR_SIZE:
        matrix[y][x] = bool(value)
        reserved[y][x] = True

def qr_draw_finder(matrix, reserved, left, top):
    for dy in range(-1, 8):
        for dx in range(-1, 8):
            x = left + dx
            y = top + dy
            if not (0 <= x < QR_SIZE and 0 <= y < QR_SIZE):
                continue
            dark = (
                0 <= dx <= 6
                and 0 <= dy <= 6
                and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
            )
            qr_set_function(matrix, reserved, x, y, dark)

def qr_draw_alignment(matrix, reserved, center_x, center_y):
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            dark = max(abs(dx), abs(dy)) in (0, 2)
            qr_set_function(matrix, reserved, center_x + dx, center_y + dy, dark)

def qr_draw_function_patterns(matrix, reserved):
    qr_draw_finder(matrix, reserved, 0, 0)
    qr_draw_finder(matrix, reserved, QR_SIZE - 7, 0)
    qr_draw_finder(matrix, reserved, 0, QR_SIZE - 7)
    qr_draw_alignment(matrix, reserved, 34, 34)
    for index in range(QR_SIZE):
        if not reserved[6][index]:
            qr_set_function(matrix, reserved, index, 6, index % 2 == 0)
        if not reserved[index][6]:
            qr_set_function(matrix, reserved, 6, index, index % 2 == 0)
    for index in range(9):
        qr_set_function(matrix, reserved, 8, index, False)
        qr_set_function(matrix, reserved, index, 8, False)
    for index in range(8):
        qr_set_function(matrix, reserved, QR_SIZE - 1 - index, 8, False)
        qr_set_function(matrix, reserved, 8, QR_SIZE - 1 - index, False)
    qr_set_function(matrix, reserved, 8, 4 * QR_VERSION + 9, True)

def qr_format_bits(mask):
    data = (0b01 << 3) | int(mask or 0)
    remainder = data << 10
    generator = 0x537
    for shift in range(14, 9, -1):
        if (remainder >> shift) & 1:
            remainder ^= generator << (shift - 10)
    return ((data << 10) | remainder) ^ 0x5412

def qr_draw_format_bits(matrix, reserved, mask=0):
    bits = qr_format_bits(mask)

    def bit(index):
        return ((bits >> index) & 1) != 0

    for index in range(6):
        qr_set_function(matrix, reserved, 8, index, bit(index))
    qr_set_function(matrix, reserved, 8, 7, bit(6))
    qr_set_function(matrix, reserved, 8, 8, bit(7))
    qr_set_function(matrix, reserved, 7, 8, bit(8))
    for index in range(9, 15):
        qr_set_function(matrix, reserved, 14 - index, 8, bit(index))
    for index in range(8):
        qr_set_function(matrix, reserved, QR_SIZE - 1 - index, 8, bit(index))
    for index in range(8, 15):
        qr_set_function(matrix, reserved, 8, QR_SIZE - 15 + index, bit(index))
    qr_set_function(matrix, reserved, 8, QR_SIZE - 8, True)

def qr_matrix(text):
    matrix, reserved = qr_empty_matrix()
    qr_draw_function_patterns(matrix, reserved)
    codeword_bits = []
    for codeword in qr_codewords_for_text(text):
        for shift in range(7, -1, -1):
            codeword_bits.append((codeword >> shift) & 1)
    bit_index = 0
    upward = True
    right = QR_SIZE - 1
    while right > 0:
        if right == 6:
            right -= 1
        for row_index in range(QR_SIZE):
            y = QR_SIZE - 1 - row_index if upward else row_index
            for x in (right, right - 1):
                if reserved[y][x]:
                    continue
                value = codeword_bits[bit_index] if bit_index < len(codeword_bits) else 0
                if (x + y) % 2 == 0:
                    value ^= 1
                matrix[y][x] = bool(value)
                bit_index += 1
        upward = not upward
        right -= 2
    qr_draw_format_bits(matrix, reserved, mask=0)
    return matrix

def qr_svg(text, border=4):
    matrix = qr_matrix(text)
    size = len(matrix) + border * 2
    modules = []
    for y, row_data in enumerate(matrix):
        for x, dark in enumerate(row_data):
            if dark:
                modules.append(f"M{x + border},{y + border}h1v1h-1z")
    path = "".join(modules)
    return (
        f'<svg class="totp-qr-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'role="img" aria-label="Two-factor setup QR code" shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/>'
        f'<path d="{path}" fill="#000"/>'
        "</svg>"
    )

def generate_recovery_codes(count=10):
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(5).upper()
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes

def normalize_recovery_code(code):
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())

def hash_recovery_code(code):
    normalized = normalize_recovery_code(code)
    if not normalized:
        return ""
    salt = secrets.token_hex(8)
    digest = hashlib.sha256(f"{salt}:{normalized}".encode("utf-8")).hexdigest()
    return f"{salt}${digest}"

def verify_recovery_code(code, stored_hash):
    normalized = normalize_recovery_code(code)
    try:
        salt, expected = str(stored_hash or "").split("$", 1)
    except ValueError:
        return False
    if not normalized or not salt or not expected:
        return False
    actual = hashlib.sha256(f"{salt}:{normalized}".encode("utf-8")).hexdigest()
    return hmac.compare_digest(actual, expected)

def recovery_code_hashes(codes):
    return json.dumps([hash_recovery_code(code) for code in codes], separators=(",", ":"))

def load_recovery_code_hashes(user):
    try:
        data = json.loads(row_value(user, "totp_recovery_codes", "") or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data if str(item or "").strip()] if isinstance(data, list) else []

def two_factor_enabled(user):
    return bool(row_value(user, "totp_enabled", 0) and row_value(user, "totp_secret", ""))

def user_totp_setup_details(user):
    secret = row_value(user, "totp_secret", "")
    if not secret:
        return None
    return {
        "secret": secret,
        "formatted_secret": format_totp_secret(secret),
        "otpauth_uri": totp_otpauth_uri(user, secret),
        "qr_svg": qr_svg(totp_otpauth_uri(user, secret)),
    }

def start_user_totp_setup(user_id):
    secret = generate_totp_secret()
    timestamp = now_iso()
    execute(
        """
        UPDATE users
        SET totp_secret = ?, totp_enabled = 0, totp_recovery_codes = '', totp_enabled_at = '', updated_at = ?
        WHERE id = ?
        """,
        (secret, timestamp, user_id),
    )
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    return user_totp_setup_details(user)

def enable_user_totp(user_id, code):
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user or not row_value(user, "totp_secret", ""):
        raise ValueError("Start two-factor setup before enabling it.")
    if not verify_totp_code(user["totp_secret"], code):
        raise ValueError("That authenticator code did not match.")
    recovery_codes = generate_recovery_codes()
    timestamp = now_iso()
    execute(
        """
        UPDATE users
        SET totp_enabled = 1, totp_recovery_codes = ?, totp_enabled_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (recovery_code_hashes(recovery_codes), timestamp, timestamp, user_id),
    )
    execute("DELETE FROM two_factor_challenges WHERE user_id = ?", (user_id,))
    return recovery_codes

def disable_user_totp(user_id):
    execute(
        """
        UPDATE users
        SET totp_secret = '', totp_enabled = 0, totp_recovery_codes = '', totp_enabled_at = '', updated_at = ?
        WHERE id = ?
        """,
        (now_iso(), user_id),
    )
    execute("DELETE FROM two_factor_challenges WHERE user_id = ?", (user_id,))

def regenerate_user_totp_recovery_codes(user_id):
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user or not two_factor_enabled(user):
        raise ValueError("Two-factor authentication is not enabled.")
    recovery_codes = generate_recovery_codes()
    execute(
        """
        UPDATE users
        SET totp_recovery_codes = ?, updated_at = ?
        WHERE id = ?
        """,
        (recovery_code_hashes(recovery_codes), now_iso(), user_id),
    )
    return recovery_codes

def consume_user_recovery_code(user_id, code):
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        return False
    hashes = load_recovery_code_hashes(user)
    for index, stored_hash in enumerate(hashes):
        if verify_recovery_code(code, stored_hash):
            del hashes[index]
            execute(
                "UPDATE users SET totp_recovery_codes = ?, updated_at = ? WHERE id = ?",
                (json.dumps(hashes, separators=(",", ":")), now_iso(), user_id),
            )
            return True
    return False

def verify_user_two_factor(user_id, code):
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user or not two_factor_enabled(user):
        return False, ""
    if verify_totp_code(user["totp_secret"], code):
        return True, "totp"
    if consume_user_recovery_code(user_id, code):
        return True, "recovery"
    return False, ""

def create_two_factor_challenge(user_id):
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + 300
    with db() as conn:
        conn.execute("DELETE FROM two_factor_challenges WHERE user_id = ? OR expires_at < ?", (user_id, int(time.time())))
        conn.execute(
            "INSERT INTO two_factor_challenges (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, expires_at, now_iso()),
        )
    return token, expires_at

def two_factor_challenge(token):
    clean_token = sanitize_text_input(token, max_length=200).strip()
    if not clean_token:
        return None
    now_ts = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM two_factor_challenges WHERE expires_at < ?", (now_ts,))
        return conn.execute(
            """
            SELECT two_factor_challenges.*, users.username, users.display_name, users.is_banned,
                users.registration_status, users.totp_enabled, users.totp_secret
            FROM two_factor_challenges
            JOIN users ON users.id = two_factor_challenges.user_id
            WHERE two_factor_challenges.token = ?
                AND two_factor_challenges.expires_at >= ?
                AND users.is_banned = 0
                AND users.registration_status = 'active'
            """,
            (clean_token, now_ts),
        ).fetchone()

def delete_two_factor_challenge(token):
    execute("DELETE FROM two_factor_challenges WHERE token = ?", (sanitize_text_input(token, max_length=200).strip(),))

def complete_two_factor_login(token, code):
    challenge = two_factor_challenge(token)
    if not challenge:
        raise ValueError("That two-factor login session expired. Please sign in again.")
    verified, method = verify_user_two_factor(challenge["user_id"], code)
    if not verified:
        raise ValueError("That two-factor code did not match.")
    delete_two_factor_challenge(token)
    return row("SELECT * FROM users WHERE id = ?", (challenge["user_id"],)), method

PASSKEY_CHALLENGE_TTL_SECONDS = 300
PASSKEY_SUPPORTED_ALG = -7
PASSKEY_UP_FLAG = 0x01
PASSKEY_UV_FLAG = 0x04
PASSKEY_AT_FLAG = 0x40

P256_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
P256_A = P256_P - 3
P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
P256_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
P256_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551

def passkey_b64encode(data):
    return base64.urlsafe_b64encode(bytes(data)).rstrip(b"=").decode("ascii")

def passkey_b64decode(value):
    text = str(value or "").strip()
    if not text:
        return b""
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.urlsafe_b64decode((text + padding).encode("ascii"))
    except (binascii.Error, ValueError):
        return b""

def passkey_user_handle(user_id):
    return passkey_b64encode(str(int(user_id)).encode("ascii"))

def passkey_clean_rp_id(value):
    text = sanitize_text_input(value, max_length=200).strip().lower()
    if ":" in text and not text.startswith("["):
        text = text.split(":", 1)[0]
    return text or "localhost"

def passkey_existing_credentials(user_id):
    return rows(
        """
        SELECT *
        FROM passkey_credentials
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (user_id,),
    )

def passkey_credential_count(user_id):
    found = row("SELECT COUNT(*) AS count FROM passkey_credentials WHERE user_id = ?", (user_id,))
    return int(found["count"] if found else 0)

def create_passkey_challenge(user_id, challenge_type, rp_id, origin):
    clean_type = sanitize_text_input(challenge_type, max_length=40).strip()
    if clean_type not in ("registration", "authentication"):
        raise ValueError("Invalid passkey challenge type.")
    token = secrets.token_urlsafe(32)
    challenge = passkey_b64encode(secrets.token_bytes(32))
    expires_at = int(time.time()) + PASSKEY_CHALLENGE_TTL_SECONDS
    with db() as conn:
        conn.execute("DELETE FROM passkey_challenges WHERE expires_at < ?", (int(time.time()),))
        if user_id:
            conn.execute(
                "DELETE FROM passkey_challenges WHERE user_id = ? AND challenge_type = ?",
                (int(user_id), clean_type),
            )
        conn.execute(
            """
            INSERT INTO passkey_challenges
                (token, user_id, challenge, challenge_type, rp_id, origin, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                int(user_id) if user_id else None,
                challenge,
                clean_type,
                passkey_clean_rp_id(rp_id),
                sanitize_text_input(origin, max_length=300).strip(),
                expires_at,
                now_iso(),
            ),
        )
    return {"token": token, "challenge": challenge, "expires_at": expires_at}

def passkey_challenge_row(token, challenge_type):
    clean_token = sanitize_text_input(token, max_length=200).strip()
    clean_type = sanitize_text_input(challenge_type, max_length=40).strip()
    if not clean_token:
        return None
    now_ts = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM passkey_challenges WHERE expires_at < ?", (now_ts,))
        return conn.execute(
            """
            SELECT *
            FROM passkey_challenges
            WHERE token = ?
                AND challenge_type = ?
                AND expires_at >= ?
            """,
            (clean_token, clean_type, now_ts),
        ).fetchone()

def delete_passkey_challenge(token):
    execute("DELETE FROM passkey_challenges WHERE token = ?", (sanitize_text_input(token, max_length=200).strip(),))

def passkey_registration_options(user, rp_id, origin):
    challenge = create_passkey_challenge(user["id"], "registration", rp_id, origin)
    exclude_credentials = [
        {
            "type": "public-key",
            "id": item["credential_id"],
            "transports": json.loads(row_value(item, "transports", "[]") or "[]"),
        }
        for item in passkey_existing_credentials(user["id"])
    ]
    return {
        "token": challenge["token"],
        "publicKey": {
            "challenge": challenge["challenge"],
            "rp": {"name": APP_NAME},
            "user": {
                "id": passkey_user_handle(user["id"]),
                "name": row_value(user, "username", ""),
                "displayName": row_value(user, "display_name", row_value(user, "username", "")),
            },
            "pubKeyCredParams": [{"type": "public-key", "alg": PASSKEY_SUPPORTED_ALG}],
            "timeout": PASSKEY_CHALLENGE_TTL_SECONDS * 1000,
            "attestation": "none",
            "excludeCredentials": exclude_credentials,
            "authenticatorSelection": {
                "residentKey": "preferred",
                "requireResidentKey": False,
                "userVerification": "required",
            },
        },
    }

def passkey_authentication_options(username, rp_id, origin):
    found = get_user_by_username(username)
    if not found or row_value(found, "is_banned", 0) or row_value(found, "registration_status", "active") != "active":
        raise ValueError("No passkeys are available for that username.")
    credentials = passkey_existing_credentials(found["id"])
    if not credentials:
        raise ValueError("No passkeys are available for that username.")
    challenge = create_passkey_challenge(found["id"], "authentication", rp_id, origin)
    return {
        "token": challenge["token"],
        "publicKey": {
            "challenge": challenge["challenge"],
            "timeout": PASSKEY_CHALLENGE_TTL_SECONDS * 1000,
            "userVerification": "required",
            "allowCredentials": [
                {
                    "type": "public-key",
                    "id": item["credential_id"],
                    "transports": json.loads(row_value(item, "transports", "[]") or "[]"),
                }
                for item in credentials
            ],
        },
    }

def cbor_read_length(data, index, additional):
    if additional < 24:
        return additional, index
    if additional == 24:
        return data[index], index + 1
    if additional == 25:
        return int.from_bytes(data[index:index + 2], "big"), index + 2
    if additional == 26:
        return int.from_bytes(data[index:index + 4], "big"), index + 4
    if additional == 27:
        return int.from_bytes(data[index:index + 8], "big"), index + 8
    raise ValueError("Unsupported indefinite-length CBOR value.")

def cbor_read(data, index=0):
    if index >= len(data):
        raise ValueError("Truncated CBOR value.")
    initial = data[index]
    index += 1
    major = initial >> 5
    additional = initial & 0x1F
    length, index = cbor_read_length(data, index, additional)
    if major == 0:
        return length, index
    if major == 1:
        return -1 - length, index
    if major == 2:
        end = index + length
        if end > len(data):
            raise ValueError("Truncated CBOR byte string.")
        return data[index:end], end
    if major == 3:
        end = index + length
        if end > len(data):
            raise ValueError("Truncated CBOR text string.")
        return data[index:end].decode("utf-8", errors="strict"), end
    if major == 4:
        items = []
        for _ in range(length):
            item, index = cbor_read(data, index)
            items.append(item)
        return items, index
    if major == 5:
        found = {}
        for _ in range(length):
            key, index = cbor_read(data, index)
            value, index = cbor_read(data, index)
            found[key] = value
        return found, index
    if major == 7:
        if additional == 20:
            return False, index
        if additional == 21:
            return True, index
        if additional == 22:
            return None, index
    raise ValueError("Unsupported CBOR value.")

def passkey_client_data(client_data_json, expected_type, expected_challenge, expected_origin):
    try:
        client_data = json.loads(client_data_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Passkey client data could not be decoded.") from exc
    if client_data.get("type") != expected_type:
        raise ValueError("Passkey response type did not match the request.")
    if client_data.get("challenge") != expected_challenge:
        raise ValueError("Passkey challenge did not match.")
    if client_data.get("origin") != expected_origin:
        raise ValueError("Passkey origin did not match this site.")
    if client_data.get("crossOrigin"):
        raise ValueError("Cross-origin passkey responses are not accepted.")
    return client_data

def passkey_parse_authenticator_data(auth_data, require_attested=False):
    if len(auth_data) < 37:
        raise ValueError("Passkey authenticator data is incomplete.")
    parsed = {
        "rp_id_hash": auth_data[:32],
        "flags": auth_data[32],
        "sign_count": int.from_bytes(auth_data[33:37], "big"),
    }
    if require_attested:
        if not parsed["flags"] & PASSKEY_AT_FLAG:
            raise ValueError("Passkey registration did not include credential data.")
        offset = 37
        if len(auth_data) < offset + 18:
            raise ValueError("Passkey credential data is incomplete.")
        aaguid = auth_data[offset:offset + 16]
        offset += 16
        credential_length = int.from_bytes(auth_data[offset:offset + 2], "big")
        offset += 2
        credential_id = auth_data[offset:offset + credential_length]
        offset += credential_length
        if not credential_id:
            raise ValueError("Passkey credential id is missing.")
        cose_key, key_end = cbor_read(auth_data, offset)
        parsed.update({
            "aaguid": aaguid.hex(),
            "credential_id": credential_id,
            "credential_public_key": cose_key,
            "credential_public_key_bytes": auth_data[offset:key_end],
        })
    return parsed

def passkey_require_flags(flags):
    if not flags & PASSKEY_UP_FLAG:
        raise ValueError("Passkey user presence was not verified.")
    if not flags & PASSKEY_UV_FLAG:
        raise ValueError("Passkey user verification is required.")

def passkey_validate_rp_hash(auth_data, rp_id):
    expected = hashlib.sha256(passkey_clean_rp_id(rp_id).encode("utf-8")).digest()
    if not hmac.compare_digest(auth_data["rp_id_hash"], expected):
        raise ValueError("Passkey relying-party id did not match this site.")

def passkey_parse_cose_ec2_key(cose_key):
    if not isinstance(cose_key, dict):
        raise ValueError("Passkey public key is not a COSE key.")
    if cose_key.get(1) != 2 or cose_key.get(3) != PASSKEY_SUPPORTED_ALG or cose_key.get(-1) != 1:
        raise ValueError("Only ES256 passkeys are supported right now.")
    x = cose_key.get(-2)
    y = cose_key.get(-3)
    if not isinstance(x, bytes) or not isinstance(y, bytes) or len(x) != 32 or len(y) != 32:
        raise ValueError("Passkey public key coordinates are invalid.")
    return x, y

def p256_inverse(value, modulo):
    if value == 0:
        raise ZeroDivisionError("No inverse for zero.")
    return pow(value, -1, modulo)

def p256_is_on_curve(point):
    if point is None:
        return True
    x, y = point
    if not (0 <= x < P256_P and 0 <= y < P256_P):
        return False
    return (y * y - (x * x * x + P256_A * x + P256_B)) % P256_P == 0

def p256_point_add(left, right):
    if left is None:
        return right
    if right is None:
        return left
    x1, y1 = left
    x2, y2 = right
    if x1 == x2 and (y1 + y2) % P256_P == 0:
        return None
    if left == right:
        slope = ((3 * x1 * x1 + P256_A) * p256_inverse(2 * y1 % P256_P, P256_P)) % P256_P
    else:
        slope = ((y2 - y1) * p256_inverse((x2 - x1) % P256_P, P256_P)) % P256_P
    x3 = (slope * slope - x1 - x2) % P256_P
    y3 = (slope * (x1 - x3) - y1) % P256_P
    return x3, y3

def p256_scalar_mult(scalar, point):
    scalar = int(scalar) % P256_N
    result = None
    addend = point
    while scalar:
        if scalar & 1:
            result = p256_point_add(result, addend)
        addend = p256_point_add(addend, addend)
        scalar >>= 1
    return result

def ecdsa_der_signature_rs(signature):
    data = bytes(signature)
    if len(data) < 8 or data[0] != 0x30:
        raise ValueError("Passkey signature was not DER encoded.")
    index = 2
    if data[1] & 0x80:
        length_octets = data[1] & 0x7F
        if not length_octets or length_octets > 2 or index + length_octets > len(data):
            raise ValueError("Passkey signature length was invalid.")
        total_length = int.from_bytes(data[index:index + length_octets], "big")
        index += length_octets
    else:
        total_length = data[1]
    if index + total_length != len(data):
        raise ValueError("Passkey signature length was invalid.")
    if data[index] != 0x02:
        raise ValueError("Passkey signature did not contain r.")
    r_length = data[index + 1]
    index += 2
    if r_length <= 0 or index + r_length > len(data):
        raise ValueError("Passkey signature r length was invalid.")
    r = int.from_bytes(data[index:index + r_length], "big")
    index += r_length
    if index >= len(data) or data[index] != 0x02:
        raise ValueError("Passkey signature did not contain s.")
    s_length = data[index + 1]
    index += 2
    if s_length <= 0 or index + s_length != len(data):
        raise ValueError("Passkey signature s length was invalid.")
    s = int.from_bytes(data[index:index + s_length], "big")
    return r, s

def ecdsa_verify_p256(public_x, public_y, message, signature):
    q = (int.from_bytes(public_x, "big"), int.from_bytes(public_y, "big"))
    if not p256_is_on_curve(q):
        return False
    try:
        r, s = ecdsa_der_signature_rs(signature)
    except ValueError:
        return False
    if not (1 <= r < P256_N and 1 <= s < P256_N):
        return False
    digest = hashlib.sha256(message).digest()
    z = int.from_bytes(digest, "big")
    w = p256_inverse(s, P256_N)
    u1 = (z * w) % P256_N
    u2 = (r * w) % P256_N
    point = p256_point_add(
        p256_scalar_mult(u1, (P256_GX, P256_GY)),
        p256_scalar_mult(u2, q),
    )
    if point is None:
        return False
    return (point[0] % P256_N) == r

def passkey_payload_value(payload, *path):
    value = payload
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value if isinstance(value, str) else ""

def parse_passkey_payload(payload_json):
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Passkey response was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Passkey response was not valid JSON.")
    return payload

def complete_passkey_registration(user_id, token, payload_json, nickname=""):
    challenge = passkey_challenge_row(token, "registration")
    if not challenge or int(challenge["user_id"] or 0) != int(user_id):
        raise ValueError("Passkey setup expired. Try adding the passkey again.")
    payload = parse_passkey_payload(payload_json)
    client_data_json = passkey_b64decode(passkey_payload_value(payload, "response", "clientDataJSON"))
    attestation_object = passkey_b64decode(passkey_payload_value(payload, "response", "attestationObject"))
    raw_id = passkey_b64decode(payload.get("rawId") or payload.get("id"))
    if not client_data_json or not attestation_object or not raw_id:
        raise ValueError("Passkey setup response was incomplete.")
    passkey_client_data(client_data_json, "webauthn.create", challenge["challenge"], challenge["origin"])
    try:
        attestation, _ = cbor_read(attestation_object, 0)
    except ValueError as exc:
        raise ValueError("Passkey attestation could not be decoded.") from exc
    auth_data_bytes = attestation.get("authData") if isinstance(attestation, dict) else None
    if not isinstance(auth_data_bytes, bytes):
        raise ValueError("Passkey attestation did not include authenticator data.")
    auth_data = passkey_parse_authenticator_data(auth_data_bytes, require_attested=True)
    passkey_require_flags(auth_data["flags"])
    passkey_validate_rp_hash(auth_data, challenge["rp_id"])
    credential_id = auth_data["credential_id"]
    if not hmac.compare_digest(credential_id, raw_id):
        raise ValueError("Passkey credential id did not match.")
    public_x, public_y = passkey_parse_cose_ec2_key(auth_data["credential_public_key"])
    transports = payload.get("transports")
    if not isinstance(transports, list):
        transports = []
    transports = [sanitize_text_input(item, max_length=40).strip() for item in transports if str(item or "").strip()]
    clean_nickname = sanitize_text_input(nickname, max_length=80).strip() or "Passkey"
    timestamp = now_iso()
    with db() as conn:
        existing = conn.execute("SELECT id FROM passkey_credentials WHERE credential_id = ?", (passkey_b64encode(credential_id),)).fetchone()
        if existing:
            raise ValueError("That passkey is already registered.")
        cursor = conn.execute(
            """
            INSERT INTO passkey_credentials
                (user_id, credential_id, public_key_cose, public_key_x, public_key_y,
                 sign_count, nickname, aaguid, transports, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                passkey_b64encode(credential_id),
                passkey_b64encode(auth_data["credential_public_key_bytes"]),
                passkey_b64encode(public_x),
                passkey_b64encode(public_y),
                auth_data["sign_count"],
                clean_nickname,
                auth_data["aaguid"],
                json.dumps(transports, separators=(",", ":")),
                timestamp,
            ),
        )
        conn.execute("DELETE FROM passkey_challenges WHERE token = ?", (challenge["token"],))
        return cursor.lastrowid

def complete_passkey_authentication(token, payload_json):
    challenge = passkey_challenge_row(token, "authentication")
    if not challenge:
        raise ValueError("Passkey sign-in expired. Try again.")
    payload = parse_passkey_payload(payload_json)
    credential_id = passkey_b64encode(passkey_b64decode(payload.get("rawId") or payload.get("id")))
    if not credential_id:
        raise ValueError("Passkey sign-in response was incomplete.")
    credential = row("SELECT * FROM passkey_credentials WHERE credential_id = ?", (credential_id,))
    if not credential or int(credential["user_id"]) != int(challenge["user_id"] or 0):
        raise ValueError("That passkey is not registered for this sign-in.")
    user = row(
        "SELECT * FROM users WHERE id = ? AND is_banned = 0 AND registration_status = 'active'",
        (credential["user_id"],),
    )
    if not user:
        raise ValueError("That passkey is not available.")
    client_data_json = passkey_b64decode(passkey_payload_value(payload, "response", "clientDataJSON"))
    authenticator_data = passkey_b64decode(passkey_payload_value(payload, "response", "authenticatorData"))
    signature = passkey_b64decode(passkey_payload_value(payload, "response", "signature"))
    if not client_data_json or not authenticator_data or not signature:
        raise ValueError("Passkey sign-in response was incomplete.")
    passkey_client_data(client_data_json, "webauthn.get", challenge["challenge"], challenge["origin"])
    auth_data = passkey_parse_authenticator_data(authenticator_data)
    passkey_require_flags(auth_data["flags"])
    passkey_validate_rp_hash(auth_data, challenge["rp_id"])
    signed_data = authenticator_data + hashlib.sha256(client_data_json).digest()
    public_x = passkey_b64decode(credential["public_key_x"])
    public_y = passkey_b64decode(credential["public_key_y"])
    if not ecdsa_verify_p256(public_x, public_y, signed_data, signature):
        raise ValueError("Passkey signature could not be verified.")
    stored_count = int(row_value(credential, "sign_count", 0) or 0)
    new_count = int(auth_data["sign_count"] or 0)
    if stored_count and new_count and new_count <= stored_count:
        raise ValueError("Passkey signature counter did not advance.")
    execute(
        """
        UPDATE passkey_credentials
        SET sign_count = ?, last_used_at = ?
        WHERE id = ?
        """,
        (max(stored_count, new_count), now_iso(), credential["id"]),
    )
    delete_passkey_challenge(token)
    return user, credential

def delete_passkey_credential(user_id, credential_id):
    with db() as conn:
        cursor = conn.execute(
            "DELETE FROM passkey_credentials WHERE id = ? AND user_id = ?",
            (int(credential_id), int(user_id)),
        )
        return cursor.rowcount

def create_session(user_id):
    user = row("SELECT id FROM users WHERE id = ? AND is_banned = 0 AND registration_status = 'active'", (user_id,))
    if not user:
        raise ValueError("Only active accounts can start a session.")
    token = secrets.token_urlsafe(32)
    expires_at = int(datetime.now(timezone.utc).timestamp()) + SESSION_TTL_SECONDS
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (session_token_hash(token), user_id, expires_at, now_iso()),
        )
    return token, expires_at

def delete_session(token):
    token_hash = session_token_hash(token)
    if not token_hash:
        return
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

def get_user_by_session(token):
    token_hash = session_token_hash(token)
    if not token_hash:
        return None
    now_ts = int(datetime.now(timezone.utc).timestamp())
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_ts,))
        return conn.execute(
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
                AND sessions.expires_at >= ?
                AND users.is_banned = 0
                AND users.registration_status = 'active'
            """,
            (token_hash, now_ts),
        ).fetchone()

def get_user_by_username(username):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (sanitize_text_input(username, max_length=40).strip(),)).fetchone()

def create_user(username, password, display_name, is_admin=None, email="", role=None, registration_status="active"):
    username = validate_username(username)
    display_name = sanitize_text_input(display_name, max_length=80).strip() or username
    email = validate_email(email)
    created_at = now_iso()
    with db() as conn:
        if is_admin is None:
            is_admin = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"] == 0
        if role is None:
            role = ROLE_OWNER if is_admin else ROLE_MEMBER
        role = normalize_user_role(role, is_admin=is_admin)
        is_admin = bool(role_sync_is_admin(role))
        registration_status = sanitize_text_input(registration_status, max_length=20).strip().lower()
        if registration_status not in ("active", "pending", "denied"):
            registration_status = "active"
        cursor = conn.execute(
            """
            INSERT INTO users
                (username, password_hash, email, display_name, role, registration_status, is_admin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (username, hash_password(password), email, display_name, role, registration_status, 1 if is_admin else 0, created_at, created_at),
        )
        return cursor.lastrowid

__all__ = [
    'hash_password',
    'verify_password',
    'generate_totp_secret',
    'normalize_totp_secret',
    'normalize_totp_code',
    'totp_secret_bytes',
    'totp_code',
    'verify_totp_code',
    'user_totp_label',
    'totp_otpauth_uri',
    'format_totp_secret',
    'QR_VERSION',
    'QR_SIZE',
    'QR_DATA_CODEWORDS',
    'QR_ECC_CODEWORDS_PER_BLOCK',
    'QR_BLOCK_COUNT',
    'QR_DATA_CODEWORDS_PER_BLOCK',
    'qr_gf_tables',
    'QR_GF_EXP',
    'QR_GF_LOG',
    'qr_gf_multiply',
    'qr_reed_solomon_generator',
    'qr_reed_solomon_remainder',
    'qr_bit_buffer_for_text',
    'qr_codewords_for_text',
    'qr_empty_matrix',
    'qr_set_function',
    'qr_draw_finder',
    'qr_draw_alignment',
    'qr_draw_function_patterns',
    'qr_format_bits',
    'qr_draw_format_bits',
    'qr_matrix',
    'qr_svg',
    'generate_recovery_codes',
    'normalize_recovery_code',
    'hash_recovery_code',
    'verify_recovery_code',
    'recovery_code_hashes',
    'load_recovery_code_hashes',
    'two_factor_enabled',
    'user_totp_setup_details',
    'start_user_totp_setup',
    'enable_user_totp',
    'disable_user_totp',
    'regenerate_user_totp_recovery_codes',
    'consume_user_recovery_code',
    'verify_user_two_factor',
    'create_two_factor_challenge',
    'two_factor_challenge',
    'delete_two_factor_challenge',
    'complete_two_factor_login',
    'PASSKEY_CHALLENGE_TTL_SECONDS',
    'PASSKEY_SUPPORTED_ALG',
    'PASSKEY_UP_FLAG',
    'PASSKEY_UV_FLAG',
    'PASSKEY_AT_FLAG',
    'P256_P',
    'P256_A',
    'P256_B',
    'P256_GX',
    'P256_GY',
    'P256_N',
    'passkey_b64encode',
    'passkey_b64decode',
    'passkey_user_handle',
    'passkey_clean_rp_id',
    'passkey_existing_credentials',
    'passkey_credential_count',
    'create_passkey_challenge',
    'passkey_challenge_row',
    'delete_passkey_challenge',
    'passkey_registration_options',
    'passkey_authentication_options',
    'cbor_read_length',
    'cbor_read',
    'passkey_client_data',
    'passkey_parse_authenticator_data',
    'passkey_require_flags',
    'passkey_validate_rp_hash',
    'passkey_parse_cose_ec2_key',
    'p256_inverse',
    'p256_is_on_curve',
    'p256_point_add',
    'p256_scalar_mult',
    'ecdsa_der_signature_rs',
    'ecdsa_verify_p256',
    'passkey_payload_value',
    'parse_passkey_payload',
    'complete_passkey_registration',
    'complete_passkey_authentication',
    'delete_passkey_credential',
    'session_token_hash',
    'create_session',
    'delete_session',
    'get_user_by_session',
    'get_user_by_username',
    'create_user',
]
