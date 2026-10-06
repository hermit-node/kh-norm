from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from norm_runtime.prompt_worker import PromptWorker


class QueueStub:
    def __init__(self):
        self.consumer = ""

    def oldest_task_id(self):
        return None


class DurableStub:
    def __init__(self):
        self.state = {}

    def get_runtime_state(self, key, default=None):
        return self.state.get(key, default)

    def set_runtime_state(self, key, value):
        self.state[key] = value

    def delete_runtime_state(self, key):
        self.state.pop(key, None)


class RedisStub:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, **_kwargs):
        self.values[key] = value
        return True

    def delete(self, key):
        self.values.pop(key, None)


class MaintenanceControlTests(unittest.TestCase):
    def make_worker(self):
        durable = DurableStub()
        redis = RedisStub()
        queue = QueueStub()
        worker = PromptWorker(queue, None, None, durable, poll_ms=100)
        worker._runtime_config = lambda: {"maintenance": {}}
        worker._maintenance_redis_client = lambda: redis
        return worker, durable, redis

    def test_suppress_task_falls_through_to_active_maintenance(self):
        worker, durable, redis = self.make_worker()
        key = "norm:maintenance:weekly_cleanup:active"
        redis.values[key] = json.dumps({
            "status": "cleanup_started",
            "run_id": "run-123",
            "mode": "regular",
            "phase": "background_memory",
        })

        result = worker.request_suppress_task(reason="operator test")
        self.assertTrue(result["suppressed"], result)
        self.assertTrue(result["maintenance"], result)
        self.assertEqual(result["mode"], "regular")
        self.assertEqual(result["phase"], "background_memory")

        parked = durable.get_runtime_state("weekly_maintenance_parked")
        self.assertEqual(parked["status"], "suppressed")
        self.assertEqual(parked["run_id"], "run-123")
        self.assertEqual(json.loads(redis.values[key])["status"], "suppressed")
        self.assertTrue(worker._maintenance_suppress_requested.is_set())

    def test_resume_maintenance_clears_durable_park_and_marks_manual_resume(self):
        worker, durable, redis = self.make_worker()
        key = "norm:maintenance:weekly_cleanup:active"
        marker = {
            "status": "requires_attention",
            "run_id": "run-456",
            "mode": "regular",
            "phase": "background_memory",
            "failure_kind": "model_output_truncated",
            "error": "too long",
        }
        redis.values[key] = json.dumps(marker)
        durable.set_runtime_state("weekly_maintenance_parked", {
            "status": "requires_attention",
            "run_id": "run-456",
            "mode": "regular",
            "phase": "background_memory",
            "reason": "too long",
        })

        result = worker.request_resume_maintenance()
        self.assertTrue(result["resumed"], result)
        self.assertIsNone(durable.get_runtime_state("weekly_maintenance_parked"))
        resumed = json.loads(redis.values[key])
        self.assertEqual(resumed["status"], "cleanup_resumed")
        self.assertTrue(resumed["resumed_by_operator"])
        self.assertTrue(resumed["auto_resume"])
        self.assertNotIn("failure_kind", resumed)
        self.assertNotIn("error", resumed)

    def test_parked_maintenance_status_is_not_active(self):
        worker, durable, redis = self.make_worker()
        key = "norm:maintenance:weekly_cleanup:active"
        redis.values[key] = json.dumps({
            "status": "requires_attention",
            "run_id": "run-789",
            "mode": "regular",
            "phase": "background_memory",
        })
        durable.set_runtime_state("weekly_maintenance_parked", {
            "status": "requires_attention",
            "run_id": "run-789",
            "mode": "regular",
            "phase": "background_memory",
            "reason": "output budget exhausted",
        })
        status = worker.maintenance_status()
        self.assertFalse(status["active"])
        self.assertTrue(status["parked"])
        self.assertEqual(status["status"], "requires_attention")

    def test_regular_snapshot_contract_is_tight_and_not_source_by_source(self):
        source = (ROOT / "core" / "norm_runtime" / "history_maintenance.py").read_text(encoding="utf-8")
        config = (ROOT / "config" / "runtime.json").read_text(encoding="utf-8")
        self.assertIn("consolidation_batch_target_chars", config)
        self.assertIn("consolidation_snapshot_target_chars", config)
        self.assertIn("Do NOT produce one bullet/line per source record", source)
        self.assertIn("This is NOT an archive", source)
        self.assertIn("do not continue it", source)
        self.assertIn("num_predict=900, max_segments=2", source)
        self.assertIn("num_predict=1800, max_segments=2", source)
        self.assertIn("soft target", source)
        self.assertNotIn("refused compression target", source)

    def test_output_truncation_parks_scheduled_maintenance(self):
        source = (ROOT / "core" / "norm_runtime" / "prompt_worker.py").read_text(encoding="utf-8")
        self.assertIn("except ModelOutputTruncated as exc:", source)
        self.assertIn('"status": "requires_attention"', source)
        self.assertIn('"auto_resume": False', source)
        self.assertIn('set_runtime_state("weekly_maintenance_parked"', source)

    def test_prompt_queue_surfaces_maintenance_and_resume_control(self):
        gui = (ROOT / "tools" / "norm_gui_prompt.py").read_text(encoding="utf-8")
        common = (ROOT / "tools" / "norm_gui_common.py").read_text(encoding="utf-8")
        self.assertIn("print_maintenance_status(maintenance_from_busy(ep))", gui)
        self.assertIn('lowered == "/resume-task maintenance"', gui)
        self.assertIn('"resume_maintenance"', common)


if __name__ == "__main__":
    unittest.main(verbosity=2)
