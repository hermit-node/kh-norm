from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.history_maintenance import DeepHistoryMaintainer


class FakeClient:
    def __init__(self):
        self.complete_calls = []

    def generate_complete_text(self, prompt, **kwargs):
        self.complete_calls.append((prompt, kwargs))
        return "prior-task 2026-10-04 recovered useful lessons"

    def generate(self, prompt, **kwargs):
        if kwargs.get("response_format"):
            return json.dumps({
                "prior_task_identified": True,
                "request_understood": True,
                "lessons_used": True,
                "sufficient_context": True,
                "issues": [],
            })
        raise AssertionError("replay generation must use continuation-aware generate_complete_text")

    @staticmethod
    def parse_json(raw):
        return json.loads(raw)


class MaintenanceReliabilityTests(unittest.TestCase):
    def test_checkpoint_write_replaces_existing_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            path.write_text('{"old": true}', encoding="utf-8")
            DeepHistoryMaintainer._checkpoint_write(path, {"fingerprint": "abc", "step": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["step"], 2)
            self.assertEqual(list(path.parent.glob(path.name + ".*.tmp")), [])

    def test_replay_uses_continuation_aware_generation(self):
        client = FakeClient()
        maintainer = DeepHistoryMaintainer.__new__(DeepHistoryMaintainer)
        maintainer.client = client
        maintainer.durable = SimpleNamespace()
        record = {
            "original_request": "recover this request",
            "primary_task_id": "prior-task",
            "task_date": datetime.date(2026, 10, 4),
        }
        passed, verdict, replay = maintainer._replay_one(record)
        self.assertTrue(passed)
        self.assertTrue(client.complete_calls)
        prompt, kwargs = client.complete_calls[0]
        self.assertEqual(kwargs["num_predict"], 4800)
        self.assertEqual(kwargs["max_segments"], 4)
        self.assertIn("SINGLE COMPACT MEMORY", prompt)
        self.assertEqual(prompt.count("prior-task"), 1)
        self.assertNotIn("RETRIEVED COMPACT HISTORY", prompt)
        self.assertIn("prior-task", replay)
        self.assertEqual(verdict["issues"], [])


    def test_full_history_batches_cover_all_rows(self):
        maintainer = DeepHistoryMaintainer.__new__(DeepHistoryMaintainer)

        def group(index):
            row = {
                "task_id": f"task-{index:03d}",
                "started_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
                    + datetime.timedelta(days=index),
            }
            # Some compact rows may represent more than one raw retry/source task.
            return [dict(row), {**row, "task_id": f"task-{index:03d}-retry"}] if index % 10 == 0 else [row]

        groups = [group(i) for i in range(450)]
        batches = maintainer._batch_groups_by_rows(groups, 200)
        self.assertEqual([len(batch) for batch in batches], [200, 200, 50])
        self.assertEqual(sum(len(batch) for batch in batches), 450)

    def test_full_history_samples_twelve_per_two_hundred_rows(self):
        records = [
            {
                "primary_task_id": f"task-{i:03d}",
                "task_date": datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
                "task_kind": "root",
                "status": "completed",
            }
            for i in range(200)
        ]
        sampled = DeepHistoryMaintainer._sample_records(records, 12)
        self.assertEqual(len(sampled), 12)
        self.assertEqual(len({record["primary_task_id"] for record in sampled}), 12)

    def test_full_merge_windows_are_neighboring_rows(self):
        maintainer = DeepHistoryMaintainer.__new__(DeepHistoryMaintainer)
        records = [
            {
                "primary_task_id": f"task-{i}",
                "task_date": datetime.date(2026, 1, i),
            }
            for i in range(1, 10)
        ]
        clusters = maintainer._full_merge_clusters(records, max_size=3)
        self.assertEqual(
            [[record["primary_task_id"] for record in cluster] for cluster in clusters],
            [
                ["task-9", "task-8", "task-7"],
                ["task-6", "task-5", "task-4"],
                ["task-3", "task-2", "task-1"],
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
