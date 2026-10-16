"""Norm 0.55.3: regression checks for N2 voice/system roles and task provenance.

Uses a fake Ollama transport and a stubbed worker; never contacts Ollama, Redis,
or PostgreSQL. The production source is imported with no-op optional driver stubs.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from threading import Event, Lock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
for name in ("redis", "psycopg"):
    if name not in sys.modules:
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)

from norm_runtime.ollama_client import OllamaClient
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.prompt_worker import PromptWorker


class Transport:
    def __init__(self, lines):
        self.lines = [(json.dumps(x) + "\n").encode() for x in lines]

    def __enter__(self):
        return iter(self.lines)

    def __exit__(self, *_):
        return False


def test_per_call_voice_system_does_not_mutate_base_system_or_verifier():
    client = OllamaClient("http://127.0.0.1:11434", system_instructions="N2 base safety policy")
    calls = []
    def transport(req, **kw):
        calls.append(json.loads(req.data))
        if req.full_url.endswith("/api/generate"):
            return Transport([{"response": "mate", "done": True}])
        return Transport([{"message": {"content": "mate"}, "done": True}])
    with patch.object(client, "_urlopen", side_effect=transport):
        assert client.generate("Say hello", system_context="Cockney throughout reasoning", emit_stream=False) == "mate"
        assert client.generate("Verify JSON", emit_stream=False) == "mate"
        client.chat_with_tools([{"role": "system", "content": "Active Cockney"},
                                {"role": "user", "content": "User's actual message"}], [], think=True)
    assert calls[0]["system"] == "N2 base safety policy\n\nCockney throughout reasoning"
    assert calls[0]["prompt"] == "Say hello"
    assert calls[1]["system"] == "N2 base safety policy"
    assert calls[2]["messages"][0] == {"role": "system", "content": "N2 base safety policy"}
    assert calls[2]["messages"][1] == {"role": "system", "content": "Active Cockney"}
    assert calls[2]["messages"][2]["role"] == "user"


class Queue:
    def task_rounds(self, *_):
        return 0

    def task_round_budget_state(self, *_args, **_kwargs):
        return {"granted_limit": 6, "next_review": 10}

    def add_task_rounds(self, *_):
        return 1


class Tools:
    def instructions(self):
        return "TOOLS: available tools and usage policy"

    def schemas(self):
        return []


class StubClient:
    def __init__(self):
        self.calls = []
        self.force_blank = False

    def chat_with_tools(self, messages, *_args, **_kwargs):
        self.calls.append(json.loads(json.dumps(messages)))
        if self.force_blank and len(self.calls) == 1:
            return {"content": "", "thinking": "Think in voice", "tool_calls": [], "done_reason": "stop"}
        return {"content": "Ain't got one, mate.", "thinking": "Still in voice", "tool_calls": [], "done_reason": "stop"}


class Injections:
    def __init__(self):
        self.active = True

    def read(self, task_id, after=0):
        return ([{"id": 1, "content": "Apply short replies", "retire_when_satisfied": True}]
                if self.active else [])


def make_worker(with_injection=False, force_blank=False):
    worker = PromptWorker.__new__(PromptWorker)
    worker.queue = Queue()
    worker.client = StubClient()
    worker.client.force_blank = force_blank
    worker.durable = None
    worker.n1_gatekeeper = None
    worker.context_injections = Injections() if with_injection else None
    worker._drain = Event()
    worker._state_lock = Lock()
    worker._suppress_requested = set()
    job = PromptJob(
        message_id="m", task_id="t", step_id="step-01", prompt="Generate a plan",
        project_id="p", context="", attempt=0, created_at=0,
        metadata={"prompt_origin": "norm_generated_step", "original_user_prompt": "How's your mother?"},
        chain_id="c", previous_prompt_id="", next_prompt_id="", chain_index=0,
        recovery_attempted=False, request_type="information_retrieval",
    )
    patches = [
        patch.object(PromptWorker, "_task_round_policy", return_value={"step_limit": 6, "hard_limit": 6, "base_limit": 6, "first_review": 5}),
        patch.object(PromptWorker, "_runtime_config", return_value={}),
        patch.object(PromptWorker, "_active_voice_instructions", return_value="COCKNEY IN BOTH THINKING AND ANSWER"),
        patch.object(PromptWorker, "_persist_thinking_now", return_value=None),
        patch.object(PromptWorker, "_context_headroom_state", return_value={"checkpoint": False}),
        patch.object(PromptWorker, "_verify_pending", return_value=[]),
    ]
    return worker, job, patches


def test_n2_task_and_review_roles_preserve_original_user_provenance():
    worker, job, patches = make_worker()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        first = worker._run_tool_slice(job, "INTERNAL STEP: decide what to do", Tools())
        assert first["answer"] == "Ain't got one, mate."
        review_text = worker._review_prompt("INTERNAL STEP", first["answer"])
        reviewed = worker._run_tool_slice(job, review_text, Tools())
        assert reviewed["answer"] == first["answer"]
    first_messages, review_messages = worker.client.calls
    for messages in (first_messages, review_messages):
        # Preserve the exact ORIGINAL user message even on generated steps;
        # explicit system instructions keep active execution bounded.
        assert [msg["role"] for msg in messages] == ["system", "user"]
        assert messages[1]["content"] == "How's your mother?"
        assert "EXECUTION PHASE — ACTIVE STEP" in messages[0]["content"]
        assert "PROSECUTE the CURRENT work_request.instruction" in messages[0]["content"]
        assert "COCKNEY IN BOTH THINKING AND ANSWER" in messages[0]["content"]
        assert "TOOLS:" in messages[0]["content"]
        assert "Do not redo completed steps" in messages[0]["content"]
    assert "INTERNAL STEP: decide" in first_messages[0]["content"]
    assert "Do one quick completion review" in review_messages[0]["content"]
    assert "NOT A NEW USER REQUEST" in review_messages[0]["content"]
    assert review_messages[1] == {"role": "user", "content": "How's your mother?"}
    with patch.object(PromptWorker, "_runtime_config", return_value={}):
        assert worker._typed_prompt(job, "information_retrieval").count("How's your mother?") >= 1


def test_direct_operator_prompt_keeps_actual_user_role():
    worker, job, patches = make_worker()
    job.request_type = "prompt"
    job.prompt = "How's your mother?"
    job.metadata["prompt_origin"] = "user_prompt"
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = worker._run_tool_slice(job, worker._typed_prompt(job, "prompt"), Tools())
    assert result["answer"] == "Ain't got one, mate."
    assert [m["role"] for m in worker.client.calls[0]] == ["system", "user"]
    assert worker.client.calls[0][1]["content"] == "How's your mother?"


def test_retired_injection_removed_from_subsequent_n2_round():
    worker, job, patches = make_worker(with_injection=True, force_blank=True)
    original = worker.client.chat_with_tools

    def end_injection_after_first(messages, *args, **kwargs):
        res = original(messages, *args, **kwargs)
        if len(worker.client.calls) == 1:
            worker.context_injections.active = False
        return res

    worker.client.chat_with_tools = end_injection_after_first
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = worker._run_tool_slice(job, "generated step", Tools())
    assert result["answer"] == "Ain't got one, mate."
    assert "Apply short replies" in worker.client.calls[0][0]["content"]
    assert "Apply short replies" not in worker.client.calls[1][0]["content"]
    assert not any(m.get("role") == "user" and m.get("content") != "How's your mother?"
                   for messages in worker.client.calls for m in messages)


def test_inactive_voice_profile_does_not_invent_cockney():
    worker, job, patches = make_worker()
    patches[2] = patch.object(PromptWorker, "_active_voice_instructions", return_value="")
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        worker._run_tool_slice(job, "Generated step", Tools())
    assert "ACTIVE N2 VOICE PROFILE" not in worker.client.calls[0][0]["content"]
