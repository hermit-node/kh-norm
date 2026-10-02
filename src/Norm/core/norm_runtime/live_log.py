from __future__ import annotations

import json
from .secret_redaction import redact
from datetime import datetime, timezone
from typing import Any

import redis


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RedisTaskLog:
    def __init__(self, client: redis.Redis, prefix: str = "norm:task") -> None:
        self.client = client
        self.prefix = prefix.rstrip(":")

    @classmethod
    def localhost(cls, db: int = 0) -> "RedisTaskLog":
        client = redis.Redis(host="127.0.0.1", port=6379, db=db, decode_responses=True)
        client.ping()
        return cls(client)

    def _state_key(self, task_id: str) -> str:
        return f"{self.prefix}:{task_id}:state"

    def _stream_key(self, task_id: str) -> str:
        return f"{self.prefix}:{task_id}:events"

    def _validation_key(self, task_id: str) -> str:
        return f"{self.prefix}:{task_id}:validations"

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
            "ok", "path", "paths", "sha256", "created", "error", "staged", "staged_path", "note_path",
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

    def record_validations(self, task_id: str, step_id: str, items: list[dict]) -> list[dict]:
        saved=[]
        key=self._validation_key(task_id)
        now=_now()
        for item in items or []:
            if not isinstance(item,dict): continue
            subject=str(item.get("subject") or "").strip()[:500]
            value=str(item.get("value") or "").strip()[:4000]
            source=str(item.get("source") or "unspecified").strip()[:500] or "unspecified"
            note=str(item.get("note") or "").strip()[:2000]
            if not subject or not value: continue
            raw=self.client.hget(key,subject); old={}
            if raw:
                try: old=json.loads(raw)
                except Exception: old={}
            checks=int(old.get("check_count") or 0)+1
            contradictions=int(old.get("contradiction_count") or 0)+(1 if old.get("value") not in (None,value) else 0)
            sources=dict(old.get("source_counts") or {}); sources[source]=int(sources.get(source) or 0)+1
            values=dict(old.get("value_counts") or {}); values[value]=int(values.get(value) or 0)+1
            record={"subject":subject,"value":value,"check_count":checks,"contradiction_count":contradictions,"source_counts":sources,"value_counts":values,"first_checked_at":old.get("first_checked_at") or now,"last_checked_at":now,"last_step_id":step_id,"note":note}
            self.client.hset(key,subject,json.dumps(record,ensure_ascii=False,sort_keys=True)); saved.append(record)
        return saved

    def recent_validations(self, task_id: str, *, subjects: list[str] | None = None, limit: int = 20) -> list[dict]:
        raw=self.client.hgetall(self._validation_key(task_id)); wanted={str(x).strip() for x in (subjects or []) if str(x).strip()}
        rows=[]
        for subject,payload in raw.items():
            if wanted and subject not in wanted: continue
            try: record=json.loads(payload)
            except Exception: continue
            if isinstance(record,dict): rows.append(record)
        rows.sort(key=lambda x:str(x.get("last_checked_at") or ""),reverse=True)
        return rows[:max(1,int(limit))]

    def cleanup(self, task_id: str) -> None:
        keys = [self._state_key(task_id), self._stream_key(task_id), self._validation_key(task_id)]
        keys.extend(self.client.scan_iter(match=f"{self.prefix}:{task_id}:model-buffer:*"))
        if keys:
            self.client.delete(*keys)
