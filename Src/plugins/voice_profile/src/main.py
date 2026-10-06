from __future__ import annotations

from typing import Literal

from . import _engine


def build_from_pdf_folder(
    profile_name: str,
    folder: str,
    extraction_mode: Literal["auto", "text", "hybrid", "vision"] = "auto",
    recursive: bool = True,
    use_model: bool = True,
    anchor_count: int = 6,
) -> dict:
    """Build or extend a voice profile from PDFs using Norm's existing vision_parse plugin."""
    return _engine.build_from_pdf_folder(
        profile_name, folder, extraction_mode, recursive, use_model, anchor_count
    )


def ingest_text(
    profile_name: str,
    source: str,
    text: str,
    page_start: int = 0,
    page_end: int = 0,
    extraction_method: str = "external",
) -> dict:
    """Add already-extracted text to a voice corpus without re-reading the source PDF."""
    return _engine.ingest_text(
        profile_name, source, text, page_start, page_end, extraction_method
    )


def ingest_vision_result(profile_name: str, result: dict) -> dict:
    """Ingest a vision_parse result; only its transcription is used for voice analysis."""
    return _engine.ingest_vision_result(profile_name, result)


def build_profile(
    profile_name: str,
    use_model: bool = True,
    anchor_count: int = 6,
) -> dict:
    """Analyze accumulated corpus text and persist an inspectable voice profile."""
    return _engine.build_profile(profile_name, use_model, anchor_count)


def profile_summary(profile_name: str) -> dict:
    """Return style measurements, synthesis, source inventory, and anchor provenance."""
    return _engine.profile_summary(profile_name)


def list_profiles() -> dict:
    """List built voice profiles and show which profile is active."""
    return _engine.list_profiles()


def render_voice_prompt(profile_name: str, anchor_count: int = 4) -> dict:
    """Render the style-only voice prompt with bounded, source-labeled rich excerpts."""
    return _engine.render_voice_prompt(profile_name, anchor_count)


def retrieve_context(
    profile_name: str,
    query: str,
    limit: int = 5,
    max_chars_per_excerpt: int = 1800,
) -> dict:
    """Retrieve diverse source-grounded corpus evidence relevant to a current query."""
    return _engine.retrieve_context(profile_name, query, limit, max_chars_per_excerpt)


def compose_context(
    profile_name: str,
    query: str,
    knowledge_limit: int = 4,
    anchor_count: int = 3,
) -> dict:
    """Compose voice instructions plus query-relevant corpus evidence with provenance."""
    return _engine.compose_context(profile_name, query, knowledge_limit, anchor_count)


def activate_profile(
    profile_name: str,
    anchor_count: int = 3,
    max_prompt_chars: int = 32000,
) -> dict:
    """Make a built style profile active for future normal Norm conversations."""
    return _engine.activate_profile(profile_name, anchor_count, max_prompt_chars)


def deactivate_profile() -> dict:
    """Disable active voice-profile conditioning without deleting any profile data."""
    return _engine.deactivate_profile()


def active_profile() -> dict:
    """Show which voice profile is currently active."""
    return _engine.active_profile()


def clear_profile(profile_name: str, confirm: bool = False) -> dict:
    """Delete one inactive profile only when confirm=true."""
    return _engine.clear_profile(profile_name, confirm)
