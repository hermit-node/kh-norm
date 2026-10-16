"""Offline r8 Celery check timing; no live worker or Redis changes."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from norm_runtime.async_n1_delivery import N1AsyncBridge, EARLY_SECONDS

class Store:
    def __init__(self):self.items={}
    def set(self,k,v,ex=None):self.items[k]=v
    def get(self,k):return self.items.get(k)
    def delete(self,k):self.items.pop(k,None)
class Broker:
    def __init__(self):self.results={};self.calls=[]
    def poll(self,id):
        self.calls.append(id)
        return self.results.get(id)
    def inspect(self,id):return {"state":"STARTED","worker_heartbeats":4}
class Gate:
    def __init__(self):self.estimate_calls=[];self.after_calls=[]
    def estimate_async_next_check(self,**kw):
        self.estimate_calls.append(kw)
        return {"check_after_seconds":480 if len(self.estimate_calls)==1 else 180,
                "reason":"large read still executing"}
    def after_tool(self,**kw):
        self.after_calls.append(kw)
        return {"checked":"N1"}
    def information_tool(self,name):return True
class Owner:
    def __init__(self):
        self.queue=SimpleNamespace(r=Store());self.n1_gatekeeper=Gate()
        self.persisted=[]
    def _persist_evidence_now(self,j,e):self.persisted.extend(e)
def bridge():
    owner=Owner()
    b=N1AsyncBridge.__new__(N1AsyncBridge)
    b.owner=owner;b.job=SimpleNamespace(task_id="task",step_id="step")
    b.store=owner.queue.r;b.key="norm:async-link:task:step";b.enabled=True
    b.broker=Broker()
    b.pending=[{"id":"dispatch","signature":N1AsyncBridge._signature("inspect_file",{"path":"C:/example.txt"}),"name":"inspect_file",
                "arguments":{"path":"C:/example.txt"},
                "outcome":{"execute":True,"arguments":{"path":"C:/example.txt"},
                           "target":"C:/example.txt","need":"read","scope":"task",
                           "streamed_read":False,"signature":"tool-sig","candidates":[]},
                "submitted_at":10000,"next_check_at":10030,"poll_count":0,
                "n1_estimates":0}]
    return b,owner
b,o=bridge()
msgs=[];ev=[]
assert b.deliver(msgs,ev,now=10029)==0 and not b.broker.calls
for i,offset in enumerate(EARLY_SECONDS):
    assert b.pending[0]["next_check_at"]==10000+offset,(offset,b.pending[0]["next_check_at"])
    assert b.deliver(msgs,ev,now=10000+offset)==0
    if i<len(EARLY_SECONDS)-1:
        assert not o.n1_gatekeeper.estimate_calls
assert len(o.n1_gatekeeper.estimate_calls)==1
assert b.pending[0]["next_check_at"]==10000+900+480
assert b.deliver(msgs,ev,now=10000+1380)==0
assert len(o.n1_gatekeeper.estimate_calls)==2
assert b.pending[0]["next_check_at"]==10000+1380+180
b.broker.results["dispatch"]={"ok":True,"path":"C:/example.txt","size":14}
assert b.deliver(msgs,ev,now=10000+1560)==1
assert not b.pending
assert len(o.n1_gatekeeper.after_calls)==1
assert o.persisted[0]["result"]["path"]=="C:/example.txt"
assert "[N1 ASYNC TOOL RESULT" in msgs[-1]["content"]
print("PASS 30/60/three-minute checks, first N1 review at 15m, adaptive N1 checks, N1-before-N2")
b,o=bridge()
b.pending[0]["next_check_at"]=10000+3600
msgs=[];ev=[]
assert b.deliver(msgs,ev,now=10000+3600)==1
assert len(o.n1_gatekeeper.estimate_calls)==0
assert ev[0]["result"]["status"]=="unresolved"
assert o.queue.r.get("norm:async-unresolved:task:"+"0"*64) is None
assert any(k.startswith("norm:async-unresolved:task:") for k in o.queue.r.items)
class UnsafeBroker:
    def submit(self,*a):raise AssertionError("previous unresolved call resubmitted")
b.broker=UnsafeBroker()
b.enabled=True
outcome=SimpleNamespace()
answer=b.receipt("inspect_file",{"path":"C:/example.txt"},outcome)
assert answer["status"]=="unresolved"
assert answer["async_dispatch_id"]=="dispatch"
print("PASS 60-minute hard cap, unknown outcome distinguished, no execution retry")
b,o=bridge()
b.broker.results["dispatch"]={"ok":False,"error":"file not found"}
msgs=[];ev=[]
assert b.deliver(msgs,ev,now=10030)==1
assert len(o.n1_gatekeeper.after_calls)==1
assert ev[0]["result"]["ok"] is False
print("PASS negative tool result still passes through N1")
