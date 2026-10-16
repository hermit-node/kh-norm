"""r9 baseline invariants on the r7 parent-first recovery pipeline.

The original r9 fixed five-stage recovery expectation was intentionally
replaced by the parent-ledger reconciliation and N1-approved remaining steps.
Keep its original parent-retention and operator prompt guarantees.
"""
from types import SimpleNamespace
from norm_runtime.prompt_worker import PromptWorker
from test_redis_pre_recovery_r7 import w, j, child, PATH, PROMPT

assert w.durable.children[child].original_request == PROMPT
assert all(x.metadata["original_user_prompt"] == PROMPT for x in w.queue.saved)
assert len(w.queue.saved) == 2  # one remaining action + a conclusion
assert PATH in w.queue.saved[0].prompt
assert len(w.durable.children) == 1
assert "ORIGINAL USER PROMPT: REFERENCE ONLY" in (
    PromptWorker._tool_slice_initial_messages(w.queue.saved[0])[1]["content"]
)
print("PASS r9 source lineage, original prompt, one successor, parent-approved remaining work")

# A PASSED_TO parent is an archive, NOT a cleanup target: no soft deletion,
# model-buffer removal, SOS deletion, or live Redis history purge.
cleaned=[]
w4=PromptWorker.__new__(PromptWorker)
w4.durable=SimpleNamespace(terminal_summary_verified=lambda t: True,
                           task_status=lambda t: 'passed_to')
w4.queue=SimpleNamespace(cleanup_task=lambda task: cleaned.append(task))
w4.live=SimpleNamespace(cleanup=lambda task: (_ for _ in ()).throw(AssertionError('live evidence erased')))
w4._ensure_effectiveness_note=lambda task: (_ for _ in ()).throw(AssertionError('unsupervised summary generated'))
w4._soft_delete_task_assets=lambda *a,**k: (_ for _ in ()).throw(AssertionError('recovery artifacts deleted'))
w4._clear_processed_sos=lambda task: (_ for _ in ()).throw(AssertionError('SOS erased'))
w4._clear_terminal_redis('orig-task')
assert cleaned == ['orig-task']
print('PASS passed_to retains old Redis history, SOS, artifacts and checkpoints')
