"""Offline queue regression: async Redis delayed jobs cannot affect normal yield."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from norm_runtime.prompt_queue import PromptJob, RedisPromptQueue

class FakePipe:
    def __init__(self,r):self.r=r;self.ops=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def watch(self,*args):pass
    def unwatch(self):pass
    def multi(self):pass
    def execute(self):
        values=[]
        for name,args,kws in self.ops:
            values.append(getattr(self.r,name)(*args,**kws))
        self.ops=[]
        return values
    def zrangebyscore(self,*a,**kw):return self.r.zrangebyscore(*a,**kw)
    def __getattr__(self,name):
        def record(*args,**kwargs):
            self.ops.append((name,args,kwargs))
            return self
        return record
class FakeRedis:
    def __init__(self):
        self.streams={"work":[]};self.zsets={};self.serial=0
    def pipeline(self,**kw):return FakePipe(self)
    def delete(self,*keys):return 0
    def xadd(self,key,fields):
        self.serial+=1;msg=str(self.serial)
        self.streams.setdefault(key,[]).append((msg,dict(fields)))
        return msg
    def xrange(self,key,min="-",max="+"):
        rows=self.streams.get(key,[])
        if min!="-": rows=[x for x in rows if x[0]>=min]
        if max!="+": rows=[x for x in rows if x[0]<=max]
        return list(rows)
    def xack(self,*args):return 1
    def xdel(self,key,*ids):
        prev=self.streams.get(key,[])
        self.streams[key]=[entry for entry in prev if entry[0] not in ids]
        return len(prev)-len(self.streams[key])
    def zadd(self,key,mapping):
        self.zsets.setdefault(key,{}).update(mapping);return len(mapping)
    def zrange(self,key,start,end):
        rows=sorted(self.zsets.get(key,{}).items(),key=lambda x:x[1])
        return [x[0] for x in rows[start:len(rows) if end == -1 else end+1]]
    def zcard(self,key):return len(self.zsets.get(key,{}))
    def zrem(self,key,*members):
        found=0
        for member in members:
            if member in self.zsets.get(key,{}):
                del self.zsets[key][member];found+=1
        return found
    def zrangebyscore(self,key,lower,upper,start=0,num=None):
        low=float('-inf') if lower=='-inf' else float(lower)
        high=float('inf') if upper=='+inf' else float(upper)
        rows=sorted(self.zsets.get(key,{}).items(),key=lambda x:x[1])
        matched=[k for k,v in rows if low<=v<=high]
        return matched[start:start+num if num is not None else None]

def makejob(step,index):
    return PromptJob(
        message_id=f"id-{step}",task_id="task",step_id=step,
        prompt="Internal work",project_id="default",context="",attempt=0,
        metadata={"original_user_prompt":"Exact original user request"},
        created_at=0,chain_id="chain",previous_prompt_id="",next_prompt_id="",
        chain_index=index,request_type="step",recovery_attempted=False,
        original_user_prompt="Exact original user request")
r=FakeRedis()
q=RedisPromptQueue.__new__(RedisPromptQueue)
q.r=r;q.stream="work";q.group="grp";q.durable=SimpleNamespace(
    task_status=lambda _:"running",is_task_tree_suppressed=lambda _:False)
q.retry_stream="retry";q.escalation_stream="escalation";q.dead_letter_stream="dead"
jobs=[makejob("step-01",0),makejob("step-02",1)]
ids=[q.enqueue(x) for x in jobs]
q.yield_async_job(ids[0],jobs[0],"Checkpoint containing original result",
                  next_check_at=500,step_rounds=1,task_rounds=1)
assert not r.streams['work']
assert q.async_delayed_count()==2
assert q.promote_due_async(now=499)==0
assert q.promote_due_async(now=500)==2
assert q.async_delayed_count()==0 and len(r.streams['work'])==2
rows=[q._fields_to_job(*x) for x in r.streams['work']]
assert [x.step_id for x in rows]==["step-01","step-02"]
assert rows[0].metadata["original_user_prompt"]=="Exact original user request"
assert rows[0].metadata["last_yield_reason"]=="n1_async_pending"
print("PASS entire chain parked until due; normal work remains separate; original prompt preserved")
r=FakeRedis();q.r=r
ids=[q.enqueue(x) for x in jobs]
q.yield_async_job(ids[0],jobs[0],"checkpoint",next_check_at=500,step_rounds=1,task_rounds=1)
counts=q.cleanup_task("task")
assert counts['async_delayed']==2
assert q.async_delayed_count()==0
print("PASS flush task clears its delayed async entries")
