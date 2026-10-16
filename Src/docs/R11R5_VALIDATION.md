# r11r5 — detached Celery worker safety and suppressed receipt recovery

**Candidate:** source only; not installed on KHzz. Based directly on the uploaded r11r4 release.

## Safety behavior

- Norm service renews an expiring Redis lease (DB4) at most every 10 seconds, after the chat/control runtime becomes ready. Each service generation has a distinct token. Workers launched before service readiness have a 300-second bootstrap allowance.
- A separate daemon thread in each Celery solo process checks the lease every five seconds. If the service lease stops arriving, it self-terminates after a further 180 seconds. The lease itself expires 75 seconds after the last service renewal: expect watchdog exit within roughly **75 + 180 + 5 seconds** of a crashed main loop, assuming worker scheduling and Redis remain functional.
- Workers refuse to execute queued approvals when the supervising Norm lease is absent or its generation has changed. The watchdog terminates a worker if its active approval belongs to an older service generation. Old-format approvals (without a generation token) retain best-effort compatibility.
- The per-dispatch absolute age limit is 3,600 seconds measured from N1 submission, regardless of later N1 poll decisions. New approvals store `submitted_at`; old ones derive it from their 24-hour signed expiry. Queued expired jobs return `timed_out` without executing.
- Watchdog death is intentionally process-scoped (`os._exit`) because Celery's solo pool can be blocked executing a tool. This ends the process and its PostgreSQL connections; uncommitted DB transactions roll back through connection closure, but completed external side effects cannot be undone.
- Prior to hard exit, an active job is annotated as `orphaned` or `timed_out` in Redis when available. `Broker.poll` returns that observation to N1 as unresolved and fences automatic duplicate dispatch. If Redis itself is unavailable, the observation might not persist; final outcome remains unknown.
- Configured worker_count is 4, using the original identity-locked launcher. No new daemon, Redis DB, PostgreSQL pool, plugin privilege, or service restart introduced.

## Suppression/resume continuity

- Pending Celery receipts now accompany the existing PostgreSQL-bound task suppression snapshot. They are restored atomically with its Redis queue entries, without counting receipt restoration as a queued job.
- Receipts that were in flight when suppressed are marked explicitly. On resume after the ordinary Redis TTL expires, N1 can receive a confirmed result (when available) or an unresolved suppression/cancellation observation under the original dispatch ID, **not automatically rerun that tool**.
- This does not implement cross-step independent execution or remove the existing queue's dependence ordering.

## Focused offline verification

- `tools/test_celery_watchdog_r11r5.py`: 8 passed — lease startup/orphan timing, generation and bounded age predicates, unresolved result forwarding, safe launcher shape, delayed receipt restoration and N1 receipt delivery.
- `tools/test_celery_n1_suppression_r11r4.py`: 6 passed.
- `tools/test_celery_suppress_n1_r11r3.py`: 3 passed.
- `tools/test_recovery_suppress_celery_r11r3.py`: 7 passed.
- `tools/test_permission_gate_r11r3.py`: 3 passed.
- Python compilation: passed for modified sources and tests. Source ZIP SHA-256 manifest regenerated and verified separately.

**Total: 27 focused cases.** These use isolated fakes/source extraction; they do not establish live Windows process behavior or real Redis/Celery/PostgreSQL reliability.

## Release gates (not yet run)

1. On an expendable Windows task, verify worker count remains exactly four across launch/relaunch; no duplicate slots.
2. Start a deliberately blocked informational Celery tool, forcibly crash `norm.exe` only, verify worker PID exits within the stated grace and marker is read by N1 after restart.
3. Verify 1-hour dispatch deadline on a controlled long-running tool. Check no silent retry and no leaked PostgreSQL sessions.
4. Suppress a task tree while an async read is in progress. Confirm only the matching process is terminated, siblings/unrelated tools are not killed, and resumption reports cancellation rather than reexecution.
5. Retain a suppressed snapshot beyond two hours (using controlled TTL expiry instead of actually waiting); resume and verify original dispatch ID and evidence persist.
6. Verify that a clean shutdown still stops all Norm-owned workers and does not stop the shared Redis/PostgreSQL services.

**Known edge case:** A worker thread cannot enforce a hard wall-clock limit if a native extension holds the Python GIL indefinitely, or if the OS stops scheduling the entire process. A stronger OS-enforced watchdog would require explicit approval for a supervisor process or Windows Job Object integration; not added to this minimal patch.
