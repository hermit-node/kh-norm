from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from .ollama_client import ModelGenerationCancelled, OllamaClient


class MaintenanceModelClient:
    """Small failover wrapper used only by memory-condensation jobs.

    Model order is primary -> one configured backup -> Norm. Blank configured
    model names resolve to ``norm``. Configured names are resolved against the
    currently installed Ollama model inventory at the start of each maintenance
    pass, permitting convenient nicknames such as ``granite`` or
    ``north-mini-code`` when the match is unambiguous. Once a model fails during
    a maintenance pass, the next model becomes active for the remainder of that
    pass.
    """

    def __init__(
        self,
        base_client: OllamaClient,
        *,
        primary_model: str = "",
        backup_model: str = "",
        norm_model: str = "norm",
    ) -> None:
        self.base_client = base_client
        self.norm_model = str(norm_model or "norm").strip() or "norm"
        requested_primary = str(primary_model or "").strip() or self.norm_model
        requested_backup = str(backup_model or "").strip() or self.norm_model

        installed: list[str] | None = None
        try:
            list_models = getattr(base_client, "list_models", None)
            if callable(list_models):
                installed = [str(name).strip() for name in list_models() if str(name).strip()]
        except Exception as exc:
            logging.warning(
                "Maintenance model inventory lookup failed; using configured names directly error=%s",
                exc,
            )

        requested = (requested_primary, requested_backup, self.norm_model)
        models: list[str] = []
        self.resolution_log: list[dict[str, object]] = []
        for configured in requested:
            resolved = configured
            match_kind = "configured"
            candidates: tuple[str, ...] = ()
            if installed is not None:
                resolved, match_kind, candidates = self.resolve_model_name(configured, installed)
                if resolved is None:
                    logging.warning(
                        "Maintenance model selector could not unambiguously resolve configured=%r kind=%s candidates=%s; skipping",
                        configured,
                        match_kind,
                        list(candidates),
                    )
                    self.resolution_log.append(
                        {
                            "configured": configured,
                            "resolved": None,
                            "match": match_kind,
                            "candidates": list(candidates),
                        }
                    )
                    continue
            if resolved and resolved not in models:
                models.append(resolved)
            self.resolution_log.append(
                {
                    "configured": configured,
                    "resolved": resolved,
                    "match": match_kind,
                    "candidates": list(candidates),
                }
            )
            if resolved != configured:
                logging.info(
                    "Maintenance model selector resolved configured=%r -> installed=%r match=%s",
                    configured,
                    resolved,
                    match_kind,
                )

        # If Ollama inventory was available but even the configured norm nickname did
        # not resolve, retaining the literal ``norm`` is still the final safety path.
        # It may be a valid alias created after the inventory lookup, and normal model
        # failover will surface an actual generation error if it is not available.
        if self.norm_model not in models and not any(
            self._same_model_family(name, self.norm_model) for name in models
        ):
            models.append(self.norm_model)

        self.models = tuple(models or (self.norm_model,))
        self._active_index = 0
        self._clients: dict[str, OllamaClient] = {}

    @staticmethod
    def _normalize_model_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    @staticmethod
    def _base_model_name(value: str) -> str:
        return str(value or "").split(":", 1)[0].strip()

    @classmethod
    def _same_model_family(cls, left: str, right: str) -> bool:
        return cls._normalize_model_name(cls._base_model_name(left)) == cls._normalize_model_name(
            cls._base_model_name(right)
        )

    @classmethod
    def resolve_model_name(
        cls,
        requested: str,
        installed_models: list[str] | tuple[str, ...],
        *,
        fuzzy_floor: float = 0.62,
        fuzzy_margin: float = 0.10,
    ) -> tuple[str | None, str, tuple[str, ...]]:
        """Resolve a user-friendly model name to one installed Ollama tag.

        Deterministic precedence:
          1. exact full tag, case-insensitive
          2. exact base model name (before ``:tag``), preferring an explicit
             ``:latest`` when exactly one exists
          3. normalized starts-with
          4. normalized substring
          5. fuzzy similarity with a minimum score and winning margin

        Any ambiguous tier returns ``None`` instead of silently choosing a model.
        """
        wanted = str(requested or "").strip()
        installed = tuple(dict.fromkeys(str(name).strip() for name in installed_models if str(name).strip()))
        if not wanted:
            return None, "blank", ()
        if not installed:
            return None, "not-installed", ()

        folded = wanted.casefold()
        exact = tuple(name for name in installed if name.casefold() == folded)
        if len(exact) == 1:
            return exact[0], "exact-tag", exact
        if len(exact) > 1:
            return None, "ambiguous-exact-tag", exact

        wanted_base = cls._base_model_name(wanted).casefold()
        base = tuple(name for name in installed if cls._base_model_name(name).casefold() == wanted_base)
        if len(base) == 1:
            return base[0], "exact-base", base
        if len(base) > 1:
            latest = tuple(name for name in base if name.casefold().endswith(":latest"))
            if len(latest) == 1:
                return latest[0], "exact-base-latest", latest
            return None, "ambiguous-exact-base", base

        wanted_norm = cls._normalize_model_name(cls._base_model_name(wanted))
        if not wanted_norm:
            return None, "blank-normalized", ()

        normalized = [(name, cls._normalize_model_name(cls._base_model_name(name))) for name in installed]
        starts = tuple(name for name, norm in normalized if norm.startswith(wanted_norm))
        if len(starts) == 1:
            return starts[0], "starts-with", starts
        if len(starts) > 1:
            return None, "ambiguous-starts-with", starts

        contains = tuple(name for name, norm in normalized if wanted_norm in norm)
        if len(contains) == 1:
            return contains[0], "substring", contains
        if len(contains) > 1:
            return None, "ambiguous-substring", contains

        scored = sorted(
            (
                SequenceMatcher(None, wanted_norm, norm).ratio(),
                name,
            )
            for name, norm in normalized
            if norm
        )
        scored.reverse()
        if not scored or scored[0][0] < float(fuzzy_floor):
            return None, "no-match", ()
        best_score, best_name = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if len(scored) > 1 and (best_score - second_score) < float(fuzzy_margin):
            close = tuple(name for score, name in scored if score >= max(fuzzy_floor, best_score - fuzzy_margin))
            return None, "ambiguous-fuzzy", close
        return best_name, "fuzzy", (best_name,)

    @property
    def model(self) -> str:
        return self.models[self._active_index]

    @property
    def model_chain(self) -> tuple[str, ...]:
        return self.models

    @staticmethod
    def parse_json(text: str) -> dict:
        return OllamaClient.parse_json(text)

    def _client(self, model: str) -> OllamaClient:
        existing = self._clients.get(model)
        if existing is not None:
            return existing
        source = self.base_client
        client = OllamaClient(
            base_url=source.base_url,
            model=model,
            timeout_seconds=source.timeout_seconds,
            activity_sink=source.activity_sink,
            activity_source=f"maintenance-condense:{model}",
            crash_sink=source.crash_sink,
        )
        self._clients[model] = client
        return client

    def _call(self, method: str, *args, **kwargs):
        last_error: Exception | None = None
        start = self._active_index
        for index in range(start, len(self.models)):
            model = self.models[index]
            client = self._client(model)
            try:
                value = getattr(client, method)(*args, **kwargs)
                self._active_index = index
                return value
            except ModelGenerationCancelled:
                raise
            except Exception as exc:
                last_error = exc
                if index + 1 < len(self.models):
                    logging.warning(
                        "Maintenance condensation model failed model=%s; trying fallback model=%s error=%s",
                        model, self.models[index + 1], exc,
                    )
                    self._active_index = index + 1
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("maintenance model chain is empty")

    def generate(self, prompt: str, **kwargs) -> str:
        return self._call("generate", prompt, **kwargs)

    def generate_complete_text(self, prompt: str, **kwargs) -> str:
        return self._call("generate_complete_text", prompt, **kwargs)
