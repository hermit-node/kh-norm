from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.n1_gatekeeper import N1Gatekeeper


class Live:
    def __init__(self):
        self.count = 45
        self.value = "0.53.14"
        self.record_id = "version-record"
        self.recorded = []

    def validation_candidates(self, **_kwargs):
        return [{
            "record_id": self.record_id,
            "tool": "read_file",
            "target": r"C:\Norm\Norm.exe",
            "description": "deployed Norm version",
            "value": self.value,
            "num_checks": self.count,
            "previous_value": "0.53.13",
            "changed_at": "2026-10-04T12:00:00+00:00",
            "description_match": 0.98,
            "storage": "redis_live",
        }]

    def record_global_validations(self, task_id, step_id, items):
        self.recorded.append((task_id, step_id, copy.deepcopy(items)))
        self.count += 1
        self.value = str(items[0]["value"])
        return self.validation_candidates()

    def invalidate_validation_target(self, _target):
        return 0


class Client:
    def __init__(self):
        self.decision_calls = 0

    def generate(self, prompt, **_kwargs):
        if "Decide whether the existing LIVE Redis verification record" in prompt:
            self.decision_calls += 1
            assert '"num_checks": 45' in prompt
            return json.dumps({
                "decision": "reuse",
                "record_id": "version-record",
                "reason": "same deployed-version fact already has repeated live verification",
            })
        if "fresh information tool just returned" in prompt:
            return json.dumps({"value": "0.53.14"})
        if "rabbit hole" in prompt:
            return json.dumps({"instruction": "Use the existing version evidence and continue."})
        raise AssertionError(prompt[:200])


gate = N1Gatekeeper(Client(), Live())
outcome = gate.before_tool(
    task_id="t",
    step_id="s",
    name="read_file",
    arguments={
        "path": r"C:\Norm\Norm.exe",
        "n1_target": r"C:\Norm\Norm.exe",
        "n1_need": "what version of Norm is deployed",
    },
)
assert outcome.execute is False
assert outcome.result["n1_reused"] is True
assert outcome.result["value"] == "0.53.14"
assert outcome.result["num_checks"] == 45
print("PASS: N1 sees the live Redis count before deciding and returns the existing verified answer without invoking N2's proposed tool")
