"""Redis-checkpointed final-stage verification and paragraph composition.

Only invoked for the final generated conclusion step of a multi-step task.
Never executes project mutation tools. Normal task planning/execution is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import dataclass

from .models import StepResult, StepStatus, TaskPlan, TaskStep
from .terminal_celery import redis_client, submit_verify, submit_paragraph
from .ollama_client import OllamaClient
from .prompt_queue import PromptJob
from .protocol import PROTOCOL_VERSION


@dataclass
class TerminalOutcome:
    status: str                # completed | passed_to | unresolved
    answer: str
    report: dict
    successor_id: str = ""


class TerminalConclusion:
    """Finite, restartable terminal work. Redis is the report card, not a reasoning agent."""

    def __init__(self, worker):
        self.w = worker
        self.redis = redis_client()
        self.config = (worker._runtime_config().get("terminal_conclusion") or {})
        self.deadline_seconds = max(60, int(self.config.get("deadline_seconds", 3600)))
        self.concurrency = max(1, min(2, int(self.config.get("verify_workers", 2))))
        self.max_checks = max(1, min(32, int(self.config.get("max_checks", 24))))
        self.ttl = max(86400, int(self.config.get("report_ttl_seconds", 30 * 86400)))

    def key(self, task_id, category):
        return f"norm:terminal:v2:{task_id}:{category}"

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _decode(value):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None

    def _save(self, task_id, bucket, field, value):
        key = self.key(task_id, bucket)
        self.redis.hset(key, str(field), self._json(value))
        self.redis.expire(key, self.ttl)

    def _load(self, task_id, bucket, field):
        value = self.redis.hget(self.key(task_id, bucket), str(field))
        return self._decode(value)

    def _items(self, task_id, bucket):
        return {(k.decode("utf-8") if isinstance(k,bytes) else str(k)):
                self._decode(v) for k,v in self.redis.hgetall(self.key(task_id,bucket)).items()}

    def _check_guard(self, task_id):
        stop = getattr(getattr(self.w,"_stop_all_now",None),"is_set",lambda:False)()
        suppressed = getattr(self.w,"_suppression_requested",lambda _:False)(task_id)
        if not suppressed and hasattr(self.w.durable,"task_status"):
            suppressed = self.w.durable.task_status(task_id) == "suppressed"
        if suppressed or stop:
            from .ollama_client import ModelGenerationCancelled
            raise ModelGenerationCancelled("Terminal conclusion suppressed or stopped")

    def _n1_json(self, prompt, schema, *, tokens=650):
        gate = getattr(self.w, "n1_gatekeeper", None)
        if gate is None or not getattr(gate, "enabled", True):
            raise RuntimeError("N1 is unavailable for terminal planning")
        raw = gate._review_generate(
            prompt, think=False, temperature=0.0, num_predict=tokens,
            response_format=schema, emit_stream=False, timeout_seconds=75.0,
        )
        return OllamaClient.parse_json(raw)

    def _ensure_workers(self):
        """Auto-start missing independent Celery workers at terminal entry only.

        This never invokes tools, changes project source or restarts norm.exe.
        """
        if not self.config.get("autostart_workers",True):
            return
        root = self.w._runtime_root()
        starter=root/"tools"/"start_terminal_workers.py"
        py=root/".venv"/"Scripts"/"python.exe"
        if not starter.is_file() or not py.is_file():
            raise RuntimeError("Missing Celery worker startup script or Norm Python environment")
        count=max(1,min(5,int(self.config.get("workers",5))))
        result=subprocess.run(
            [str(py),str(starter),"--count",str(count)],
            cwd=str(root),capture_output=True,text=True,
            timeout=25,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
        )
        if result.returncode:
            raise RuntimeError("Celery workers could not be started: "
                + (result.stderr or result.stdout or "unknown error")[-750:])

    def _plan_checks(self, task_id, records):
        existing = self._items(task_id, "checks")
        if self._load(task_id, "meta", "checks_planned") and existing:
            return existing
        checks = {}
        for record in records:
            if record.get("id") in {"plan-breakdown", "verify-plan", "final-verify"}:
                continue
            if str(record.get("id") or "").startswith("plan-"):
                continue
            step_id = str(record["id"])
            if len(checks) >= self.max_checks:
                # A capped plan cannot silently drop later requirements.
                cid = step_id + ":budget"
                checks[cid] = {"id":cid,"step_id":step_id,
                    "target":"Verification budget exhausted before this step",
                    "verdict":"UNKNOWN",
                    "evidence":"Not checked; max_checks limit was reached"}
                continue
            if record.get("status") != "completed":
                checks[step_id + ":0"] = {
                    "id": step_id + ":0", "step_id": step_id,
                    "target": "Original execution step has a completed durable checkpoint",
                    "verdict": "FALSE", "evidence": "Persisted status is " + str(record.get("status")),
                }
                continue
            schema = {"type":"object","properties":{
                "checks":{"type":"array","minItems":1,"maxItems":3,"items":{
                    "type":"object","properties":{
                        "target":{"type":"string"},
                        "method":{"type":"string","enum":[
                            "file_exists","directory_exists","sha256_matches",
                            "text_contains","model_review"]},
                        "args":{"type":"object"},
                    },"required":["target","method","args"],
                    "additionalProperties":False}}
                },"required":["checks"],"additionalProperties":False}
            try:
                response = self._n1_json(
                    "You are N1 preparing READ-ONLY checks for ONE completed execution step. "
                    "Choose 1-3 atomic checks based on prior evidence. Prefer deterministic "
                    "file_exists (args: path), directory_exists (args: path), "
                    "sha256_matches (args: path, sha256), or text_contains "
                    "(args: path, text) whenever the EXACT path and comparison are grounded "
                    "in recorded evidence, favoring the requested DESTINATION path "
                    "over a source/temporary path. Use model_review (args: {}) only for meaning "
                    "or semantic correctness; NEVER ask a language model to check whether "
                    "a file exists. Do not guess paths. No mutation, no new execution steps. JSON only.\n"
                    + self._json({"step_id":step_id,"name":record.get("name"),
                         "instruction":str(record.get("description") or "")[:1800],
                         "requirement":str(record.get("verify") or "")[:850],
                         "observed_summary":str(record.get("summary") or "")[:1000],
                         "observed_tool_evidence":record.get("evidence",[])[:5]}),
                    schema, tokens=450,
                )
                planned = response.get("checks",[])
                methods = {"file_exists","directory_exists","sha256_matches",
                           "text_contains","model_review"}
                checks_for_step = [
                    {"target":str(v.get("target") or "")[:320],
                     "method":str(v.get("method") or "model_review"),
                     "args":v.get("args") if isinstance(v.get("args"),dict) else {}}
                    for v in planned[:3] if isinstance(v,dict)
                    and str(v.get("target") or "").strip()
                    and str(v.get("method") or "") in methods
                ]
                if not checks_for_step:
                    raise ValueError("N1 produced no usable atomic checks")
            except Exception as exc:
                logging.warning("Terminal N1 decomposition unavailable task=%s step=%s: %s", task_id, step_id, exc)
                checks_for_step = [{"target":str(record.get("verify") or record.get("name") or step_id)[:320],
                    "method":"model_review","args":{}}]
            for index, check in enumerate(checks_for_step):
                cid = f"{step_id}:{index + 1}"
                item = {"id":cid,"step_id":step_id,**check,
                    "verdict":"PENDING","evidence":""}
                if len(checks) >= self.max_checks:
                    item.update(verdict="UNKNOWN",evidence="Verification budget exceeded")
                checks[cid] = item
        if not checks:
            raise RuntimeError("No earlier execution steps available for terminal verification")
        # Atomic report-card creation: a crash cannot leave a partial plan
        # that appears complete on the next invocation.
        with self.redis.pipeline(transaction=True) as pipe:
            key = self.key(task_id, "checks")
            pipe.delete(key)
            pipe.hset(key, mapping={cid:self._json(entry) for cid,entry in checks.items()})
            pipe.expire(key,self.ttl)
            pipe.hset(self.key(task_id, "meta"),"checks_planned",self._json(True))
            pipe.expire(self.key(task_id, "meta"),self.ttl)
            pipe.execute()
        return checks

    def _verify(self, task_id, records, checks, deadline):
        """Distribute one verification assertion per Celery task; N1 polls Redis."""
        by_step = {str(r["id"]):r for r in records}
        pending = [v for v in checks.values() if v.get("verdict")=="PENDING"]
        model_name = str(getattr(self.w.client,"model","") or "")
        for check in pending:
            cid=check["id"]
            record=by_step[check["step_id"]]
            payload={
                "method":check.get("method","model_review"),"args":check.get("args",{}),
                "assertion":check["target"],"step_id":check["step_id"],
                "step_instruction":str(record.get("description") or "")[:1500],
                "persisted_evidence":{
                    "step_status":record.get("status"),
                    "step_summary":str(record.get("summary") or "")[:4000],
                    "verification_requirement":str(record.get("verify") or "")[:1000],
                    "tool_evidence":record.get("evidence",[])[:12],
                    "evidence_truncated":bool(record.get("evidence_truncated")),
                }
            }
            # The Redis report card was persisted before task publication.
            # Re-delivery after a crash is safe: Celery workers use atomic CAS.
            submit_verify(task_id,cid,payload,deadline,model_name)
        while time.time()<deadline:
            self._check_guard(task_id)
            current=self._items(task_id,"checks")
            if not any(v.get("verdict")=="PENDING" for v in current.values()):
                break
            time.sleep(3)
        for cid,entry in self._items(task_id,"checks").items():
            if entry.get("verdict")=="PENDING":
                self._save(task_id,"checks",cid,dict(entry,
                    verdict="UNKNOWN",evidence="Deadline; no Celery worker verdict"))
        return self._items(task_id,"checks")

    def _report(self, task_id, records, checks):
        steps = []
        for step in records:
            selected = [c for c in checks.values() if c["step_id"] == step["id"]]
            if not selected: continue
            verdicts = {c["verdict"] for c in selected}
            aggregate = "FALSE" if "FALSE" in verdicts else "TRUE" if verdicts == {"TRUE"} else "UNKNOWN"
            steps.append({"id":step["id"],"name":step.get("name"),"verdict":aggregate,
                "checks":selected})
        return {"task_id":task_id,"steps":steps,
            "passed":[s["id"] for s in steps if s["verdict"]=="TRUE"],
            "failed":[s["id"] for s in steps if s["verdict"]=="FALSE"],
            "unknown":[s["id"] for s in steps if s["verdict"]=="UNKNOWN"]}

    def run(self, job):
        task_id = job.task_id
        cached = self._load(task_id, "meta", "outcome")
        if cached:
            return TerminalOutcome(**cached)
        # Explicit manual re-verification (not automatic replay) can retry UNKNOWN
        # children. Previously accepted checks and paragraphs remain immutable.
        if bool((job.metadata or {}).get("retry_terminal_verification")):
            for cid, check in self._items(task_id,"checks").items():
                if check.get("verdict")=="UNKNOWN":
                    self._save(task_id,"checks",cid,dict(check,verdict="PENDING",evidence=""))
            self._save(task_id,"meta","started_at",time.time())
        started = self._load(task_id, "meta", "started_at")
        if started is None:
            started = time.time()
            self._save(task_id, "meta", "started_at", started)
        deadline = float(started) + self.deadline_seconds
        self._check_guard(task_id)
        self._ensure_workers()
        records = self.w.durable.terminal_step_records(task_id, job.step_id)
        checks = self._plan_checks(task_id, records)
        checks = self._verify(task_id, records, checks, deadline)
        self._check_guard(task_id)
        report = self._report(task_id, records, checks)
        self._save(task_id, "meta", "report", report)
        if report["failed"]:
            outcome = self._make_successor(job, report)
        elif report["unknown"]:
            outcome = TerminalOutcome("unresolved",
                "Final verification could not establish completion for: "
                + ", ".join(report["unknown"])
                + ". The original work and report card remain persisted; no successful completion is asserted.",
                report)
        else:
            outcome = self._compose(job, report, records)
        if outcome.status != "unresolved":
            self._save(task_id, "meta", "outcome", vars(outcome))
            self._save(task_id, "meta", "closed", True)
        return outcome

    def _outline(self, job, report):
        cached = self._load(job.task_id, "meta", "outline")
        if cached: return cached
        schema = {"type":"object","properties":{
            "parts":{"type":"array","minItems":2,"maxItems":14,"items":{
                "type":"object","properties":{
                    "title":{"type":"string"},"goal":{"type":"string"},
                    "step_id":{"type":"string"}
                },"required":["title","goal","step_id"],"additionalProperties":False}}
        },"required":["parts"],"additionalProperties":False}
        steps = report["steps"]
        try:
            planned = self._n1_json(
                "You are N1. Plan a user-facing completion report for this ALREADY VERIFIED "
                "task. Break it into 4-14 short independent paragraphs, each 350-750 characters, "
                "each grounded in ONE step's evidence. Include scope, important implementation, "
                "verification, constraints, and limitations. No invented facts, no full prose. "
                "step_id must be one of the listed ids. Only JSON.\n"
                + self._json({"request":str((job.metadata or {}).get("task_user_prompt") or "")[:1800],
                    "verified_steps":[{"id":s["id"],"name":s["name"],
                        "checks":[c["target"] for c in s["checks"]]} for s in steps]}),
                schema, tokens=850,
            )
            valid_ids = {s["id"] for s in steps}
            parts = [{"title":str(x["title"])[:100], "goal":str(x["goal"])[:280],
                "step_id":str(x["step_id"])} for x in planned.get("parts", [])
                if isinstance(x, dict) and str(x.get("step_id")) in valid_ids]
        except Exception:
            logging.warning("N1 paragraph-outline call failed task=%s", job.task_id, exc_info=True)
            parts = []
        if not parts:
            parts = [{"title":"Scope and execution","goal":"Summarize the original task objective and the completed work without speculation",
                "step_id":steps[0]["id"]}]
            for s in steps:
                parts.append({"title":str(s["name"])[:80],
                    "goal":"Explain the evidenced result and material technical details for this completed step",
                    "step_id":s["id"]})
            parts.append({"title":"Verification","goal":"State what was actually verified and any limitations",
                "step_id":steps[-1]["id"]})
        parts = parts[:14]
        self._save(job.task_id, "meta", "outline", parts)
        return parts

    def _compose(self, job, report, records):
        """Draft and check individual paragraphs via independent Celery tasks."""
        task_id=job.task_id
        steps={r["id"]:r for r in records}
        parts=self._outline(job,report)
        deadline=float(self._load(task_id,"meta","started_at") or time.time())+self.deadline_seconds
        model_name=str(getattr(self.w.client,"model","") or "")
        evidence={}
        for idx,item in enumerate(parts):
            step=steps[item["step_id"]]
            checks=next(s["checks"] for s in report["steps"] if s["id"]==item["step_id"])
            evidence[f"{idx:02d}"]={
                "section":item,
                "authoritative_evidence":{
                    "original_request":str((job.metadata or {}).get("task_user_prompt") or "")[:1700],
                    "step":step["id"],"step_name":step["name"],
                    "completed_step_summary":str(step.get("summary") or "")[:3900],
                    "verification_requirement":str(step.get("verify") or "")[:750],
                    "tool_evidence":step.get("evidence",[])[:8],
                    "assertions":[{"target":c["target"],"evidence":c["evidence"]} for c in checks],
                }
            }
        while time.time()<deadline:
            self._check_guard(task_id)
            jobs=[]
            for idx,item in enumerate(parts):
                section=f"{idx:02d}"
                current=self._load(task_id,"paragraphs",section)
                if current and current.get("status")=="accepted":
                    continue
                if current and current.get("status")=="PENDING":
                    # Pending from an earlier crashed coordinator: re-publish
                    # the same deterministic task ID; CAS prevents double commit.
                    attempt=int(current.get("attempt") or 1)
                elif current and int(current.get("attempt") or 0)>=2:
                    continue
                else:
                    attempt=(int(current.get("attempt") or 0)+1) if current else 1
                    self._save(task_id,"paragraphs",section,{
                        "status":"PENDING","attempt":attempt,"section":item,
                        "issue":str((current or {}).get("issue") or "")[:350]})
                jobs.append((section,attempt))
            if not jobs:
                break
            for section,attempt in jobs:
                submit_paragraph(task_id,section,evidence[section],attempt,deadline,model_name)
            # Wait for this bounded Celery batch, collecting results every 3sec.
            while time.time()<deadline:
                self._check_guard(task_id)
                current=self._items(task_id,"paragraphs")
                if all((current.get(f"{i:02d}") or {}).get("status")!="PENDING"
                       for i in range(len(parts))):
                    break
                time.sleep(3)
            if any((self._load(task_id,"paragraphs",f"{i:02d}") or {}).get("status")=="PENDING"
                   for i in range(len(parts))):
                break
        accepted=[]
        for idx in range(len(parts)):
            section=f"{idx:02d}"
            result=self._load(task_id,"paragraphs",section) or {}
            if result.get("status")!="accepted":
                return TerminalOutcome("unresolved",
                    f"Paragraph {idx+1}/{len(parts)} has no verified acceptance. "
                    "Redis retains accepted sections for restart; task was not declared complete.",report)
            accepted.append(str(result["text"]))
        answer="\n\n".join(accepted)
        if not answer.strip():
            return TerminalOutcome("unresolved","No verified final answer exists.",report)
        candidate=self.w._build_final_candidate(job,answer)
        if not self.w._final_artifact_state(candidate):
            return TerminalOutcome("unresolved",
                "Final answer assembled but deterministic artifact verification is incomplete.",report)
        self._save(task_id,"meta","assembled",answer)
        return TerminalOutcome("completed",answer,report)

    def _make_successor(self, job, report):
        failed = [s for s in report["steps"] if s["verdict"] == "FALSE"]
        if not failed:
            return TerminalOutcome("unresolved","No proved failures to repair.",report)
        schema = {"type":"object","properties":{
            "steps":{"type":"array","minItems":1,"maxItems":8,"items":{
                "type":"object","properties":{
                    "name":{"type":"string"},"instruction":{"type":"string"},"verify":{"type":"string"}
                },"required":["name","instruction","verify"],"additionalProperties":False}}
        },"required":["steps"],"additionalProperties":False}
        recorded = self._load(job.task_id, "meta", "repair_plan")
        if recorded is None:
            try:
                planned = self._n1_json(
                    "You are N1, reconstructing a NEW INTERNAL RECOVERY TASK. The original task "
                    "already executed; return ONLY concrete missing/failed work as 1-8 bounded "
                    "action steps. Preserve everything verified TRUE. Do not repeat completed work. "
                    "Do not treat UNKNOWN as proven failure. Include precise validation for each repair. "
                    "Provide strict JSON.\n"
                    + self._json({"original_request":str((job.metadata or {}).get("task_user_prompt") or "")[:1900],
                      "failures":failed[:12], "unknown_require_reverification":report["unknown"],
                      "completed_step_ids":report["passed"]}),
                    schema, tokens=1200,
                )
                recorded = [{"name":str(s["name"])[:120],"instruction":str(s["instruction"])[:1800],
                    "verify":str(s["verify"])[:500]} for s in planned["steps"][:8]]
                if not recorded: raise ValueError("empty N1 repair plan")
                self._save(job.task_id, "meta", "repair_plan", recorded)
            except Exception as exc:
                logging.warning("N1 successor planning unavailable task=%s",job.task_id,exc_info=True)
                return TerminalOutcome("unresolved",
                    "Verification found incomplete work, but N1 could not produce a reliable "
                    "corrective task. Original task remains available for explicit recovery.",report)
        if (report["unknown"] and not any(
                str(x.get("name") or "") == "Read-only recheck of unresolved targets"
                for x in recorded)):
            recorded = list(recorded[:7]) + [{
                "name":"Read-only recheck of unresolved targets",
                "instruction":"Independently verify UNKNOWN conclusions using source artifacts "
                    "and prior evidence; do not modify anything merely because verification "
                    "was inconclusive. Unknown step IDs: " + ", ".join(report["unknown"]),
                "verify":"Each formerly unknown assertion must have recorded TRUE/FALSE evidence.",
            }]
            self._save(job.task_id, "meta", "repair_plan", recorded)
        parent_id = job.task_id
        child_id = "recovery-" + uuid.uuid5(uuid.NAMESPACE_URL, parent_id + ":" + job.step_id).hex[:20]
        child_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, child_id + "/task"))
        original = str((job.metadata or {}).get("original_user_prompt") or
            (job.metadata or {}).get("task_user_prompt") or "")
        preserve = ("Original task: " + parent_id + ". Verified completed step ids: "
                    + ", ".join(report["passed"]) + ". Report-card Redis key: "
                    + self.key(parent_id, "checks") + ". Do not redo verified work.")
        initial = [
            TaskStep("plan-breakdown","N1 recovery decomposition",
                     "N1 delegated only outstanding work after terminal verification",
                     "N1 recorded corrective action plan",node_id=str(uuid.uuid5(uuid.NAMESPACE_URL,child_id+"/plan"))),
            TaskStep("verify-plan","Verify recovery scope",
                     "Preserve all previously accepted steps",
                     "Recovery scope contains only failed checks",depends_on=("plan-breakdown",),
                     node_id=str(uuid.uuid5(uuid.NAMESPACE_URL,child_id+"/verify-plan"))),
        ]
        previous = "verify-plan"
        repairs = []
        for i, part in enumerate(recorded, 1):
            sid = f"step-{i:02d}"
            repairs.append(TaskStep(sid,part["name"],
                "AUTHORITATIVE PRIMARY REQUEST (full in metadata):\n" + original[:2500]
                + "\n\nRECOVERY SCOPE:\n" + preserve
                + "\n\nNORM-GENERATED EXECUTION STEP:\n" + part["instruction"]
                + "\n\nVERIFICATION REQUIREMENT:\n" + part["verify"],
                part["verify"],depends_on=(previous,),
                node_id=str(uuid.uuid5(uuid.NAMESPACE_URL,child_id+"/"+sid))))
            previous = sid
        last_id = f"step-{len(repairs)+1:02d}"
        last = TaskStep(last_id,"Compile repaired work and conclude",
            "Synthesize the repaired steps and preserved original work into the direct user answer. "
            "Do not rerun already verified tasks.\n" + preserve,
            "Report completion accurately with checked evidence",depends_on=(previous,),
            node_id=str(uuid.uuid5(uuid.NAMESPACE_URL,child_id+"/"+last_id)))
        final = TaskStep("final-verify","Verify final candidate",
            "Validate the final candidate evidence",
            "Final candidate and deterministic artifact check must pass",
            depends_on=(last_id,),
            node_id=str(uuid.uuid5(uuid.NAMESPACE_URL,child_id+"/final-verify")))
        plan = TaskPlan(
            task_id=child_id,title="Recovery of incomplete work from " + parent_id,
            steps=tuple([*initial,*repairs,last,final]),task_uuid=child_uuid,
            original_request=original,
            source_prompt_id=str((job.metadata or {}).get("source_prompt_id") or ""),
            source_user_message_id=str((job.metadata or {}).get("original_user_message_id") or ""),
        )
        # Successor ID and N1 plan are deterministic and persisted across a crash.
        self._check_guard(parent_id)
        existing_status = self.w.durable.task_status(child_id)
        lineage = (self.w.durable.task_lineage(child_id)
            if existing_status is not None and hasattr(self.w.durable, "task_lineage") else None)
        if existing_status is None or (isinstance(lineage, dict)
                and str(lineage.get("parent_task_id") or "") != parent_id):
            self.w.durable.start_child_task(plan,parent_id,job.step_id,
                original_request=original,task_kind="recovery")
            self.w.live.start_task(child_id,plan.title,plan.as_dict())
            for item in initial:
                now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                self.w.durable.checkpoint_step(StepResult(child_id,item.id,item.name,
                    StepStatus.COMPLETED,"N1 terminal recovery control: "+preserve,
                    verification=item.verify,started_at=now,completed_at=now))
                self.w.live.step_started(child_id,item.id,item.name)
                self.w.live.step_completed(child_id,item.id,preserve,item.verify)
        if not self.w.queue.contains_message_id(repairs[0].node_id):
            jobs = []
            for idx, step in enumerate([*repairs,last]):
                meta = dict(job.metadata or {})
                meta.update({"step_name":step.name,"verification":step.verify,
                    "prompt_origin":"norm_generated_step","context_id":child_id,
                    "is_final_candidate":idx==len(repairs),
                    "generated_step_index":idx+1,
                    "generated_step_count":len(repairs)+1,
                    "final_verification_node_id":final.node_id,
                    "recovery_parent_task_id":parent_id,
                    "recovery_report_key":self.key(parent_id,"checks"),
                    "original_user_prompt":original,
                    "task_user_prompt":original,
                    "allow_subtask":False,
                })
                jobs.append(PromptJob(message_id=step.node_id,task_id=child_id,
                    step_id=step.id,prompt=step.description,project_id=job.project_id,
                    context=preserve,attempt=0,created_at=time.time(),metadata=meta,
                    previous_prompt_id="",next_prompt_id="",chain_index=idx,
                    recovery_attempted=False,
                    chain_id=plan.task_uuid,task_uuid=plan.task_uuid,node_id=step.node_id,
                    chain_uuid=plan.task_uuid,request_type="conclusion" if idx==len(repairs) else "step"))
            self.w.queue.enqueue_chain(jobs)
        self._save(parent_id, "meta", "successor_id", child_id)
        return TerminalOutcome("passed_to",
            "Task " + parent_id + " concluded as PASSED_TO:" + child_id
            + ". N1 delegated only the failed verification targets. "
            + "Previously verified steps are referenced by the successor and remain intact.",report,child_id)
