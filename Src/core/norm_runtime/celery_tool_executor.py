"""N1-approved Celery transport; worker contains no model/agent decisions."""
from __future__ import annotations
import configparser,hashlib,hmac,json,logging,os,secrets,subprocess,sys,time,uuid
from pathlib import Path
from functools import lru_cache
from celery import Celery
from redis import Redis

def root():
    env=os.environ.get("NORM_ROOT")
    return Path(env).resolve() if env else (Path(sys.executable).resolve().parent.parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parents[2])

def config(base=None):
    base=Path(base or root())
    from norm_runtime.settings import load_network_settings,load_ports,resolve_network_host
    parser=configparser.ConfigParser(interpolation=None)
    parser.read(base/"config"/"settings.ini",encoding="utf-8-sig")
    section=parser["celery"] if parser.has_section("celery") else {}
    val=lambda k,d:section.get(k,d)
    db=int(val("redis_db","4"))
    runtime=json.loads((base/"config"/"runtime.json").read_text(encoding="utf-8-sig"))
    reserved={int((runtime.get(k) or {}).get("db",default)) for k,default in
              (("redis",0),("prompt_queue",1),("deletion_queue",2),("console_queue",3))}
    if db in reserved or db<0 or db>15:raise ValueError("Celery Redis DB overlaps existing Norm use or is outside 0..15")
    net=load_network_settings(base)
    return {"enabled":str(val("enabled","false")).lower() in ("true","yes","1","on"),
            "db":db,"host":resolve_network_host(net,"redis_host"),
            "port":int(load_ports(base)["redis"]),
            "queue":str(val("queue","norm-async-tools")),
            "count":min(8,max(1,int(val("worker_count","4")))),
            # A detached solo worker must fail closed if Norm disappears.
            "orphan_grace_seconds":min(900,max(30,int(val("orphan_grace_seconds","180")))),
            "startup_grace_seconds":min(1800,max(60,int(val("startup_grace_seconds","300")))),
            "max_job_age_seconds":min(3600,max(900,int(val("max_job_age_seconds","3600")))),
            "prefix":"norm:async:v1"}

CFG=config()
def make_app():
    url=f"redis://{CFG['host']}:{CFG['port']}/{CFG['db']}"
    app=Celery("norm_async_tools",broker=url,backend=url)
    app.conf.update(task_serializer="json",result_serializer="json",
        accept_content=["json"],task_default_queue=CFG["queue"],
        task_track_started=True,result_expires=172800,
        worker_prefetch_multiplier=1,broker_connection_retry_on_startup=True)
    return app
app=make_app()
def redis():
    return Redis(host=CFG["host"],port=CFG["port"],db=CFG["db"],
                 socket_connect_timeout=2,socket_timeout=5)
def key(jobid):
    return CFG["prefix"]+":approved:"+str(uuid.UUID(jobid))

def cancellation_key(jobid):
    return CFG["prefix"]+":cancelled:"+str(uuid.UUID(jobid))

def supervisor_key():
    return CFG["prefix"] + ":supervisor-heartbeat"

def expiry_reason(started, last_heartbeat, now, *, startup_grace, orphan_grace):
    """Pure elapsed-time watchdog decision (monotonic seconds)."""
    if last_heartbeat is None:
        return "supervisor_never_started" if now-started >= startup_grace else None
    return "supervisor_lost" if now-last_heartbeat >= orphan_grace else None

def dispatch_started_at(approved):
    """Legacy sealed approvals only had a 24h expiry, not a start time."""
    return float(approved.get("submitted_at", float(approved["expires"]) - 86400))
def signature(payload):
    path=root()/"state"/"n1-celery-seal.key"
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        try:
            with path.open("xb") as f:f.write(secrets.token_bytes(32))
        except FileExistsError:pass
    secret=path.read_bytes()
    if len(secret)!=32:raise RuntimeError("invalid approval seal")
    value=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    return hmac.new(secret,value,hashlib.sha256).hexdigest()

@lru_cache(maxsize=1)
def _worker_tool_executor():
    """One toolkit per Celery *solo process*, not per tool invocation.

    Reuse the same local PostgreSQL pools and hot-loaded plugin runtime. This
    cache must never be shared between processes or concurrent task threads.
    """
    from runtime_bootstrap import build_conversation_service
    tools = build_conversation_service(root(), ensure_schema=False).file_tools
    if tools is None:
        raise RuntimeError("no registered tool executor")
    return tools


def _execution_marker(jobid, state, **details):
    """Best-effort execution timeline; never change tool execution semantics."""
    payload = {"state": state, "at": time.time(), "pid": os.getpid(), **details}
    try:
        redis().set(CFG["prefix"] + ":execution:" + jobid,
                    json.dumps(payload, ensure_ascii=False, default=str), ex=172800)
    except Exception:
        import logging
        logging.exception("Celery execution marker could not persist id=%s state=%s", jobid, state)


@app.task(name="norm.execute_approved_tool")
def execute_approved_tool(jobid):
    r=redis()
    raw=r.get(key(jobid))
    if not raw:raise PermissionError("N1 approval unavailable")
    receipt=json.loads(raw)
    approved=receipt["approved"]
    if r.exists(cancellation_key(jobid)):
        return {"ok":False,"status":"cancelled","error":"Task suppressed before Celery execution"}
    if not hmac.compare_digest(signature(approved),receipt.get("seal","")):
        raise PermissionError("N1 approval not authentic")
    if approved["id"]!=jobid or time.time()>approved["expires"]:
        raise PermissionError("N1 approval stale")
    current_supervisor = r.get(supervisor_key())
    if not current_supervisor or (approved.get("supervisor_token") and
                                  current_supervisor.decode("utf-8") != approved["supervisor_token"]):
        _execution_marker(jobid, "orphaned", reason="supervisor_unavailable_or_replaced")
        return {"ok":False,"status":"orphaned", "async_unresolved":True,
                "dispatch_id":jobid,
                "error":"Norm supervisor absent; Celery refused to execute an old dispatch."}
    if time.time() - dispatch_started_at(approved) >= CFG["max_job_age_seconds"]:
        _execution_marker(jobid, "timed_out", reason="expired_before_execution")
        return {"ok":False,"status":"timed_out", "dispatch_id":jobid,
                "error":"Celery dispatch expired before execution; no tool ran."}
    claimed=r.set(CFG["prefix"]+":claimed:"+jobid,os.getpid(),ex=172800,nx=True)
    if not claimed:raise RuntimeError("duplicate execution blocked")
    started = time.monotonic()
    active_key = CFG["prefix"] + ":active:" + str(os.getpid())
    r.set(active_key, jobid, ex=86400)
    _execution_marker(jobid, "bootstrapping")
    try:
        if r.exists(cancellation_key(jobid)):
            raise RuntimeError("dispatch cancelled by operator during startup")
        tools = _worker_tool_executor()
        name = approved["name"]
        if name not in {v["function"]["name"] for v in tools.schemas()}:
            raise PermissionError("tool missing from registered schema")
        tools.set_task_context(approved["task_id"], approved["step_id"])
        setup_seconds = round(time.monotonic() - started, 3)
        _execution_marker(jobid, "executing", setup_seconds=setup_seconds,
                          tool=name)
        if r.exists(cancellation_key(jobid)):
            _execution_marker(jobid, "cancelled", tool=name)
            return {"ok":False,"status":"cancelled","error":"Task suppressed before tool execution"}
        output = tools.execute(name, approved["arguments"])
        _execution_marker(jobid, "completed", setup_seconds=setup_seconds,
                          elapsed_seconds=round(time.monotonic() - started, 3),
                          result_ok=bool(isinstance(output, dict) and output.get("ok")))
        return output
    except BaseException as exc:
        _execution_marker(jobid, "failed", elapsed_seconds=round(time.monotonic() - started, 3),
                          error_type=type(exc).__name__)
        raise
    finally:
        if r.get(active_key) == jobid.encode("utf-8"):
            r.delete(active_key)

class Broker:
    def __init__(self):
        self.redis=redis()
    def available(self):
        if not CFG["enabled"]:return False
        try:return any(self.redis.scan_iter(match=CFG["prefix"]+":worker:*",count=20))
        except Exception:return False
    def submit(self,task_id,step_id,name,arguments):
        if not self.available():raise RuntimeError("no Celery worker available")
        jobid=str(uuid.uuid4())
        token = self.redis.get(supervisor_key())
        if not token:
            raise RuntimeError("Norm supervisor heartbeat absent; Celery dispatch refused")
        now = time.time()
        approved={"id":jobid,"task_id":task_id,"step_id":step_id,
                  "name":name,"arguments":arguments,"submitted_at":now,
                  "supervisor_token":token.decode("utf-8"),"expires":now+86400}
        payload={"approved":approved,"seal":signature(approved)}
        if not self.redis.set(key(jobid),json.dumps(payload,ensure_ascii=False),ex=172800,nx=True):
            raise RuntimeError("dispatch collision")
        try:
            _execution_marker(jobid, "submitted")
            app.send_task("norm.execute_approved_tool", args=[jobid], task_id=jobid, queue=CFG["queue"])
        except Exception:
            self.redis.delete(key(jobid))
            raise
        return jobid
    def inspect(self, jobid):
        """Non-executing state read for N1 adaptive completion estimates.

        No payload, path, arguments or secret-bearing results are exposed.
        This never re-dispatches a Celery task.
        """
        result = app.AsyncResult(jobid)
        state = str(result.state or "UNKNOWN")
        workers = 0
        try:
            workers = sum(1 for _ in self.redis.scan_iter(
                match=CFG["prefix"] + ":worker:*", count=20))
        except Exception:
            pass
        marker = {}
        try:
            raw = self.redis.get(CFG["prefix"] + ":execution:" + jobid)
            if raw:
                marker = json.loads(raw)
        except Exception:
            pass
        pid = str(marker.get("pid") or "")
        worker_alive = False
        if pid:
            try:
                worker_alive = bool(self.redis.exists(CFG["prefix"] + ":worker:" + pid))
            except Exception:
                pass
        return {"state": state, "worker_heartbeats": workers,
                "ready": bool(result.ready()), "execution": marker.get("state", "unknown"),
                "execution_age_seconds": max(0, round(time.time()-float(marker.get("at") or time.time()))),
                "worker_alive": worker_alive,
                "setup_seconds": marker.get("setup_seconds"),
                "elapsed_seconds": marker.get("elapsed_seconds")}

    def poll(self,jobid):
        if self.redis.exists(cancellation_key(jobid)):
            return {"ok":False,"status":"cancelled", "dispatch_id":jobid,
                    "error":"Celery dispatch cancelled by operator task suppression; re-evaluate before retrying."}
        result=app.AsyncResult(jobid)
        if not result.ready():
            # A force-exited solo worker may never publish a Celery result.
            # Preserve the actual reason rather than leaving N1 waiting an hour.
            raw = self.redis.get(CFG["prefix"] + ":execution:" + jobid)
            if raw:
                try:
                    marker = json.loads(raw)
                    if marker.get("state") in {"orphaned", "timed_out"}:
                        return {"ok": False, "status": marker["state"],
                                "dispatch_id":jobid, "async_unresolved": True,
                                "error": "Celery worker was stopped by its safety watchdog; execution outcome unknown."}
                except (ValueError, TypeError):
                    pass
            return None
        if result.successful() and isinstance(result.result,dict):return result.result
        return {"ok":False,"error":"Async tool failed: "+str(result.result)[:500]}

def cancel_task_dispatches(receipt_store, task_ids, *, runtime_root=None):
    """Hard-stop only Norm-owned solo workers running dispatched jobs in this tree.

    This does not clear the durable N1 receipts. Resuming the original task
    delivers a cancellation observation to N1 rather than duplicating a job.
    """
    report = {"found":0,"cancelled":0,"workers_stopped":0,
              "replenish_requested":False,"errors":[]}
    try:
        from norm_runtime.celery_shutdown import _owned_workers, stop_task_celery_worker
        allowed = {int(w["pid"]) for w in _owned_workers(Path(runtime_root or root()))}
    except Exception as exc:
        allowed = set()
        report["errors"].append("worker inventory unavailable: " + type(exc).__name__)
    r = redis()
    try:
        ids = set()
        for task_id in task_ids:
            for raw_key in receipt_store.scan_iter(match=f"norm:async-link:{task_id}:*", count=50):
                try:
                    payload = receipt_store.get(raw_key)
                    rows = json.loads(payload) if payload else []
                    for item in rows:
                        if isinstance(item, dict) and item.get("id"):
                            ids.add(str(uuid.UUID(str(item["id"]))))
                except Exception as exc:
                    report["errors"].append("receipt unreadable: " + type(exc).__name__)
        # The N1 receipt may have expired or not yet been checkpointed when
        # suppression arrives. The sealed approval is the dispatch authority.
        for raw_key in r.scan_iter(match=CFG["prefix"] + ":approved:*", count=50):
            try:
                raw = r.get(raw_key)
                candidate = json.loads(raw)["approved"] if raw else {}
                if str(candidate.get("task_id")) in task_ids and candidate.get("id"):
                    ids.add(str(uuid.UUID(str(candidate["id"]))))
            except (ValueError, KeyError, TypeError):
                report["errors"].append("unreadable approved dispatch")
        for jobid in sorted(ids):
            try:
                raw = r.get(key(jobid))
                approved = json.loads(raw).get("approved", {}) if raw else {}
                if approved.get("task_id") not in task_ids or approved.get("id") != jobid:
                    continue
                report["found"] += 1
                if app.AsyncResult(jobid).ready():
                    continue  # N1 must still receive an already-completed result.
                r.set(cancellation_key(jobid), "operator_suppressed", ex=172800)
                report["cancelled"] += 1
                # Solo workers cannot interrupt executing Python via revoke.
                # Revoke queued jobs, and separately terminate verified active PIDs.
                try:
                    app.control.revoke(jobid, terminate=False)
                except Exception as exc:
                    report["errors"].append("revoke " + jobid + ": " + type(exc).__name__)
                raw_marker = r.get(CFG["prefix"] + ":execution:" + jobid)
                marker = json.loads(raw_marker) if raw_marker else {}
                pid = int(marker.get("pid") or 0)
                if marker.get("state") not in {"bootstrapping", "executing"} or pid not in allowed:
                    continue
                # Re-check both dispatch ownership and PID identity immediately before kill.
                new_marker = r.get(CFG["prefix"] + ":execution:" + jobid)
                latest = json.loads(new_marker) if new_marker else {}
                if (int(latest.get("pid") or 0) != pid or
                    latest.get("state") not in {"bootstrapping", "executing"} or
                    pid not in {int(x["pid"]) for x in _owned_workers(Path(runtime_root or root()))}):
                    continue
                if stop_task_celery_worker(Path(runtime_root or root()), pid, jobid, r, CFG["prefix"]):
                    report["workers_stopped"] += 1
                else:
                    report["errors"].append(f"worker PID {pid}: ownership/termination not confirmed")
            except Exception as exc:
                report["errors"].append("cancel " + jobid + ": " + type(exc).__name__)
        if report["workers_stopped"] and os.name == "nt":
            # Maintain the configured pool size without restarting Norm or
            # terminating the other solo workers. Existing launcher owns the
            # identity lock, so two concurrent replenishments cannot duplicate.
            try:
                base = Path(runtime_root or root())
                runner = base / ".venv" / "Scripts" / "python.exe"
                launcher = base / "tools" / "start_async_workers.py"
                if not runner.is_file() or not launcher.is_file():
                    raise FileNotFoundError("Celery launcher or interpreter missing")
                with (base / "logs" / "celery-replenishment.log").open("ab") as output:
                    subprocess.Popen([str(runner), "-B", str(launcher)], cwd=str(base),
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                report["replenish_requested"] = True
            except Exception as exc:
                report["errors"].append("worker replenishment: " + type(exc).__name__)
        if report["errors"]:
            logging.warning("Celery task suppression incomplete: %s", report)
        else:
            logging.info("Celery task suppression: %s", report)
    finally:
        r.close()
    return report
