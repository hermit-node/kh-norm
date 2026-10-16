# r11r4 scoped validation

Base: uploaded `Norm-0.55.10r11r2-Browser-Postgres-Unified-Portable-Source(1).zip`; previous changes retained from r11r3. No private system modifications.

## Offline results
- `python -m compileall -q core tools/celery_tool_worker.py tools/test_celery_n1_suppression_r11r4.py`: PASS
- `python tools/test_celery_n1_suppression_r11r4.py`: 6 PASS
- `python tools/test_recovery_suppress_celery_r11r3.py`: 7 PASS
- `python tools/test_permission_gate_r11r3.py`: 3 PASS
- `python tools/test_failed_task_resume.py`: PASS
- `python tools/test_n1_step_scope_shared_voice_0556.py`: 5 PASS (offline mock deliberately prints a simulated RuntimeError)

## Known outliers / limits
- Historical `test_celery_adaptive_r8.py` fails a path assertion **in the supplied r11r2 baseline too**; it was not changed. Its old hard-timeout assertion also assumes the former `status=unresolved` rather than explicit `status=timed_out`.
- Historical Celery, Redis and psycopg integration tests could not import production dependencies in this Linux sandbox; they were not counted as passing.
- No live Windows Celery solo worker was killed or restarted. Verify task PID attribution and instant response under concurrent suppress/resume; test queued work plus a truly running read, then one unrelated worker's job, before using the change on destructive tasks.
- N1 may stop observation at 15 minutes, but this **does not kill the tool**; only user suppression requests actual task-scoped cancellation. N1 stop leaves the outcome unresolved and forbids re-dispatch automatically.
- Existing `read_file` chunked execution and all mutations remain on their synchronous N1 gate path. This version does not implement cross-step independent Celery receipt delivery.
