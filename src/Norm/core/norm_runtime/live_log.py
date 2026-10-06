from __future__ import annotations

import difflib
import hashlib
import json
import ntpath
import re
from .secret_redaction import redact
from datetime import datetime, timezone
from typing import Any

import redis


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_validation_value(value: object) -> str:
    """Canonicalize model-supplied semantic values without pretending to do semantic matching."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
        except Exception:
            return text
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RedisTaskLog:
    def __init__(self, client: redis.Redis, prefix: str = "norm:task", validation_prefix: str = "norm:validation") -> None:
        self.client = client
        self.prefix = prefix.rstrip(":")
        self.validation_prefix = validation_prefix.rstrip(":")

    @classmethod
    def localhost(cls, db: int = 0) -> "RedisTaskLog":
        client = redis.Redis(host="127.0.0.1", port=6379, db=db, decode_responses=True)
        client.ping()
        return cls(client)

    def _state_key(self, task_id: str) -> str:
        return f"{self.prefix}:{task_id}:state"

    def _stream_key(self, task_id: str) -> str:
        return f"{self.prefix}:{task_id}:events"

    def _global_validation_key(self) -> str:
        # The validation pool is intentionally one Redis hash.  Individual facts are
        # hash fields, never top-level Redis keys.
        return f"{self.validation_prefix}:pool"

    def _legacy_validation_facts_key(self) -> str:
        return f"{self.validation_prefix}:facts"

    def _legacy_validation_recent_index_key(self) -> str:
        return f"{self.validation_prefix}:recent"

    def _legacy_validation_pending_change_key(self) -> str:
        return f"{self.validation_prefix}:pending-changes"

    def _model_buffer_key(self, task_id: str, step_id: str) -> str:
        return f"{self.prefix}:{task_id}:model-buffer:{step_id}"

    def _emit(self, task_id: str, event: str, **fields: Any) -> str:
        payload = {"event": event, "at": _now()}
        payload.update({k: json.dumps(v) if isinstance(v, (dict, list, tuple)) else str(v) for k, v in redact(fields).items()})
        return self.client.xadd(self._stream_key(task_id), payload, maxlen=5000, approximate=True)

    def start_task(self, task_id: str, title: str, plan: dict[str, Any]) -> None:
        state = {
            "task_id": task_id,
            "title": title,
            "status": "running",
            "current_step": "",
            "started_at": _now(),
            "updated_at": _now(),
        }
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(self._state_key(task_id), mapping=state)
        pipe.hset(self._state_key(task_id), "plan", json.dumps(plan))
        pipe.execute()
        self._emit(task_id, "task_started", title=title, plan=plan)

    def update_plan(self, task_id: str, title: str, plan: dict[str, Any]) -> None:
        self.client.hset(self._state_key(task_id), mapping={
            "title": title,
            "plan": json.dumps(plan),
            "updated_at": _now(),
        })
        self._emit(task_id, "plan_materialized", title=title, plan=plan)

    def step_started(self, task_id: str, step_id: str, name: str) -> None:
        self.client.hset(self._state_key(task_id), mapping={"current_step": step_id, "step_name": name, "updated_at": _now()})
        self._emit(task_id, "step_started", step_id=step_id, name=name)

    def heartbeat(self, task_id: str, step_id: str, elapsed_seconds: int) -> None:
        self.client.hset(self._state_key(task_id), mapping={"updated_at": _now(), "heartbeat_at": _now()})
        self._emit(task_id, "heartbeat", step_id=step_id, elapsed_seconds=elapsed_seconds)

    def step_completed(
        self, task_id: str, step_id: str, summary: str, verification: str = "",
        *, preserve_model_buffer: bool = False,
    ) -> None:
        self.client.hset(self._state_key(task_id), mapping={"updated_at": _now(), "last_completed_step": step_id})
        self._emit(task_id, "step_completed", step_id=step_id, summary=summary, verification=verification)
        if not preserve_model_buffer:
            self.clear_model_buffer(task_id, step_id)

    def step_failed(self, task_id: str, step_id: str, error: str) -> None:
        self.client.hset(self._state_key(task_id), mapping={"status": "failed", "updated_at": _now()})
        self._emit(task_id, "step_failed", step_id=step_id, error=error)

    def step_cancelled(self, task_id: str, step_id: str, reason: str) -> None:
        self.client.hset(self._state_key(task_id), mapping={"status": "cancelled", "current_step": "", "updated_at": _now()})
        self._emit(task_id, "step_cancelled", step_id=step_id, reason=reason)

    def finish(self, task_id: str, summary: str) -> None:
        self.client.hset(self._state_key(task_id), mapping={"status": "completed", "current_step": "", "updated_at": _now()})
        self._emit(task_id, "task_completed", summary=summary)

    def state(self, task_id: str) -> dict[str, str]:
        return self.client.hgetall(self._state_key(task_id))

    def events(self, task_id: str, count: int = 100) -> list[tuple[str, dict[str, str]]]:
        return self.client.xrevrange(self._stream_key(task_id), count=count)

    def record_model_buffer(self, task_id: str, step_id: str, chunks: list[dict[str, str]]) -> str | None:
        if not chunks:
            return None
        payload = {"at": _now(), "chunks": json.dumps(chunks, ensure_ascii=False)}
        return self.client.xadd(self._model_buffer_key(task_id, step_id), payload, maxlen=10000, approximate=True)

    def model_buffer(self, task_id: str, step_id: str) -> list[tuple[str, dict[str, str]]]:
        return self.client.xrange(self._model_buffer_key(task_id, step_id), min="-", max="+")

    def clear_model_buffer(self, task_id: str, step_id: str) -> int:
        return int(self.client.delete(self._model_buffer_key(task_id, step_id)))

    @staticmethod
    def _bounded_evidence_value(value, depth: int = 0):
        if depth >= 4:
            return "[nested evidence omitted]"
        if isinstance(value, str):
            return value if len(value) <= 24000 else value[:24000] + "...[truncated]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, list):
            return [RedisTaskLog._bounded_evidence_value(v, depth + 1) for v in value[:100]]
        if isinstance(value, dict):
            return {str(k): RedisTaskLog._bounded_evidence_value(v, depth + 1) for k, v in list(value.items())[:100]}
        return str(value)[:24000]

    def record_evidence(self, task_id: str, step_id: str, evidence: list[dict]) -> None:
        compact = []
        arg_keys = ("path", "paths", "expected_sha256", "profile")
        result_keys = (
            "ok", "path", "paths", "sha256", "file_mutation", "created", "error", "staged", "staged_path", "note_path",
            "attempts", "answer", "content", "text", "summary", "analysis", "observations", "data", "items",
            "image_count", "output_dir", "analysis_json", "geometry_overlay", "horizontal_consensus", "profile",
            "device", "width", "height", "reconstruction_similarity", "artifact_fraction", "resource_status", "storage_context"
        )
        for item in evidence:
            if not isinstance(item, dict):
                continue
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            compact.append({
                "tool": item.get("tool"),
                "arguments": {k: self._bounded_evidence_value(arguments[k]) for k in arg_keys if k in arguments},
                "result": {k: self._bounded_evidence_value(result[k]) for k in result_keys if k in result},
                "deterministic_verification": bool(item.get("deterministic_verification")),
            })
        if compact:
            self._emit(task_id, "step_evidence", step_id=step_id, evidence=compact)

    def task_evidence(self, task_id: str) -> list[dict]:
        collected: list[dict] = []
        for _, fields in self.client.xrange(self._stream_key(task_id), min="-", max="+"):
            if fields.get("event") != "step_evidence":
                continue
            try:
                items = json.loads(fields.get("evidence", "[]"))
            except json.JSONDecodeError:
                continue
            if isinstance(items, list):
                collected.extend(item for item in items if isinstance(item, dict))
        return collected

    def working_memory(self, task_id: str) -> str:
        state = self.state(task_id)
        events = []
        for stream_id, fields in self.client.xrange(self._stream_key(task_id), min="-", max="+"):
            if fields.get("event") == "heartbeat":
                continue
            event = {"redis_id": stream_id, **fields}
            for key in ("plan", "evidence"):
                value = event.get(key)
                if isinstance(value, str):
                    try:
                        event[key] = json.loads(value)
                    except json.JSONDecodeError:
                        pass
            events.append(event)
        return json.dumps({"task_id": task_id, "state": state, "events": events}, ensure_ascii=False)

    def task_ids(self) -> set[str]:
        ids: set[str] = set()
        prefix = f"{self.prefix}:"
        suffix = ":state"
        for key in self.client.scan_iter(match=f"{self.prefix}:*:state"):
            text = key.decode() if isinstance(key, bytes) else str(key)
            if text.startswith(prefix) and text.endswith(suffix):
                task_id = text[len(prefix):-len(suffix)]
                if task_id:
                    ids.add(task_id)
        return ids

    @staticmethod
    def _normalize_validation_target(target: object) -> str:
        text = " ".join(str(target or "").strip().split())
        if not text:
            return ""
        # Windows paths are Norm's common case.  Normalize slash/case differences so
        # D:\A\B and d:/a/b are one target rather than two Redis facts.
        if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\"):
            return ntpath.normpath(text).casefold()
        return text.casefold()

    @staticmethod
    def _normalize_validation_description(description: object) -> str:
        text = " ".join(str(description or "").strip().casefold().split())
        return " ".join(re.findall(r"[a-z0-9_.:+\\/-]+", text))

    @classmethod
    def _validation_description_similarity(cls, left: object, right: object) -> float:
        a = cls._normalize_validation_description(left)
        b = cls._normalize_validation_description(right)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        seq = difflib.SequenceMatcher(None, a, b).ratio()
        sa, sb = set(a.split()), set(b.split())
        jac = len(sa & sb) / max(1, len(sa | sb))
        return max(seq, jac)

    @classmethod
    def _validation_record_id(cls, target: object, description: object) -> str:
        basis = cls._normalize_validation_target(target) + "\n" + cls._normalize_validation_description(description)
        return hashlib.sha256(basis.encode("utf-8", errors="replace")).hexdigest()[:32]

    @staticmethod
    def _public_validation_record(record: dict, record_id: str = "") -> dict:
        row = {
            "record_id": str(record_id or record.get("record_id") or ""),
            "tool": str(record.get("tool") or ""),
            "target": str(record.get("target") or ""),
            "description": str(record.get("description") or ""),
            "value": str(record.get("value") or ""),
            "num_checks": int(record.get("num_checks") or 0),
            "previous_value": str(record.get("previous_value") or ""),
            "changed_at": str(record.get("changed_at") or ""),
            "storage": "redis_live",
        }
        return row

    def global_validation(self, record_id: str, **_kwargs) -> dict | None:
        record_id = str(record_id or "").strip()
        if not record_id:
            return None
        raw = self.client.hget(self._global_validation_key(), record_id)
        if not raw:
            return None
        try:
            record = json.loads(raw)
        except Exception:
            return None
        if not isinstance(record, dict):
            return None
        return self._public_validation_record(record, record_id)

    def validation_candidates(self, *, tool: str = "", target: str, description: str, limit: int = 12) -> list[dict]:
        """Return likely Redis records for one stable target without creating anything.

        The hash field is not chosen from model prose.  We first narrow by normalized
        target, then rank existing descriptions.  This is what keeps small wording
        changes from manufacturing new Redis keys/records.
        """
        target_key = self._normalize_validation_target(target)
        if not target_key:
            return []
        tool_key = str(tool or "").strip().casefold()
        rows: list[dict] = []
        for record_id, raw in self.client.hscan_iter(self._global_validation_key()):
            try:
                record = json.loads(raw)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            if self._normalize_validation_target(record.get("target")) != target_key:
                continue
            score = self._validation_description_similarity(description, record.get("description"))
            if tool_key and str(record.get("tool") or "").strip().casefold() == tool_key:
                score = min(1.0, score + 0.03)
            row = self._public_validation_record(record, str(record_id))
            row["description_match"] = round(score, 4)
            rows.append(row)
        rows.sort(key=lambda item: (-float(item.get("description_match") or 0.0), str(item.get("description") or "")))
        return rows[:max(1, int(limit))]

    def invalidate_validation_target(self, target: str) -> int:
        """Drop live Redis verification records for a target after a known mutation.

        Mutation invalidation is deliberately target-based so N1 cannot hand N2 a
        pre-mutation observation simply because the wording/tool request matches.
        """
        target_key = self._normalize_validation_target(target)
        if not target_key:
            return 0
        doomed: list[str] = []
        for record_id, raw in self.client.hscan_iter(self._global_validation_key()):
            try:
                record = json.loads(raw)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            if self._normalize_validation_target(record.get("target")) == target_key:
                doomed.append(str(record_id))
        if not doomed:
            return 0
        return int(self.client.hdel(self._global_validation_key(), *doomed) or 0)

    def recent_global_validations(self, *, limit: int = 64, **_kwargs) -> list[dict]:
        rows: list[tuple[str, dict]] = []
        for record_id, raw in self.client.hscan_iter(self._global_validation_key()):
            try:
                record = json.loads(raw)
            except Exception:
                continue
            if isinstance(record, dict):
                rows.append((str(record_id), record))
        # Internal pool-start time is only migration bookkeeping; it is not part of
        # the model-facing verification record.
        rows.sort(key=lambda pair: str(pair[1].get("_pool_started_at") or ""), reverse=True)
        return [self._public_validation_record(record, record_id) for record_id, record in rows[:max(1, int(limit))]]

    def record_global_validations(self, task_id: str, step_id: str, items: list[dict], **_kwargs) -> list[dict]:
        """Check information-tool results into the one shared Redis validation hash."""
        saved: list[dict] = []
        pool_key = self._global_validation_key()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or item.get("source") or "unspecified").strip()[:500] or "unspecified"
            target = " ".join(str(item.get("target") or "").strip().split())[:2000]
            description = " ".join(str(item.get("description") or "").strip().split())[:2000]
            value = _canonical_validation_value(item.get("value"))[:12000]
            requested_id = str(item.get("record_id") or "").strip()
            if not target or not description or not value:
                continue
            record_id = requested_id if requested_id and requested_id != "new" else self._validation_record_id(target, description)
            for attempt in range(32):
                try:
                    with self.client.pipeline(transaction=True) as pipe:
                        pipe.watch(pool_key)
                        now_iso = datetime.now(timezone.utc).isoformat()
                        raw = pipe.hget(pool_key, record_id)
                        prior: dict = {}
                        if raw:
                            try:
                                loaded = json.loads(raw)
                                prior = loaded if isinstance(loaded, dict) else {}
                            except Exception:
                                prior = {}
                        same_value = bool(prior) and str(prior.get("value") or "") == value
                        changed = bool(prior) and not same_value
                        if same_value:
                            num_checks = int(prior.get("num_checks") or 0) + 1
                            previous_value = str(prior.get("previous_value") or "")
                            changed_at = str(prior.get("changed_at") or "")
                            pool_started_at = str(prior.get("_pool_started_at") or now_iso)
                            canonical_description = str(prior.get("description") or description)
                            canonical_target = str(prior.get("target") or target)
                        elif changed:
                            num_checks = 1
                            previous_value = str(prior.get("value") or "")
                            changed_at = now_iso
                            # A verified value change starts a fresh hot Redis epoch.
                            pool_started_at = now_iso
                            canonical_description = str(prior.get("description") or description)
                            canonical_target = str(prior.get("target") or target)
                        else:
                            num_checks = 1
                            previous_hint = str(item.get("previous_value_hint") or "")
                            if previous_hint and previous_hint != value:
                                previous_value = previous_hint
                                changed_at = str(item.get("changed_at_hint") or now_iso)
                            else:
                                previous_value = ""
                                changed_at = ""
                            pool_started_at = now_iso
                            canonical_description = description
                            canonical_target = target
                        record = {
                            "record_id": record_id,
                            "tool": tool,
                            "target": canonical_target,
                            "description": canonical_description,
                            "value": value,
                            "num_checks": num_checks,
                            "previous_value": previous_value,
                            "changed_at": changed_at,
                            "_pool_started_at": pool_started_at,
                            "_last_verified_at": now_iso,
                        }
                        pipe.multi()
                        pipe.hset(pool_key, record_id, json.dumps(record, ensure_ascii=False, sort_keys=True))
                        pipe.execute()
                        public = self._public_validation_record(record, record_id)
                        public["value_changed"] = changed
                        saved.append(public)
                    break
                except redis.WatchError:
                    if attempt == 31:
                        raise
        return saved

    def stale_validation_records(self, *, older_than_seconds: int = 86400, limit: int = 512) -> list[dict]:
        """Return complete Redis records whose hot epoch began before the eligibility cutoff."""
        cutoff = datetime.now(timezone.utc).timestamp() - max(1, int(older_than_seconds))
        rows: list[dict] = []
        for record_id, raw in self.client.hscan_iter(self._global_validation_key()):
            try:
                record = json.loads(raw)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            started_raw = str(record.get("_pool_started_at") or "")
            try:
                started = datetime.fromisoformat(started_raw)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                started_ts = started.timestamp()
            except Exception:
                continue
            if started_ts > cutoff:
                continue
            row = dict(record)
            row["record_id"] = str(record_id)
            rows.append(row)
            if len(rows) >= max(1, int(limit)):
                break
        return rows

    def acknowledge_validation_records(self, record_ids: list[str]) -> int:
        cleaned = [str(item).strip() for item in (record_ids or []) if str(item).strip()]
        if not cleaned:
            return 0
        return int(self.client.hdel(self._global_validation_key(), *cleaned) or 0)

    def migrate_legacy_validation_layout(self) -> dict:
        """Collapse pre-0.53.13 exploded validation keys into norm:validation:pool.

        Legacy observation zsets are deliberately not copied one-by-one: the legacy
        fact already contains the current generation count.  Copying both would double
        count the same checks.  Once each legacy fact is represented in the pool, the
        obsolete facts/recent/observations/pending keys are removed.
        """
        legacy_facts = self._legacy_validation_facts_key()
        raw_facts = self.client.hgetall(legacy_facts)
        if not raw_facts:
            obsolete = list(self.client.scan_iter(match=f"{self.validation_prefix}:observations:*"))
            obsolete += [
                self._legacy_validation_recent_index_key(),
                self._legacy_validation_pending_change_key(),
                f"{self.validation_prefix}:pending-generations",
            ]
            obsolete += list(self.client.scan_iter(match=f"{self.prefix}:*:validations"))
            existing = [key for key in obsolete if self.client.exists(key)]
            if existing:
                self.client.delete(*existing)
            return {"migrated": 0, "removed_legacy_keys": len(existing)}

        pending_by_subject: dict[str, dict] = {}
        for _pid, raw in self.client.hscan_iter(self._legacy_validation_pending_change_key()):
            try:
                change = json.loads(raw)
            except Exception:
                continue
            if isinstance(change, dict) and str(change.get("subject") or ""):
                pending_by_subject[str(change.get("subject"))] = change

        pool_key = self._global_validation_key()
        migrated = 0
        for subject, raw in raw_facts.items():
            try:
                old = json.loads(raw)
            except Exception:
                continue
            if not isinstance(old, dict):
                continue
            identity = dict(old.get("identity") or {})
            target = str(identity.get("where") or subject or "").strip()
            description = str(identity.get("what") or subject or "").strip()
            tool = str(old.get("last_source") or identity.get("how") or "legacy").strip() or "legacy"
            value = str(old.get("value") or "").strip()
            if not target or not description or not value:
                continue
            record_id = self._validation_record_id(target, description)
            if self.client.hexists(pool_key, record_id):
                continue
            pending = pending_by_subject.get(str(subject), {})
            previous_value = str(pending.get("old_value") or "") if str(pending.get("new_value") or "") == value else ""
            changed_at = str(pending.get("changed_at") or "") if previous_value else ""
            started_at = str(old.get("generation_started_at") or old.get("last_checked_at") or _now())
            last_verified_at = str(old.get("last_checked_at") or started_at)
            record = {
                "record_id": record_id,
                "tool": tool,
                "target": target,
                "description": description,
                "value": value,
                "num_checks": max(1, int(old.get("generation_checks") or 1)),
                "previous_value": previous_value,
                "changed_at": changed_at,
                "_pool_started_at": started_at,
                "_last_verified_at": last_verified_at,
            }
            self.client.hset(pool_key, record_id, json.dumps(record, ensure_ascii=False, sort_keys=True))
            migrated += 1

        obsolete = [legacy_facts, self._legacy_validation_recent_index_key(), self._legacy_validation_pending_change_key(), f"{self.validation_prefix}:pending-generations"]
        obsolete.extend(self.client.scan_iter(match=f"{self.validation_prefix}:observations:*"))
        obsolete.extend(self.client.scan_iter(match=f"{self.prefix}:*:validations"))
        existing = [key for key in obsolete if self.client.exists(key)]
        if existing:
            self.client.delete(*existing)
        return {"migrated": migrated, "removed_legacy_keys": len(existing)}

    def cleanup(self, task_id: str) -> None:
        keys = [self._state_key(task_id), self._stream_key(task_id), f"{self.prefix}:{task_id}:validations"]
        keys.extend(self.client.scan_iter(match=f"{self.prefix}:{task_id}:model-buffer:*"))
        if keys:
            self.client.delete(*keys)
