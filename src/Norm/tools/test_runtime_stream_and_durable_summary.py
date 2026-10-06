from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.ollama_client import ModelDegenerateOutput, OllamaClient


def main() -> int:
    events = []
    client = OllamaClient(
        "http://127.0.0.1:11434",
        model="norm",
        activity_sink=events.append,
        activity_source="test",
    )

    client._emit("answer", text="abc")
    assert len(events) == 1, events
    assert events[0]["type"] == "answer" and events[0]["text"] == "abc", events
    assert client._display_pending == {}, client._display_pending

    client._emit("thinking", text="tiny")
    assert len(events) == 2 and events[-1]["type"] == "thinking", events

    summary = "Current state: Norm 0.53.13. Older superseded version facts are pruned."
    client.publish_durable_summary(summary)
    assert events[-1]["type"] == "durable_summary", events[-1]
    assert events[-1]["text"] == summary, events[-1]

    repeated = (
        "I need to determine whether the documented build is present in the folder and whether an older record should count as a preserved copy before I continue. "
        * 50
    )
    try:
        OllamaClient._guard_model_stream("", repeated)
    except ModelDegenerateOutput:
        pass
    else:
        raise AssertionError("long repeated coherent phrase was not stopped by the stream guard")

    print("PASS normal model stream fragments publish immediately without newline/1KiB display buffering")
    print("PASS end-of-task durable summary publishes once as a complete dedicated event")
    print("PASS long coherent phrase repetition is stopped, not only tiny-token loops")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
