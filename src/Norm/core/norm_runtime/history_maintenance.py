from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import timezone
from pathlib import Path


class DeepHistoryMaintainer:
    def __init__(self, durable, client, *, runtime_root, config: dict, queue=None, drain_event=None) -> None:
        self.durable = durable
        self.client = client
        self.runtime_root = runtime_root
        self.config = config
        self.queue = queue
        self.drain_event = drain_event

    @staticmethod
    def _normalized_request(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip().lower())

    @staticmethod
    def _archive_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "maxLength": 1800},
                "lessons": {"type": "string", "maxLength": 1800},
                "future_note": {"type": "string", "maxLength": 1200},
            },
            "required": ["outcome", "lessons", "future_note"],
            "additionalProperties": False,
        }

    @staticmethod
    def _replay_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "prior_task_identified": {"type": "boolean"},
                "request_understood": {"type": "boolean"},
                "lessons_used": {"type": "boolean"},
                "sufficient_context": {"type": "boolean"},
                "issues": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 300}},
            },
            "required": ["prior_task_identified", "request_understood", "lessons_used", "sufficient_context", "issues"],
            "additionalProperties": False,
        }

    @staticmethod
    def _choose_primary(group: list[dict]) -> dict:
        priority = {"completed": 3, "failed": 2, "cancelled": 1}
        return max(group, key=lambda item: (priority.get(item["status"], 0), item["updated_at"]))

    def _group_candidates(self, candidates: list[dict]) -> list[list[dict]]:
        groups: dict[tuple[str, str, str, str, str], list[dict]] = {}
        for item in candidates:
            day = item["started_at"].astimezone(timezone.utc).date().isoformat()
            request = self._normalized_request(item.get("original_request")) or f"task:{item['task_id']}"
            key = (day, request, str(item.get("task_kind") or "root"), str(item.get("parent_task_id") or ""), str(item.get("parent_step_id") or ""))
            groups.setdefault(key, []).append(item)
        return list(groups.values())

    def _summarize_group(self, group: list[dict]) -> dict:
        primary = self._choose_primary(group)
        payload = []
        for item in group:
            payload.append({
                "task_id": item["task_id"],
                "status": item["status"],
                "title": item["title"],
                "request": item.get("original_request", ""),
                "task_kind": item.get("task_kind", "root"),
                "parent_task_id": item.get("parent_task_id", ""),
                "parent_step_id": item.get("parent_step_id", ""),
                "task_depth": item.get("task_depth", 0),
                "terminal_summary": item.get("terminal_summary", "")[:5000],
                "effectiveness_note": item.get("effectiveness_note", "")[:1200],
                "steps": item.get("steps", [])[:20],
                "evidence": item.get("evidence", [])[:12],
            })
        prompt = (
            "Compress this terminal Norm task history into one durable reusable record. Repeated attempts of the same request may be present. "
            "Outcome: say what the user wanted, whether the final attempt succeeded/failed/cancelled, and preserve important artifact/data results. "
            "Lessons: preserve only reusable fixes, tweaks, constraints, or new useful data learned from the attempts. "
            "Future_note: state what we should tell ourselves if the same kind of task is asked again, including how to reuse prior work instead of repeating it. "
            "Do not preserve routine chatter, verifier narration, transient smoke details, credentials, or redundant failures that taught nothing. Consolidation must delete semantic redundancy: if the same state appears multiple times, preserve/reference the initial authoritative instance and represent later repetitions only as that state happening again with a timezone-aware timestamp. Default timestamps to America/New_York when unspecified. "
            "Return only the structured JSON required by the schema.\n\nTASK ATTEMPTS:\n" + json.dumps(payload, ensure_ascii=False)
        )
        last_error = None
        for _ in range(3):
            try:
                raw = self.client.generate(prompt, think=False, temperature=0.0, num_predict=1800, response_format=self._archive_schema())
                parsed = self.client.parse_json(raw)
                if not all(str(parsed.get(k) or "").strip() for k in ("outcome", "lessons", "future_note")):
                    raise ValueError("archive summary contained blank required fields")
                return {
                    "primary_task_id": primary["task_id"],
                    "task_date": primary["started_at"].date(),
                    "title": primary["title"],
                    "status": primary["status"],
                    "original_request": primary.get("original_request") or primary["title"],
                    "outcome": str(parsed["outcome"]).strip(),
                    "lessons": str(parsed["lessons"]).strip(),
                    "future_note": str(parsed["future_note"]).strip(),
                    "source_task_ids": [item["task_id"] for item in group],
                    "task_kind": str(primary.get("task_kind") or "root"),
                    "parent_task_id": str(primary.get("parent_task_id") or ""),
                    "parent_step_id": str(primary.get("parent_step_id") or ""),
                    "task_depth": int(primary.get("task_depth") or 0),
                }
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"task-history compression failed after 3 attempts: {last_error}")

    def _replay_one(self, record: dict) -> tuple[bool, dict, str]:
        retrieved = self.durable.relevant_task_history(record["original_request"], limit=5, include_unvalidated=True)
        prompt = (
            "You are replaying an old user request using ONLY the compact task-history records below. Do not use raw messages, raw task steps, or tools. "
            "Explain how Norm should handle the request now, explicitly cite the most relevant prior task by task ID and date, reuse its lessons, preserve important constraints, and preserve parent/child lineage when the compact record is a child task. "
            "Do not claim you actually executed the task.\n\nOLD USER REQUEST:\n" + record["original_request"]
            + "\n\nRETRIEVED COMPACT HISTORY:\n" + json.dumps(retrieved, ensure_ascii=False)
        )
        replay = self.client.generate(prompt, think=False, temperature=0.0, num_predict=1200).strip()
        if not replay:
            return False, {"issues": ["blank replay"]}, replay
        verify_prompt = (
            "Verify whether this replay proves the compact history is sufficient to recover useful prior context. "
            "prior_task_identified is true only if the replay identifies the correct prior task/date. request_understood is true only if it preserves the old request's material constraints. "
            "lessons_used is true only if it applies reusable lessons from the compact history. sufficient_context is true only if the compact record would materially help a future repeat without raw history. "
            "Return only structured JSON.\n\nAUTHORITATIVE OLD REQUEST:\n" + record["original_request"]
            + "\n\nCOMPACT RECORD:\n" + json.dumps(record, ensure_ascii=False, default=str)
            + "\n\nREPLAY:\n" + replay
        )
        raw = self.client.generate(verify_prompt, think=False, temperature=0.0, num_predict=700, response_format=self._replay_schema())
        verdict = self.client.parse_json(raw)
        deterministic_id = record["primary_task_id"] in replay and record["task_date"].isoformat() in replay
        passed = deterministic_id and all(bool(verdict.get(k)) for k in ("prior_task_identified", "request_understood", "lessons_used", "sufficient_context")) and not verdict.get("issues")
        return passed, verdict, replay

    @staticmethod
    def _sample_records(records: list[dict], count: int) -> list[dict]:
        if len(records) <= count:
            return records
        indexes = sorted({0, len(records) // 2, len(records) - 1})
        selected = [records[i] for i in indexes]
        if count >= 2 and any(r.get("task_kind") == "child" for r in records) and not any(r.get("task_kind") == "child" for r in selected):
            selected[-1] = next(r for r in records if r.get("task_kind") == "child")
        return selected[:count]

    @staticmethod
    def _checkpoint_read(path: Path, fingerprint: str) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return data if data.get("fingerprint") == fingerprint else {}

    @staticmethod
    def _checkpoint_write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temp.replace(path)

    def rebuild_background_snapshot(self, *, incremental: bool = False) -> str:
        all_records = self.durable.background_memory_source()
        if not all_records:
            return ""
        latest = self.durable.latest_background_snapshot() if incremental else None
        base_summary = str((latest or {}).get("summary") or "")
        source_cutoff = (latest or {}).get("source_through")
        delta_all = [r for r in all_records if source_cutoff is None or r["at"] > source_cutoff] if incremental else all_records
        if incremental:
            ignored_markers = ("[manual_background_condensation]", "[deep_history_consolidation]", "[background_memory_consolidation]")
            records = [r for r in delta_all if not (r.get("source") == "runtime_maintenance" and any(marker in r.get("text", "") for marker in ignored_markers))]
            if not records:
                if base_summary and delta_all:
                    self.durable.save_background_snapshot(base_summary, max(r["at"] for r in delta_all))
                    self.durable.keep_latest_background_snapshot()
                return base_summary
        else:
            ignored_markers = ("[manual_background_condensation]", "[deep_history_consolidation]", "[background_memory_consolidation]")
            records = [r for r in all_records if not (r.get("source") == "runtime_maintenance" and any(marker in r.get("text", "") for marker in ignored_markers))]
            if not records:
                return ""
        batch_chars = max(4000, int(self.config.get("consolidation_batch_chars", 14000)))
        lines = [f"[{record['at'].isoformat()}][{record['source']}] {record['text']}" for record in records]
        chunks, current, used = [], [], 0
        for line in lines:
            if current and used + len(line) + 1 > batch_chars:
                chunks.append("\n".join(current)); current, used = [], 0
            current.append(line); used += len(line) + 1
        if current:
            chunks.append("\n".join(current))
        mode = "incremental" if incremental else "deep-rebuild"
        fingerprint = hashlib.sha256((mode + "\n" + str(source_cutoff) + "\n" + "\n".join(lines)).encode("utf-8")).hexdigest()
        checkpoint_path = Path(self.runtime_root) / "state" / f"background-condensation-{mode}.json"
        state = self._checkpoint_read(checkpoint_path, fingerprint)
        if not state:
            state = {"fingerprint": fingerprint, "mode": mode, "completed_batches": [], "current_index": 0, "current_partial": "", "merge_partial": ""}
            self._checkpoint_write(checkpoint_path, state)
        partials = list(state.get("completed_batches") or [])
        for index, chunk in enumerate(chunks):
            if index < len(partials):
                continue
            prompt = (
                "Compress this PostgreSQL history slice into compact reusable Norm background memory. "
                "Preserve current architecture, verified behavior, durable decisions/preferences, reusable failure lessons, unresolved work, and important artifact pointers. "
                "Collapse retries/smokes and superseded narration. Preserve meaningful timezone-aware chronology. "
                "Target <=3500 characters. Do not invent facts. Return plain text only.\n\n" + chunk
            )
            resume = str(state.get("current_partial") or "") if int(state.get("current_index", -1)) == index else ""
            def save_batch_partial(text: str, complete: bool, segment: int, idx=index) -> None:
                state.update({"current_index": idx, "current_partial": text, "current_complete": bool(complete), "current_segment": int(segment)})
                self._checkpoint_write(checkpoint_path, state)
            part = self.client.generate_complete_text(
                prompt, think=False, temperature=0.0, num_predict=1800, max_segments=4,
                response_so_far=resume, on_partial=save_batch_partial,
            ).strip()
            if not part:
                raise RuntimeError(f"background condensation returned blank batch {index + 1}/{len(chunks)}")
            partials.append(part)
            state.update({"completed_batches": partials, "current_index": index + 1, "current_partial": "", "current_complete": True})
            self._checkpoint_write(checkpoint_path, state)
        combined = "\n\n--- MEMORY BATCH ---\n\n".join(partials)
        base = ("PREVIOUS CONSOLIDATED MEMORY:\n" + base_summary + "\n\n") if base_summary else ""
        merge_prompt = (
            "Merge the prior consolidated memory and these newer/surviving summaries into one highly compressed global background memory. "
            "Prefer the newest non-superseded state, remove repetition/transient execution chatter, and retain only information likely to improve future work. "
            "Validated compact task-history replaces deleted raw attempts. Preserve meaningful chronology with timezone-aware timestamps. "
            "Target <=12000 characters. Do not invent facts. Return plain text only.\n\n" + base + "NEW/SURVIVING SUMMARIES:\n" + combined
        )
        def save_merge_partial(text: str, complete: bool, segment: int) -> None:
            state.update({"stage": "merge", "merge_partial": text, "merge_complete": bool(complete), "merge_segment": int(segment)})
            self._checkpoint_write(checkpoint_path, state)
        summary = self.client.generate_complete_text(
            merge_prompt, think=False, temperature=0.0, num_predict=3000, max_segments=4,
            response_so_far=str(state.get("merge_partial") or ""), on_partial=save_merge_partial,
        ).strip()
        if not summary:
            raise RuntimeError("background condensation merge returned blank")
        source_through = max(record["at"] for record in (delta_all if incremental else records))
        self.durable.save_background_snapshot(summary, source_through)
        self.durable.keep_latest_background_snapshot()
        checkpoint_path.unlink(missing_ok=True)
        return summary

    def _rebuild_background_snapshot(self) -> str:
        return self.rebuild_background_snapshot(incremental=False)

    def run(self) -> dict | None:
        retention_days = max(1, int(self.config.get("deep_history_retention_days", 30)))
        interval_days = max(1, int(self.config.get("deep_history_interval_days", 7)))
        max_tasks = max(1, int(self.config.get("deep_history_max_tasks_per_pass", 500)))
        replay_samples = max(1, int(self.config.get("deep_history_replay_samples", 3)))
        if not self.durable.needs_deep_history_consolidation(retention_days, interval_days):
            return None
        if self.queue is not None:
            stats = self.queue.stats()
            if stats.get("stream_length", 0) or stats.get("pending_count", 0):
                return None
        if self.drain_event is not None and self.drain_event.is_set():
            return None
        candidates = self.durable.deep_history_candidates(retention_days, max_tasks)
        if not candidates:
            return None
        backup_dir = str(self.config.get("deep_history_backup_dir") or (self.runtime_root.parent / "Norm-backups" / "history-maintenance"))
        backup_path = self.durable.create_sql_backup(backup_dir)
        records: list[dict] = []
        try:
            for group in self._group_candidates(candidates):
                records.append(self._summarize_group(group))
            primary_ids = self.durable.upsert_task_history(records, validated=False)
            replay_results = []
            for record in self._sample_records(records, replay_samples):
                passed, verdict, replay = self._replay_one(record)
                replay_results.append({"task_id": record["primary_task_id"], "passed": passed, "verdict": verdict, "replay": replay[:2000]})
            if not replay_results or not all(item["passed"] for item in replay_results):
                self.durable.record_maintenance_note(
                    "deep_history_consolidation", "Compact-history replay validation failed; raw history was preserved and SQL backup retained.",
                    details={"status": "replay_failed", "backup": backup_path, "replays": replay_results},
                )
                return {"status": "replay_failed", "backup": backup_path, "replays": replay_results}
            self.durable.mark_task_history_validated(primary_ids)
            source_task_ids = [task_id for record in records for task_id in record["source_task_ids"]]
            deleted_tasks = self.durable.delete_validated_archived_tasks(source_task_ids)
            conversation = self.durable.prune_covered_conversation_history(retention_days)
            superseded_deleted = self.durable.delete_superseded_memories()
            snapshot = self._rebuild_background_snapshot()
            self.durable.delete_sql_backup(backup_path)
            details = {
                "status": "success", "archived_records": len(records), "deleted_tasks": deleted_tasks,
                "source_task_ids": len(source_task_ids), "replay_samples": len(replay_results),
                "messages_deleted": conversation["messages_deleted"],
                "thread_summaries_deleted": conversation["thread_summaries_deleted"],
                "memory_links_preserved": conversation.get("memory_links_preserved", 0),
                "superseded_memories_deleted": superseded_deleted,
                "background_chars": len(snapshot), "backup_deleted": True,
            }
            self.durable.record_maintenance_note(
                "deep_history_consolidation", "Old Norm history was compacted, replay-validated, hard-pruned, and the temporary SQL backup was deleted.",
                details=details,
            )
            return details
        except Exception as exc:
            try:
                self.durable.record_maintenance_note(
                    "deep_history_consolidation", "Deep history maintenance failed; SQL backup retained for recovery.",
                    details={"status": "failed", "backup": backup_path, "error": f"{type(exc).__name__}: {exc}"[:1200]},
                )
            except Exception:
                logging.exception("Could not record failed deep-history maintenance note")
            raise
