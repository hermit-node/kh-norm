from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.chunked_evidence import ChunkedEvidenceManager


class ChunkedEvidenceTests(unittest.TestCase):
    def test_budget_uses_fraction_of_remaining_context(self) -> None:
        manager = ChunkedEvidenceManager({
            "raw_evidence_fraction": 0.20,
            "safety_fraction": 0.08,
            "estimated_chars_per_token": 4.0,
            "estimated_text_bytes_per_token": 3.0,
            "min_raw_evidence_tokens": 1,
            "max_raw_evidence_tokens": 100000,
            "min_safety_tokens": 0,
        })
        budget = manager.budget(
            context_limit_tokens=100_000,
            output_reserve_tokens=10_000,
            system_instructions="x" * 4_000,
            messages=[{"role": "user", "content": "y" * 8_000}],
            tool_schemas=[{"z": "q" * 8_000}],
            max_chunk_bytes=1_000_000,
        )
        # About 5k fixed tokens, 8k safety, 10k output => ~77k usable;
        # raw evidence should target ~20% of that, not 20% of the raw 100k.
        self.assertGreater(budget.raw_evidence_target_tokens, 14_000)
        self.assertLess(budget.raw_evidence_target_tokens, 17_000)
        self.assertEqual(budget.raw_evidence_target_bytes, budget.raw_evidence_target_tokens * 3)

    def test_seams_cover_start_middle_end_without_neighbors(self) -> None:
        manager = ChunkedEvidenceManager()
        self.assertEqual(manager.seam_before(1), "{0:1}")
        self.assertEqual(manager.seam_after(1, 15), "{1:2}")
        self.assertEqual(manager.seam_before(7), "{6:7}")
        self.assertEqual(manager.seam_after(7, 15), "{7:8}")
        self.assertEqual(manager.seam_before(15), "{14:15}")
        self.assertEqual(manager.seam_after(15, 15), "{15:0}")

    def test_piece_addressing_and_eviction(self) -> None:
        manager = ChunkedEvidenceManager({"estimated_chars_per_token": 4.0})
        budget = manager.budget(
            context_limit_tokens=65_536,
            output_reserve_tokens=8_000,
            system_instructions="",
            messages=[],
            tool_schemas=[],
            max_chunk_bytes=20_000,
        )
        stream = manager.create_stream(
            path=r"C:\\tmp\\big.txt",
            source_fingerprint="abc",
            source_size_bytes=45_000,
            chunk_bytes=20_000,
            budget=budget,
        )
        self.assertEqual(stream.estimated_pieces, 3)
        self.assertEqual(manager.piece_arguments(stream, 3), {"start_byte": 40_000, "max_bytes": 20_000})
        wrapped = manager.wrap_result(
            {
                "ok": True,
                "tool": "read_file",
                "path": stream.path,
                "mode": "text",
                "content": "hello",
                "returned_bytes": 5,
                "start_byte": 0,
                "next_byte": 5,
                "truncated": True,
            },
            stream,
            1,
        )
        self.assertEqual(wrapped["content"], "{0:1}\nhello\n{1:2}")
        messages = [{"role": "tool", "tool_name": "read_file", "content": json.dumps(wrapped)}]
        retained = manager.retain(stream, 1, "fact one; fact two")
        self.assertTrue(manager.evict_tool_message(messages, stream=stream, piece=1, retained=retained))
        compact = json.loads(messages[0]["content"])
        self.assertTrue(compact["chunk_evicted"])
        self.assertNotIn("content", compact)
        self.assertEqual(compact["retained_facts"], "fact one; fact two")

    def test_stream_snapshot_restore_survives_existing_checkpoint_requeue(self) -> None:
        manager = ChunkedEvidenceManager()
        budget = manager.budget(
            context_limit_tokens=65536, output_reserve_tokens=8000,
            system_instructions="", messages=[], tool_schemas=[], max_chunk_bytes=20000,
        )
        stream = manager.create_stream(
            path="x.txt", source_fingerprint="fp", source_size_bytes=65000,
            chunk_bytes=20000, budget=budget,
        )
        manager.retain(stream, 1, "header parsed")
        stream.last_delivered_piece = 1
        state = manager.snapshot_streams()

        resumed = ChunkedEvidenceManager()
        self.assertEqual(resumed.restore_streams(state), 1)
        restored = resumed.get(stream.stream_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.chunk_bytes, 20000)
        self.assertEqual(restored.retained_by_piece[1], "header parsed")
        self.assertEqual(resumed.piece_arguments(restored, 3), {"start_byte": 40000, "max_bytes": 20000})

    def test_retained_facts_are_bounded_by_compact_partition(self) -> None:
        manager = ChunkedEvidenceManager({
            "compact_state_max_fraction": 0.10,
            "estimated_chars_per_token": 4.0,
            "max_retained_chars_per_piece": 10_000,
        })
        budget = manager.budget(
            context_limit_tokens=10_000,
            output_reserve_tokens=1000,
            system_instructions="",
            messages=[],
            tool_schemas=[],
            max_chunk_bytes=1000,
        )
        stream = manager.create_stream(
            path="x",
            source_fingerprint="f",
            source_size_bytes=10_000,
            chunk_bytes=1000,
            budget=budget,
        )
        retained = manager.retain(stream, 1, "a" * 5000)
        # compact partition = 1000 tokens ~= 4000 chars across 10 pieces => ~400 chars/piece
        self.assertLessEqual(len(retained), 401)


class N1ReadFileCanonicalizationTests(unittest.TestCase):
    def test_read_file_canonical_value_does_not_echo_content(self) -> None:
        from norm_runtime.n1_gatekeeper import N1Gatekeeper

        value = N1Gatekeeper._canonical_streamed_read_value({
                "ok": True,
                "path": r"C:\\tmp\\big.txt",
                "source_fingerprint": "fp",
                "source_size_bytes": 99999,
                "returned_bytes": 20000,
                "start_byte": 0,
                "next_byte": 20000,
                "truncated": True,
                "content": "SECRET-BULK" * 10000,
            })
        self.assertNotIn("SECRET-BULK", value)
        parsed = json.loads(value)
        self.assertEqual(parsed["source_fingerprint"], "fp")
        self.assertEqual(parsed["source_size_bytes"], 99999)


    def test_streamed_read_checks_in_metadata_without_llm_or_redis_substitution(self) -> None:
        from norm_runtime.n1_gatekeeper import N1Gatekeeper

        class Client:
            timeout_seconds = 10
            def generate(self, *args, **kwargs):
                raise AssertionError("streamed read canonicalization must not call the model")

        class Live:
            def __init__(self):
                self.recorded = []
            def validation_candidates(self, **kwargs):
                return [{
                    "record_id": "old", "description_match": 1.0, "value": "stale compact value",
                    "target": kwargs.get("target"), "description": kwargs.get("description"),
                }]
            def record_global_validations(self, task_id, step_id, rows):
                self.recorded.extend(rows)
                return {"ok": True}

        live = Live()
        gate = N1Gatekeeper(Client(), live)
        outcome = gate.before_tool(
            task_id="t", step_id="s", name="read_file",
            arguments={
                "path": r"C:\\tmp\\big.txt",
                "start_byte": 0, "max_bytes": 20000,
                "_norm_chunked_evidence": True,
            },
        )
        self.assertTrue(outcome.execute)
        self.assertTrue(outcome.streamed_read)
        self.assertNotIn("_norm_chunked_evidence", outcome.arguments)
        result = {
            "ok": True, "tool": "read_file", "path": r"C:\\tmp\\big.txt",
            "source_fingerprint": "fp", "source_size_bytes": 90000, "returned_bytes": 20000,
            "start_byte": 0, "next_byte": 20000, "mode": "text", "truncated": True,
            "content": "x" * 20000,
        }
        meta = gate.after_tool(task_id="t", step_id="s", name="read_file", outcome=outcome, result=result)
        self.assertTrue(meta["recorded"])
        self.assertEqual(len(live.recorded), 1)
        self.assertNotIn("xxxxx", live.recorded[0]["value"])
        parsed = json.loads(live.recorded[0]["value"])
        self.assertEqual(parsed["source_fingerprint"], "fp")


    def test_fixed_byte_piece_map_reconstructs_file_exactly(self) -> None:
        import tempfile
        import types

        if "redis" not in sys.modules:
            redis_stub = types.ModuleType("redis")
            redis_stub.Redis = object
            sys.modules["redis"] = redis_stub
        from norm_runtime.file_tool_executor import FileToolExecutor

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "big.txt"
            original = ("alpha beta gamma\n" * 5000) + "END"
            source.write_text(original, encoding="utf-8")
            executor = FileToolExecutor(
                [str(root)],
                backup_root=str(root / "backup"),
                audit_log=str(root / "audit.jsonl"),
                read_chunk_bytes=12000,
                read_chunk_max_bytes=12000,
            )
            manager = ChunkedEvidenceManager()
            budget = manager.budget(
                context_limit_tokens=65536, output_reserve_tokens=8000,
                system_instructions="", messages=[], tool_schemas=[], max_chunk_bytes=12000,
            )
            first = executor.execute("read_file", {
                "path": str(source), "start_byte": 0, "max_bytes": 12000, "mode": "text",
            })
            stream = manager.create_stream(
                path=first["path"], source_fingerprint=first["source_fingerprint"],
                source_size_bytes=first["source_size_bytes"], chunk_bytes=12000, budget=budget,
            )
            rebuilt = bytearray()
            for piece in range(1, stream.estimated_pieces + 1):
                args = manager.piece_arguments(stream, piece)
                res = executor.execute("read_file", {"path": stream.path, "mode": "bytes_base64", **args})
                import base64
                rebuilt.extend(base64.b64decode(res["base64"]))
            self.assertEqual(bytes(rebuilt), source.read_bytes())


    def test_single_piece_stream_has_start_and_end_seams(self) -> None:
        manager = ChunkedEvidenceManager()
        budget = manager.budget(
            context_limit_tokens=65536, output_reserve_tokens=8000,
            system_instructions="", messages=[], tool_schemas=[], max_chunk_bytes=50000,
        )
        stream = manager.create_stream(
            path="single.txt", source_fingerprint="one", source_size_bytes=20000,
            chunk_bytes=50000, budget=budget,
        )
        wrapped = manager.wrap_result({
            "ok": True, "tool": "read_file", "path": "single.txt", "mode": "text",
            "content": "hello", "returned_bytes": 5, "start_byte": 0, "next_byte": 5,
            "truncated": False,
        }, stream, 1)
        self.assertEqual(stream.estimated_pieces, 1)
        self.assertEqual(wrapped["content"], "{0:1}\nhello\n{1:0}")



if __name__ == "__main__":
    unittest.main()
