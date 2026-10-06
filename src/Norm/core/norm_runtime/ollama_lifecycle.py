import json
import logging
import os
import signal
import subprocess
import time
from urllib import request


def api_ready(base_url: str, timeout: float = 0.5) -> bool:
    try:
        with request.urlopen(f"{base_url}/api/tags", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def unload_model(base_url: str, model_name: str, timeout: float = 15.0) -> bool:
    if not api_ready(base_url):
        return True
    payload = json.dumps({"model": model_name, "keep_alive": 0}).encode("utf-8")
    req = request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=5) as response:
            response.read()
    except Exception:
        logging.exception("Ollama model unload request failed")
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with request.urlopen(f"{base_url}/api/ps", timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            names = {str(item.get("name", "")).split(":", 1)[0] for item in payload.get("models", [])}
            if model_name.split(":", 1)[0] not in names:
                return True
        except Exception:
            if not api_ready(base_url):
                return True
        time.sleep(0.25)
    return False


def remaining_ollama_processes() -> list[dict]:
    if os.name != "nt":
        return []
    script = (
        "$p=Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -in @('ollama.exe','ollama app.exe','llama-server.exe') }; "
        "$p | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    text = completed.stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logging.warning("Could not parse Ollama process inventory: %s", text[:500])
        return []
    if isinstance(data, dict):
        data = [data]
    return [item for item in data if isinstance(item, dict)]


def _ollama_server_pids() -> list[int]:
    pids: list[int] = []
    for item in remaining_ollama_processes():
        name = str(item.get("Name") or "").lower()
        command = str(item.get("CommandLine") or "").lower()
        if name == "ollama.exe" and "serve" in command:
            try:
                pids.append(int(item.get("ProcessId")))
            except (TypeError, ValueError):
                pass
    return sorted(set(pids))


def _send_windows_ctrl_break(pid: int) -> bool:
    member = (
        '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool FreeConsole();'
        '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool AttachConsole(uint processId);'
        '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetConsoleCtrlHandler(System.IntPtr handler, bool add);'
        '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool GenerateConsoleCtrlEvent(uint ctrlEvent, uint processGroupId);'
        '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern uint GetConsoleProcessList(uint[] processList, uint processCount);'
    )
    script = (
        "$t=Add-Type -Name NormConsoleCtrl -Namespace Norm -MemberDefinition '" + member + "' -PassThru; "
        "$t::FreeConsole() | Out-Null; "
        f"if (-not $t::AttachConsole([uint32]{pid})) {{ exit 11 }}; "
        "$t::SetConsoleCtrlHandler([IntPtr]::Zero,$true) | Out-Null; "
        "$ids=New-Object uint32[] 64; $n=$t::GetConsoleProcessList($ids,64); "
        f"$ok=$t::GenerateConsoleCtrlEvent(1,[uint32]{pid}); "
        "if (-not $ok) { "
        f"$unsafe=@($ids[0..([Math]::Max([int]$n-1,0))] | Where-Object {{ $_ -ne [uint32]{pid} -and $_ -ne [uint32]$PID }}); "
        "if ($n -eq 0 -or $unsafe.Count -gt 0) { $t::FreeConsole() | Out-Null; exit 12 }; "
        "$ok=$t::GenerateConsoleCtrlEvent(1,0) }; "
        "if (-not $ok) { $t::FreeConsole() | Out-Null; exit 13 }; "
        "Start-Sleep -Milliseconds 250; $t::FreeConsole() | Out-Null; exit 0"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    # 0xC000013A is Windows STATUS_CONTROL_C_EXIT. The helper may receive the
    # same console event it successfully delivered to the otherwise-orphaned server.
    if completed.returncode in {0, 0xC000013A}:
        return True
    logging.error("Attached Ctrl+Break helper failed pid=%s rc=%s stderr=%s", pid, completed.returncode, completed.stderr[-500:])
    return False


def graceful_stop_server(process: subprocess.Popen | None, base_url: str, timeout: float = 15.0) -> bool:
    if not api_ready(base_url):
        return True
    owned = process is not None and process.poll() is None
    pids = [int(process.pid)] if owned else _ollama_server_pids()
    if not pids:
        logging.error("Ollama API is live but no ollama serve process could be identified")
        return False
    for pid in pids:
        try:
            if os.name == "nt":
                if owned:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                elif not _send_windows_ctrl_break(pid):
                    return False
            else:
                os.kill(pid, signal.SIGINT)
            logging.info("Sent graceful Ollama stop signal pid=%s owned=%s", pid, owned)
        except Exception:
            logging.exception("Graceful Ollama server signal failed pid=%s", pid)
            return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not api_ready(base_url):
            return True
        time.sleep(0.25)
    logging.error("Ollama server did not exit after graceful signal")
    return False


def shutdown_ollama(process: subprocess.Popen | None, base_url: str, model_name: str) -> dict:
    unloaded = unload_model(base_url, model_name)
    server_stopped = graceful_stop_server(process, base_url)
    deadline = time.monotonic() + 10.0
    leftovers = remaining_ollama_processes()
    while leftovers and time.monotonic() < deadline:
        time.sleep(0.25)
        leftovers = remaining_ollama_processes()
    return {
        "status": "ok" if unloaded and server_stopped and not leftovers else "incomplete",
        "model_unloaded": unloaded,
        "server_stopped": server_stopped,
        "remaining_processes": leftovers,
    }
