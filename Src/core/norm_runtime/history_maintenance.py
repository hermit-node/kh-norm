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
    def __init__(self, durable, client, *, runtime_root, config: dict, queue=None, drain_event=None, n1_gatekeeper=None) -> None:
        self.durable = durable
        self.client = client
        self.runtime_root = Path(runtime_root).resolve()
        self.state_root = load_path_settings(self.runtime_root)["state_root"]
        self.config = config
        self.queue = queue
        self.drain_event = drain_event
        self.n1_gatekeeper = n1_gatekeeper

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
    def _destructive_archive_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "disposition": {"type": "string", "enum": ["keep", "destroy"]},
                "reason": {"type": "string", "maxLength": 800},
                "outcome": {"type": "string", "maxLength": 3000},
                "lessons": {"type": "string", "maxLength": 2600},
                "future_note": {"type": "string", "maxLength": 1800},
            },
            "required": ["disposition", "reason", "outcome", "lessons", "future_note"],
            "additionalProperties": False,
        }

    def _n1_judge(self, *, mode: str, source_text: str, candidate: str, attempt: int, max_attempts: int, ideal_chars: int, acceptable_chars: int, proposed_disposition: str = "keep") -> dict:
        gate = self.n1_gatekeeper
        if gate is None or not hasattr(gate, "judge_memory_condensation"):
            destructive_delete = mode == "destructive" and proposed_disposition == "destroy"
            decision = "retry" if destructive_delete else ("accept" if len(candidate) <= ideal_chars or attempt >= max_attempts else "retry")
            return {
                "decision": decision,
                "reason": "No N1 maintenance judge available; destructive deletion not authorized." if destructive_delete else "No N1 maintenance judge available; bounded fallback used.",
                "retry_instruction": "Preserve this record; N1 is required to authorize destruction." if destructive_delete else ("Compress further while preserving important state." if decision == "retry" else ""),
            }
        return gate.judge_memory_condensation(
            mode=mode, source_text=source_text, candidate=candidate, attempt=attempt,
            max_attempts=max_attempts, ideal_chars=ideal_chars, acceptable_chars=acceptable_chars,
            proposed_disposition=proposed_disposition,
            client=self.client,
        )

    def _condense_plain_with_n1(self, *, prompt: str, source_text: str, mode: str, ideal_chars: int, acceptable_chars: int, num_predict: int, max_attempts: int, response_so_far: str = "", on_partial=None, max_output_segments: int = 4) -> tuple[str, list[dict]]:
        candidates: list[str] = []
        judgments: list[dict] = []
        current_prompt = prompt
        resume = response_so_far
        reviewability_instruction = (
            "N1 could not process your submission. Check the formatting, structure, wording, nesting, and unnecessary complexity. "
            "Repair anything that may make the result difficult to review, preserve the same semantic work, and resubmit plain text only. "
            "Do not add new research or hunt for omissions merely because N1 failed to process it."
        )
        for attempt in range(1, max_attempts + 1):
            candidate = self.client.generate_complete_text(
                current_prompt, think=False, temperature=0.0, num_predict=num_predict, max_segments=max_output_segments,
                response_so_far=resume if attempt == 1 else "", on_partial=on_partial if attempt == 1 else None,
            ).strip()
            if not candidate:
                continue
            candidates.append(candidate)
            judgment = self._n1_judge(
                mode=mode, source_text=source_text, candidate=candidate, attempt=attempt,
                max_attempts=max_attempts, ideal_chars=ideal_chars, acceptable_chars=acceptable_chars,
            )
            judgments.append({"attempt": attempt, "review_try": 1, "chars": len(candidate), **judgment})
            if bool(judgment.get("processing_failed")):
                cleanup_prompt = (
                    reviewability_instruction
                    + f"\n\nIDEAL SIZE <= {ideal_chars} characters; practical ceiling <= {acceptable_chars}."
                    + "\n\nSOURCE EXCERPT (for semantic fidelity only):\n" + source_text[:18000]
                    + "\n\nSUBMISSION N1 COULD NOT PROCESS:\n" + candidate
                )
                repaired = self.client.generate_complete_text(
                    cleanup_prompt, think=False, temperature=0.0, num_predict=num_predict, max_segments=max_output_segments
                ).strip()
                if repaired:
                    candidate = repaired
                    candidates.append(candidate)
                judgment = self._n1_judge(
                    mode=mode, source_text=source_text, candidate=candidate, attempt=attempt,
                    max_attempts=max_attempts, ideal_chars=ideal_chars, acceptable_chars=acceptable_chars,
                )
                judgments.append({"attempt": attempt, "review_try": 2, "chars": len(candidate), **judgment})
                if bool(judgment.get("processing_failed")):
                    gate = self.n1_gatekeeper
                    if gate is not None and hasattr(gate, "record_maintenance_review_failure_pass"):
                        gate.record_maintenance_review_failure_pass()
                    judgments[-1]["review_failure_pass"] = True
                    logging.warning(
                        "N1 could not process maintenance candidate twice; accepting reductive N2 candidate by review-failure pass attempt=%s chars=%s",
                        attempt, len(candidate),
                    )
                    return candidate, judgments
            if str(judgment.get("decision") or "").lower() == "accept":
                return candidate, judgments
            instruction = str(judgment.get("retry_instruction") or "Compress further while preserving important state.").strip()
            current_prompt = (
                "Rewrite the candidate below as a better maintenance condensation. Follow N1's critique exactly. "
                f"Ideal early-stop size <= {ideal_chars} characters; practical ceiling <= {acceptable_chars}. "
                "Do not invent facts. Return plain text only.\n\n"
                f"N1 CRITIQUE: {instruction}\n\nSOURCE EXCERPT:\n{source_text[:18000]}\n\nCANDIDATE:\n{candidate}"
            )
        if not candidates:
            raise RuntimeError("maintenance condensation returned blank on every attempt")
        under_ceiling = [c for c in candidates if len(c) <= acceptable_chars]
        if under_ceiling:
            return min(under_ceiling, key=len), judgments
        return min(candidates, key=len), judgments

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

    def _summarize_group(self, group: list[dict], *, newer_context: list[dict] | None = None, destructive: bool = False) -> dict | None:
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
        if destructive:
            prompt = (
                "This is Norm's DESTRUCTIVE long-cycle memory maintenance. Review this terminal task history and first decide whether it still has durable future value. "
                "You may return disposition='destroy' when the material is now useless to future work: obsolete TODOs whose work is complete, superseded state, abandoned intermediate plans, stale failures with no reusable lesson, redundant chronology, or facts now implicit in the stable baseline. "
                "Do NOT destroy current constraints, active obligations, durable decisions/preferences, reusable technical lessons, or still-useful artifact/data pointers. "
                "If disposition='keep', compress it into one durable reusable record: outcome=current durable state/results; lessons=reusable fixes/constraints/what worked or failed; future_note=only still-useful next-time guidance. "
                "When newer compact history materially supersedes this record, prefer the newer state and destroy or rewrite stale wording rather than carrying 'still unimplemented' or similar historical garbage forward. "
                "N2 owns this ranking/destruction proposal; N1 will judge the result afterward. Return only the structured JSON required by the schema.\n\nTASK ATTEMPTS:\n"
                + json.dumps(payload, ensure_ascii=False)
                + ("\n\nRELEVANT NEWER COMPACT HISTORY:\n" + json.dumps(newer, ensure_ascii=False, default=str) if newer else "")
            )
            schema = self._destructive_archive_schema()
        else:
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
            schema = self._archive_schema()

        max_attempts = max(1, min(8, int(self.config.get("consolidation_max_attempts", 4))))
        ideal_chars = max(800, int(self.config.get("consolidation_batch_target_chars", 1800)))
        acceptable_chars = max(ideal_chars, int(self.config.get("consolidation_batch_accept_chars", 3400)))
        last_error = None
        best: tuple[int, dict] | None = None
        best_safe_keep: tuple[int, dict] | None = None
        accepted = False
        current_prompt = prompt
        source_for_judge = json.dumps({"attempts": payload, "newer": newer}, ensure_ascii=False, default=str)
        reviewability_instruction = (
            "N1 could not process your submission. Check the formatting, structure, wording, nesting, and unnecessary complexity. "
            "Repair anything that may make the record difficult to review, preserve the same semantic work, and resubmit structured JSON only. "
            "Do not add new research or hunt for omissions merely because N1 failed to process it."
        )
        for attempt in range(1, max_attempts + 1):
            try:
                raw = self.client.generate(current_prompt, think=False, temperature=0.0, num_predict=1800, response_format=schema)
                parsed = self.client.parse_json(raw)
                disposition = str(parsed.get("disposition") or "keep").strip().lower() if destructive else "keep"
                if disposition not in {"keep", "destroy"}:
                    raise ValueError("invalid destructive disposition")
                if disposition == "keep" and not all(str(parsed.get(k) or "").strip() for k in ("outcome", "lessons", "future_note")):
                    raise ValueError("archive summary contained blank required fields")
                candidate_text = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
                score = len(candidate_text)
                if best is None or score < best[0]:
                    best = (score, parsed)
                if disposition == "keep" and (best_safe_keep is None or score < best_safe_keep[0]):
                    best_safe_keep = (score, parsed)
                judgment = self._n1_judge(
                    mode="destructive" if destructive else "reductive",
                    source_text=source_for_judge, candidate=candidate_text, attempt=attempt, max_attempts=max_attempts,
                    ideal_chars=ideal_chars, acceptable_chars=acceptable_chars, proposed_disposition=disposition,
                )
                if bool(judgment.get("processing_failed")):
                    repair_prompt = (
                        prompt
                        + "\n\nN1 PROCESSING FAILURE - REVIEWABILITY REPAIR ONLY:\n"
                        + reviewability_instruction
                        + "\n\nSUBMISSION N1 COULD NOT PROCESS:\n"
                        + candidate_text
                    )
                    repaired_raw = self.client.generate(
                        repair_prompt, think=False, temperature=0.0, num_predict=1800, response_format=schema
                    )
                    repaired = self.client.parse_json(repaired_raw)
                    repaired_disposition = str(repaired.get("disposition") or "keep").strip().lower() if destructive else "keep"
                    if repaired_disposition not in {"keep", "destroy"}:
                        raise ValueError("invalid destructive disposition after reviewability repair")
                    if repaired_disposition == "keep" and not all(str(repaired.get(k) or "").strip() for k in ("outcome", "lessons", "future_note")):
                        raise ValueError("reviewability repair contained blank required fields")
                    parsed = repaired
                    disposition = repaired_disposition
                    candidate_text = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
                    score = len(candidate_text)
                    if best is None or score < best[0]:
                        best = (score, parsed)
                    if disposition == "keep" and (best_safe_keep is None or score < best_safe_keep[0]):
                        best_safe_keep = (score, parsed)
                    judgment = self._n1_judge(
                        mode="destructive" if destructive else "reductive",
                        source_text=source_for_judge, candidate=candidate_text, attempt=attempt, max_attempts=max_attempts,
                        ideal_chars=ideal_chars, acceptable_chars=acceptable_chars, proposed_disposition=disposition,
                    )
                    if bool(judgment.get("processing_failed")):
                        gate = self.n1_gatekeeper
                        if gate is not None and hasattr(gate, "record_maintenance_review_failure_pass"):
                            gate.record_maintenance_review_failure_pass()
                        if destructive and disposition == "destroy":
                            logging.warning(
                                "N1 could not process destructive discard twice; destruction not authorized primary_task_id=%s",
                                primary["task_id"],
                            )
                            if best_safe_keep is not None:
                                best = best_safe_keep
                                break
                            current_prompt = (
                                prompt
                                + "\n\nN1 could not review the proposed destruction twice, so destruction is not authorized. "
                                "Return disposition='keep' and preserve/reduce this material into a reviewable durable record.\n\n"
                                + candidate_text
                            )
                            continue
                        logging.warning(
                            "N1 could not process compact-history candidate twice; accepting safe keep candidate by review-failure pass primary_task_id=%s",
                            primary["task_id"],
                        )
                        best = (score, parsed)
                        accepted = True
                        break
                if str(judgment.get("decision") or "").lower() == "accept":
                    best = (score, parsed)
                    accepted = True
                    break
                instruction = str(judgment.get("retry_instruction") or "Improve density and preserve only durable useful state.").strip()
                current_prompt = (
                    prompt
                    + "\n\nN1 REVIEW OF PRIOR CANDIDATE:\n"
                    + instruction
                    + "\n\nPRIOR CANDIDATE:\n"
                    + candidate_text
                    + "\nReturn a revised structured JSON record only."
                )
            except Exception as exc:
                last_error = exc
        if best is None:
            raise RuntimeError(f"task-history compression failed after {max_attempts} attempts: {last_error}")
        parsed = best[1]
        if destructive and str(parsed.get("disposition") or "keep").strip().lower() == "destroy":
            if not accepted:
                if best_safe_keep is not None:
                    parsed = best_safe_keep[1]
                else:
                    raise RuntimeError(f"N1 did not approve destructive discard for task {primary['task_id']} after {max_attempts} attempts")
            else:
                logging.info("N1 approved destructive memory discard primary_task_id=%s reason=%s", primary["task_id"], str(parsed.get("reason") or "")[:500])
                return None
        return {
            "_n1_approved": bool(accepted) if destructive else True,
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
        batch_accept_chars = max(batch_target_chars, int(self.config.get("consolidation_batch_accept_chars", 3400)))
        max_attempts = max(1, min(8, int(self.config.get("consolidation_max_attempts", 4))))
        max_output_segments = max(2, min(8, int(self.config.get("consolidation_max_output_segments", 4))))
        snapshot_target_chars = max(2500, int(self.config.get("consolidation_snapshot_target_chars", 6000)))
        snapshot_accept_chars = max(snapshot_target_chars, int(self.config.get("consolidation_snapshot_accept_chars", max(9000, snapshot_target_chars))))
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
        # Freeze the input from the first attempt. New PostgreSQL memories must not
        # invalidate already-approved chunks while this run is still unfinished.
        try:
            frozen = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            frozen = {}
        if (isinstance(frozen, dict) and frozen.get("mode") == mode
                and isinstance(frozen.get("source_chunks"), list)
                and frozen.get("frozen_source_through")):
            state = frozen
            chunks = list(frozen["source_chunks"])
            base_summary = str(frozen.get("frozen_base_summary") or "")
            source_through = datetime.fromisoformat(frozen["frozen_source_through"])
        else:
            # Old checkpoints without a frozen inventory retain their existing
            # fingerprint rule. Do not resume them against unproven source text.
            state = self._checkpoint_read(checkpoint_path, fingerprint)
            if not state:
                state = {"fingerprint": fingerprint, "mode": mode, "completed_batches": [],
                         "current_index": 0, "current_partial": "", "merge_partial": ""}
            state["source_chunks"] = chunks
            state["frozen_base_summary"] = base_summary
            source_through = max(record["at"] for record in (delta_all if incremental else records))
            state["frozen_source_through"] = source_through.isoformat()
            self._checkpoint_write(checkpoint_path, state)
        partials = list(state.get("completed_batches") or [])
        for index, chunk in enumerate(chunks):
            if index < len(partials):
                continue
            prompt = (
                "Distill this PostgreSQL history slice into SMALL working-memory notes, not a rewritten transcript. "
                "Keep only information that is likely to materially change a future answer or action: current project state, durable decisions, stable preferences, unresolved obligations, reusable technical lessons, and artifact/data pointers that remain operationally useful. "
                "This is a REDUCTIVE pass: rank information by future usefulness. Give important/current state, decisions, constraints, active obligations, and durable lessons enough detail; make less-important material much smaller. "
                "Merge exact duplicates and superseded wording. Reduce routine chatter, smoke-test narration, one-off examples, transient values, temporary shorthand, intermediate attempts, and redundant chronology to the smallest trace that preserves useful context rather than treating low importance alone as permission to erase a distinct subject. "
                "Do NOT produce one bullet/line per source record. Fewer synthesized items are better. Preserve a timestamp only when timing itself remains meaningful. "
                f"Compression target: about {batch_target_chars} characters. Treat this as a strong target, not a reason to fail the maintenance run. Do not invent facts. Return plain text only.\n\n" + chunk
            )
            resume = str(state.get("current_partial") or "") if int(state.get("current_index", -1)) == index else ""
            def save_batch_partial(text: str, complete: bool, segment: int, idx=index) -> None:
                state.update({"current_index": idx, "current_partial": text, "current_complete": bool(complete), "current_segment": int(segment)})
                self._checkpoint_write(checkpoint_path, state)
            part, judgments = self._condense_plain_with_n1(
                prompt=prompt, source_text=chunk, mode="reductive",
                ideal_chars=batch_target_chars, acceptable_chars=batch_accept_chars,
                num_predict=900, max_attempts=max_attempts, response_so_far=resume, on_partial=save_batch_partial,
                max_output_segments=max_output_segments,
            )
            state.setdefault("n1_batch_judgments", {})[str(index)] = judgments
            if len(part) > batch_accept_chars:
                over_target = state.setdefault("over_target_batches", [])
                over_target.append({"index": index, "chars": len(part), "ideal": batch_target_chars, "ceiling": batch_accept_chars})
                logging.warning(
                    "Background condensation batch %s/%s remained above practical ceiling chars=%s ideal=%s ceiling=%s after %s N2 attempt(s); keeping N1-approved/best valid result",
                    index + 1, len(chunks), len(part), batch_target_chars, batch_accept_chars, len(judgments),
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
        summary, merge_judgments = self._condense_plain_with_n1(
            prompt=merge_prompt, source_text=(base + combined), mode="reductive",
            ideal_chars=snapshot_target_chars, acceptable_chars=snapshot_accept_chars,
            num_predict=1800, max_attempts=max_attempts,
            response_so_far=str(state.get("merge_partial") or ""), on_partial=save_merge_partial,
            max_output_segments=max_output_segments,
        )
        state["n1_merge_judgments"] = merge_judgments
        if len(summary) > snapshot_accept_chars:
            logging.warning(
                "Background condensation merge remained above practical ceiling chars=%s ideal=%s ceiling=%s after %s N2 attempt(s); saving N1-approved/best valid snapshot",
                len(summary), snapshot_target_chars, snapshot_accept_chars, len(merge_judgments),
            )
            state["merge_over_target"] = {"chars": len(summary), "ideal": snapshot_target_chars, "ceiling": snapshot_accept_chars}
            self._checkpoint_write(checkpoint_path, state)
        self.durable.save_background_snapshot(summary, source_through)
        self.durable.keep_latest_background_snapshot()
        checkpoint_path.unlink(missing_ok=True)
        return summary

    def _rebuild_background_snapshot(self) -> str:
        return self.rebuild_background_snapshot(incremental=False)

    @staticmethod
    def _full_source_batches(groups: list[list[dict]], limit: int = 20) -> list[list[list[dict]]]:
        """Keep a grouped task intact; checkpoint at most 20 source memories otherwise."""
        cap = max(1, int(limit))
        batches: list[list[list[dict]]] = []
        batch: list[list[dict]] = []
        count = 0
        for group in groups:
            if batch and count + len(group) > cap:
                batches.append(batch)
                batch, count = [], 0
            batch.append(group)
            count += len(group)
            if count >= cap:
                batches.append(batch)
                batch, count = [], 0
        if batch:
            batches.append(batch)
        return batches

    @staticmethod
    def _thaw_full_source_groups(groups: list[list[dict]]) -> list[list[dict]]:
        """Restore timestamps from PostgreSQL JSONB's frozen source inventory."""
        thawed = []
        for group in groups:
            restored = []
            for original in group:
                item = dict(original)
                for key in ('started_at', 'updated_at'):
                    if isinstance(item.get(key), str):
                        item[key] = datetime.fromisoformat(item[key])
                restored.append(item)
            thawed.append(restored)
        return thawed

    def _run_full_chunked(self, *, retention_days: int, merge_max_records: int) -> dict | None:
        """Destructive full maintenance with frozen PG inventory and 20-memory commits.

        A verified batch is compacted and its raw/obsolete sources deleted in ONE
        PostgreSQL transaction. An interrupted run resumes only pending batches.
        """
        required = ('active_full_condensation_run', 'create_full_condensation_run',
                    'commit_full_condensation_batch', 'finish_full_condensation_run')
        if not all(hasattr(self.durable, method) for method in required):
            raise RuntimeError('full condensation requires PostgreSQL inventory methods; refusing legacy uncheckpointed prune')
        run = self.durable.active_full_condensation_run()
        if run is None:
            raw = self.durable.deep_history_candidates(retention_days, 1, all_history=True, unbounded=True)
            archived = self.durable.task_history_archive_candidates()
            raw_ids = {str(item['task_id']) for item in raw}
            candidates = raw + [item for item in archived if str(item['task_id']) not in raw_ids]
            if not candidates:
                return None
            candidates.sort(key=lambda item: (item['started_at'], item['updated_at']), reverse=True)
            groups = self._group_candidates(candidates)
            batches = self._full_source_batches(groups, 20)
            backup_dir = str(self.config.get('deep_history_backup_dir') or
                             (self.runtime_root.parent / 'Norm-backups' / 'history-maintenance'))
            backup_path = self.durable.create_sql_backup(backup_dir)
            run = self.durable.create_full_condensation_run(batches, backup_path)
        backup_path = str(run['backup_path'])
        if not Path(backup_path).is_file():
            raise RuntimeError('full condensation SQL safety backup missing; refusing destructive resume')
        run_id = str(run['run_id'])
        verified = [record for batch in run['batches'] if batch['status'] == 'committed'
                    for record in batch.get('results', [])]
        completed = sum(batch['status'] == 'committed' for batch in run['batches'])
        deleted = sum(int(batch.get('original_deleted') or 0) for batch in run['batches'])
        replay_count = sum(sum(item.get('kind') == 'replay' and item.get('passed') for item in batch.get('verification', []))
                           for batch in run['batches'] if batch['status'] == 'committed')
        for batch in run['batches']:
            if batch['status'] == 'committed':
                continue
            if batch['status'] != 'pending' or not batch.get('groups'):
                raise RuntimeError(f'full condensation batch {batch["index"]} lacks frozen source; no pruning')
            groups = self._thaw_full_source_groups(batch['groups'])
            group_outputs = []
            discarded_ids = []
            verification = []
            # Group-level N1 must approve every keep/destroy before any source is removed.
            for group in groups:
                newer_context = self._relevant_newer_records(group, verified + group_outputs, limit=8)
                summary = self._summarize_group(group, newer_context=newer_context, destructive=True)
                members = [str(item['task_id']) for item in group]
                if summary is None:
                    discarded_ids.extend(members)
                    verification.extend({'kind': 'n1_destroy', 'task_id': sid, 'passed': True}
                                        for sid in members)
                else:
                    if summary.get('_n1_approved') is not True:
                        raise RuntimeError(f'N1 did not explicitly approve kept memory {members[0]}; originals preserved')
                    summary.pop('_n1_approved', None)
                    verification.extend({'kind': 'n1_keep', 'task_id': sid, 'passed': True}
                                        for sid in members)
                    passed, verdict, replay = self._replay_one(summary)
                    repaired = False
                    if not passed:
                        summary = self._repair_compact_record(group, summary, verdict, replay)
                        repaired = True
                        passed, verdict, replay = self._replay_one(summary)
                    if not passed:
                        logging.warning('Full condensation batch %s replay failed for %s; no sources deleted',
                                        batch['index'], members[0])
                        return {'status': 'replay_failed', 'run_id': run_id, 'failed_batch': batch['index'],
                                'completed_batches': completed, 'backup': backup_path, 'verdict': verdict}
                    group_outputs.append(summary)
                    verification.extend({'kind': 'replay', 'task_id': sid, 'passed': True,
                                         'repaired': repaired, 'verdict': verdict} for sid in members)
            # The final within-batch merge is additionally replay-verified against
            # each constituent. Failed merges fall back to independently checked rows.
            merged, merge_stats = self._full_merge_records(group_outputs, max_size=merge_max_records)
            # Check the model did not accidentally omit an input, even across merge.
            kept_ids = {str(item['task_id']) for group in groups for item in group} - set(discarded_ids)
            covered = {str(sid) for r in merged for sid in [r['primary_task_id'], *(r.get('source_task_ids') or [])]}
            if kept_ids - covered:
                raise RuntimeError('full condensation merge lost constituent memory; originals preserved')
            commit = self.durable.commit_full_condensation_batch(
                run_id=run_id, batch_index=int(batch['index']), records=merged,
                verification=verification, discard_ids=discarded_ids,
            )
            completed += 1
            deleted += int(commit['deleted_raw']) + int(commit['deleted_compact'])
            replay_count += sum(v.get('kind') == 'replay' and v.get('passed') for v in verification)
            verified.extend(merged)
            logging.info('Full condensation verified/committed batch %s/%s sources=%s compact=%s deleted=%s',
                         batch['index'], run['total_batches'], sum(map(len, groups)),
                         len(merged), commit['deleted_raw'] + commit['deleted_compact'])
        # Existing full-maintenance cleanup semantics, but only after ALL frozen
        # batches are verified and committed; checkpoint is kept until finalization.
        conversation = self.durable.prune_covered_conversation_history(retention_days, all_history=True)
        superseded_deleted = self.durable.delete_superseded_memories()
        self.durable.finish_full_condensation_run(run_id)
        self.durable.delete_sql_backup(backup_path)
        details = {
            'status': 'success', 'mode': 'full', 'run_id': run_id,
            'validation_policy': 'all_memories_replay_verified_in_20_source_batches',
            'validation_batches': completed, 'replay_samples_passed': replay_count,
            'compact_records_in_pass': len(verified), 'original_records_deleted': deleted,
            'messages_deleted': conversation['messages_deleted'],
            'thread_summaries_deleted': conversation['thread_summaries_deleted'],
            'memory_links_preserved': conversation.get('memory_links_preserved', 0),
            'superseded_memories_deleted': superseded_deleted,
            'backup_deleted': True, 'durable_archive': 'task_history',
        }
        self.durable.record_maintenance_note(
            'deep_history_consolidation',
            'Full maintenance committed and pruned each validated 20-memory PostgreSQL batch.',
            details=details,
        )
        return details

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
        if full:
            return self._run_full_chunked(retention_days=retention_days,
                                          merge_max_records=full_merge_max_records)
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
        destroyed_groups = 0
        destroyed_source_tasks = 0
        destroyed_task_ids: set[str] = set()
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
                        record = self._summarize_group(group, newer_context=newer_context, destructive=True)
                        if record is None:
                            destroyed_groups += 1
                            destroyed_source_tasks += len(group)
                            destroyed_task_ids.update(str(item.get("task_id") or "") for item in group if str(item.get("task_id") or ""))
                            continue
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

                    if batch_records and (not batch_replays or not all(item["passed"] for item in batch_replays)):
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
                primary_ids = self.durable.upsert_task_history(records, validated=False) if records else []
            elif deep_sampled:
                for batch_index, batch_groups in enumerate(validation_batches, start=1):
                    batch_records: list[dict] = []
                    for group in batch_groups:
                        record = self._summarize_group(group, destructive=True)
                        if record is None:
                            destroyed_groups += 1
                            destroyed_source_tasks += len(group)
                            destroyed_task_ids.update(str(item.get("task_id") or "") for item in group if str(item.get("task_id") or ""))
                            continue
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

                    if batch_records and (not batch_replays or not all(item["passed"] for item in batch_replays)):
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
                primary_ids = self.durable.upsert_task_history(records, validated=False) if records else []
            else:
                records = [record for group in groups if (record := self._summarize_group(group, destructive=False)) is not None]
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
                primary_ids = self.durable.upsert_task_history(records, validated=False) if records else []

            if primary_ids:
                self.durable.mark_task_history_validated(primary_ids)
            raw_task_ids = [str(item["task_id"]) for item in raw_candidates]
            source_task_ids = [task_id for record in records for task_id in record["source_task_ids"]]
            raw_destroyed_ids = [task_id for task_id in raw_task_ids if task_id in destroyed_task_ids]
            raw_archived_ids = [task_id for task_id in raw_task_ids if task_id not in destroyed_task_ids]
            deleted_tasks = self.durable.delete_validated_archived_tasks(raw_archived_ids) if raw_archived_ids else 0
            destructively_deleted_tasks = (
                self.durable.delete_n1_approved_discarded_tasks(
                    raw_destroyed_ids, approval="n1-approved-destructive-maintenance"
                )
                if raw_destroyed_ids else 0
            )
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
                "destructively_discarded_groups": destroyed_groups,
                "destructively_discarded_source_tasks": destroyed_source_tasks,
                "destructively_deleted_raw_tasks": destructively_deleted_tasks,
                "recent_only": bool(recent_only and not full),
                "regular_memory_window_days": regular_window_days if recent_only and not full else None,
                **merge_stats,
                "messages_deleted": conversation["messages_deleted"],
                "thread_summaries_deleted": conversation["thread_summaries_deleted"],
                "memory_links_preserved": conversation.get("memory_links_preserved", 0),
                "superseded_memories_deleted": superseded_deleted,
                "durable_archive": "task_history", "aggregate_archive_char_cap": None,
                "backup_deleted": True,
                "n1_maintenance_judgments": (self.n1_gatekeeper.maintenance_judgment_stats() if self.n1_gatekeeper is not None and hasattr(self.n1_gatekeeper, "maintenance_judgment_stats") else {}),
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
