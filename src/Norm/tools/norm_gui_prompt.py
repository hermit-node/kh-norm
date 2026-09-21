from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from urllib import request

import psycopg
from psycopg import sql

from norm_gui_common import (
    DISPLAY_FILE,
    FALLBACK_LOG,
    ROOT,
    append_fallback_jsonl,
    atomic_write_utf8,
    endpoints,
    norm_process_running,
    start_norm_detached,
)

PROJECT_ID = "default"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def load_last_turn_from_postgres() -> tuple[str | None, str | None, str | None]:
    cfg = json.loads((ROOT / "config" / "runtime.json").read_text(encoding="utf-8-sig"))
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
    with psycopg.connect(pg["conninfo"]) as conn, conn.cursor() as cur:
        cur.execute(query, (PROJECT_ID,))
        rows = cur.fetchall()
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
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, timeout: float = 5) -> dict:
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

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
    print("Norm is not running. Starting app\\norm.exe...")
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
    print("  help or /help       List every operator command and its description")
    print("  /status             Show Norm's current busy/queue/runtime state")
    print("  /backup-zip         Create a ZIP backup of PostgreSQL, workspace, and runtime")
    print("  /new                Start a fresh GUI conversation thread")
    print("  /multi              Start multiline prompt entry")
    print("    ::send             Submit the multiline prompt")
    print("    ::cancel           Cancel the multiline prompt without sending it")
    print("  /repeat-submission  Requeue the last submitted prompt verbatim")
    print("  /repeat-answer      Requeue the last completed Norm answer verbatim")
    print("  /exit               Close only this prompt console; Norm keeps running")
    print("  /shutdown           Request Norm's graceful shutdown and close this console")
    print("  Ctrl+C              Request the same graceful shutdown, even while waiting")


def run_backup_zip() -> None:
    script = ROOT / "tools" / "norm_backup.py"
    if not script.is_file():
        print(f"Backup helper is missing: {script}")
        return
    print("Creating Norm backup ZIP...")
    proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT), check=False)
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
    print("Multiline mode. Type ::send on its own line to submit, ::cancel to abort.")
    lines: list[str] = []
    while True:
        line = input("... ")
        if line == "::send":
            return "\n".join(lines).strip()
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


def publish_final_answer(reply: str) -> None:
    text = (reply or "[Norm returned an empty reply]").rstrip() + "\n"
    atomic_write_utf8(DISPLAY_FILE, text)


def send_prompt(
    ep: dict[str, str], message: str, thread_id: str | None
) -> tuple[str | None, str, dict]:
    payload: dict[str, object] = {"message": message, "project_id": PROJECT_ID}
    if thread_id:
        payload["thread_id"] = thread_id
    print("Sent to Norm. Waiting for completion; Ctrl+C requests graceful shutdown.")
    result = post_json(ep["chat"], payload=payload, timeout=None)
    reply = str(result.get("reply", ""))
    publish_final_answer(reply)
    if not persistence_complete(result):
        backup_turn(message, reply, result, "PostgreSQL conversation persistence incomplete")
        print("PostgreSQL history was incomplete; saved a UTF-8 local fallback record.")
    else:
        print("Turn persisted in PostgreSQL history.")
    print("Completed answer staged as UTF-8 and released to the Norm Replies window.")
    returned = result.get("primary_thread_id") or result.get("thread_id")
    next_thread = returned if isinstance(returned, str) and returned else thread_id
    return next_thread, reply, result


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


def main() -> int:
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
    print("=== Norm Prompt Console ===")
    print(f"Connected target: {ep['host']}  project={PROJECT_ID}")
    print("Type help for commands. Ctrl+C gracefully shuts Norm down.")
    last_submission, last_answer, thread_id, history_source = load_last_turn()
    if history_source != "none":
        print(f"Recovered the latest GUI turn from {history_source} history.")
    try:
        while True:
            prompt = input("\nYou> ")
            text = prompt.strip()
            if not text:
                continue
            lowered = prompt.strip().lower()
            remember_submission = True
            if lowered in {"help", "/help"}:
                show_help()
                continue
            if lowered == "/backup-zip":
                run_backup_zip()
                continue
            if lowered == "/status":
                print(json.dumps(get_json(ep["busy"]), indent=2, ensure_ascii=False))
                continue
            if lowered == "/new":
                thread_id = None
                print("Started a fresh GUI conversation thread.")
                continue
            if lowered == "/multi":
                text = read_multiline()
                if not text:
                    print("Multiline prompt cancelled.")
                    continue
            elif lowered == "/repeat-submission":
                if not last_submission:
                    print("No previous submission is available in this GUI session.")
                    continue
                text = last_submission
                remember_submission = False
                print("Requeueing the last submission verbatim.")
            elif lowered == "/repeat-answer":
                if not last_answer:
                    print("No previous Norm answer is available in this GUI session.")
                    continue
                text = last_answer
                remember_submission = False
                print("Requeueing the last completed Norm answer verbatim.")
            elif lowered == "/exit":
                print("Closing GUI prompt console; Norm keeps running.")
                return 0
            elif lowered == "/shutdown":
                graceful_shutdown(ep)
                return 0

            if remember_submission:
                last_submission = text
            thread_id, reply, _ = send_prompt(ep, text, thread_id)
            last_answer = reply
    except KeyboardInterrupt:
        graceful_shutdown(ep)
        return 130
    except EOFError:
        print("\nClosing GUI prompt console; Norm keeps running.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
