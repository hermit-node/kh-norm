from __future__ import annotations
from ._client import bearer, model_config, remote, request_json, resolve_model, secret, settings


def jan_status() -> dict:
    """Check the configured remote Jan API and return endpoint/model availability without exposing credentials."""
    cfg = remote("jan")
    if not cfg["enabled"]:
        return {"enabled": False, "configured": bool(cfg["host"]), "endpoint": cfg["base"]}
    if not cfg["host"]:
        raise RuntimeError("Jan is enabled but remote_agents.jan_host is blank")
    result = request_json(cfg["base"] + "/v1/models", headers=bearer("NORM_JAN_API_KEY"), timeout=10)
    return {"enabled": True, "endpoint": cfg["base"], "models": result.get("data", []) if isinstance(result, dict) else result}


def jan_models() -> dict:
    """List models exposed by the configured remote Jan OpenAI-compatible API."""
    cfg = remote("jan")
    if not cfg["enabled"] or not cfg["host"]:
        raise RuntimeError("Jan remote integration is not enabled/configured")
    result = request_json(cfg["base"] + "/v1/models", headers=bearer("NORM_JAN_API_KEY"), timeout=15)
    return {"endpoint": cfg["base"], "models": result.get("data", []) if isinstance(result, dict) else result}


def jan_chat(prompt: str, model: str = "primary", system: str = "", max_tokens: int = 2048, temperature: float = 0.2, timeout_seconds: int = 180) -> dict:
    """Send one bounded chat request to remote Jan, resolving primary/secondary against Norm's shared model settings."""
    cfg = remote("jan")
    if not cfg["enabled"] or not cfg["host"]:
        raise RuntimeError("Jan remote integration is not enabled/configured")
    model_id = resolve_model(model)
    if not model_id:
        raise RuntimeError("No Jan model was supplied and model_provider.primary_model is blank")
    messages = []
    if system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model_id, "messages": messages, "stream": False, "max_tokens": max(1, min(int(max_tokens), 32768)), "temperature": float(temperature)}
    result = request_json(cfg["base"] + "/v1/chat/completions", method="POST", body=payload, headers=bearer("NORM_JAN_API_KEY"), timeout=timeout_seconds)
    text = ""
    if isinstance(result, dict):
        choices = result.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            if isinstance(msg, dict):
                text = str(msg.get("content") or "")
    return {"model": model_id, "text": text, "response": result}
