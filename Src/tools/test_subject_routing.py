from __future__ import annotations

import json
import sys
import types
from pathlib import Path

if "psycopg" not in sys.modules:
    fake_psycopg = types.ModuleType("psycopg")
    class _PsycopgError(Exception):
        pass
    fake_psycopg.Error = _PsycopgError
    fake_psycopg.sql = types.SimpleNamespace(Identifier=lambda value: value, SQL=lambda value: value)
    sys.modules["psycopg"] = fake_psycopg
    fake_types = types.ModuleType("psycopg.types")
    fake_json = types.ModuleType("psycopg.types.json")
    class _Jsonb:
        def __init__(self, value):
            self.value = value
    fake_json.Jsonb = _Jsonb
    sys.modules["psycopg.types"] = fake_types
    sys.modules["psycopg.types.json"] = fake_json

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

# This focused unit test exercises conversation routing only; stub heavyweight runtime
# adapters so it can run in the portable-source validation environment without Redis/Postgres clients.
_store_mod = types.ModuleType("norm_runtime.conversation_store")
_store_mod.ConversationStore = object
sys.modules["norm_runtime.conversation_store"] = _store_mod
_file_mod = types.ModuleType("norm_runtime.file_tool_executor")
_file_mod.FileToolExecutor = object
sys.modules["norm_runtime.file_tool_executor"] = _file_mod
_queue_mod = types.ModuleType("norm_runtime.prompt_queue")
_queue_mod.PromptJob = object
_queue_mod.RedisPromptQueue = object
sys.modules["norm_runtime.prompt_queue"] = _queue_mod

from norm_runtime.conversation_service import ConversationService, RouteDecision, SubjectAssignment


class FakeStore:
    def __init__(self):
        self.threads = [
            {"thread_id": "t-norm", "title": "Norm", "summary": "Norm runtime work", "parent_thread_id": None, "updated_at": "now"},
            {"thread_id": "t-hw", "title": "Hardware", "summary": "PC hardware", "parent_thread_id": None, "updated_at": "now"},
        ]
        self.created = []

    def list_threads(self, project_id, limit=12):
        return list(self.threads[:limit])

    def recent_messages(self, thread_id, limit=4):
        return []

    def thread_exists(self, project_id, thread_id):
        return any(t["thread_id"] == thread_id for t in self.threads)

    def create_thread(self, project_id, title, parent_thread_id=None, thread_id=None):
        tid = thread_id or f"new-{len(self.created)+1}"
        self.created.append((tid, title))
        self.threads.append({"thread_id": tid, "title": title, "summary": "", "parent_thread_id": None, "updated_at": "now"})
        return tid


class FakeOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected generate call")
        value = self.responses.pop(0)
        return json.dumps(value) if isinstance(value, dict) else str(value)

    @staticmethod
    def parse_json(raw):
        return json.loads(raw)


class FakeN1:
    def __init__(self, decisions=None):
        self.decisions = list(decisions or ["accept"])
        self.calls = []

    def judge_subject_routing(self, **kwargs):
        self.calls.append(kwargs)
        decision = self.decisions.pop(0) if self.decisions else "accept"
        return {
            "decision": decision,
            "reason": "ok" if decision == "accept" else "weak placement",
            "retry_instruction": "Create a clean subject split and avoid the weak thread match." if decision == "retry" else "",
            "processing_failed": False,
        }


def _proposal(norm_conf=0.91, hw_conf=0.61):
    return {
        "subjects": [
            {
                "label": "Norm routing",
                "content": "Fix Norm's subject routing.",
                "thread_id": "t-norm",
                "match_confidence": norm_conf,
                "create_thread_title": None,
                "primary": True,
            },
            {
                "label": "RAID controller",
                "content": "My RAID controller is weird.",
                "thread_id": "t-hw",
                "match_confidence": hw_conf,
                "create_thread_title": "RAID controller",
                "primary": False,
            },
        ],
        "confidence": 0.86,
        "clarification_question": None,
    }


def test_subthreshold_match_creates_new_subject_thread_at_point_68():
    store = FakeStore()
    n1 = FakeN1(["accept"])
    service = ConversationService(store, FakeOllama([_proposal()]), routing_threshold=0.68, n1_gatekeeper=n1)
    route = service._route("default", "Fix Norm's subject routing. Also my RAID controller is weird.")
    assert route.primary_thread_id == "t-norm"
    assert route.thread_ids == ("t-norm", "new-1")
    assert "t-hw" not in route.thread_ids
    assert store.created == [("new-1", "RAID controller")]
    assert route.thread_projections()["t-norm"] == "Fix Norm's subject routing."
    assert route.thread_projections()["new-1"] == "My RAID controller is weird."
    assert n1.calls[0]["threshold"] == 0.68


def test_n1_rejects_first_route_without_leaving_orphan_threads():
    store = FakeStore()
    first = _proposal()
    second = _proposal(hw_conf=0.0)
    second["subjects"][1]["thread_id"] = None
    second["subjects"][1]["create_thread_title"] = "RAID controller"
    n1 = FakeN1(["retry", "accept"])
    ollama = FakeOllama([first, second])
    service = ConversationService(store, ollama, routing_threshold=0.68, n1_gatekeeper=n1)
    route = service._route("default", "Fix Norm's subject routing. Also my RAID controller is weird.")
    assert len(n1.calls) == 2
    assert store.created == [("new-1", "RAID controller")]
    assert route.thread_ids == ("t-norm", "new-1")
    assert "N1 ROUTING REVIEW FEEDBACK" in ollama.prompts[1]


def test_assistant_projection_accepts_only_verbatim_excerpts():
    route = RouteDecision(
        ("t-norm", "t-raid"), "t-norm", 0.9,
        subjects=(
            SubjectAssignment("t-norm", "Norm", "Fix Norm.", 0.9, True),
            SubjectAssignment("t-raid", "RAID", "RAID is weird.", 0.9, False),
        ),
    )
    ollama = FakeOllama([{
        "projections": [
            {"thread_id": "t-norm", "excerpts": ["Norm is fixed."]},
            {"thread_id": "t-raid", "excerpts": ["invented text", "RAID remains unchanged."]},
        ]
    }])
    service = ConversationService(FakeStore(), ollama, routing_threshold=0.68)
    result = service._project_assistant_reply("Norm is fixed. RAID remains unchanged.", route)
    assert result == {"t-norm": "Norm is fixed.", "t-raid": "RAID remains unchanged."}


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("PASS", test.__name__)
