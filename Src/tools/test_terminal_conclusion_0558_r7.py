"""Offline terminal-phase tests; no live Redis/Ollama/PostgreSQL calls."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from norm_runtime.terminal_conclusion import TerminalConclusion
import norm_runtime.terminal_conclusion as tc


class Redis:
    def __init__(self): self.d = {}
    def hget(self,k,f): return self.d.get(k,{}).get(f)
    def hgetall(self,k): return dict(self.d.get(k,{}))
    def hset(self,k,f=None,v=None,mapping=None):
        d=self.d.setdefault(k,{})
        if mapping is not None: d.update(mapping)
        else: d[f]=v
    def expire(self,*a): return True
    def delete(self,k): self.d.pop(k,None)
    def pipeline(self,transaction=True): return self
    def execute(self): return []
    def __enter__(self): return self
    def __exit__(self,*args): return False


class Gate:
    enabled = True
    def __init__(self):self.calls=0
    def _review_generate(self,prompt,*,response_format,**kw):
        self.calls+=1
        if "targets" in response_format.get("properties",{}):
            return json.dumps({"targets":["Confirmed required result"]})
        if "parts" in response_format.get("properties",{}):
            return json.dumps({"parts":[{"title":"Verified work",
                "goal":"Summarize only the recorded completion","step_id":"step-01"}]})
        if "steps" in response_format.get("properties",{}):
            return json.dumps({"steps":[{"name":"Correct missing step",
                "instruction":"Finish the missing requirement only",
                "verify":"Required output exists and is evidenced"}]})
        raise AssertionError("unexpected N1 call")


class Model:
    def __init__(self,verdict="TRUE"): self.calls=[];self.verdict=verdict
    def generate(self,prompt,**kwargs):
        self.calls.append((prompt,kwargs))
        if kwargs.get("response_format") is None:
            return "**Verified implementation** — Completed the requested result with recorded evidence."
        if "issue" in kwargs["response_format"].get("properties",{}):
            return json.dumps({"verdict":self.verdict,"issue":"insufficient evidence" if self.verdict!="TRUE" else ""})
        return json.dumps({"verdict":self.verdict,"evidence":"Persisted result supports the specific assertion"})


class Durable:
    def __init__(self,step_status="completed"): self.step_status=step_status;self.children={};self.checkpoints=[]
    def terminal_step_records(self,task_id,step_id):
        return [{"id":"step-01","name":"Implement change","description":"Perform bounded source change",
          "verify":"Change was made","status":self.step_status,
          "summary":"The change is complete; test succeeded","evidence":[{"tool":"test","result":"ok"}],
          "evidence_truncated":False}]
    def task_status(self,task_id): return "running" if task_id in self.children else None
    def start_child_task(self,plan,parent_id,parent_step,**kwargs):
        self.children[plan.task_id]=(plan,parent_id,parent_step,kwargs)
    def checkpoint_step(self,result): self.checkpoints.append(result)
    def task_lineage(self,task_id): return {"parent_task_id":self.children[task_id][1]}


class Queue:
    def __init__(self):self.r=Redis();self.jobs=[]
    def contains_message_id(self,message_id):return any(x.node_id==message_id for x in self.jobs)
    def enqueue_chain(self,jobs):self.jobs.extend(jobs)


class Live:
    def __init__(self):self.started=[]
    def start_task(self,*args):self.started.append(args)
    def step_started(self,*args):pass
    def step_completed(self,*args):pass


class Worker:
    def __init__(self,step_status="completed",verdict="TRUE"):
        self.queue=Queue();self.client=Model(verdict);self.durable=Durable(step_status)
        self.n1_gatekeeper=Gate();self.live=Live()
    def _runtime_config(self):return {"terminal_conclusion":{"verify_workers":1,"autostart_workers":False}}
    def _build_final_candidate(self,job,answer):return {"user_reply":answer}
    def _final_artifact_state(self,candidate):return True


def job():
    return SimpleNamespace(task_id="task-X",step_id="step-02",project_id="default",
        metadata={"task_user_prompt":"Implement the change","original_user_prompt":"Implement the change"})


def make_service(w,monkeypatch):
    # No live Redis or Celery submissions in unit tests.
    monkeypatch.setattr(tc,"redis_client",lambda: w.queue.r)
    def fake_verify(task_id,cid,payload,deadline,model_name=""):
        entry=json.loads(w.queue.r.hget(tc.TerminalConclusion(w).key(task_id,"checks"),cid))
        entry["verdict"]=w.client.verdict
        entry["evidence"]="Mocked independent check"
        w.queue.r.hset(tc.TerminalConclusion(w).key(task_id,"checks"),cid,json.dumps(entry))
    def fake_paragraph(task_id,cid,payload,attempt,deadline,model_name=""):
        k=tc.TerminalConclusion(w).key(task_id,"paragraphs")
        entry=json.loads(w.queue.r.hget(k,cid))
        entry["status"]="accepted" if w.client.verdict=="TRUE" else "unknown"
        entry["text"]="**Verified implementation** — Completed the requested result with recorded evidence."
        w.queue.r.hset(k,cid,json.dumps(entry))
    monkeypatch.setattr(tc,"submit_verify",fake_verify)
    monkeypatch.setattr(tc,"submit_paragraph",fake_paragraph)
    return TerminalConclusion(w)


def test_completed_task_retains_original_id_and_cached_paragraph(monkeypatch):
    w=Worker()
    svc=make_service(w,monkeypatch)
    result=svc.run(job())
    assert result.status=="completed"
    assert "Verified implementation" in result.answer
    assert not w.durable.children
    assert svc._load("task-X","paragraphs","00")["status"]=="accepted"
    count=len(w.client.calls)
    cached=svc.run(job())
    assert cached.answer==result.answer
    assert len(w.client.calls)==count


def test_incomplete_step_creates_single_successor_and_reference(monkeypatch):
    w=Worker(step_status="failed")
    svc=make_service(w,monkeypatch)
    result=svc.run(job())
    assert result.status=="passed_to"
    assert result.successor_id.startswith("recovery-")
    assert result.report["failed"]==["step-01"]
    assert len(w.durable.children)==1
    assert len(w.queue.jobs)==2
    assert w.queue.jobs[-1].request_type=="conclusion"
    assert "step-01" in w.queue.jobs[0].metadata["recovery_report_key"] or "task-X" in w.queue.jobs[0].metadata["recovery_report_key"]


def test_uncertain_verification_never_passes_or_repairs_automatically(monkeypatch):
    w=Worker(verdict="UNKNOWN")
    svc=make_service(w,monkeypatch)
    result=svc.run(job())
    assert result.status=="unresolved"
    assert result.report["unknown"]==["step-01"]
    assert not w.durable.children
    assert not w.queue.jobs

def test_manual_unknown_recheck_reuses_task_id(monkeypatch):
    w=Worker(verdict="UNKNOWN")
    svc=make_service(w,monkeypatch)
    first=svc.run(job())
    assert first.status=="unresolved"
    assert w.durable.children=={}
    w.client.verdict="TRUE"
    resumed=job()
    resumed.metadata["retry_terminal_verification"]=True
    second=svc.run(resumed)
    assert second.status=="completed"
    assert len(w.durable.children)==0


def test_stop_guard_refuses_terminal_completion(monkeypatch):
    from norm_runtime.ollama_client import ModelGenerationCancelled
    import pytest
    w=Worker()
    w._suppression_requested=lambda task_id:True
    with pytest.raises(ModelGenerationCancelled):
        make_service(w,monkeypatch).run(job())
    assert w.client.calls==[]
    assert not w.durable.children


def test_successor_id_stable_across_retry_without_duplication(monkeypatch):
    w=Worker(step_status="failed")
    svc=make_service(w,monkeypatch)
    outcome=svc.run(job())
    assert outcome.status=="passed_to"
    new=make_service(w,monkeypatch).run(job())
    assert new.successor_id==outcome.successor_id
    assert len(w.durable.children)==1
    assert len(w.queue.jobs)==2
