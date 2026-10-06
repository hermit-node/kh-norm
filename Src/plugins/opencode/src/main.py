from __future__ import annotations
import urllib.parse
from ._client import basic, remote, request_json, resolve_model, settings


def _cfg() -> tuple[dict, str, str, str]:
    r = remote("opencode")
    p = settings()
    user = p.get("remote_agents", "opencode_username", fallback="opencode").strip() or "opencode"
    provider = p.get("remote_agents", "opencode_provider_id", fallback="norm-shared").strip() or "norm-shared"
    default_model = p.get("remote_agents", "opencode_model", fallback="primary").strip() or "primary"
    return r, user, provider, default_model


def _headers(user: str) -> dict[str, str]:
    return basic(user, "NORM_OPENCODE_PASSWORD")


def opencode_status() -> dict:
    """Check remote OpenCode health/version and configured shared-model routing."""
    r, user, provider, default_model = _cfg()
    if not r["enabled"]:
        return {"enabled": False, "configured": bool(r["host"]), "endpoint": r["base"]}
    if not r["host"]:
        raise RuntimeError("OpenCode is enabled but remote_agents.opencode_host is blank")
    health = request_json(r["base"] + "/global/health", headers=_headers(user), timeout=10)
    return {"enabled": True, "endpoint": r["base"], "health": health, "provider_id": provider, "default_model": resolve_model(default_model)}


def opencode_sessions() -> dict:
    """List sessions on the configured remote OpenCode server."""
    r, user, _, _ = _cfg()
    if not r["enabled"] or not r["host"]:
        raise RuntimeError("OpenCode remote integration is not enabled/configured")
    return {"sessions": request_json(r["base"] + "/session", headers=_headers(user), timeout=20)}


def opencode_task(prompt: str, session_id: str = "", title: str = "Norm delegated task", model: str = "primary", agent: str = "", system: str = "", timeout_seconds: int = 600) -> dict:
    """Create/continue a remote OpenCode session, send a task, and wait for its response using the shared model selection."""
    r, user, provider, default_model = _cfg()
    if not r["enabled"] or not r["host"]:
        raise RuntimeError("OpenCode remote integration is not enabled/configured")
    headers = _headers(user)
    sid = session_id.strip()
    created = False
    if not sid:
        made = request_json(r["base"] + "/session", method="POST", body={"title": title[:200]}, headers=headers, timeout=20)
        if not isinstance(made, dict) or not made.get("id"):
            raise RuntimeError(f"OpenCode did not return a session id: {made}")
        sid = str(made["id"]); created = True
    model_id = resolve_model(model or default_model)
    if not model_id:
        raise RuntimeError("No OpenCode model was supplied and model_provider.primary_model is blank")
    body = {"model": {"providerID": provider, "modelID": model_id}, "parts": [{"type": "text", "text": prompt}]}
    if agent.strip(): body["agent"] = agent.strip()
    if system.strip(): body["system"] = system
    result = request_json(r["base"] + f"/session/{urllib.parse.quote(sid, safe='')}/message", method="POST", body=body, headers=headers, timeout=timeout_seconds)
    return {"session_id": sid, "created": created, "provider_id": provider, "model": model_id, "response": result}


def opencode_diff(session_id: str) -> dict:
    """Return the file diff produced by a remote OpenCode session."""
    r, user, _, _ = _cfg()
    if not r["enabled"] or not r["host"]: raise RuntimeError("OpenCode remote integration is not enabled/configured")
    sid = urllib.parse.quote(session_id.strip(), safe="")
    return {"session_id": session_id, "diff": request_json(r["base"] + f"/session/{sid}/diff", headers=_headers(user), timeout=30)}


def opencode_messages(session_id: str, limit: int = 20) -> dict:
    """Read recent messages from a remote OpenCode session."""
    r, user, _, _ = _cfg()
    if not r["enabled"] or not r["host"]: raise RuntimeError("OpenCode remote integration is not enabled/configured")
    sid = urllib.parse.quote(session_id.strip(), safe="")
    lim = max(1, min(int(limit), 100))
    return {"session_id": session_id, "messages": request_json(r["base"] + f"/session/{sid}/message?limit={lim}", headers=_headers(user), timeout=30)}


def opencode_abort(session_id: str) -> dict:
    """Abort a currently running remote OpenCode session."""
    r, user, _, _ = _cfg()
    if not r["enabled"] or not r["host"]: raise RuntimeError("OpenCode remote integration is not enabled/configured")
    sid = urllib.parse.quote(session_id.strip(), safe="")
    return {"session_id": session_id, "aborted": request_json(r["base"] + f"/session/{sid}/abort", method="POST", body={}, headers=_headers(user), timeout=20)}
