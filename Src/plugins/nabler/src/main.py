"""Nabler native tool: format observed, data-only verification results."""
from __future__ import annotations

from nabler import from_records


def render_report(title: str, rows: list[dict], mode: str = "auto", output: str = "markdown") -> dict:
    """Use once near task conclusion for multi-row results. Rows must already be observed data; do not submit executable scripts."""
    report = from_records(title, rows, mode=mode)
    payload = report.as_dict()
    payload["rendered"] = report.render(mode=mode, output=output)
    payload["mode"] = mode
    payload["output"] = output
    return payload
