from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from urllib import error, request
from urllib.parse import quote

from psycopg import sql

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout

from norm_gui_dispatch import GuiPromptDispatcher

from norm_gui_common import (
    acquire_windows_mutex,
    FALLBACK_LOG,
    ROOT,
    append_fallback_jsonl,
    endpoints,
    norm_process_running,
    start_norm_detached,
    load_runtime_config,
)
from norm_runtime.about import format_about
from runtime_bootstrap import build_postgres_pool


PROJECT_ID = "default"
_resume_command_lock = threading.Lock()


def submit_resume_command(ep: dict[str, str], dispatcher, command: str) -> None:
    """Send an explicit resume command directly to the existing handler."""
    parts = command.split()
    if len(parts) > 2:
        print("Usage: /resume-task [index|task-id|unique-prefix]")
        return
    if not _resume_command_lock.acquire(blocking=False):
        print("A resume command is already awaiting a response; no duplicate was sent.")
        return
    payload = {"message": " ".join(parts), "project_id": PROJECT_ID}
    try:
        thread_id = dispatcher.redis.get(dispatcher.thread_key)
        if thread_id:
            payload["thread_id"] = thread_id
    except Exception as exc:
        _resume_command_lock.release()
        print(f"Cannot read the conversation for the resume command: {exc}")
        return

    def send() -> None:
        try:
            result = post_json(ep["chat"], payload=payload)
            reply = str(result.get("reply") or "Resume command returned no reply.")
            dispatcher.publish_reply(reply, source="command")
            print(f"\n{reply}")
        except Exception as exc:
            print(f"Resume command outcome could not be confirmed: {exc}. Check /status before retrying.")
        finally:
            _resume_command_lock.release()

    try:
        threading.Thread(target=send, name="norm-resume-command", daemon=True).start()
    except Exception:
        _resume_command_lock.release()
        raise
    print("Resume command sent directly to Norm; results will appear here and in Norm Replies.")


class InputCancelled(Exception):
    """Return one level without sending a runtime control request."""


def input_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("escape")
    @bindings.add("c-c")
    def cancel(event):
        event.app.exit(exception=InputCancelled())

    return bindings


_input_session = None


def read_console_line(label: str) -> str:
    global _input_session
    if _input_session is None:
        _input_session = PromptSession(key_bindings=input_bindings())
        # Allow complete terminal sequences (e.g. arrow keys) before treating
        # an isolated Escape as Back. This also works over SSH/ConPTY.
        _input_session.app.ttimeoutlen = 0.2
        _input_session.app.timeoutlen = 0.3
    with patch_stdout():
        return _input_session.prompt(label)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def load_last_turn_from_postgres() -> tuple[str | None, str | None, str | None]:
    cfg = load_runtime_config(ROOT)
    pg = cfg["postgres"]
    schema = str(pg.get("schema", "norm_runtime"))
    query = sql.SQL(
        "SELECT m.role, m.content, mt.thread_id "
        "FROM {}.messages m "
        "JOIN {}.message_threads mt ON mt.message_id=m.message_id "
        "JOIN {}.threads t ON t.thread_id=mt.thread_id "
        "WHERE t.project_id=%s AND mt.is_primary AND m.role IN ('user','assistant') "
        "ORDER BY m.created_at DESC LIMIT 50"
    ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
    pool = build_postgres_pool(ROOT)
    try:
        with pool.connection("norm") as conn, conn.cursor() as cur:
            cur.execute(query, (PROJECT_ID,))
            rows = cur.fetchall()
    finally:
        # The prompt helper needs PostgreSQL only for one startup history read. Do
        # not leave psycopg-pool worker threads alive for the lifetime of the UI.
        pool.close()
    last_submission = next((row[1] for row in rows if row[0] == "user"), None)
    last_answer = next((row[1] for row in rows if row[0] == "assistant"), None)
    thread_id = rows[0][2] if rows else None
    return last_submission, last_answer, thread_id


def load_last_turn_from_fallback() -> tuple[str | None, str | None, str | None]:
    if not FALLBACK_LOG.is_file():
        return None, None, None
    lines = FALLBACK_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        return record.get("submission"), record.get("answer"), record.get("thread_id")
    return None, None, None


def load_last_turn() -> tuple[str | None, str | None, str | None, str]:
    try:
        submission, answer, thread_id = load_last_turn_from_postgres()
        if submission or answer:
            return submission, answer, thread_id, "PostgreSQL"
    except Exception:
        pass
    submission, answer, thread_id = load_last_turn_from_fallback()
    return submission, answer, thread_id, "local fallback" if (submission or answer) else "none"


def post_json(url: str, payload: dict | None = None, timeout: float | None = None) -> dict:
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                detail = str(parsed.get("error") or parsed.get("reason") or parsed.get("status") or "").strip()
            if not detail:
                detail = raw.strip()
        except Exception:
            detail = ""
        message = f"HTTP {exc.code}" + (f": {detail}" if detail else "")
        raise RuntimeError(message) from None


def get_json(url: str, timeout: float = 5) -> dict:
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def enrich_queue_with_runtime(ep: dict[str, str], items: list[dict]) -> tuple[list[dict], str]:
    """Join DB3 ingress rows to authoritative running-task status when possible."""
    try:
        busy = get_json(ep["busy"], timeout=3)
    except Exception:
        return [dict(item) for item in items], "unknown"
    phase = str(busy.get("phase") or "unknown")
    signals = busy.get("signals") if isinstance(busy.get("signals"), dict) else {}
    running = signals.get("running_tasks") if isinstance(signals.get("running_tasks"), list) else []
    # running_tasks is newest-first. Keep the most recently updated task for a
    # prompt (often an active child/recovery task) rather than letting an older
    # root task overwrite it in a dict comprehension.
    by_prompt: dict[str, dict] = {}
    for task in running:
        if not isinstance(task, dict):
            continue
        prompt_id = str(task.get("source_prompt_id") or "")
        if prompt_id and prompt_id not in by_prompt:
            by_prompt[prompt_id] = task
    enriched = []
    for item in items:
        row = dict(item)
        prompt_id = str(row.get("prompt_id") or "")
        task = by_prompt.get(prompt_id)
        if task is not None:
            row["runtime_task"] = task
            row["runtime_phase"] = phase
        elif row.get("state") in {"dispatching", "in-flight"}:
            # During the short prequeue window the durable task does not exist yet.
            # Report that phase explicitly rather than pretending the prompt preview
            # describes the work currently being executed.
            row["runtime_phase"] = phase
        enriched.append(row)
    return enriched, phase


def _chat_api_base(ep: dict[str, str]) -> str:
    chat = str(ep["chat"])
    suffix = "/api/chat"
    return chat[:-len(suffix)] if chat.endswith(suffix) else chat.rsplit("/", 1)[0]


def list_threads(ep: dict[str, str], project_id: str = PROJECT_ID, limit: int = 100) -> list[dict]:
    url = f"{_chat_api_base(ep)}/api/threads?project_id={quote(project_id)}&limit={max(1, min(int(limit), 200))}"
    payload = get_json(url, timeout=10)
    rows = payload.get("threads")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def create_named_thread(ep: dict[str, str], title: str, project_id: str = PROJECT_ID) -> dict:
    return post_json(
        f"{_chat_api_base(ep)}/api/threads/new",
        payload={"project_id": project_id, "title": title},
        timeout=10,
    )


def resolve_thread_selector(rows: list[dict], selector: str) -> tuple[dict | None, list[dict]]:
    selector = str(selector or "").strip()
    if not selector:
        return None, []
    folded = selector.casefold()
    exact_id = [row for row in rows if str(row.get("thread_id") or "").casefold() == folded]
    if len(exact_id) == 1:
        return exact_id[0], exact_id
    exact_title = [row for row in rows if str(row.get("title") or "").strip().casefold() == folded]
    if len(exact_title) == 1:
        return exact_title[0], exact_title
    id_prefix = [row for row in rows if str(row.get("thread_id") or "").casefold().startswith(folded)]
    if len(id_prefix) == 1:
        return id_prefix[0], id_prefix
    title_prefix = [row for row in rows if str(row.get("title") or "").strip().casefold().startswith(folded)]
    if len(title_prefix) == 1:
        return title_prefix[0], title_prefix
    candidates = exact_title or id_prefix or title_prefix
    return None, candidates


def print_thread_list(ep: dict[str, str], dispatcher) -> None:
    rows = list_threads(ep)
    current = dispatcher.redis.get(dispatcher.thread_key) or ""
    if not rows:
        print("No active conversation threads found.")
        return
    print("Conversation threads (newest first):")
    for index, row in enumerate(rows):
        thread_id = str(row.get("thread_id") or "")
        title = str(row.get("title") or "[untitled]")
        marker = "*" if thread_id == current else " "
        updated = str(row.get("updated_at") or "")
        print(f" {marker} [{index}] {title}  ({thread_id})  {updated}")


def health_ok(url: str) -> bool:
    try:
        return str(get_json(url, timeout=2).get("status", "")) == "ok"
    except Exception:
        return False


def ensure_norm_running(ep: dict[str, str]) -> None:
    if health_ok(ep["chat_health"]) and health_ok(ep["activity_health"]):
        return
    if norm_process_running():
        print("Existing norm.exe detected. Waiting up to 60 seconds for its APIs, then attaching...")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if health_ok(ep["chat_health"]) and health_ok(ep["activity_health"]):
                print("Existing Norm is online. Attached to the running instance.")
                return
            if not norm_process_running():
                raise RuntimeError("The existing norm.exe exited before its APIs became healthy.")
            time.sleep(0.5)
        raise RuntimeError(
            "norm.exe is still running but its APIs did not become healthy within 60 seconds; "
            "refusing to start a duplicate Norm instance."
        )
    print("Norm is not running. Starting core\\norm.exe...")
    start_norm_detached()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if health_ok(ep["chat_health"]) and health_ok(ep["activity_health"]):
            print("Norm is online.")
            return
        time.sleep(0.5)
    raise RuntimeError("Norm was started but did not become healthy within 30 seconds.")


def show_help() -> None:
    print("Norm GUI commands:")
    print("  help or /help       List current operator commands")
    print("  /about              Show Norm version, runtime, package, and plugin summary")
    print("  /status             Show runtime and queue state")
    print("  /status/busy        Show authoritative runtime busy state")
    print("  /network-map [--json]  Passive Tailscale inventory plus explicitly allowlisted probes")
    print("  /queue [N]          Show queued GUI prompts with stable snapshot indexes")
    print("  /queue-full         Show full prompts and live + parked/uncertain state")
    print("  /resume-queue [N]   Resume/rotate the GUI queue, optionally from snapshot index N")
    print("  /new [name]         Start a fresh conversation thread, optionally with a title")
    print("  /thread-list        List active conversation threads; * marks the current thread")
    print("  /thread-resume NAME Switch back to a thread by exact title, unique prefix, ID, or ID prefix")
    print("  /multi              Start multiline prompt entry; finish with ::send or cancel with ::cancel")
    print("  /inject-context [--task ID] TEXT  Save context for the current task tree without creating a new task")
    print("  /repeat-submission  Requeue the last submitted prompt verbatim")
    print("  /repeat-answer      Redisplay the last completed Norm answer verbatim")
    print("  /suppress-task      Park the active task tree, or oldest next queued task tree")
    print("  /resume-task [N]    List suppressed tasks, or resume an index/task-id/prefix directly")
    print("  /flush-suppressed   Permanently delete suppressed tasks and parked delivery records")
    print("  /delete-list        List reversible soft-deleted files")
    print("  /restore-delete ID  Restore one deletion ID; use all for every non-conflicting item")
    print("  /delete-files       Permanently purge reversible trash now")
    print("  /backup             Create a portable installer/source backup")
    print("  /backup full        Create a sensitive full backup with private state and PostgreSQL")
    print("  /memory-condense    Compact/replay-validate only the recent memory window (default 14 days)")
    print("  /memory-condense -deep  Run one bounded older-history compaction/validation pass without hierarchical merging")
    print("  /memory-condense -full  Sweep full history: sample 12 per 200 dated rows, then merge neighboring rows conservatively")
    print("  /stop-all           Finish the current step, then stop Norm/Ollama")
    print("  /stop-all now       Emergency snapshot to temp\\recovery\\SOS.md, then force-stop Norm/Ollama")
    print("  /shutdown           Request Norm's graceful shutdown and close this console")
    print("  /shutdown now       Immediately request Norm shutdown and close this console")


def run_backup(*, full: bool) -> None:
    script = ROOT / "tools" / "norm_backup.py"
    if not script.is_file():
        print(f"Backup helper is missing: {script}")
        return
    mode = "full" if full else "source"
    if full:
        print("Creating SENSITIVE full Norm backup (includes .ssh, secrets, workspace, plugins, and PostgreSQL; excludes .venv)...")
    else:
        print("Creating portable Norm source backup (no secrets, .ssh, PostgreSQL, or private workspace state)...")
    proc = subprocess.run([sys.executable, str(script), "--mode", mode], cwd=str(ROOT), check=False)
    if proc.returncode != 0:
        print(f"Backup failed with exit code {proc.returncode}.")


def graceful_shutdown(ep: dict[str, str]) -> None:
    print("\nRequesting graceful Norm shutdown...")
    try:
        result = post_json(ep["shutdown"], timeout=5)
        print(f"Shutdown accepted: {result.get('shutdown', 'graceful')}.")
    except Exception as exc:
        print(f"Could not request graceful shutdown: {exc}")
        print("Norm may already be stopped or unreachable.")


def read_multiline() -> str:
    print("Multiline mode. Type ::send to submit; Esc, Ctrl+C, or ::cancel to return to the prompt.")
    lines: list[str] = []
    while True:
        try:
            line = read_console_line("... ")
        except (InputCancelled, KeyboardInterrupt):
            return ""
        if line == "::send":
            return "\n".join(lines)
        if line == "::cancel":
            return ""
        lines.append(line)

def persistence_complete(result: dict) -> bool:
    return bool(result.get("user_message_id")) and bool(result.get("assistant_message_id"))


def backup_turn(message: str, reply: str, result: dict, reason: str) -> None:
    append_fallback_jsonl(
        {
            "timestamp": now_iso(),
            "reason": reason,
            "project_id": PROJECT_ID,
            "thread_id": result.get("primary_thread_id") or result.get("thread_id"),
            "task_id": result.get("task_id"),
            "user_message_id": result.get("user_message_id"),
            "assistant_message_id": result.get("assistant_message_id"),
            "submission": message,
            "answer": reply,
        }
    )


def probe(ep: dict[str, str]) -> int:
    print(f"Host: {ep['host']}")
    print(f"Chat: {ep['chat']}")
    print(f"Events: {ep['events']}")
    print(f"Shutdown: {ep['shutdown']}")
    print(f"norm.exe running: {norm_process_running()}")
    chat_ok = health_ok(ep["chat_health"])
    activity_ok = health_ok(ep["activity_health"])
    print(f"Chat health: {'ok' if chat_ok else 'offline'}")
    print(f"Activity health: {'ok' if activity_ok else 'offline'}")
    return 0 if chat_ok and activity_ok else 1


def load_console_queue_config() -> dict:
    cfg = load_runtime_config(ROOT)
    return dict(cfg.get("console_queue", {}))


def completed_turn(message: str, reply: str, result: dict) -> None:
    if not persistence_complete(result):
        backup_turn(message, reply, result, "PostgreSQL conversation persistence incomplete")
    task_id = str(result.get("task_id") or "n/a")
    print(f"\nQueued prompt completed task={task_id}; answer released to Norm Replies.")


def stop_all(ep: dict[str, str], immediate: bool) -> None:
    if immediate:
        helper = ROOT / "tools" / "norm_emergency_stop.py"
        print("Emergency stop: preserving current recovery state to temp\\recovery\\SOS.md, then force-stopping Norm/Ollama...")
        proc = subprocess.run([sys.executable, str(helper)], cwd=str(ROOT), check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"Emergency stop helper failed with exit code {proc.returncode}; Norm was not force-killed.")
        return
    result = post_json(ep["stop_all"], timeout=5)
    print(f"Stop-all accepted: {result.get('mode', 'after-step')}.")


def main() -> int:
    if not acquire_windows_mutex('NormGuiPrompt'):
        print("Norm Prompt is already running in another process. Close the old operator console/process, then run Run-Norm.bat again.")
        return 0
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        ep = endpoints()
    except Exception as exc:
        print(f"Cannot initialize Norm GUI: {exc}")
        return 1
    if "--probe" in sys.argv:
        return probe(ep)
    try:
        ensure_norm_running(ep)
    except Exception as exc:
        print(f"Cannot start Norm GUI: {exc}")
        return 1

    last_submission, last_answer, thread_id, history_source = load_last_turn()
    dispatcher = GuiPromptDispatcher(ep, load_console_queue_config(), on_result=completed_turn, source_name="ssh-gui")
    dispatcher.seed_history(last_submission, last_answer, thread_id)
    dispatcher.start()
    print("=== Norm Prompt Console ===")
    print(f"Connected target: {ep['host']}  project={PROJECT_ID}")
    print("Normal prompts are durably queued in Redis and never block this input window.")
    print("Type help for commands. Esc returns to the menu/terminal; /stop-all finishes the current step and shuts down.")
    if history_source != "none":
        print(f"Recovered the latest GUI turn from {history_source} history.")
    try:
        while True:
            prompt = read_console_line("\nYou> ")
            text = prompt.strip()
            if not text:
                continue
            lowered = " ".join(text.lower().split())
            if lowered in {"help", "/help"}:
                show_help()
                continue
            if lowered == "/about":
                try:
                    print(format_about(ROOT))
                except Exception as exc:
                    print(f"Could not build about information: {exc}")
                continue
            if lowered == "/inject-context" or lowered.startswith("/inject-context "):
                body = text[len("/inject-context"):].strip()
                task_id = None
                if body.startswith("--task "):
                    parts = body.split(maxsplit=2)
                    task_id = parts[1] if len(parts) > 1 else None
                    body = parts[2] if len(parts) > 2 else ""
                if not body:
                    body = read_multiline()
                if not body:
                    print("Context injection cancelled.")
                    continue
                payload = {"task_id": task_id, "content": body, "request_id": str(uuid.uuid4())}
                try:
                    result = post_json(ep["inject_context"], payload=payload, timeout=10)
                    print(f"Context {result['injection_id']} saved for {result['task_id']}; applies at the next model-call boundary. No new task created.")
                except Exception as exc:
                    print(f"Context injection could not be confirmed: {exc}. Request ID: {payload['request_id']}")
                continue
            if lowered == "/backup":
                run_backup(full=False)
                continue
            if lowered in {"/backup full", "/backup-zip"}:
                run_backup(full=True)
                continue
            if lowered in {"/status", "/status/busy"}:
                print(json.dumps(get_json(ep["busy"]), indent=2, ensure_ascii=False))
                queued, uncertain = dispatcher.queue_stats()
                if queued is None:
                    print(f"GUI Redis queue: unknown (group stats unavailable); {uncertain} parked/uncertain records (see /queue-full).")
                else:
                    print(f"GUI Redis queue: {queued} queued/in-flight; {uncertain} parked/uncertain records (see /queue-full).")
                continue
            if lowered in {"/network-map", "/network-map --json"}:
                try:
                    from norm_network_map import emit_network_map
                    emit_network_map(json_output=lowered.endswith(" --json"))
                except Exception as exc:
                    print(f"Network map failed: {type(exc).__name__}: {exc}")
                continue
            if lowered == "/queue-full":
                try:
                    live_items = dispatcher.queue_snapshot(limit=None)
                    live_items, runtime_phase = enrich_queue_with_runtime(ep, live_items)
                    items = dispatcher.uncertain_snapshot()
                except Exception as exc:
                    print(f"Cannot read the full queue: {exc}")
                    continue
                print(f"LIVE QUEUE: {len(live_items)} queued/in-flight entry(s)  runtime_phase={runtime_phase}")
                for item in live_items:
                    print(json.dumps(item, indent=2, ensure_ascii=False))
                print(f"PARKED / UNCERTAIN: {len(items)} delivery record(s)")
                for item in items:
                    print(json.dumps(item, indent=2, ensure_ascii=False))
                print("Read-only snapshot; nothing retried or changed. Repeated prompt IDs are delivery attempts for the same prompt.")
                continue
            if lowered == "/queue" or lowered.startswith("/queue "):
                parts = text.split()
                limit = 100
                if len(parts) > 2:
                    print("Usage: /queue [N]")
                    continue
                if len(parts) == 2:
                    try:
                        limit = max(1, int(parts[1]))
                    except ValueError:
                        print("Usage: /queue [N]")
                        continue
                items = dispatcher.queue_snapshot(limit=limit)
                if not items:
                    print("GUI Redis queue is empty.")
                    continue
                items, runtime_phase = enrich_queue_with_runtime(ep, items)
                for item in items:
                    task = item.get("runtime_task") if isinstance(item.get("runtime_task"), dict) else None
                    if task is not None:
                        current = task.get("current_step") if isinstance(task.get("current_step"), dict) else None
                        detail = str(task.get("title") or task.get("original_request") or "active task")
                        if current:
                            detail += f" | step {current.get('index')}/{current.get('total')}: {current.get('name') or current.get('id')}"
                        count = int(task.get("ingrained_detail_count") or 0)
                        if count:
                            detail += f" | ingrained details: {count}"
                    elif item.get("state") in {"dispatching", "in-flight"}:
                        detail = f"runtime: {item.get('runtime_phase') or runtime_phase}; durable task not created/matched yet"
                    else:
                        preview = item["message"].replace("\r", " ").replace("\n", " ")
                        if len(preview) > 110:
                            preview = preview[:107] + "..."
                        detail = preview
                    print(f"[{item['index']}] {item['state']:<11} {item['entry_id']} {item['prompt_id'][:8]}  {detail}")
                continue
            if lowered == "/resume-queue" or lowered.startswith("/resume-queue "):
                parts = text.split()
                if len(parts) > 2:
                    print("Usage: /resume-queue [index]")
                    continue
                index = None
                if len(parts) == 2:
                    try:
                        index = int(parts[1])
                    except ValueError:
                        print("Usage: /resume-queue [index]")
                        continue
                try:
                    result = dispatcher.resume_queue(index=index)
                except (IndexError, ValueError) as exc:
                    print(f"Cannot resume queue: {exc}")
                    continue
                if result.get("status") == "scheduled":
                    print(f"Queue resume scheduled ({result.get('requested')}); it will apply when the current HTTP dispatch releases or after restart.")
                elif result.get("rotated"):
                    first = str(result.get("first_message") or "").replace("\r", " ").replace("\n", " ")
                    if len(first) > 100:
                        first = first[:97] + "..."
                    print(f"Queue resumed at index {result.get('start_index')} across {result.get('count')} item(s). First: {first}")
                else:
                    print("GUI Redis queue is empty; nothing to resume.")
                continue
            if lowered == "/new" or lowered.startswith("/new "):
                name = text[len("/new"):].strip()
                if name:
                    try:
                        created = create_named_thread(ep, name)
                        thread_id = str(created.get("thread_id") or "")
                        title = str(created.get("title") or name)
                        entry_id = dispatcher.enqueue_thread_switch(thread_id, title)
                        print(f"Created thread '{title}' ({thread_id}) and queued the switch as {entry_id}.")
                    except Exception as exc:
                        print(f"Could not create named thread: {exc}")
                    continue
                entry_id = dispatcher.enqueue_thread_reset()
                print(f"Queued a fresh auto-titled conversation thread as {entry_id}.")
                continue
            if lowered == "/thread-list":
                try:
                    print_thread_list(ep, dispatcher)
                except Exception as exc:
                    print(f"Could not list threads: {exc}")
                continue
            if lowered == "/thread-resume" or lowered.startswith("/thread-resume "):
                selector = text[len("/thread-resume"):].strip()
                if not selector:
                    print("Usage: /thread-resume <thread name|thread id>")
                    continue
                try:
                    rows = list_threads(ep)
                    selected, candidates = resolve_thread_selector(rows, selector)
                    if selected is None:
                        if candidates:
                            print("Thread name/id is ambiguous. Matching threads:")
                            for row in candidates:
                                print(f"  {row.get('title') or '[untitled]'}  ({row.get('thread_id')})")
                        else:
                            print(f"No active thread matched: {selector}")
                        continue
                    thread_id = str(selected.get("thread_id") or "")
                    title = str(selected.get("title") or "[untitled]")
                    entry_id = dispatcher.enqueue_thread_switch(thread_id, title)
                    print(f"Queued switch to '{title}' ({thread_id}) as {entry_id}.")
                except Exception as exc:
                    print(f"Could not resume thread: {exc}")
                continue
            if lowered == "/multi":
                text = read_multiline()
                if not text:
                    print("Multiline prompt cancelled.")
                    continue
            elif lowered == "/repeat-submission":
                text = dispatcher.last_submission() or ""
                if not text:
                    print("No previous submission is available.")
                    continue
                print("Requeueing the last submission verbatim.")
            elif lowered == "/repeat-answer":
                text = dispatcher.last_answer() or ""
                if not text:
                    print("No previous Norm answer is available in Redis.")
                    continue
                dispatcher.publish_reply(text, source="repeat")
                print("Redisplayed the last completed Norm answer verbatim from Redis.")
                continue
            elif lowered == "/resume-task" or lowered.startswith("/resume-task "):
                submit_resume_command(ep, dispatcher, text)
                continue
            elif lowered == "/suppress-task":
                retry_guard_ids = dispatcher.active_prompt_ids()
                try:
                    result = post_json(
                        ep["suppress_task"],
                        payload={"reason": "Operator requested /suppress-task from Norm GUI."},
                        timeout=5,
                    )
                except Exception as exc:
                    print(f"Could not suppress task: {exc}. Prompt console remains available; check /queue-full or /status/busy before retrying.")
                    continue
                if result.get("suppressed"):
                    dispatcher.suppress_prompt_retries(retry_guard_ids)
                    print(f"Suppressed: {result.get('title') or result.get('task_id')}.")
                else:
                    print(f"Nothing suppressed: {result.get('reason') or result.get('task_status') or 'no eligible task'}.")
                continue
            elif lowered == "/flush-suppressed":
                try:
                    result = post_json(ep["flush_suppressed"], timeout=10)
                except Exception as exc:
                    print(f"Could not flush suppressed tasks: {exc}. Prompt console remains available.")
                    continue
                print(f"Flushed {int(result.get('deleted') or 0)} suppressed task(s) and {int(result.get('delivery_deleted') or 0)} suppressed delivery record(s).")
                continue
            elif lowered == "/delete-list":
                result = post_json(ep["delete_list"], timeout=10)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                continue
            elif lowered == "/delete-files":
                result = post_json(ep["delete_files"], timeout=10)
                print(f"Purged {int(result.get('purged') or 0)} trash item(s); held {int(result.get('held') or 0)}.")
                continue
            elif lowered == "/restore-delete" or lowered.startswith("/restore-delete "):
                parts = text.strip().split(maxsplit=1)
                if len(parts) != 2 or not parts[1].strip():
                    print("Usage: /restore-delete <deletion-id|all>")
                    continue
                result = post_json(ep["restore_delete"], payload={"deletion_id": parts[1].strip()}, timeout=10)
                print(f"Restored {len(result.get('restored') or [])}; conflicts {len(result.get('conflicts') or [])}; missing {len(result.get('missing') or [])}.")
                continue
            elif lowered in {
                "/memory-condense",
                "/memory-condense -deep", "/memory-condense --deep", "/memory-condense deep",
                "/memory-condense -full", "/memory-condense --full", "/memory-condense full",
            }:
                deep = "deep" in lowered
                full = "full" in lowered
                try:
                    result = post_json(ep["memory_condense"], payload={"full": full, "deep": deep}, timeout=10)
                    if result.get("scheduled"):
                        print(f"Memory condensation scheduled in {result.get('mode')} mode; it will run inside the existing worker when idle (no new user task).")
                    else:
                        print(f"Memory condensation was not scheduled: {result.get('reason') or result.get('status') or 'unknown reason'}")
                except Exception as exc:
                    print(f"Could not schedule memory condensation: {exc}")
                continue
            elif lowered == "/exit":
                print("Closing GUI prompt console; queued Redis input is preserved.")
                return 0
            elif lowered in {"/stop", "/stop-all now", "/stop-all -now", "/stop all now", "/stop all -now"}:
                stop_all(ep, immediate=True)
                return 0
            elif lowered in {"/stop-all", "/stop all"}:
                stop_all(ep, immediate=False)
                return 0
            elif lowered == "/shutdown":
                graceful_shutdown(ep)
                return 0
            elif lowered == "/shutdown now":
                result = post_json(ep["shutdown_now"], timeout=5)
                print(f"Shutdown accepted: {result.get('shutdown', 'now')}.")
                return 0

            # Command detection uses trimmed text; normal input stays verbatim.
            if lowered not in {"/multi", "/repeat-submission"}:
                text = prompt
            if not text.strip():
                continue
            if text.lstrip().startswith("/"):
                command = text.lstrip().split(maxsplit=1)[0]
                if command.lower() == "/suppress-task":
                    print("Usage: /suppress-task (no arguments; suppresses the active or next task)")
                else:
                    print(f"{command} not recognized, or arguments not supported. Type /help. Nothing queued.")
                continue
            try:
                entry_id, prompt_id = dispatcher.enqueue_prompt(text)
            except Exception as exc:
                # A foreground prompt must not kill the operator console merely
                # because Redis/Tailscale/runtime transport is temporarily down.
                # enqueue_prompt only returns after the DB3 XADD succeeds, so an
                # exception here means acceptance was not confirmed.
                print(f"Prompt was not accepted into Redis DB3: {type(exc).__name__}: {exc}")
                print("Norm Prompt remains open; retry after connectivity recovers. Nothing was intentionally acknowledged.")
                continue
            queued, uncertain = dispatcher.queue_stats()
            if queued is None:
                print(f"Queued {prompt_id[:8]} in Redis as {entry_id} (queue depth unknown; {uncertain} uncertain).")
            else:
                print(
                    f"Queued {prompt_id[:8]} in Redis as {entry_id} "
                    f"({queued} queued/in-flight; {uncertain} uncertain)."
                )
    except (InputCancelled, KeyboardInterrupt):
        print("\nPrompt cancelled. Returning to menu/terminal; Norm keeps running.")
        return 0
    except EOFError:
        print("\nClosing GUI prompt console; queued Redis input is preserved.")
        return 0
    finally:
        dispatcher.stop()


if __name__ == "__main__":
    raise SystemExit(main())
