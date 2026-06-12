"""Shared BinderBridge test case and test-only helpers."""

import csv
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app
from binderbridge import config as bb_config
from tests import factories as factory


def _der_integer(value):
    data = int(value).to_bytes(max(1, (int(value).bit_length() + 7) // 8), "big")
    if data[0] & 0x80:
        data = b"\x00" + data
    return b"\x02" + bytes([len(data)]) + data


def _der_signature(r, s):
    payload = _der_integer(r) + _der_integer(s)
    return b"\x30" + bytes([len(payload)]) + payload


def _p256_sign(message, private_key, nonce=9):
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    k = nonce % app.P256_N
    while True:
        point = app.p256_scalar_mult(k, (app.P256_GX, app.P256_GY))
        r = point[0] % app.P256_N if point else 0
        if r:
            s = (app.p256_inverse(k, app.P256_N) * (z + r * private_key)) % app.P256_N
            if s:
                return _der_signature(r, s)
        k = (k + 1) % app.P256_N


class BinderBridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DATA_DIR = Path(self.tmpdir.name)
        app.DB_PATH = app.DATA_DIR / "test.sqlite3"
        app.init_db()
        app.clear_rate_limits()

    def tearDown(self):
        self.tmpdir.cleanup()


__all__ = [
    "BinderBridgeTestCase",
    "app",
    "bb_config",
    "factory",
    "csv",
    "hashlib",
    "io",
    "json",
    "os",
    "tempfile",
    "time",
    "unittest",
    "zipfile",
    "datetime",
    "timedelta",
    "timezone",
    "Path",
    "_der_integer",
    "_der_signature",
    "_p256_sign",
]
