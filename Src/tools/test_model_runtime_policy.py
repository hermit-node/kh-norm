from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.ollama_client import ModelContextExceeded, OllamaClient
from norm_runtime.model_runtime_policy import agent_model_client_kwargs, agent_persistent_instructions, effective_context_length


class FakeResponse:
    def __init__(self, lines: list[dict]):
        self._lines = [json.dumps(v).encode("utf-8") + b"\n" for v in lines]
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def __iter__(self): return iter(self._lines)
    def close(self): pass


def test_generate_forwards_system_and_context():
    captured = {}
    def fake_urlopen(req, timeout=None):
        captured.update(json.loads(req.data.decode("utf-8")))
        return FakeResponse([{"response":"OK", "done": True, "done_reason":"stop"}])
    c = OllamaClient("http://127.0.0.1:11434", model="granite4.2:30b", context_length=65536,
                     model_context_overrides={"norm":180000}, system_instructions=["one","two"])
    with patch("norm_runtime.ollama_client.request.urlopen", fake_urlopen):
        assert c.generate("x", emit_stream=False) == "OK"
    assert captured["options"]["num_ctx"] == 65536
    assert captured["system"] == "- one\n- two"


def test_chat_override_survives_model_switch():
    payloads=[]
    def fake_urlopen(req, timeout=None):
        payloads.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse([{"message":{"content":"OK"}, "done": True, "done_reason":"stop"}])
    c = OllamaClient("http://127.0.0.1:11434", model="norm:latest", context_length=65536,
                     model_context_overrides={"norm":180000}, system_instructions="runtime rules")
    with patch("norm_runtime.ollama_client.request.urlopen", fake_urlopen):
        c.chat_with_tools([{"role":"user","content":"x"}], [], think=False)
        c.model="granite4.2:30b"
        c.chat_with_tools([{"role":"user","content":"x"}], [], think=False)
    assert payloads[0]["options"]["num_ctx"] == 180000
    assert payloads[1]["options"]["num_ctx"] == 65536
    assert payloads[0]["messages"][0] == {"role":"system","content":"runtime rules"}
    assert payloads[1]["messages"][0] == {"role":"system","content":"runtime rules"}


def test_context_error_is_structured():
    from urllib.error import HTTPError
    body = json.dumps({"error": json.dumps({"error": {"code":400,"message":"request (34250 tokens) exceeds the available context size (32768 tokens), try increasing it","type":"exceed_context_size_error","n_prompt_tokens":34250,"n_ctx":32768}})}).encode()
    def fake_urlopen(req, timeout=None):
        raise HTTPError(req.full_url, 400, "Bad Request", {"Content-Type":"application/json"}, BytesIO(body))
    c=OllamaClient("http://127.0.0.1:11434", model="granite4.2:30b")
    with patch("norm_runtime.ollama_client.request.urlopen", fake_urlopen):
        try:
            c.chat_with_tools([{"role":"user","content":"x"}], [])
        except ModelContextExceeded as exc:
            assert exc.request_tokens == 34250
            assert exc.context_size == 32768
        else:
            raise AssertionError("expected ModelContextExceeded")


def test_runtime_json_owns_context_and_agent_instructions():
    config = json.loads((ROOT / "config" / "runtime.json").read_text(encoding="utf-8"))
    n1 = agent_persistent_instructions(config, "n1")
    n2 = agent_persistent_instructions(config, "n2")
    assert n1 and n2 and n1 != n2
    shared = list(config.get("persistent_instructions") or [])
    assert n1[:len(shared)] == shared
    assert n2[:len(shared)] == shared
    assert any("gatekeeper" in item.lower() for item in n1)
    assert any("front-point ai coordinator" in item.lower() for item in n2)
    assert effective_context_length(config, "n2", "norm:latest") == 180000
    assert effective_context_length(config, "n2", "granite4.2:30b") == 65536
    assert agent_model_client_kwargs(config, "n1")["system_instructions"] == n1
    assert agent_model_client_kwargs(config, "n2")["system_instructions"] == n2


def test_profile_context_is_appended_to_agent_system_instructions():
    config = json.loads((ROOT / "config" / "runtime.json").read_text(encoding="utf-8"))
    config["_profile_context"] = {"n1": "N1 PROFILE", "n2": "N2 PROFILE"}
    assert agent_model_client_kwargs(config, "n1")["system_instructions"][-1] == "N1 PROFILE"
    assert agent_model_client_kwargs(config, "n2")["system_instructions"][-1] == "N2 PROFILE"


def test_context_authority_not_duplicated_in_legacy_files():
    settings = (ROOT / "config" / "settings.ini").read_text(encoding="utf-8").casefold()
    norm_json = json.loads((ROOT / "config" / "norm.json").read_text(encoding="utf-8"))
    modelfile = (ROOT / "Modelfile").read_text(encoding="utf-8").casefold()
    assert "num_ctx" not in settings
    assert "context_length" not in norm_json
    assert "parameter num_ctx" not in modelfile
    assert "system \"\"\"" not in modelfile


if __name__ == "__main__":
    tests=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn(); print("PASS", fn.__name__)
