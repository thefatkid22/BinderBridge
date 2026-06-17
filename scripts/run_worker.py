"""Run BinderBridge's durable background job worker as a separate process."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


if __name__ == "__main__":
    app.init_db()
    app.write_log_message("BinderBridge external background worker started.", stream=sys.stdout)
    app.run_background_worker_forever(worker_id="external-worker", seed_schedules=True)
