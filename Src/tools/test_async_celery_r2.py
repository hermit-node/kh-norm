"""Regression checks for asynchronous N1 tool delivery. No live Redis mutations."""
from __future__ import annotations
import json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"core"))
from norm_runtime.n1_gatekeeper import GateOutcome
from norm_runtime.async_n1_delivery import N1AsyncBridge
import norm_runtime.celery_tool_executor as celery_transport

class MemoryStore:
    def __init__(self):self.data={}
    def get(self,k):return self.data.get(k)
    def set(self,k,v,**kwargs):self.data[k]=v;return True
    def delete(self,k):return self.data.pop(k,None)

class FakeBroker:
    def __init__(self):
        self.jobs={}
        self.calls=[]
    def available(self):return True
    def submit(self,task_id,step_id,name,arguments):
        dispatch="01234567-1234-4123-a123-123456789abc"
        self.jobs[dispatch]=None
        self.calls.append((task_id,step_id,name,arguments))
        return dispatch
    def poll(self,ident):return self.jobs[ident]

class FakeN1:
    def __init__(self):self.records=[]
    def information_tool(self,name):
        return name in {"postgres_query", "postgres_pool"}
    def after_tool(self,**kwargs):
        self.records.append(kwargs)
        return {"recorded":True,"executed":True}

class FakeOwner:
    def __init__(self):
        self.queue=type("Queue",(),{"r":MemoryStore()})()
        self.n1_gatekeeper=FakeN1()
        self.saved=[]
        self._drain=type("Drain",(),{"is_set":lambda self:False})()
    def _persist_evidence_now(self,job,rows):self.saved.extend(rows)
    def _suppression_requested(self,task_id):return False

class FakeJob:
    task_id="same-original-task"
    step_id="step-07"

class DeliveryTests(unittest.TestCase):
    def test_async_dispatch_dedup_and_deliver(self):
        owner=FakeOwner()
        broker=FakeBroker()
        with patch.object(celery_transport,"Broker",return_value=broker):
            with patch.dict(celery_transport.CFG,{"enabled":True}):
                bridge=N1AsyncBridge(owner,FakeJob(),tools=object())
                outcome=GateOutcome(True,{"query":"SELECT 1"},
                    target="norm",need="one",scope="norm")
                result=bridge.receipt("postgres_query",{"query":"SELECT 1"},outcome)
                self.assertEqual(result["status"],"pending")
                duplicate=bridge.receipt("postgres_query",{"query":"SELECT 1"},outcome)
                self.assertTrue(duplicate["deduplicated"])
                self.assertEqual(len(broker.calls),1)
                self.assertEqual(len(bridge.pending),1)
                restored=N1AsyncBridge(owner,FakeJob(),tools=object())
                self.assertEqual(len(restored.pending),1)
                broker.jobs[result["dispatch_id"]]={"ok":True,"rows":[{"one":1}]}
                messages=[]
                evidence=[]
                self.assertEqual(bridge.deliver(messages,evidence),1)
                self.assertEqual(len(owner.n1_gatekeeper.records),1)
                self.assertEqual(owner.n1_gatekeeper.records[0]["task_id"],FakeJob.task_id)
                self.assertEqual(evidence[0]["result"]["rows"][0]["one"],1)
                self.assertFalse(bridge.pending)
                self.assertFalse(owner.queue.r.data)
                self.assertEqual(len(messages),1)

    def test_mutating_tool_uses_existing_synchronous_path(self):
        owner=FakeOwner()
        broker=FakeBroker()
        with patch.object(celery_transport,"Broker",return_value=broker):
            with patch.dict(celery_transport.CFG,{"enabled":True}):
                bridge=N1AsyncBridge(owner,FakeJob(),tools=object())
                self.assertIsNone(bridge.receipt("postgres_execute",
                    {"action":"update"},GateOutcome(True,{"action":"update"})))
                self.assertEqual(broker.calls,[])

    def test_completed_result_is_not_mistaken_for_cached_replay(self):
        owner=FakeOwner()
        broker=FakeBroker()
        with patch.object(celery_transport,"Broker",return_value=broker):
            with patch.dict(celery_transport.CFG,{"enabled":True}):
                bridge=N1AsyncBridge(owner,FakeJob(),tools=object())
                result=bridge.receipt("postgres_pool",{},GateOutcome(True,{},target="norm",need="health"))
                self.assertFalse(owner.n1_gatekeeper.records)
                broker.jobs[result["dispatch_id"]]={"ok":False,"error":"expected failure"}
                bridge.deliver([],[])
                self.assertFalse(owner.n1_gatekeeper.records[0]["result"]["ok"])

if __name__=="__main__":unittest.main(verbosity=2)