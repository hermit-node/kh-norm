"""Offline Celery ownership and N1 non-response regressions; no workers launched."""
import json
import os
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from test_recovery_suppress_celery_r11r3 import method


class Store:
    def __init__(self):
        self.rows = {}
        self.events = []
    def scan_iter(self, match, count=50):
        prefix = match[:-1]
        return [x for x in self.rows if str(x).startswith(prefix)]
    def get(self, key):
        return self.rows.get(key)
    def set(self, key, value, ex=None, nx=False):
        self.rows[key] = value.encode() if isinstance(value, str) else value
        self.events.append(("set", key))
        return True
    def exists(self, key):
        return key in self.rows
    def close(self):
        pass


class TestCeleryScopedSuppression(unittest.TestCase):
    def test_one_task_is_fenced_without_touching_other_task(self):
        rel = "core/norm_runtime/celery_tool_executor.py"
        r = Store()
        for id_, task, pid in (("00000000-0000-0000-0000-000000000001", "stop", 41),
                               ("00000000-0000-0000-0000-000000000002", "keep", 42)):
            r.rows[f"norm:async:v1:approved:{id_}"] = json.dumps({"approved":{"id":id_, "task_id":task}}).encode()
            r.rows[f"norm:async:v1:execution:{id_}"] = json.dumps({"pid":pid,"state":"executing"}).encode()
        fake_app = types.SimpleNamespace(
            control=types.SimpleNamespace(revoke=lambda jobid, terminate=False: r.events.append(("revoke",jobid))),
            AsyncResult=lambda jobid: types.SimpleNamespace(ready=lambda:False))
        stopped=[]
        stop_func=lambda root,pid,jobid,redis,prefix: stopped.append((pid,jobid)) or True
        shutdown_mod=types.SimpleNamespace(_owned_workers=lambda root:[{"pid":41},{"pid":42}],stop_task_celery_worker=stop_func)
        import sys
        with patch.dict(sys.modules, {"norm_runtime.celery_shutdown":shutdown_mod}):
            # Global top-level function (not a class method).
            import ast
            tree=ast.parse((Path(__file__).resolve().parents[1]/rel).read_text())
            f=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=="cancel_task_dispatches")
            ns={"Path":Path,"root":lambda:Path("C:/Norm"),"redis":lambda:r,
                "CFG":{"prefix":"norm:async:v1"},"app":fake_app,
                "key":lambda jobid:"norm:async:v1:approved:"+jobid,
                "cancellation_key":lambda jobid:"norm:async:v1:cancelled:"+jobid,
                "json":json,"uuid":__import__("uuid"),"os":types.SimpleNamespace(name="posix"),
                "logging":types.SimpleNamespace(info=lambda *a,**k:None,warning=lambda *a,**k:None)}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[f],type_ignores=[])),rel,"exec"),ns)
            report=ns["cancel_task_dispatches"](Store(),["stop"])
        self.assertEqual(report["found"],1)
        self.assertEqual(report["cancelled"],1)
        self.assertEqual(report["workers_stopped"],1)
        self.assertEqual(stopped[0][0],41)
        self.assertIn("norm:async:v1:cancelled:00000000-0000-0000-0000-000000000001",r.rows)
        self.assertNotIn("norm:async:v1:cancelled:00000000-0000-0000-0000-000000000002",r.rows)
        self.assertEqual(report["errors"],[])

    def test_hardstop_refuses_unverified_dispatch_or_pid(self):
        fn=__import__("ast").parse((Path(__file__).resolve().parents[1]/"core/norm_runtime/celery_shutdown.py").read_text())
        f=next(x for x in fn.body if isinstance(x,__import__("ast").FunctionDef) and x.name=="stop_task_celery_worker")
        killed=[]
        ns={"os":types.SimpleNamespace(name="nt"),"Path":Path,
            "_owned_workers":lambda root:[{"pid":41}],
            "subprocess":None}
        # Avoid any OS subprocess: the worker must be proved current BEFORE kill.
        ns["subprocess"]=types.SimpleNamespace(run=lambda *a,**kw:killed.append(a) or types.SimpleNamespace(returncode=0), CREATE_NO_WINDOW=0)
        ns["logging"]=types.SimpleNamespace(exception=lambda *a,**kw:None)
        exec(compile(__import__("ast").fix_missing_locations(__import__("ast").Module(body=[f],type_ignores=[])),"stop_test","exec"),ns)
        store=Store()
        self.assertFalse(ns["stop_task_celery_worker"](Path("/tmp"),41,"job1",store,"p"))
        store.rows["p:active:41"]=b"different_job"
        self.assertFalse(ns["stop_task_celery_worker"](Path("/tmp"),41,"job1",store,"p"))
        self.assertEqual(killed,[])

    def test_n1_receives_pending_non_response_at_fifteen_minutes(self):
        seen=[]
        gate=types.SimpleNamespace(estimate_async_next_check=lambda **kw: seen.append(kw) or {
            "decision":"stop","check_after_seconds":180,"reason":"worker never started"})
        fn=method("core/norm_runtime/async_n1_delivery.py","N1AsyncBridge","_estimate_next",
                  {"HARD_DEADLINE_SECONDS":3600,"logging":types.SimpleNamespace(exception=lambda *a,**kw:None)})
        owner=types.SimpleNamespace(broker=types.SimpleNamespace(inspect=lambda jobid:{"state":"PENDING","execution":"submitted"}),
                                    owner=types.SimpleNamespace(n1_gatekeeper=gate),
                                    job=types.SimpleNamespace(task_id="a",step_id="b"))
        item={"submitted_at":1000,"id":"dispatch","name":"get_file_info","arguments":{}}
        due=fn(owner,item,1900)
        self.assertEqual(due,1900)
        self.assertIn("n1_stop_reason",item)
        self.assertEqual(seen[0]["elapsed_seconds"],900)

if __name__=="__main__":unittest.main(verbosity=2)
