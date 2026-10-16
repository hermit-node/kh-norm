"""Same-step Celery receipts and N1-adaptive checks, separate from sync tools."""
from __future__ import annotations

import hashlib
import json
import logging
import time

from norm_runtime.n1_gatekeeper import GateOutcome
from norm_runtime.secret_redaction import redact

# Checks 30s, 60s, then every three minutes; N1 estimates from minute 15.
EARLY_SECONDS = (30, 60, 240, 420, 600, 780, 900)
N1_REVIEW_SECONDS = 900
HARD_DEADLINE_SECONDS = 3600
RECEIPT_TTL = 7200


class N1AsyncBridge:
    def __init__(self, owner, job, tools):
        self.owner, self.job, self.tools = owner, job, tools
        self.store = owner.queue.r
        self.key = f"norm:async-link:{job.task_id}:{job.step_id}"
        self.pending = []
        self.broker = None
        self.enabled = False
        # Pending receipts must remain accessible even when Celery workers are
        # temporarily offline. Never dispatch the same operation synchronously
        # just because a heartbeat was momentarily unavailable.
        try:
            saved = self.store.get(self.key)
            if saved:
                rows = json.loads(saved)
                self.pending = rows if isinstance(rows, list) else []
        except Exception:
            logging.exception("Could not restore outstanding Celery receipts")
        try:
            from norm_runtime.celery_tool_executor import Broker, CFG
            self.broker = Broker()
            self.enabled = bool(CFG["enabled"] and self.broker.available())
        except Exception:
            logging.exception("Celery adapter unavailable; unclaimed tools remain synchronous")
        changed = False
        for item in self.pending:
            if "submitted_at" not in item:
                # Old versions had no durable send time; begin a new observation
                # period rather than pretending an old operation has timed out.
                item["submitted_at"] = time.time()
                changed = True
            if "next_check_at" not in item:
                item["next_check_at"] = float(item["submitted_at"]) + 30
                changed = True
        if changed:
            self.persist()

    def persist(self):
        if self.pending:
            self.store.set(self.key, json.dumps(self.pending, ensure_ascii=False), ex=RECEIPT_TTL)
        else:
            self.store.delete(self.key)

    @staticmethod
    def _signature(name, arguments):
        return json.dumps({"name": name, "args": arguments}, sort_keys=True, default=str)

    def _unresolved_key(self, signature):
        digest = hashlib.sha256(signature.encode("utf8")).hexdigest()
        return f"norm:async-unresolved:{self.job.task_id}:{digest}"

    def receipt(self, name, arguments, outcome):
        # Celery is for N1-approved informational tools only; mutations remain
        # on the original synchronous N1-gated execution path.
        if name == "read_file" or not self.owner.n1_gatekeeper.information_tool(name):
            return None
        signature = self._signature(name, arguments)
        for item in self.pending:
            if item["signature"] == signature:
                return {"ok": True, "tool": name, "async_pending": True,
                        "status": "pending", "dispatch_id": item["id"], "deduplicated": True}
        unresolved = self.store.get(self._unresolved_key(signature))
        if unresolved:
            return {"ok": False, "tool": name, "status": "unresolved",
                    "error": "Prior identical dispatch is unresolved after N1 stopped waiting; do not execute it twice.",
                    "async_dispatch_id": str(unresolved.decode() if isinstance(unresolved, bytes) else unresolved)}
        if not self.enabled:
            # Only new, unclaimed calls may fall back to synchronous handling.
            return None
        first = (outcome.candidates or [None])[0]
        candidate = [{k: first.get(k) for k in ("record_id", "scope", "description_match")}] if isinstance(first, dict) else []
        jobid = self.broker.submit(self.job.task_id, self.job.step_id, name, arguments)
        now = time.time()
        self.pending.append({
            "id": jobid, "signature": signature, "name": name,
            "arguments": arguments, "outcome": {
                "execute": True, "arguments": arguments, "target": outcome.target,
                "need": outcome.need, "scope": outcome.scope,
                "streamed_read": outcome.streamed_read,
                "signature": outcome.signature, "candidates": candidate,
            },
            "submitted_at": now, "next_check_at": now + EARLY_SECONDS[0],
            "poll_count": 0, "n1_estimates": 0, "status": "pending",
        })
        self.persist()
        return {"ok": True, "tool": name, "status": "pending",
                "async_pending": True, "dispatch_id": jobid}

    def next_due(self):
        return min((float(item.get("next_check_at") or time.time())
                    for item in self.pending), default=0)

    @staticmethod
    def _next_early(submitted_at, now):
        for seconds in EARLY_SECONDS:
            if submitted_at + seconds > now + 0.001:
                return submitted_at + seconds
        return submitted_at + N1_REVIEW_SECONDS

    def _estimate_next(self, item, now):
        submitted_at = float(item["submitted_at"])
        elapsed = max(0.0, now - submitted_at)
        remaining = max(0.0, HARD_DEADLINE_SECONDS - elapsed)
        try:
            status = self.broker.inspect(item["id"]) if hasattr(self.broker, "inspect") else {"state": "UNKNOWN"}
            answer = self.owner.n1_gatekeeper.estimate_async_next_check(
                task_id=self.job.task_id, step_id=self.job.step_id,
                tool=item["name"], arguments=item["arguments"],
                dispatch_id=item["id"], elapsed_seconds=int(elapsed),
                remaining_seconds=int(remaining), worker_status=status,
                prior_estimates=int(item.get("n1_estimates") or 0),
            )
            delay = float(answer["check_after_seconds"])
            if not (30 <= delay < float("inf")):
                raise ValueError("N1 proposed an invalid follow-up interval")
            item["n1_estimates"] = int(item.get("n1_estimates") or 0) + 1
            item["last_estimate_reason"] = str(answer.get("reason") or "")[:220]
            if answer.get("decision") == "stop":
                item["n1_stop_reason"] = item["last_estimate_reason"]
                return now
        except Exception:
            logging.exception("N1 async estimate failed dispatch=%s; retry review in three minutes", item["id"])
            delay = 180
            item["estimate_errors"] = int(item.get("estimate_errors") or 0) + 1
        return min(submitted_at + HARD_DEADLINE_SECONDS, now + min(delay, remaining))

    def _schedule_next(self, item, now):
        submitted_at = float(item["submitted_at"])
        elapsed = now - submitted_at
        if elapsed >= N1_REVIEW_SECONDS:
            item["next_check_at"] = self._estimate_next(item, now)
        else:
            item["next_check_at"] = self._next_early(submitted_at, now)
        item["status"] = "pending"

    def _complete(self, item, result, messages, evidence, *, unresolved=False):
        # Never deliver directly from Celery to N2: N1 processes the actual
        # returned value first, even when the tool itself reports ok=False.
        outcome = GateOutcome(**item["outcome"])
        meta = self.owner.n1_gatekeeper.after_tool(
            task_id=self.job.task_id, step_id=self.job.step_id,
            name=item["name"], outcome=outcome, result=result)
        observed = redact({"tool": item["name"], "arguments": item["arguments"],
                           "result": result, "n1_gatekeeper": meta,
                           "async_dispatch_id": item["id"]})
        self.owner._persist_evidence_now(self.job, [observed])
        evidence.append(observed)
        messages.append({"role": "system", "content":
            "[N1 ASYNC TOOL RESULT; NOT USER-AUTHORED]\\n" +
            json.dumps({"tool": item["name"], "dispatch_id": item["id"],
                        "result": redact(result)}, ensure_ascii=False, default=str) +
            "\\nN1 processed this observed result. Do not repeat completed work."})
        if unresolved:
            self.store.set(self._unresolved_key(item["signature"]),
                           item["id"], ex=7 * 86400)
        self.pending.remove(item)
        self.persist()

    def deliver(self, messages, evidence, *, now=None):
        if not self.pending:
            return 0
        now = float(now if now is not None else time.time())
        completed = 0
        modified = False
        for item in list(self.pending):
            if now < float(item.get("next_check_at") or 0):
                continue
            item["poll_count"] = int(item.get("poll_count") or 0) + 1
            submitted_at = float(item["submitted_at"])
            deadline = submitted_at + HARD_DEADLINE_SECONDS
            try:
                result = self.broker.poll(item["id"]) if self.broker is not None else None
            except Exception:
                logging.exception("Celery result lookup failed dispatch=%s", item["id"])
                result = None
            if result is not None:
                self._complete(item, result, messages, evidence)
                completed += 1
                continue
            if now >= deadline:
                # Distinguish queued / orphaned / executing from the same
                # generic 60m timeout. Nothing here authorizes another execute.
                try:
                    diagnostic = self.broker.inspect(item["id"]) if self.broker else {}
                except Exception:
                    diagnostic = {}
                logging.warning("Celery observation deadline dispatch=%s task=%s tool=%s diagnostic=%s",
                                item["id"], self.job.task_id, item["name"],
                                {k: diagnostic.get(k) for k in ("state", "execution", "worker_alive", "worker_heartbeats", "setup_seconds", "elapsed_seconds")})
                timeout = {"ok": False, "tool": item["name"],
                           "status": "timed_out", "async_unresolved": True,
                           "dispatch_id": item["id"], "n1_decision": "hard_deadline",
                           "celery_state": diagnostic.get("state", "unknown"),
                           "execution_phase": diagnostic.get("execution", "unknown"),
                           "error": "No confirmed Celery result within 60 minutes; execution outcome unknown. "
                                    "See execution_phase and celery_state; no automatic redispatch."}
                self._complete(item, timeout, messages, evidence, unresolved=True)
                completed += 1
                continue
            self._schedule_next(item, now)
            if item.get("n1_stop_reason"):
                logging.warning("N1 stopped Celery wait dispatch=%s task=%s reason=%s",
                                item["id"], self.job.task_id, item["n1_stop_reason"])
                self._complete(item, {"ok": False, "tool": item["name"],
                    "status": "timed_out", "async_unresolved": True,
                    "dispatch_id": item["id"], "n1_decision": "stop_waiting",
                    "elapsed_seconds": round(now - submitted_at),
                    "error": "N1 stopped waiting before confirmed result; execution outcome unknown. "
                             + item["n1_stop_reason"]}, messages, evidence, unresolved=True)
                completed += 1
                continue
            modified = True
        if modified:
            self.persist()
        return completed

    def inject_receipts(self, messages):
        if self.pending:
            messages.append({"role": "system", "content":
                "[N1 PENDING TOOL WORK]\\n" +
                json.dumps([{"tool": x["name"], "dispatch_id": x["id"],
                             "next_check_at": x.get("next_check_at")} for x in self.pending]) +
                "\\nThese have already been dispatched. Never repeat them; wait for N1 to return observations."})
