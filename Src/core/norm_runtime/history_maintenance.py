from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .settings import load_path_settings


class DeepHistoryMaintainer:
    def __init__(self, durable, client, *, runtime_root, config: dict, queue=None, drain_event=None) -> None:
        self.durable = durable
        self.client = client
        self.runtime_root = Path(runtime_root).resolve()
        self.state_root = load_path_settings(self.runtime_root)["state_root"]
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
                "outcome": {"type": "string", "maxLength": 3000},
                "lessons": {"type": "string", "maxLength": 2600},
                "future_note": {"type": "string", "maxLength": 1800},
            },
            "required": ["outcome", "lessons", "future_note"],
            "additionalProperties": False,
        }

    @staticmethod
    def _full_merge_schema() -> dict:
        item = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 600},
                "original_request": {"type": "string", "maxLength": 1800},
                "outcome": {"type": "string", "maxLength": 4200},
                "lessons": {"type": "string", "maxLength": 3600},
                "future_note": {"type": "string", "maxLength": 2200},
                "source_primary_ids": {
                    "type": "array", "minItems": 1, "maxItems": 6,
                    "items": {"type": "string", "maxLength": 120},
                },
            },
            "required": [
                "title", "original_request", "outcome", "lessons",
                "future_note", "source_primary_ids",
            ],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array", "minItems": 1, "maxItems": 6,
                    "items": item,
                }
            },
            "required": ["records"],
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
        ordered = list(groups.values())
        ordered.sort(
            key=lambda group: max(
                (
                    item.get("started_at") or datetime.min.replace(tzinfo=timezone.utc)
                    for item in group
                ),
                default=datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        return ordered

    @staticmethod
    def _batch_groups_by_rows(groups: list[list[dict]], max_rows: int) -> list[list[list[dict]]]:
        """Split date-ordered compact-memory rows into fixed QA batches."""
        limit = max(1, int(max_rows))
        return [groups[index:index + limit] for index in range(0, len(groups), limit)]

    @staticmethod
    def _record_tokens(text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9_]{4,}", str(text or "").lower())
            if token not in {"this", "that", "with", "from", "have", "will", "into", "then", "than", "when", "where", "what"}
        }

    def _relevant_newer_records(
        self,
        group: list[dict],
        newer_records: list[dict],
        *,
        limit: int = 8,
    ) -> list[dict]:
        if not group or not newer_records:
            return []
        primary = self._choose_primary(group)
        query = f"{primary.get('title', '')} {primary.get('original_request', '')}"
        query_tokens = self._record_tokens(query)
        if not query_tokens:
            return []
        scored: list[tuple[float, dict]] = []
        for record in newer_records:
            text = (
                f"{record.get('title', '')} {record.get('original_request', '')} "
                f"{record.get('outcome', '')} {record.get('lessons', '')} {record.get('future_note', '')}"
            )
            tokens = self._record_tokens(text)
            overlap = len(query_tokens & tokens)
            if not overlap:
                continue
            score = overlap / max(1, len(query_tokens))
            scored.append((score, record))
        scored.sort(
            key=lambda item: (
                item[0],
                str(item[1].get("task_date") or ""),
            ),
            reverse=True,
        )
        return [record for _, record in scored[:max(1, int(limit))]]

    @staticmethod
    def _history_view_record(record: dict) -> dict:
        return {
            "task_id": str(record.get("primary_task_id") or record.get("task_id") or ""),
            "date": str(record.get("task_date") or ""),
            "title": str(record.get("title") or ""),
            "status": str(record.get("status") or ""),
            "request": str(record.get("original_request") or record.get("request") or ""),
            "outcome": str(record.get("outcome") or ""),
            "lessons": str(record.get("lessons") or ""),
            "future_note": str(record.get("future_note") or ""),
            "source_task_ids": [str(v) for v in (record.get("source_task_ids") or [])],
            "task_kind": str(record.get("task_kind") or "root"),
            "parent_task_id": str(record.get("parent_task_id") or ""),
            "parent_step_id": str(record.get("parent_step_id") or ""),
            "task_depth": int(record.get("task_depth") or 0),
        }

    @classmethod
    def _full_merge_clusters(cls, records: list[dict], max_size: int = 6) -> list[list[dict]]:
        """Return chronological neighboring windows for conservative hierarchical merge."""
        cap = max(2, min(6, int(max_size)))
        ordered = sorted(
            records,
            key=lambda record: (
                str(record.get("task_date") or ""),
                str(record.get("primary_task_id") or ""),
            ),
            reverse=True,
        )
        return [ordered[index:index + cap] for index in range(0, len(ordered), cap)]

    def _merge_compact_cluster(self, cluster: list[dict]) -> list[dict]:
        if len(cluster) < 2:
            return list(cluster)
        source_by_id = {str(r["primary_task_id"]): r for r in cluster}
        payload = []
        for record in cluster:
            item = self._history_view_record(record)
            item["source_task_ids"] = list(record.get("source_task_ids") or [])
            payload.append(item)
        prompt = (
            "You are performing FULL Norm memory condensation on up to six chronologically neighboring compact memories. "
            "Do not assume the neighbors belong together. Return between 1 and the original number of compact records, merging only genuinely related/redundant subsets. Do NOT force a merge: if meaning would be lost, leave records separate. "
            "Every input task_id must appear exactly once across source_primary_ids in the outputs. "
            "Merge only genuine semantic redundancy or successive stages of the same work. "
            "Each surviving record must remain succinct but preserve enough meaning to reconstruct: the task/request; meaningful steps or full itinerary; tools actually used; final results/state and artifact pointers; efficiency/what worked or failed; reusable lessons; and notes for improvement next time. "
            "Prefer the newest non-superseded state when facts changed, while retaining older chronology only when it explains evolution or prevents repeating a mistake. "
            "Never invent facts. Return only the structured JSON.\n\nCOMPACT MEMORIES:\n"
            + json.dumps(payload, ensure_ascii=False, default=str)
        )
        raw = self.client.generate(
            prompt,
            think=False,
            temperature=0.0,
            num_predict=6500,
            response_format=self._full_merge_schema(),
        )
        parsed = self.client.parse_json(raw)
        proposals = list(parsed.get("records") or [])
        if not proposals or len(proposals) > len(cluster):
            raise ValueError("full merge returned invalid record count")
        seen: list[str] = []
        for proposal in proposals:
            ids = [str(v) for v in (proposal.get("source_primary_ids") or [])]
            if not ids or len(ids) != len(set(ids)):
                raise ValueError("full merge returned blank/duplicate source_primary_ids")
            if any(source_id not in source_by_id for source_id in ids):
                raise ValueError("full merge referenced unknown source memory")
            seen.extend(ids)
        expected = set(source_by_id)
        if len(seen) != len(set(seen)) or set(seen) != expected:
            raise ValueError("full merge did not partition source memories exactly once")

        # If the model found no safe reduction, preserve the originals exactly.
        if len(proposals) == len(cluster) and all(len(p["source_primary_ids"]) == 1 for p in proposals):
            return list(cluster)

        merged: list[dict] = []
        for proposal in proposals:
            ids = [str(v) for v in proposal["source_primary_ids"]]
            subset = [source_by_id[source_id] for source_id in ids]
            if len(subset) == 1:
                merged.append(subset[0])
                continue
            representative = max(subset, key=lambda r: str(r.get("task_date") or ""))
            all_source_ids: list[str] = []
            for item in subset:
                all_source_ids.append(str(item["primary_task_id"]))
                all_source_ids.extend(str(v) for v in (item.get("source_task_ids") or []))
            same_parent = {str(item.get("parent_task_id") or "") for item in subset}
            same_kind = {str(item.get("task_kind") or "root") for item in subset}
            merged.append({
                "primary_task_id": str(representative["primary_task_id"]),
                "task_date": representative["task_date"],
                "title": str(proposal["title"]).strip(),
                "status": str(representative.get("status") or "completed"),
                "original_request": str(proposal["original_request"]).strip(),
                "outcome": str(proposal["outcome"]).strip(),
                "lessons": str(proposal["lessons"]).strip(),
                "future_note": str(proposal["future_note"]).strip(),
                "source_task_ids": list(dict.fromkeys(all_source_ids)),
                "task_kind": next(iter(same_kind)) if len(same_kind) == 1 else "root",
                "parent_task_id": next(iter(same_parent)) if len(same_parent) == 1 else "",
                "parent_step_id": str(representative.get("parent_step_id") or "") if len(same_parent) == 1 else "",
                "task_depth": int(representative.get("task_depth") or 0) if len(same_parent) == 1 else 0,
                "_merged_source_primary_ids": ids,
            })
        return merged

    def _replay_merged_constituent(self, merged: dict, target: dict) -> tuple[bool, dict]:
        selector = {
            "task_id": str(target["primary_task_id"]),
            "date": str(target.get("task_date") or ""),
        }
        prompt = (
            "Reconstruct the selected historical task using ONLY the merged compact memory below. "
            "The target selector identifies which constituent to reconstruct; it is not additional task context. "
            "Do not use neighboring memories, raw messages, raw task steps, prior replay output, or tools. "
            "Recover the request, meaningful steps/itinerary, tools used, results/state, efficiency lessons, and improvement notes. "
            "Return the reconstruction only.\n\nTARGET SELECTOR:\n"
            + json.dumps(selector, ensure_ascii=False)
            + "\n\nMERGED COMPACT MEMORY:\n"
            + json.dumps(self._history_view_record(merged), ensure_ascii=False)
        )
        replay = self.client.generate_complete_text(
            prompt, think=False, temperature=0.0, num_predict=4800, max_segments=4
        ).strip()
        if not replay:
            return False, {"issues": ["blank merged reconstruction"]}
        verify_prompt = (
            "Grade whether the reconstruction proves the merged compact memory still preserves this constituent task. "
            "prior_task_identified requires the selected task ID/date. request_understood requires the material task and meaningful steps/itinerary. "
            "lessons_used requires tools/efficiency/reusable lessons and improvement guidance when present. "
            "sufficient_context is true only if the constituent can be usefully reconstructed without the original separate memory. "
            "Return only structured JSON.\n\nAUTHORITATIVE CONSTITUENT MEMORY:\n"
            + json.dumps(self._history_view_record(target), ensure_ascii=False)
            + "\n\nRECONSTRUCTION:\n" + replay
        )
        raw = self.client.generate(
            verify_prompt,
            think=False,
            temperature=0.0,
            num_predict=900,
            response_format=self._replay_schema(),
        )
        verdict = self.client.parse_json(raw)
        deterministic_id = (
            str(target["primary_task_id"]) in replay
            and str(target.get("task_date") or "") in replay
        )
        passed = (
            deterministic_id
            and all(bool(verdict.get(k)) for k in (
                "prior_task_identified", "request_understood",
                "lessons_used", "sufficient_context",
            ))
            and not verdict.get("issues")
        )
        return passed, verdict

    def _full_merge_records(self, records: list[dict], max_size: int = 6) -> tuple[list[dict], dict]:
        final_records: list[dict] = []
        attempts = merged_groups = fallback_groups = validations = 0
        for cluster in self._full_merge_clusters(records, max_size=max_size):
            if len(cluster) < 2:
                final_records.extend(cluster)
                continue
            attempts += 1
            try:
                proposed = self._merge_compact_cluster(cluster)
            except Exception:
                logging.exception("Full-memory merge proposal failed; preserving original compact memories")
                final_records.extend(cluster)
                fallback_groups += 1
                continue
            source_by_id = {str(r["primary_task_id"]): r for r in cluster}
            for candidate in proposed:
                merged_ids = [str(v) for v in (candidate.get("_merged_source_primary_ids") or [])]
                if len(merged_ids) < 2:
                    final_records.append(candidate)
                    continue
                subset = [source_by_id[source_id] for source_id in merged_ids]
                group_passed = True
                for target in subset:
                    validations += 1
                    passed, _verdict = self._replay_merged_constituent(candidate, target)
                    if not passed:
                        group_passed = False
                        break
                if group_passed:
                    candidate.pop("_merged_source_primary_ids", None)
                    final_records.append(candidate)
                    merged_groups += 1
                else:
                    final_records.extend(subset)
                    fallback_groups += 1
        return final_records, {
            "merge_attempt_clusters": attempts,
            "merged_groups": merged_groups,
            "merge_fallback_groups": fallback_groups,
            "merged_constituent_validations": validations,
            "records_before_merge": len(records),
            "records_after_merge": len(final_records),
        }

    def _summarize_group(self, group: list[dict], *, newer_context: list[dict] | None = None) -> dict:
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
        newer = list(newer_context or [])
        prompt = (
            "Compress this terminal Norm task history into one durable reusable record. Repeated attempts of the same request may be present. "
            "Outcome: preserve the task/request, the meaningful execution steps or itinerary needed to reconstruct the work, final state/results, and important artifact/data pointers. "
            "Lessons: preserve tools actually used, efficiency/what worked or failed, reusable fixes, constraints, and useful technical lessons. "
            "Future_note: preserve concise notes for improvement: what to do differently/better next time and how to reuse prior work instead of repeating it. "
            "When RELEVANT NEWER COMPACT HISTORY is supplied, treat newer durable state as authoritative only when it materially addresses the same fact, behavior, constraint, or lesson. Remove or correct older guidance that newer evidence supersedes or proves wrong. Keep an older fact only when it remains useful as clearly historical context. Do not let weakly related newer records overwrite unrelated history. "
            "Do not preserve routine chatter, verifier narration, transient smoke details, credentials, or redundant failures that taught nothing. Consolidation must delete semantic redundancy: if the same state appears multiple times, preserve/reference the initial authoritative instance and represent later repetitions only as that state happening again with a timezone-aware timestamp. Default timestamps to America/New_York when unspecified. "
            "Return only the structured JSON required by the schema.\n\nTASK ATTEMPTS:\n"
            + json.dumps(payload, ensure_ascii=False)
            + ("\n\nRELEVANT NEWER COMPACT HISTORY:\n" + json.dumps(newer, ensure_ascii=False, default=str) if newer else "")
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
                    "source_task_ids": list(dict.fromkeys(
                        source_id
                        for item in group
                        for source_id in ([str(item["task_id"])] + [str(v) for v in (item.get("source_task_ids") or [])])
                    )),
                    "task_kind": str(primary.get("task_kind") or "root"),
                    "parent_task_id": str(primary.get("parent_task_id") or ""),
                    "parent_step_id": str(primary.get("parent_step_id") or ""),
                    "task_depth": int(primary.get("task_depth") or 0),
                }
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"task-history compression failed after 3 attempts: {last_error}")

    def _repair_compact_record(self, group: list[dict], record: dict, verdict: dict, replay: str) -> dict:
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
            "Repair ONE compact Norm history record that failed isolated reconstruction validation. "
            "Use only this task's authoritative attempts plus the failed compact record and validator issues. "
            "Do not use neighboring memories or unrelated history. Preserve enough concrete state, constraints, results, lessons, and next-step guidance that a future model can reconstruct the work from the repaired compact record alone. "
            "Do not pad with transient chatter. Return only the structured archive JSON.\n\n"
            "AUTHORITATIVE TASK ATTEMPTS:\n" + json.dumps(payload, ensure_ascii=False, default=str)
            + "\n\nFAILED COMPACT RECORD:\n" + json.dumps(self._history_view_record(record), ensure_ascii=False)
            + "\n\nVALIDATION ISSUES:\n" + json.dumps(verdict.get("issues") or [], ensure_ascii=False)
            + "\n\nFAILED RECONSTRUCTION EXCERPT:\n" + str(replay or "")[:4000]
        )
        raw = self.client.generate(
            prompt, think=False, temperature=0.0, num_predict=2400, response_format=self._archive_schema()
        )
        parsed = self.client.parse_json(raw)
        if not all(str(parsed.get(k) or "").strip() for k in ("outcome", "lessons", "future_note")):
            raise ValueError("repaired archive summary contained blank required fields")
        return {
            "primary_task_id": primary["task_id"],
            "task_date": primary["started_at"].date(),
            "title": primary["title"],
            "status": primary["status"],
            "original_request": primary.get("original_request") or primary["title"],
            "outcome": str(parsed["outcome"]).strip(),
            "lessons": str(parsed["lessons"]).strip(),
            "future_note": str(parsed["future_note"]).strip(),
            "source_task_ids": list(dict.fromkeys(
                source_id
                for item in group
                for source_id in ([str(item["task_id"])] + [str(v) for v in (item.get("source_task_ids") or [])])
            )),
            "task_kind": str(primary.get("task_kind") or "root"),
            "parent_task_id": str(primary.get("parent_task_id") or ""),
            "parent_step_id": str(primary.get("parent_step_id") or ""),
            "task_depth": int(primary.get("task_depth") or 0),
        }

    def _replay_one(self, record: dict) -> tuple[bool, dict, str]:
        # Replay validation is deliberately isolated: one compact record in,
        # one reconstruction out. Never let neighboring memories or a prior
        # replay help the sampled record pass.
        retrieved = [self._history_view_record(record)]
        prompt = (
            "Reconstruct what Norm had been working on and how it should be handled now using ONLY the single compact memory below. "
            "Do not use raw messages, raw task steps, neighboring memories, prior replay output, or tools. "
            "Recover the request, important constraints, prior result/state, reusable lessons, and next-step guidance from this memory alone. "
            "Explicitly cite its task ID and date, and preserve parent/child lineage when present. Do not claim you actually executed the task. "
            "Return the reconstruction only.\n\nSINGLE COMPACT MEMORY:\n" + json.dumps(retrieved[0], ensure_ascii=False)
        )
        replay = self.client.generate_complete_text(
            prompt, think=False, temperature=0.0, num_predict=4800, max_segments=4
        ).strip()
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
        """Choose a chronologically distributed validation sample with edge-case coverage."""
        if not records:
            return []
        target = max(1, min(int(count), len(records)))
        if len(records) <= target:
            return list(records)

        if target == 1:
            indexes = [len(records) - 1]
        else:
            indexes = sorted({
                round(i * (len(records) - 1) / (target - 1))
                for i in range(target)
            })

        selected = [records[i] for i in indexes]

        replacement_slots = (
            list(range(1, len(selected) - 1))
            if len(selected) > 2
            else list(range(len(selected)))
        )
        replacement_cursor = 0

        def ensure(predicate) -> None:
            nonlocal replacement_cursor
            candidate = next((record for record in records if predicate(record)), None)
            if candidate is None or candidate in selected:
                return
            while replacement_cursor < len(replacement_slots):
                slot = replacement_slots[replacement_cursor]
                replacement_cursor += 1
                if selected[slot] is candidate:
                    return
                selected[slot] = candidate
                return

        ensure(lambda r: str(r.get("task_kind") or "root") == "child")
        ensure(lambda r: str(r.get("status") or "") in {"failed", "cancelled"})

        # De-duplicate replacement collisions, fill to target, then restore chronology.
        unique: list[dict] = []
        seen: set[str] = set()
        for record in selected:
            key = str(record.get("primary_task_id") or "")
            if key and key not in seen:
                seen.add(key)
                unique.append(record)
        for record in records:
            if len(unique) >= target:
                break
            key = str(record.get("primary_task_id") or "")
            if key and key not in seen:
                seen.add(key)
                unique.append(record)
        order = {
            str(record.get("primary_task_id") or ""): index
            for index, record in enumerate(records)
        }
        unique.sort(key=lambda record: order.get(str(record.get("primary_task_id") or ""), len(records)))
        return unique[:target]

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
        payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        last_error: OSError | None = None
        for attempt in range(6):
            temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            try:
                with temp.open("w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    temp.replace(path)
                    return
                except PermissionError as exc:
                    last_error = exc
                    if attempt < 5:
                        time.sleep(0.1 * (attempt + 1))
            finally:
                temp.unlink(missing_ok=True)

        # Windows can deny rename/replace while another reader temporarily holds
        # the destination without FILE_SHARE_DELETE. The checkpoint is resumable
        # state rather than authoritative history, so after bounded atomic retries
        # fall back to a durable in-place overwrite instead of failing maintenance.
        try:
            with path.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            if last_error is not None:
                raise last_error
            raise

    def _summarize_dangling_group(self, group: list[dict]) -> dict:
        if not group:
            raise ValueError("dangling recovery group must not be empty")
        primary = min(group, key=lambda item: (int(item.get("task_depth") or 0), item.get("started_at"), item.get("task_id")))
        payload = []
        for item in group:
            payload.append({
                "task_id": item["task_id"], "status": item["status"], "title": item["title"],
                "request": item.get("original_request", ""), "task_kind": item.get("task_kind", "root"),
                "parent_task_id": item.get("parent_task_id", ""), "parent_step_id": item.get("parent_step_id", ""),
                "task_depth": item.get("task_depth", 0), "latest_summary": item.get("terminal_summary", "")[:4000],
                "effectiveness_note": item.get("effectiveness_note", "")[:1200],
                "steps": item.get("steps", [])[:20], "evidence": item.get("evidence", [])[:12],
                "recovery_notes": item.get("recovery_notes", [])[:12],
            })
        prompt = (
            "This Norm task tree is stale recovery state: PostgreSQL still marks work nonterminal, but no live Redis queue entry remains and the tree has exceeded the configured stale threshold. "
            "Create one compact salvage record before the stale task rows are deleted. Outcome: preserve what the original user was trying to accomplish and the most useful work/results already established; explicitly say the task was recovered from dangling state rather than pretending it completed normally. "
            "Lessons: preserve reusable technical findings, constraints, artifact pointers, and recovery lessons from the task and recovery notes. Future_note: say how a future attempt should resume or avoid repeating completed work. "
            "Ignore routine verifier chatter and transient execution narration. Do not invent completion. Return only the structured JSON required by the schema.\n\nDANGLING TASK TREE:\n"
            + json.dumps(payload, ensure_ascii=False, default=str)
        )
        raw = self.client.generate(prompt, think=False, temperature=0.0, num_predict=1800, response_format=self._archive_schema())
        parsed = self.client.parse_json(raw)
        if not all(str(parsed.get(key) or "").strip() for key in ("outcome", "lessons", "future_note")):
            raise ValueError("dangling recovery summary contained blank required fields")
        request = str(primary.get("original_request") or "").strip() or f"Recovered dangling task: {primary.get('title') or primary['task_id']}"
        return {
            "primary_task_id": primary["task_id"], "task_date": primary["started_at"].date(),
            "title": str(primary.get("title") or "Recovered dangling task"), "status": "failed",
            "original_request": request, "outcome": str(parsed["outcome"]).strip(),
            "lessons": str(parsed["lessons"]).strip(), "future_note": str(parsed["future_note"]).strip(),
            "source_task_ids": [item["task_id"] for item in group],
            "task_kind": str(primary.get("task_kind") or "root"),
            "parent_task_id": str(primary.get("parent_task_id") or ""),
            "parent_step_id": str(primary.get("parent_step_id") or ""),
            "task_depth": int(primary.get("task_depth") or 0),
        }

    def cleanup_recovery_state(self) -> dict:
        """Prune recovery plumbing that no longer protects live work.

        Terminal verified trees lose only their recovery notes. Truly stale nonterminal
        trees are compacted into replay-validated task_history before the task rows are
        removed. Suppressed or queued trees are never treated as dangling.
        """
        stale_hours = max(1, int(self.config.get("recovery_stale_hours", 24)))
        max_trees = max(1, int(self.config.get("recovery_cleanup_max_trees", 20)))
        active_ids = self.queue.task_ids() if self.queue is not None else set()
        note_task_ids = self.durable.recovery_note_task_ids()
        roots: list[str] = []
        seen: set[str] = set()
        for task_id in note_task_ids:
            root = self.durable.root_task_id(task_id) or task_id
            if root not in seen:
                seen.add(root)
                roots.append(root)
        # Do not restrict dangling cleanup to trees that happened to create a
        # recovery note. A nonterminal root with no Redis membership can be just
        # as stale after a crash or interrupted migration.
        for task_id in self.durable.nonterminal_root_task_ids(limit=max_trees * 10):
            root = self.durable.root_task_id(task_id) or task_id
            if root not in seen:
                seen.add(root)
                roots.append(root)
        terminal_note_task_ids: set[str] = set()
        superseded_descendants_deleted = 0
        dangling_archived = 0
        dangling_deleted = 0
        replay_failed = 0
        skipped_live = 0
        skipped_due_limit = 0
        dangling_considered = 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_hours)
        terminal = {"completed", "failed", "cancelled"}
        for root in roots:
            tree = self.durable.task_tree(root)
            if not tree:
                continue
            tree_ids = {item["task_id"] for item in tree}
            statuses = {str(item.get("status") or "") for item in tree}
            root_row = next((item for item in tree if item.get("task_id") == root), tree[0])
            root_status = str(root_row.get("status") or "")
            if root_status in terminal and self.durable.terminal_summary_verified(root):
                # A verified terminal root supersedes unfinished recovery/child plumbing.
                # Keep the authoritative root result; remove descendant work that can
                # no longer affect it and delete all recovery handoff notes for the tree.
                superseded = [
                    item["task_id"] for item in tree
                    if item["task_id"] != root and str(item.get("status") or "") not in terminal
                ]
                if superseded:
                    superseded_descendants_deleted += self.durable.delete_superseded_descendants(root, superseded)
                terminal_note_task_ids.update(tree_ids)
                continue
            if statuses and statuses.issubset(terminal):
                if all(self.durable.terminal_summary_verified(task_id) for task_id in tree_ids):
                    terminal_note_task_ids.update(tree_ids)
                continue
            if tree_ids & active_ids or "suppressed" in statuses:
                skipped_live += 1
                continue
            nonterminal = [item for item in tree if str(item.get("status") or "") not in terminal]
            if not nonterminal:
                continue
            if any(item.get("updated_at") is None or item["updated_at"] > cutoff for item in nonterminal):
                continue
            if dangling_considered >= max_trees:
                skipped_due_limit += 1
                continue
            dangling_considered += 1
            group = self.durable.recovery_group_snapshot(root)
            if not group:
                continue
            try:
                record = self._summarize_dangling_group(group)
                primary_ids = self.durable.upsert_task_history([record], validated=False)
                passed, verdict, replay = self._replay_one(record)
                if not passed:
                    replay_failed += 1
                    self.durable.record_maintenance_note(
                        "recovery_state_cleanup",
                        "Dangling recovery task was summarized but replay validation failed; raw task state was preserved.",
                        task_id=root,
                        details={"status": "replay_failed", "verdict": verdict},
                    )
                    continue
                self.durable.mark_task_history_validated(primary_ids)
                deleted = self.durable.delete_validated_archived_tasks(record["source_task_ids"])
                dangling_archived += 1
                dangling_deleted += deleted
            except Exception as exc:
                replay_failed += 1
                self.durable.record_maintenance_note(
                    "recovery_state_cleanup",
                    "Dangling recovery cleanup failed; raw task state was preserved.",
                    task_id=root,
                    details={"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:1200]},
                )
        terminal_notes_deleted = self.durable.delete_recovery_notes(sorted(terminal_note_task_ids))
        orphan_archives = self.durable.delete_orphan_task_archives()
        return {
            "status": "success", "stale_hours": stale_hours, "trees_checked": len(roots),
            "terminal_notes_deleted": terminal_notes_deleted, "superseded_descendants_deleted": superseded_descendants_deleted,
            "dangling_trees_considered": dangling_considered,
            "dangling_trees_archived": dangling_archived, "dangling_tasks_deleted": dangling_deleted,
            "replay_failed": replay_failed, "skipped_live_or_suppressed": skipped_live,
            "skipped_due_limit": skipped_due_limit, "orphan_archives_deleted": orphan_archives,
        }

    def rebuild_background_snapshot(self, *, incremental: bool = False) -> str:
        all_records = self.durable.background_memory_source()
        if not all_records:
            return ""
        latest = self.durable.latest_background_snapshot() if incremental else None
        base_summary = str((latest or {}).get("summary") or "")
        source_cutoff = (latest or {}).get("source_through")
        if incremental:
            regular_window_days = max(1, int(self.config.get("regular_memory_window_days", 14)))
            recent_cutoff = datetime.now(timezone.utc) - timedelta(days=regular_window_days)
            delta_all = [
                r for r in all_records
                if r["at"] >= recent_cutoff and (source_cutoff is None or r["at"] > source_cutoff)
            ]
        else:
            delta_all = all_records
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
        batch_target_chars = max(800, int(self.config.get("consolidation_batch_target_chars", 1800)))
        snapshot_target_chars = max(2500, int(self.config.get("consolidation_snapshot_target_chars", 6000)))
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
        checkpoint_path = self.state_root / f"background-condensation-{mode}.json"
        state = self._checkpoint_read(checkpoint_path, fingerprint)
        if not state:
            state = {"fingerprint": fingerprint, "mode": mode, "completed_batches": [], "current_index": 0, "current_partial": "", "merge_partial": ""}
            self._checkpoint_write(checkpoint_path, state)
        partials = list(state.get("completed_batches") or [])
        for index, chunk in enumerate(chunks):
            if index < len(partials):
                continue
            prompt = (
                "Distill this PostgreSQL history slice into SMALL working-memory notes, not a rewritten transcript. "
                "Keep only information that is likely to materially change a future answer or action: current project state, durable decisions, stable preferences, unresolved obligations, reusable technical lessons, and artifact/data pointers that remain operationally useful. "
                "Aggressively merge repeated updates about the same project/fact into one current-state statement plus at most one reusable lesson or constraint. "
                "DROP routine chatter, successful smoke-test narration, one-off examples, transient values/market levels, temporary shorthand/terminology, intermediate attempts, redundant chronology, and details already recoverable from compact task_history unless they are needed to understand current state. "
                "Do NOT produce one bullet/line per source record. Fewer synthesized items are better. Preserve a timestamp only when timing itself remains meaningful. "
                f"Compression target: about {batch_target_chars} characters. Treat this as a strong target, not a reason to fail the maintenance run. Do not invent facts. Return plain text only.\n\n" + chunk
            )
            resume = str(state.get("current_partial") or "") if int(state.get("current_index", -1)) == index else ""
            def save_batch_partial(text: str, complete: bool, segment: int, idx=index) -> None:
                state.update({"current_index": idx, "current_partial": text, "current_complete": bool(complete), "current_segment": int(segment)})
                self._checkpoint_write(checkpoint_path, state)
            part = self.client.generate_complete_text(
                prompt, think=False, temperature=0.0, num_predict=900, max_segments=2,
                response_so_far=resume, on_partial=save_batch_partial,
            ).strip()
            if not part:
                raise RuntimeError(f"background condensation returned blank batch {index + 1}/{len(chunks)}")
            if len(part) > int(batch_target_chars * 1.20):
                tighten = (
                    "Rewrite the candidate below as materially smaller working memory. "
                    "It is still too close to source-by-source narration. Merge duplicates, remove examples/transient details, and keep only state/decisions/preferences/unresolved obligations/reusable lessons that can change future work. "
                    f"Aim for <= {batch_target_chars} characters, plain text only. If the source is unusually information-dense, preserve the smallest sufficient version instead of padding or narrating.\n\nCANDIDATE:\n" + part
                )
                tightened = self.client.generate_complete_text(
                    tighten, think=False, temperature=0.0, num_predict=900, max_segments=2,
                ).strip()
                if tightened and len(tightened) < len(part):
                    part = tightened
                if len(part) > int(batch_target_chars * 1.20):
                    over_target = state.setdefault("over_target_batches", [])
                    over_target.append({"index": index, "chars": len(part), "target": batch_target_chars})
                    logging.warning(
                        "Background condensation batch %s/%s remained above soft target chars=%s target=%s; "
                        "keeping the smallest valid condensation instead of failing weekly maintenance",
                        index + 1, len(chunks), len(part), batch_target_chars,
                    )
            partials.append(part)
            state.update({"completed_batches": partials, "current_index": index + 1, "current_partial": "", "current_complete": True})
            self._checkpoint_write(checkpoint_path, state)
        combined = "\n\n--- MEMORY BATCH ---\n\n".join(partials)
        base = ("PREVIOUS CONSOLIDATED MEMORY:\n" + base_summary + "\n\n") if base_summary else ""
        merge_prompt = (
            "Produce ONE tight global working-memory snapshot from the prior snapshot and newer distilled notes. "
            "This is NOT an archive and must NOT re-list every source item. The validated task_history archive already preserves reconstructable detail. "
            "Keep only information whose absence would likely cause a materially worse future answer/action: current project state, durable decisions, stable preferences, unresolved obligations, reusable failure/efficiency lessons, and still-useful artifact/data pointers. "
            "For repeated project updates, keep the newest non-superseded state and only history needed to explain a current constraint or lesson. "
            "Delete transient market levels, temporary terminology, examples, smoke-test narration, routine successes, superseded states, duplicated instructions, and narrative chronology that does not change current behavior. "
            "Prefer concise labeled statements over prose. Do NOT preserve one bullet per memory/source record. "
            f"Compression target: about {snapshot_target_chars} characters. This is a strong target but not a maintenance failure condition. Do not invent facts. Return plain text only.\n\n"
            + base + "NEW/SURVIVING SUMMARIES:\n" + combined
        )
        def save_merge_partial(text: str, complete: bool, segment: int) -> None:
            state.update({"stage": "merge", "merge_partial": text, "merge_complete": bool(complete), "merge_segment": int(segment)})
            self._checkpoint_write(checkpoint_path, state)
        summary = self.client.generate_complete_text(
            merge_prompt, think=False, temperature=0.0, num_predict=1800, max_segments=2,
            response_so_far=str(state.get("merge_partial") or ""), on_partial=save_merge_partial,
        ).strip()
        if not summary:
            raise RuntimeError("background condensation merge returned blank")
        if len(summary) > int(snapshot_target_chars * 1.15):
            tighten = (
                "The candidate below is too verbose for background working memory. Re-condense it; do not continue it. "
                "Remove source-by-source narration, repeated project state, examples, transient values, and anything safely recoverable from task_history. "
                "Keep only current state, durable decisions/preferences, unresolved obligations, and reusable lessons that materially affect future work. "
                f"Aim for <= {snapshot_target_chars} characters, plain text only. If that exact size would destroy useful state, return the smallest sufficient snapshot rather than failing.\n\nCANDIDATE:\n" + summary
            )
            tightened = self.client.generate_complete_text(
                tighten, think=False, temperature=0.0, num_predict=1800, max_segments=2,
            ).strip()
            if tightened and len(tightened) < len(summary):
                summary = tightened
            if len(summary) > int(snapshot_target_chars * 1.15):
                logging.warning(
                    "Background condensation merge remained above soft target chars=%s target=%s; "
                    "saving the smallest valid snapshot instead of failing weekly maintenance",
                    len(summary), snapshot_target_chars,
                )
                state["merge_over_target"] = {"chars": len(summary), "target": snapshot_target_chars}
                self._checkpoint_write(checkpoint_path, state)
        source_through = max(record["at"] for record in (delta_all if incremental else records))
        self.durable.save_background_snapshot(summary, source_through)
        self.durable.keep_latest_background_snapshot()
        checkpoint_path.unlink(missing_ok=True)
        return summary

    def _rebuild_background_snapshot(self) -> str:
        return self.rebuild_background_snapshot(incremental=False)

    def run(self, *, force: bool = False, full: bool = False, recent_only: bool = False) -> dict | None:
        retention_days = max(1, int(self.config.get("deep_history_retention_days", 30)))
        max_tasks = max(1, int(self.config.get("deep_history_max_tasks_per_pass", 500)))
        full_batch_rows = max(1, int(self.config.get("deep_history_full_batch_rows", 200)))
        full_samples_per_batch = max(1, int(self.config.get("deep_history_full_samples_per_batch", 12)))
        full_merge_max_records = max(2, min(6, int(self.config.get("deep_history_full_merge_max_records", 6))))
        deep_sampled = bool(not full and not recent_only)
        if self.queue is not None:
            stats = self.queue.stats()
            if stats.get("stream_length", 0) or stats.get("pending_count", 0):
                return None
        if self.drain_event is not None and self.drain_event.is_set():
            return None
        regular_window_days = max(1, int(self.config.get("regular_memory_window_days", 14)))
        raw_candidates = self.durable.deep_history_candidates(
            retention_days,
            max_tasks,
            all_history=bool(full),
            unbounded=bool(full),
            recent_days=regular_window_days if recent_only and not full else None,
        )
        archived_candidates = (
            self.durable.task_history_archive_candidates()
            if full and hasattr(self.durable, "task_history_archive_candidates")
            else []
        )
        candidates = list(raw_candidates)
        if full:
            raw_ids = {str(item["task_id"]) for item in raw_candidates}
            candidates.extend(
                item for item in archived_candidates
                if str(item.get("task_id") or "") not in raw_ids
            )
            candidates.sort(
                key=lambda item: (item.get("started_at"), item.get("updated_at")),
                reverse=True,
            )
        if not candidates:
            return None

        groups = self._group_candidates(candidates)

        backup_dir = str(self.config.get("deep_history_backup_dir") or (self.runtime_root.parent / "Norm-backups" / "history-maintenance"))
        backup_path = self.durable.create_sql_backup(backup_dir)
        records: list[dict] = []
        primary_ids: list[str] = []
        replay_results: list[dict] = []
        merge_stats = {
            "merge_attempt_clusters": 0,
            "merged_groups": 0,
            "merge_fallback_groups": 0,
            "merged_constituent_validations": 0,
            "records_before_merge": 0,
            "records_after_merge": 0,
        }
        try:
            validation_batches = (
                self._batch_groups_by_rows(groups, full_batch_rows)
                if full or deep_sampled
                else [groups]
            )
            validation_policy = (
                "full_sampled_batches_plus_hierarchical_merge"
                if full
                else "deep_sampled_batches"
                if deep_sampled
                else "recent_every_record"
            )

            if full:
                # Full mode walks the whole ordered archive in validation batches.
                # Batch size controls QA sampling density, not how much of the archive is processed.
                for batch_index, batch_groups in enumerate(validation_batches, start=1):
                    batch_records: list[dict] = []
                    for group in batch_groups:
                        newer_context = self._relevant_newer_records(group, records, limit=8)
                        record = self._summarize_group(group, newer_context=newer_context)
                        batch_records.append(record)
                        records.append(record)

                    sampled_records = self._sample_records(batch_records, full_samples_per_batch)
                    batch_replays: list[dict] = []
                    groups_by_primary = {
                        str(self._choose_primary(group)["task_id"]): group
                        for group in batch_groups
                    }
                    for sampled in sampled_records:
                        record = sampled
                        passed, verdict, replay = self._replay_one(record)
                        repaired = False
                        if not passed:
                            group = groups_by_primary.get(str(record["primary_task_id"]))
                            if group is not None:
                                repaired_record = self._repair_compact_record(group, record, verdict, replay)
                                target_id = str(record["primary_task_id"])
                                for collection in (batch_records, records):
                                    for idx, existing in enumerate(collection):
                                        if str(existing.get("primary_task_id") or "") == target_id:
                                            collection[idx] = repaired_record
                                            break
                                record = repaired_record
                                repaired = True
                                passed, verdict, replay = self._replay_one(record)
                        result = {
                            "task_id": record["primary_task_id"],
                            "passed": passed,
                            "repaired": repaired,
                            "verdict": verdict,
                            "batch": batch_index,
                        }
                        batch_replays.append(result)
                        replay_results.append(result)

                    if not batch_replays or not all(item["passed"] for item in batch_replays):
                        self.durable.record_maintenance_note(
                            "deep_history_consolidation",
                            "Full compact-history refresh failed replay validation; existing compact and raw history were preserved and SQL backup retained.",
                            details={
                                "status": "replay_failed",
                                "backup": backup_path,
                                "validation_policy": validation_policy,
                                "failed_batch": batch_index,
                                "validation_batches": len(validation_batches),
                                "replays": batch_replays,
                            },
                        )
                        return {
                            "status": "replay_failed",
                            "backup": backup_path,
                            "validation_policy": validation_policy,
                            "failed_batch": batch_index,
                            "validation_batches": len(validation_batches),
                            "replays": batch_replays,
                        }

                records, merge_stats = self._full_merge_records(
                    records, max_size=full_merge_max_records
                )
                primary_ids = self.durable.upsert_task_history(records, validated=False)
            elif deep_sampled:
                for batch_index, batch_groups in enumerate(validation_batches, start=1):
                    batch_records: list[dict] = []
                    for group in batch_groups:
                        record = self._summarize_group(group)
                        batch_records.append(record)
                        records.append(record)

                    sampled_records = self._sample_records(batch_records, full_samples_per_batch)
                    batch_replays: list[dict] = []
                    groups_by_primary = {
                        str(self._choose_primary(group)["task_id"]): group
                        for group in batch_groups
                    }
                    for sampled in sampled_records:
                        record = sampled
                        passed, verdict, replay = self._replay_one(record)
                        repaired = False
                        if not passed:
                            group = groups_by_primary.get(str(record["primary_task_id"]))
                            if group is not None:
                                repaired_record = self._repair_compact_record(group, record, verdict, replay)
                                target_id = str(record["primary_task_id"])
                                for collection in (batch_records, records):
                                    for idx, existing in enumerate(collection):
                                        if str(existing.get("primary_task_id") or "") == target_id:
                                            collection[idx] = repaired_record
                                            break
                                record = repaired_record
                                repaired = True
                                passed, verdict, replay = self._replay_one(record)
                        result = {
                            "task_id": record["primary_task_id"],
                            "passed": passed,
                            "repaired": repaired,
                            "verdict": verdict,
                            "batch": batch_index,
                        }
                        batch_replays.append(result)
                        replay_results.append(result)

                    if not batch_replays or not all(item["passed"] for item in batch_replays):
                        self.durable.record_maintenance_note(
                            "deep_history_consolidation",
                            "Deep compact-history batch failed sampled replay validation; raw history was preserved and SQL backup retained.",
                            details={
                                "status": "replay_failed",
                                "backup": backup_path,
                                "validation_policy": validation_policy,
                                "failed_batch": batch_index,
                                "validation_batches": len(validation_batches),
                                "replays": batch_replays,
                            },
                        )
                        return {
                            "status": "replay_failed",
                            "backup": backup_path,
                            "validation_policy": validation_policy,
                            "failed_batch": batch_index,
                            "validation_batches": len(validation_batches),
                            "replays": batch_replays,
                        }
                primary_ids = self.durable.upsert_task_history(records, validated=False)
            else:
                records = [self._summarize_group(group) for group in groups]
                batch_replays: list[dict] = []
                for index, record in enumerate(list(records)):
                    passed, verdict, replay = self._replay_one(record)
                    repaired = False
                    if not passed:
                        repaired_record = self._repair_compact_record(groups[index], record, verdict, replay)
                        records[index] = repaired_record
                        record = repaired_record
                        repaired = True
                        passed, verdict, replay = self._replay_one(record)
                    result = {
                        "task_id": record["primary_task_id"],
                        "passed": passed,
                        "repaired": repaired,
                        "verdict": verdict,
                        "batch": 1,
                    }
                    batch_replays.append(result)
                    replay_results.append(result)
                if not batch_replays or not all(item["passed"] for item in batch_replays):
                    self.durable.record_maintenance_note(
                        "deep_history_consolidation",
                        "Compact-history replay validation failed; raw history was preserved and SQL backup retained.",
                        details={
                            "status": "replay_failed",
                            "backup": backup_path,
                            "validation_policy": validation_policy,
                            "failed_batch": 1,
                            "validation_batches": 1,
                            "replays": batch_replays,
                        },
                    )
                    return {
                        "status": "replay_failed",
                        "backup": backup_path,
                        "validation_policy": validation_policy,
                        "failed_batch": 1,
                        "validation_batches": 1,
                        "replays": batch_replays,
                    }
                primary_ids = self.durable.upsert_task_history(records, validated=False)

            self.durable.mark_task_history_validated(primary_ids)
            raw_task_ids = [str(item["task_id"]) for item in raw_candidates]
            source_task_ids = [task_id for record in records for task_id in record["source_task_ids"]]
            deleted_tasks = self.durable.delete_validated_archived_tasks(raw_task_ids) if raw_task_ids else 0
            compact_rows_replaced = 0
            if full and hasattr(self.durable, "delete_task_history_rows"):
                refreshed_primary_ids = {str(v) for v in primary_ids}
                obsolete_compact_ids = [
                    str(item["task_id"]) for item in archived_candidates
                    if str(item.get("task_id") or "") not in refreshed_primary_ids
                ]
                compact_rows_replaced = self.durable.delete_task_history_rows(obsolete_compact_ids)
            if recent_only and not full:
                conversation = {
                    "messages_deleted": 0,
                    "thread_summaries_deleted": 0,
                    "memory_links_preserved": 0,
                }
                superseded_deleted = 0
            else:
                conversation = self.durable.prune_covered_conversation_history(
                    retention_days, all_history=bool(full)
                )
                superseded_deleted = self.durable.delete_superseded_memories()
            # Validated task_history rows are the durable long-term compact archive.
            # Do not collapse the growing archive into one bounded background snapshot:
            # prompt-size limits belong at retrieval/injection time, not durable storage time.
            self.durable.delete_sql_backup(backup_path)
            details = {
                "status": "success", "archived_records": len(records), "deleted_tasks": deleted_tasks,
                "source_task_ids": len(source_task_ids),
                "validation_policy": validation_policy,
                "validation_batches": len(validation_batches),
                "replay_samples_passed": len(replay_results),
                "compact_records_in_pass": len(records),
                "compact_rows_replaced": compact_rows_replaced,
                "recent_only": bool(recent_only and not full),
                "regular_memory_window_days": regular_window_days if recent_only and not full else None,
                **merge_stats,
                "messages_deleted": conversation["messages_deleted"],
                "thread_summaries_deleted": conversation["thread_summaries_deleted"],
                "memory_links_preserved": conversation.get("memory_links_preserved", 0),
                "superseded_memories_deleted": superseded_deleted,
                "durable_archive": "task_history", "aggregate_archive_char_cap": None,
                "backup_deleted": True,
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
