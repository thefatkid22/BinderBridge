"""Durable background job runner tests."""

from tests.base import *  # noqa: F401,F403


class BackgroundJobRunnerTests(BinderBridgeTestCase):
    def test_background_job_schema_and_indexes_exist(self):
        columns = {item["name"] for item in app.rows("PRAGMA table_info(background_jobs)")}
        indexes = {item["name"] for item in app.rows("PRAGMA index_list(background_jobs)")}

        self.assertTrue({
            "job_type",
            "unique_key",
            "payload_json",
            "status",
            "available_at",
            "lease_owner",
            "leased_until",
            "result_json",
            "last_error",
        }.issubset(columns))
        self.assertTrue({
            "idx_background_jobs_claim",
            "idx_background_jobs_type_status",
            "idx_background_jobs_lease",
            "idx_background_jobs_active_unique",
        }.issubset(indexes))

    def test_background_jobs_deduplicate_expedite_claim_and_finish(self):
        job_id, created = app.enqueue_background_job(
            "notification_delivery",
            {"source": "test"},
            unique_key="test:dedupe",
            delay_seconds=3600,
        )
        duplicate_id, duplicate_created = app.enqueue_background_job(
            "notification_delivery",
            {"source": "duplicate"},
            unique_key="test:dedupe",
        )

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate_id, job_id)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM background_jobs")["count"], 1)

        app.expedite_background_job("test:dedupe")
        claimed = app.claim_background_job("test-worker", lease_seconds=120)
        self.assertEqual(claimed["id"], job_id)
        self.assertEqual(claimed["status"], "running")
        self.assertEqual(claimed["attempts"], 1)
        self.assertEqual(claimed["lease_owner"], "test-worker")

        app.finish_background_job(claimed, "test-worker", {"ok": True})
        finished = app.row("SELECT * FROM background_jobs WHERE id = ?", (job_id,))
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(json.loads(finished["result_json"]), {"ok": True})
        self.assertTrue(finished["completed_at"])

    def test_expired_background_job_lease_returns_to_queue(self):
        job_id, _created = app.enqueue_background_job("notification_delivery", unique_key="test:lease")
        claimed = app.claim_background_job("lost-worker")
        app.execute(
            "UPDATE background_jobs SET leased_until = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (claimed["id"],),
        )

        recovered = app.recover_expired_background_jobs()
        job = app.row("SELECT * FROM background_jobs WHERE id = ?", (job_id,))

        self.assertEqual(recovered, 1)
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["lease_owner"], "")
        self.assertIn("lease expired", job["last_error"].lower())

    def test_failed_background_job_can_be_retried_and_cancelled(self):
        original_handler = app.BACKGROUND_JOB_HANDLERS["notification_delivery"]

        def fail_handler(job, payload, worker_id):
            raise RuntimeError("SMTP exploded")

        app.BACKGROUND_JOB_HANDLERS["notification_delivery"] = fail_handler
        try:
            job_id, _created = app.enqueue_background_job(
                "notification_delivery",
                unique_key="test:failure",
                max_attempts=1,
            )
            self.assertTrue(app.process_background_job_once("failure-worker"))
        finally:
            app.BACKGROUND_JOB_HANDLERS["notification_delivery"] = original_handler

        failed = app.row("SELECT * FROM background_jobs WHERE id = ?", (job_id,))
        self.assertEqual(failed["status"], "failed")
        self.assertIn("SMTP exploded", failed["last_error"])

        app.retry_background_job(job_id)
        retried = app.row("SELECT * FROM background_jobs WHERE id = ?", (job_id,))
        self.assertEqual(retried["status"], "pending")
        self.assertEqual(retried["attempts"], 0)

        app.cancel_background_job(job_id)
        cancelled = app.row("SELECT * FROM background_jobs WHERE id = ?", (job_id,))
        self.assertEqual(cancelled["status"], "cancelled")

    def test_recurring_background_job_reuses_active_row(self):
        original_handler = app.BACKGROUND_JOB_HANDLERS["notification_delivery"]
        app.BACKGROUND_JOB_HANDLERS["notification_delivery"] = lambda job, payload, worker_id: {
            "sent": 2,
            "repeat_seconds": 60,
        }
        try:
            job_id, _created = app.enqueue_background_job(
                "notification_delivery",
                unique_key="test:recurring",
            )
            self.assertTrue(app.process_background_job_once("recurring-worker"))
        finally:
            app.BACKGROUND_JOB_HANDLERS["notification_delivery"] = original_handler

        recurring = app.row("SELECT * FROM background_jobs WHERE id = ?", (job_id,))
        self.assertEqual(recurring["status"], "pending")
        self.assertEqual(recurring["attempts"], 0)
        self.assertEqual(json.loads(recurring["result_json"]), {"sent": 2})
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM background_jobs")["count"], 1)

    def test_schedule_seed_and_legacy_worker_entrypoint_queue_durable_jobs(self):
        app.ensure_background_job_schedules()
        app.start_scryfall_enrichment_worker()
        keys = {
            item["unique_key"]
            for item in app.rows("SELECT unique_key FROM background_jobs WHERE status = 'pending'")
        }

        self.assertTrue({
            "system:scryfall-price-refresh",
            "system:automatic-backup",
            "system:notification-delivery",
            "system:scryfall-enrichment",
        }.issubset(keys))

    def test_scryfall_orchestrator_waits_for_future_domain_retry(self):
        user_id = factory.create_user("futurelookup")
        card_id = factory.create_collection_item(user_id, "Future Sight")
        timestamp = app.now_iso()
        app.execute(
            """
            INSERT INTO scryfall_enrichment_jobs
                (collection_item_id, user_id, lookup_key, card_name, status, available_at, created_at, updated_at)
            VALUES (?, ?, 'future-sight', 'Future Sight', 'pending', ?, ?, ?)
            """,
            (card_id, user_id, app.future_iso(120), timestamp, timestamp),
        )
        job_id, _created = app.enqueue_background_job(
            "scryfall_enrichment",
            unique_key="test:future-scryfall",
        )

        self.assertTrue(app.process_background_job_once("future-scryfall-worker"))
        orchestrator = app.row("SELECT * FROM background_jobs WHERE id = ?", (job_id,))
        self.assertEqual(orchestrator["status"], "pending")
        self.assertEqual(json.loads(orchestrator["result_json"])["pending"], True)
        self.assertGreater(orchestrator["available_at"], timestamp)

    def test_notification_creation_atomically_schedules_delivery(self):
        user_id = app.create_user("queuedmail", "password123", "Queued Mail", email="queued@example.test")
        app.execute(
            """
            UPDATE users
            SET email_trade_notifications_enabled = 1,
                email_price_alert_enabled = 1
            WHERE id = ?
            """,
            (user_id,),
        )
        original_configured = app.email_delivery_configured
        app.email_delivery_configured = lambda: True
        try:
            notification_id = app.create_notification(user_id, "price_alert", "Price moved")
        finally:
            app.email_delivery_configured = original_configured

        notification = app.row("SELECT * FROM user_notifications WHERE id = ?", (notification_id,))
        queued = app.row(
            "SELECT * FROM background_jobs WHERE unique_key = 'system:notification-delivery'"
        )
        self.assertEqual(notification["email_status"], "pending")
        self.assertEqual(queued["status"], "pending")


if __name__ == "__main__":
    unittest.main()
