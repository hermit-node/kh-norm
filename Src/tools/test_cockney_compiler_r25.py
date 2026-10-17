"""r25 Cockney compiler regression. No Ollama, Redis, secrets, or state files needed."""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/voice_profile/src"))
import _engine as e


def sample(source: str, page: int, text: str) -> dict:
    return {
        "source": source,
        "page_start": page,
        "page_end": page,
        "text": text,
        "id": f"{source}:{page}",
        "words": len(e._words(text)),
    }


def run() -> None:
    pages = [sample("Volume-02.pdf", 1,
                    "COCKNEY SPEAKING CORPUS\nVolume Two Revised edition and editorial source notes. "
                    "This is a title page, not actual speech. " * 8)]
    fragments = [
        ("Volume-01.pdf", "Oi mate, what a bloody mess. That bloke could talk for hours while the bins pile up. "
         "I said the plan was daft and we'd best get moving. "
         "He just stood there with a clipboard and another fancy excuse. "),
        ("Volume-02.pdf", "Reckon you can tell the geezer what's wrong without turning it into a speech? "
         "He said his mate owed him twenty quid. "
         "That's a dodgy way to do business and no use pretending otherwise. "),
        ("Volume-03.pdf", "The build pipeline was fine. Nobody checked the hashes before making a fuss. "
         "Someone said the system was clever. I said the evidence was missing and that was the problem. "),
        ("Volume-04.pdf", "It ain't rocket science, mate. Sod the committee's pretty diagram. "
         "Get the job done before the whole team has another long meeting. "
         "The bloke can keep his fancy PowerPoint presentation. "),
        ("Volume-05.pdf", "Two directories looked alike, but one held the original. "
         "I asked for a checksum before the poor sod touched Delete. "
         "Without it, all you've got is confidence wearing a neat shirt. "),
    ]
    for i, (source, phrase) in enumerate(fragments):
        words = phrase * 5
        pages.append(sample(source, i + 2, "Source page 02 / 20\nConversational rewrite\n"
                            + words + "\nVoice corpus draft - sources preserved 2"))
    selected = e._select_anchors(pages, 6, profile_name="Cockney")
    assert len(selected) == 6
    assert len({x["source"] for x in selected}) == 5
    assert all("COCKNEY SPEAKING CORPUS" not in x["text"] for x in selected)
    assert all("Source page " not in x["text"] for x in selected)
    assert all("Voice corpus draft" not in x["text"] for x in selected)
    assert sum(e._cockney_anchor_bonus(x["text"]) > 0 for x in selected) >= 3
    assert e._cockney_anchor_text("Nobody\ufffds alright") == "Nobody's alright"

    # Generic/non-Cockney operation must not gain forced slang or output limits.
    generic = e._select_anchors(pages, 6, profile_name="Finance")
    unscoped = e._select_anchors(pages, 6)
    assert generic == unscoped

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        override = "Use a cheeky East London voice without corporate waffle."
        (folder / "style_override.txt").write_text(override, encoding="utf-8")
        saved = e._profile_dir
        e._profile_dir = lambda name: folder
        try:
            profile = {
                "profile_name": "Cockney",
                "synthesis": {"voice_summary": "East London banter", "tone": ["dry wit"]},
                "anchors": selected,
            }
            prompt = e._render_prompt(profile, 3)
            assert override in prompt
            assert "CASUAL REPLY LENGTH" in prompt
            assert "Never reproduce memorable phrases" in prompt
            assert "No verbatim lines" in prompt
            profile["profile_name"] = "Finance"
            generic_prompt = e._render_prompt(profile, 3)
            assert "CASUAL REPLY LENGTH" not in generic_prompt
        finally:
            e._profile_dir = saved

    # Use a stubbed Ollama client to verify the model prompt, without inference.
    calls = []
    fake = types.ModuleType("norm_runtime.ollama_client")

    class ClientStub:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, text, **kwargs):
            calls.append(text)
            return "{}"

        def parse_json(self, value):
            return {"tested": True}

    fake.OllamaClient = ClientStub
    prev = sys.modules.get("norm_runtime.ollama_client")
    sys.modules["norm_runtime.ollama_client"] = fake
    saved = e._ollama_settings
    e._ollama_settings = lambda: ("http://localhost:1", "fake")
    try:
        metrics = {"synthetic": True}
        assert e._model_synthesis(metrics, selected, profile_name="Cockney") == {"tested": True}
        assert "TARGET VOICE" in calls[-1] and "NOT sanitized business prose" in calls[-1]
        assert e._model_synthesis(metrics, selected, profile_name="Finance") == {"tested": True}
        assert "TARGET VOICE" not in calls[-1]
    finally:
        e._ollama_settings = saved
        if prev is None:
            del sys.modules["norm_runtime.ollama_client"]
        else:
            sys.modules["norm_runtime.ollama_client"] = prev

    print("PASS: r25 Cockney dialect anchors, PDF metadata exclusion, five-volume coverage, "
          "override, no-copy guidance, casual length, model prompt, other-profile compatibility")


if __name__ == "__main__":
    run()
