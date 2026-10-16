from __future__ import annotations

"""
Future training backend boundary.

This module is intentionally private (leading underscore) and exposes no Norm tool.
The v0.1 skeleton does not execute trainers, shell commands, Ollama, or promotion.
A later integration can implement one or more reviewed backends here while keeping
lesson capture/review/storage stable.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TrainingRequest:
    base_model: str
    candidate_tag: str
    dataset_path: Path
    dataset_sha256: str
    output_dir: Path


@dataclass(frozen=True)
class TrainingResult:
    ok: bool
    adapter_path: Path | None = None
    merged_model_path: Path | None = None
    details: dict | None = None


class TrainingBackend(Protocol):
    name: str

    def train(self, request: TrainingRequest) -> TrainingResult:
        ...


class NotConfiguredBackend:
    name = "not_configured"

    def train(self, request: TrainingRequest) -> TrainingResult:
        raise RuntimeError(
            "No training backend is configured. The lesson_model skeleton deliberately "
            "does not launch LoRA/QLoRA training or promote norm:latest."
        )
