from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "core" / "norm_runtime" / "conversation_service.py").read_text(encoding="utf-8")


def _helper():
    tree = ast.parse(SOURCE)
    service = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ConversationService")
    method = next(node for node in service.body if isinstance(node, ast.FunctionDef) and node.name == "_conservative_primary_request")
    cls = ast.ClassDef(name="Harness", bases=[], keywords=[], body=[method], decorator_list=[])
    module = ast.Module(body=[cls], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {"re": re}
    exec(compile(module, "<primary-request-test>", "exec"), ns)
    return ns["Harness"]._conservative_primary_request


def main() -> int:
    fn = _helper()

    exact = "Fix why the end of task summary is stale and make it reconcile current facts."
    details = [{
        "verbatim": "end of task summary",
        "normalized": "task summary",
        "destination": "current_task_context",
        "also_current_task": True,
    }]
    assert fn(exact, details) == exact

    # Even if the model wrongly tags a task qualifier as a durable fact, an embedded
    # non-separable phrase cannot be cut out of the executable request.
    bad = [{
        "verbatim": "end of task summary",
        "normalized": "end-of-task summaries are important",
        "destination": "memory_preference",
        "also_current_task": False,
    }]
    assert fn(exact, bad) == exact

    aside_turn = (
        "Fix the runtime summary so newer facts replace stale ones.\n"
        "By the way, I prefer version bumps once a patch gets this large."
    )
    aside = [{
        "verbatim": "By the way, I prefer version bumps once a patch gets this large.",
        "normalized": "Prefer version bumps for substantial patches.",
        "destination": "memory_preference",
        "also_current_task": False,
    }]
    result = fn(aside_turn, aside)
    assert result == "Fix the runtime summary so newer facts replace stale ones.", result

    required_side_fact = [{
        "verbatim": "By the way, I prefer version bumps once a patch gets this large.",
        "normalized": "Prefer version bumps for substantial patches.",
        "destination": "memory_preference",
        "also_current_task": True,
    }]
    assert fn(aside_turn, required_side_fact) == aside_turn

    print("PASS task qualifiers such as 'end of task summary' cannot be pruned as filler")
    print("PASS exact clearly separable sidecars may still be removed from executable work")
    print("PASS sidecars marked also_current_task remain in executable work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
