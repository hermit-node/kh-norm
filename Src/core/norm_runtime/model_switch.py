from __future__ import annotations


def _base(name: str) -> str:
    return str(name or "").strip().split(":", 1)[0].casefold()


def same_model(left: str, right: str) -> bool:
    a = str(left or "").strip().casefold()
    b = str(right or "").strip().casefold()
    if a == b:
        return True
    if a.endswith(":latest") and a[:-7] == b:
        return True
    if b.endswith(":latest") and b[:-7] == a:
        return True
    return False


def ordered_models(names: list[str] | tuple[str, ...], current: str = "norm") -> list[str]:
    unique: dict[str, str] = {}
    for raw in names:
        name = str(raw or "").strip()
        if name:
            unique.setdefault(name.casefold(), name)
    values = list(unique.values())
    values.sort(
        key=lambda name: (
            0 if same_model(name, "norm") else 1,
            name.casefold(),
        )
    )
    return values


def resolve_model_selector(models: list[str], selector: str) -> str:
    value = str(selector or "").strip()
    if not value:
        raise ValueError("model selector is required")
    if value.isdigit():
        index = int(value)
        if index < 1 or index > len(models):
            raise ValueError(f"model index {index} is out of range 1..{len(models)}")
        return models[index - 1]

    exact = [name for name in models if name.casefold() == value.casefold()]
    if len(exact) == 1:
        return exact[0]

    base_matches = [name for name in models if _base(name) == value.casefold()]
    if len(base_matches) == 1:
        return base_matches[0]
    if len(base_matches) > 1:
        raise ValueError(
            f"model name {value!r} is ambiguous; use an exact tag: "
            + ", ".join(base_matches)
        )
    raise ValueError(f"model {value!r} is not installed in Ollama")
