# r11r3 source candidate — focused regression log

Base: `Norm-0.55.10r11r2-Browser-Postgres-Unified-Portable-Source(1).zip`, with modified source files limited to Norm's task control, queue, N1, and Celery path.

Offline evidence:
- `python -m compileall -q core tools/celery_tool_worker.py`: PASS.
- `python tools/test_recovery_suppress_celery_r11r3.py`: 7 tests PASS.
- `python tools/test_permission_gate_r11r3.py`: 3 tests PASS.
- `python tools/test_failed_task_resume.py`: PASS.
- `python tools/test_n1_step_scope_shared_voice_0556.py`: 5 tests PASS (prints an intentional test-double exception from an offline N1 call).

Some original integration suites import `psycopg`, `redis`, and `celery` and cannot run in this offline Linux container where they are not installed. No production service/state was touched. Source-only; installer integration, Redis transactions under concurrent live workers, windows process lifecycles, and task-chat UI must be verified before promotion.

Minimal installation smoke scenarios:
1. Start a disposable 3-step job and have N2 call `request_user_approval` in step 2; confirm step 3 does not dispatch and that the original chat request returns a question.
2. Inject yes with `/inject-context --task <root_task_id>`; verify the same step resumes once, and only approved tools run. Repeat with denial in a separate disposable task.
3. Suppress a pending Celery job and an approval-wait task, restore each independently using `/resume-task`, and ensure no queue job duplication.
4. Observe a Celery dispatch through submitted -> bootstrapping -> executing -> completed/failed; compare bootstrap_seconds on first and subsequent jobs.
5. Force N1 scope reconsideration from a recovery child and verify it cannot touch a different root or bypass permission.

Outliers:
- A tool that continues running beyond the one-hour Celery observation deadline is still unresolved and must not be dispatched twice; there is no false claim of cancellation or automatic retry.
- Raw natural-language permission questions without native `request_user_approval` remain an unverified behavior; only the explicit native gate has deterministic pause/wake semantics.

## r11r4 targeted follow-up

Offline Python compileall and 3+4 new Celery/N1 regression tests passed, alongside seven recovery/resume tests, three approval tests and prior failed-task/N1 scope suites. No real Celery worker, Windows process termination, Redis race or installed Norm was exercised. The N1 response is a timeout/continued observation, never proof of Celery completion.
