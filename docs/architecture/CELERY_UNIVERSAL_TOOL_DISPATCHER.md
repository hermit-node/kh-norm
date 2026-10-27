# Norm design note: one Celery dispatcher for many hot-swappable tools

**Status:** Architecture proposal / research note — **not implemented**  
**Recorded:** 2026-10-27  
**Scope:** Tool-execution backend only. Does not replace Norm's agent roles, approvals, or deterministic HTTP routing.

## 1. The problem we were actually trying to solve

Norm must support many tools and hot-swappable plugins without requiring a separately launched Celery worker for each function. The earlier concern was that a file reader, database action, GUI action, and future plugin would each demand a distinct worker process. **That is not how Celery works.**

A Celery worker hosts a task registry. One worker can execute many registered task *types*. The number of workers controls capacity and isolation, not the number of functions exposed. A worker configured for concurrency 1 can execute one job at a time while still supporting hundreds of distinct tool names.

There are two workable models:

1. **Register every function as a Celery task.** Simple for static Python services, but adding/changing tasks usually requires the running worker to import updated code or restart.
2. **Register one stable task that dynamically dispatches to an approved plugin registry.** This better matches Norm's evolving plugin catalog and keeps the Celery contract stable.

Option 2 is the subject of this note. It does **not** imply that Celery itself dynamically reloads Python modules: the runtime plugin registry must implement safe refresh/versioning.

## 2. Preserve Norm's existing responsibility boundaries

Norm's N1/N2/N3/N4 model should stay authoritative:

- **N4:** deterministic intake, transport, queue/routing, and normalized messages. It does not grant tool permissions.
- **N1:** authorizes each concrete tool action; approves or denies with a reason; validates completion and makes final decisions.
- **N2:** proposes actions and manages its assigned planning/decomposition work, but never directly executes an unapproved tool call.
- **N3:** performs tightly scoped micro-actions only within N1-approved execution boundaries. If N3 is used as the tool micro-runner, Celery is its execution transport, **not** a replacement for its role.
- **Celery:** optional backend for executing an already approved action; it is not a new planner, security authority, or user-facing router.
- **Valkey/Redis:** broker holding execution messages; not an approval authority or permanent audit log.

Suggested flow:

```text
User -> N4 -> N1 intake / plan approval
                 |
                 v
             N2 proposes concrete tool action
                 |
                 v
             N1 validates exact operation + arguments
                 | approval recorded in authoritative store
                 v
             Enqueue execute_approved_tool(approval_id)
                 |
                 v
             One Celery worker -> guarded dispatcher -> allowlisted plugin
                 |
                 v
             Persist result / outcome -> N1 evaluates -> N2 continues as directed
```

N1 may execute some operations directly today. Celery would be an **alternative execution adapter** behind the existing approval gate, introduced only when useful. N4's current deterministic task queue need not be replaced.

## 3. A single universal Celery task

Only the adapter is registered with Celery. The tool catalog is a separate, dynamically resolved registry:

```python
# Illustrative architecture, NOT production-ready code.
from celery import Celery

celery_app = Celery("norm_tools", broker=BROKER_URL)

@celery_app.task(name="norm.execute_approved_tool", bind=True)
def execute_approved_tool(self, approval_id: str):
    # Authority resides in N1's approval record, not in the queue message.
    approval = approvals.claim_valid_unconsumed(approval_id)
    if not approval:
        return {"status": "rejected", "reason": "approval invalid/expired/claimed"}

    # Snapshot resolution must be pinned to the approved version/digest.
    tool = plugin_registry.resolve(
        approval.tool_name, version=approval.plugin_version
    )
    policy.validate(approval, tool)
    outcome = guarded_executor.invoke(
        tool, approval.arguments, timeout=approval.timeout_seconds,
        idempotency_key=approval.step_uuid
    )
    results.persist(approval, outcome)
    return {"status": outcome.status, "result_id": outcome.result_id}
```

The pseudocode deliberately sends **only an approval reference** through the broker. It must not be possible for an N2/N3 message or another broker client to invent an approval ID and gain capabilities. The approval record must bind the full canonical operation, parameters, tool identity, plugin version, task/step UUID, authorization scope, expiration, and any applicable execution limit.

The dispatcher must not accept arbitrary Python module paths, `eval`, `getattr` on uncontrolled objects, or unreviewed callable names from the queue. Every callable comes from the installed, allowlisted registry.

Illustrative approved records (not broker-submitted commands):

```json
{
  "approval_id": "example-approved-action",
  "task_uuid": "example-task",
  "step_uuid": "example-step",
  "tool_name": "file_read",
  "plugin_version": "sha256:<approved-package-digest>",
  "arguments": {"path": "<validated absolute path>"},
  "expires_at": "<UTC time>",
  "timeout_seconds": 20
}
```

## 4. Worker count versus task count

A single worker with `--pool=solo --concurrency=1` is conceptually a single serial executor on supported platforms. Other pools, including prefork, may spawn child processes, so **one Celery worker instance** is not necessarily **one operating-system process**. A single worker can process different registered tasks or one dispatcher routing to many tools.

- **One serial worker:** easiest ordering and resource-use model, but one slow tool blocks every subsequent tool action on that queue.
- **One worker, multiple execution slots:** enables parallel independent calls; ordering and shared-state protection become essential.
- **Several workers, same universal task:** scaling option, *still not* one worker per function.
- **Several queues/pools:** reserved for isolation needs (e.g. GUI automation must be serialized; file reads may run in parallel). Split by execution requirements, not by individual function name.

Celery is not a Windows-first runtime. A Linux container/WSL worker or other supported execution host may be preferable to assuming Celery runs natively in the KHzz Python process. Windows-bound operations would need a properly authorized adapter on the target machine. Do not move privileged GUI or filesystem calls into a remote container merely because the broker is remote.

## 5. Hot-swapping plugins is a separate problem

With standard Celery task registration, workers generally learn task names at startup. With a universal task, the Celery task remains stable and resolves plugins at execution time. The following **must** be implemented explicitly:

1. **Install/validate:** plugin package is vetted and registered before N1 may approve it.
2. **Version pin:** N1 approval binds a plugin version or digest; queued work cannot silently switch implementations when a plugin changes.
3. **Atomic registry swap:** new calls see the new validated registry snapshot; already-running calls finish using the old one (or are safely cancelled).
4. **Safe unload:** do not remove code or dependencies still needed by in-flight calls.
5. **Isolation:** decide whether plugins are imported into the worker process or executed in a bounded subprocess. In-process import is simpler, but hot-reloaded Python code, globals and dependencies are hard to unload reliably. Subprocesses provide cleaner timeouts and version isolation.
6. **Capabilities:** each plugin receives only the file paths, commands, services and privileges that policy authorizes. Refreshing a registry must not enlarge permissions.

**Practical preference:** keep the Celery worker as a small, stable dispatcher and run version-pinned plugin calls through a managed subprocess where hot swap or strong cancellation is required. One dispatcher worker can supervise many *types* of plugin actions without maintaining a permanent worker for every plugin.

## 6. Security: N1 approval is not a queue convention

A generic dispatcher is powerful; permission checks cannot be a comment above `registry.get()`.

Minimum rules:

- N1 creates a durable approval record only after validating **tool name + exact normalized arguments + task/step IDs + authorized path/capability scope + plugin version**.
- Broker messages carry a reference, not authority. Executor atomically claims/validates the approval before acting.
- Recheck policy at execution time. For filesystem paths, resolve relative paths to absolute paths and enforce the configured independent workspace/output/soft-delete rules and read/write/delete allowlists. Overlapping configured directories remain allowed.
- Fail closed on unknown plugins, stale versions, changed arguments, expired approvals, reused IDs, malformed payloads, and unsupported operations.
- N2 can revise after a denial but cannot self-approve. Any child action that needs a tool must pass the same N1 gate.
- Log approvals, denials, claimed executions, retries, timeouts and results. Secrets and sensitive data should not go into generic task logs.
- Plugin updates should not bypass the previously agreed ability to hot-swap plugins or create a new privileged direct-execution path.

This note specifies a potential architecture; it does **not** claim that the brief illustrative code enforces these conditions by itself.

## 7. The hard operational issue: at-least-once delivery

Celery commonly offers **at-least-once**, not exactly-once, execution. Consider a file-edit task: the process might write the file and crash before acknowledging the message. A retry could write it twice.

Consequences:

- Use `task_uuid/step_uuid` plus a stable operation ID as deduplication keys.
- Persist transitions such as `approved -> claimed -> running -> succeeded/failed/unknown` atomically where possible.
- Make read-only operations naturally repeatable; make mutations idempotent or guarded by preconditions (expected content/version/etag).
- Handle the ambiguous `unknown` case conservatively. **Never blindly replay destructive writes** solely because a worker disappeared.
- Use broker acknowledgments/retries according to tool semantics; do not globally enable automatic retries for every plugin.
- Store durable task outcomes and audit data in Norm's established persistent layer (e.g. PostgreSQL). Do not mistake a Redis broker message or volatile Celery result backend for a permanent record.
- Define timeout, cancellation and recovery behavior for tasks already performing side effects. A time limit does not undo an action.

A separate micro-runner process can be terminated without forcing every subsequent tool invocation to share a potentially contaminated interpreter.

## 8. Routing and performance considerations

- **N4 may already provide sufficient deterministic queueing.** Celery is useful only if its execution supervision, acknowledgment, retry machinery or cross-process throughput simplifies Norm overall.
- Running *both* N4 and Celery as independent planners/queues risks duplicate ownership and stale tasks. Give N4 message/task orchestration and Celery only the narrower approved-tool work queue, if adopted.
- Set bounded queue lengths, appropriate prefetch (for fair dispatch), per-resource serialization and an explicit single-instance rule for GUI actions.
- Plugins performing lengthy local work should not monopolize N1's request path. Have Celery return a result reference and let N1 verify it through the existing message model.
- Keep transport errors, plugin failures and N1 denials distinct. A failed invocation should not generate an uncontrolled retry loop.
- Prefer few execution workers and a simple broker topology; avoid per-tool services unless isolation/special hardware justifies them.

## 9. Decision table

| Option | Advantage | Cost / limitation |
| --- | --- | --- |
| Existing N4 + current gated executor | Least architectural churn | Must maintain execution scheduling/retries internally |
| Celery task registered separately for every tool | Straightforward static Python tasks | Worker imports/restart for new task names; weaker fit for dynamic plugins |
| **One Celery universal dispatcher** | One stable task interface for many plugins; supports queueing and scaling | Requires custom approval binding, registry, reload, idempotency and supervision |
| One worker per plugin | Easy coarse isolation | Unnecessary process overhead; harder lifecycle management |
| Universal dispatcher + subprocess execution | Hot-swap/version/timeout isolation | More IPC and lifecycle complexity |

## 10. Small, falsifiable prototype before redesign

**Do not replace working N4/N1/N2 code based on this note alone.** In a separate lab:

1. Register one Celery task, `norm.execute_approved_tool`, with concurrency 1.
2. Put two harmless plugins in a registry: `echo` and `read_test_fixture`.
3. Have N1 issue fixed, expiring approval records; N2's proposals alone must be rejected.
4. Enqueue ten mixed approved calls through the **same worker**; confirm sequential handling and correct return-to-N1 correlation.
5. Add a third plugin while the worker stays alive; verify explicit registry refresh and pinning rather than assuming Python hot reload.
6. Change a plugin version with an older task in flight; show that the older task uses its approved version or rejects safely.
7. Simulate worker crashes before/after a side effect; prove the guard prevents unsafe duplicate mutation.
8. Compare latency, recovery complexity, Redis usage and lines of integration code against current N4 + executor. Keep only the approach that demonstrably improves those measurements.

### Conclusion

The earlier objection — "Norm would need a different Celery worker for every function" — was based on an incorrect premise. **One worker can dispatch many tool functions**, either through Celery's static task registry or, more appropriately for Norm, a single universal task backed by a versioned plugin registry.

That removes one objection to Celery, but does **not** by itself justify replacing N4. The main engineering work is safe dynamic loading, approval integrity, replay protection, side-effect recovery and runtime isolation — not worker count.
