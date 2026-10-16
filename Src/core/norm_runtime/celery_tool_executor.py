"""N1-approved Celery transport; worker contains no model/agent decisions."""
from __future__ import annotations
import configparser,hashlib,hmac,json,os,secrets,sys,time,uuid
from pathlib import Path
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

@app.task(name="norm.execute_approved_tool")
def execute_approved_tool(jobid):
    r=redis()
    raw=r.get(key(jobid))
    if not raw:raise PermissionError("N1 approval unavailable")
    receipt=json.loads(raw)
    approved=receipt["approved"]
    if not hmac.compare_digest(signature(approved),receipt.get("seal","")):
        raise PermissionError("N1 approval not authentic")
    if approved["id"]!=jobid or time.time()>approved["expires"]:
        raise PermissionError("N1 approval stale")
    claimed=r.set(CFG["prefix"]+":claimed:"+jobid,os.getpid(),ex=172800,nx=True)
    if not claimed:raise RuntimeError("duplicate execution blocked")
    from runtime_bootstrap import build_conversation_service
    tools=build_conversation_service(root(),ensure_schema=False).file_tools
    if tools is None:raise RuntimeError("no registered tool executor")
    name=approved["name"]
    if name not in {v["function"]["name"] for v in tools.schemas()}:
        raise PermissionError("tool missing from registered schema")
    tools.set_task_context(approved["task_id"],approved["step_id"])
    return tools.execute(name,approved["arguments"])

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
        approved={"id":jobid,"task_id":task_id,"step_id":step_id,
                  "name":name,"arguments":arguments,"expires":time.time()+86400}
        payload={"approved":approved,"seal":signature(approved)}
        if not self.redis.set(key(jobid),json.dumps(payload,ensure_ascii=False),ex=172800,nx=True):
            raise RuntimeError("dispatch collision")
        try:app.send_task("norm.execute_approved_tool",args=[jobid],task_id=jobid,queue=CFG["queue"])
        except Exception:
            self.redis.delete(key(jobid))
            raise
        return jobid
    def poll(self,jobid):
        result=app.AsyncResult(jobid)
        if not result.ready():return None
        if result.successful() and isinstance(result.result,dict):return result.result
        return {"ok":False,"error":"Async tool failed: "+str(result.result)[:500]}