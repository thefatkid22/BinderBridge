"""API idempotency coverage for Android offline-write replay."""

from http import HTTPStatus

from binderbridge.api_idempotency import (
    begin_idempotent_request,
    finalize_idempotent_request,
)
from tests.base import *  # noqa: F401,F403


class FailingWriter:
    def write(self, _data):
        raise BrokenPipeError("simulated response loss")


class IdempotentApiRequest:
    def __init__(self, token, method, path, body=None, key="", writer=None):
        encoded = json.dumps(body or {}).encode("utf-8")
        self.command = method
        self._request_method = method
        self._request_path = path
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Length": str(len(encoded)),
            "Content-Type": "application/json",
            "User-Agent": "BinderBridge Android idempotency test",
        }
        if key:
            self.headers["Idempotency-Key"] = key
        self.rfile = io.BytesIO(encoded)
        self.wfile = writer or io.BytesIO()
        self.response_status = None
        self.response_headers = {}

    def __getattr__(self, name):
        if name.startswith("api_"):
            return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
        raise AttributeError(name)

    def send_response(self, status):
        self.response_status = int(status)

    def send_header(self, name, value):
        self.response_headers[name] = value

    def send_security_headers(self):
        return None

    def end_headers(self):
        return None

    def client_ip(self):
        return "203.0.113.80"

    def json_response(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class ApiIdempotencyTests(BinderBridgeTestCase):
    def setUp(self):
        super().setUp()
        self.user_id = app.create_user("idempotent-user", "password123", "Idempotent User")
        self.token = app.create_api_token(
            self.user_id,
            "Android idempotency test",
            ["read", "write"],
        )["token"]

    def collection_payload(self, quantity=1):
        return {
            "game": "pokemon",
            "card_name": "Pikachu",
            "quantity": quantity,
            "merge": False,
        }

    def dispatch(self, request):
        app.api_dispatch(
            request,
            request._request_method,
            request._request_path,
            {},
        )
        return request

    def test_response_loss_replays_original_response_without_duplicate_write(self):
        key = "android-write-550e8400-e29b-41d4-a716-446655440000"
        first = IdempotentApiRequest(
            self.token,
            "POST",
            "/api/v1/collection",
            self.collection_payload(),
            key,
            FailingWriter(),
        )

        with self.assertRaises(BrokenPipeError):
            self.dispatch(first)

        self.assertEqual(
            app.row(
                "SELECT COUNT(*) AS count FROM collection_items WHERE user_id = ?",
                (self.user_id,),
            )["count"],
            1,
        )
        stored = app.row(
            "SELECT * FROM api_idempotency_keys WHERE user_id = ?",
            (self.user_id,),
        )
        self.assertIsNotNone(stored)
        self.assertNotEqual(stored["key_hash"], key)
        self.assertEqual(stored["state"], "completed")
        self.assertEqual(stored["response_status"], HTTPStatus.CREATED)

        replay = self.dispatch(
            IdempotentApiRequest(
                self.token,
                "POST",
                "/api/v1/collection",
                dict(reversed(list(self.collection_payload().items()))),
                key,
            )
        )
        self.assertEqual(replay.response_status, HTTPStatus.CREATED)
        self.assertEqual(replay.response_headers.get("Idempotency-Replayed"), "true")
        self.assertEqual(
            replay.json_response()["data"]["id"],
            json.loads(stored["response_json"])["data"]["id"],
        )
        self.assertEqual(
            app.row(
                "SELECT COUNT(*) AS count FROM collection_items WHERE user_id = ?",
                (self.user_id,),
            )["count"],
            1,
        )

    def test_same_key_with_different_request_is_rejected(self):
        key = "android-write-11111111-2222-3333-4444-555555555555"
        first = self.dispatch(
            IdempotentApiRequest(
                self.token,
                "POST",
                "/api/v1/collection",
                self.collection_payload(1),
                key,
            )
        )
        self.assertEqual(first.response_status, HTTPStatus.CREATED)

        mismatch = self.dispatch(
            IdempotentApiRequest(
                self.token,
                "POST",
                "/api/v1/collection",
                self.collection_payload(2),
                key,
            )
        )
        self.assertEqual(mismatch.response_status, HTTPStatus.CONFLICT)
        self.assertIn("different request", mismatch.json_response()["error"])
        self.assertEqual(
            app.row(
                "SELECT COUNT(*) AS count FROM collection_items WHERE user_id = ?",
                (self.user_id,),
            )["count"],
            1,
        )

    def test_keys_are_account_scoped_and_invalid_keys_are_not_stored(self):
        key = "android-write-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        first = self.dispatch(
            IdempotentApiRequest(
                self.token,
                "POST",
                "/api/v1/collection",
                self.collection_payload(),
                key,
            )
        )
        self.assertEqual(first.response_status, HTTPStatus.CREATED)

        other_user_id = app.create_user("idempotent-other", "password123", "Other User")
        other_token = app.create_api_token(
            other_user_id,
            "Other Android device",
            ["read", "write"],
        )["token"]
        other = self.dispatch(
            IdempotentApiRequest(
                other_token,
                "POST",
                "/api/v1/collection",
                self.collection_payload(),
                key,
            )
        )
        self.assertEqual(other.response_status, HTTPStatus.CREATED)
        self.assertEqual(
            app.row("SELECT COUNT(*) AS count FROM api_idempotency_keys")["count"],
            2,
        )

        invalid = self.dispatch(
            IdempotentApiRequest(
                self.token,
                "POST",
                "/api/v1/collection",
                self.collection_payload(),
                "short",
            )
        )
        self.assertEqual(invalid.response_status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(
            app.row("SELECT COUNT(*) AS count FROM api_idempotency_keys")["count"],
            2,
        )

    def test_server_errors_release_key_for_retry(self):
        key = "android-write-error-12345678-1234-1234-1234-123456789abc"
        request = IdempotentApiRequest(
            self.token,
            "POST",
            "/api/v1/collection",
            self.collection_payload(),
            key,
        )
        request._api_idempotency_context = begin_idempotent_request(
            self.user_id,
            key,
            "POST",
            "/api/v1/collection",
            self.collection_payload(),
        )["context"]

        app.api_json(
            request,
            {"error": "temporary failure"},
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

        self.assertIsNone(
            app.row(
                "SELECT * FROM api_idempotency_keys WHERE user_id = ?",
                (self.user_id,),
            )
        )

    def test_processing_keys_pause_duplicates_and_expired_records_restart(self):
        key = "android-write-processing-1234-5678-90ab-cdef12345678"
        first = begin_idempotent_request(
            self.user_id,
            key,
            "POST",
            "/api/v1/collection",
            self.collection_payload(),
        )
        self.assertEqual(first["state"], "new")

        duplicate = begin_idempotent_request(
            self.user_id,
            key,
            "POST",
            "/api/v1/collection",
            self.collection_payload(),
        )
        self.assertEqual(duplicate["state"], "processing")

        with app.db() as conn:
            conn.execute(
                "UPDATE api_idempotency_keys SET expires_at = ? WHERE user_id = ?",
                ("2000-01-01T00:00:00+00:00", self.user_id),
            )
        restarted = begin_idempotent_request(
            self.user_id,
            key,
            "POST",
            "/api/v1/collection",
            self.collection_payload(),
        )
        self.assertEqual(restarted["state"], "new")
        finalize_idempotent_request(
            restarted["context"],
            {"error": "test cleanup"},
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def test_schema_has_bounded_idempotency_storage(self):
        indexes = {
            item["name"]
            for item in app.rows("PRAGMA index_list(api_idempotency_keys)")
        }
        self.assertIn("idx_api_idempotency_expiry", indexes)
        self.assertEqual(
            app.get_setting(app.SCHEMA_VERSION_KEY),
            str(app.CURRENT_SCHEMA_VERSION),
        )


if __name__ == "__main__":
    unittest.main()
