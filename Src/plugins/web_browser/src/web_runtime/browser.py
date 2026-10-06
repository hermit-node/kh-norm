from __future__ import annotations

import contextlib
import importlib.metadata
import os
import shutil
from pathlib import Path
from typing import Iterator

from .security import validate_session_name


def runtime_root() -> Path:
    return Path(__file__).resolve().parents[4]


def workspace_root() -> Path:
    from norm_runtime.settings import load_path_settings
    return Path(load_path_settings(runtime_root())["workspace_root"]).resolve()


def web_root() -> Path:
    root = workspace_root() / "web"
    for child in (root, root / "downloads", root / "pages", root / "sessions", root / "cache"):
        child.mkdir(parents=True, exist_ok=True)
    return root


def browser_status_info() -> dict:
    info: dict = {
        "playwright_installed": False,
        "playwright_version": None,
        "system_browsers": [],
        "workspace": str(web_root()),
    }
    try:
        info["playwright_version"] = importlib.metadata.version("playwright")
        info["playwright_installed"] = True
    except importlib.metadata.PackageNotFoundError:
        pass

    candidates: list[tuple[str, Path]] = []
    for executable in ("msedge.exe", "chrome.exe", "msedge", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(executable)
        if found:
            candidates.append((executable, Path(found)))
    if os.name == "nt":
        for env_name, rels in {
            "ProgramFiles(x86)": [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"],
            "ProgramFiles": [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"],
            "LOCALAPPDATA": [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"],
        }.items():
            base = os.environ.get(env_name)
            if not base:
                continue
            for rel in rels:
                path = Path(base) / rel
                if path.is_file():
                    candidates.append((path.name, path))
    seen: set[str] = set()
    for label, path in candidates:
        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        info["system_browsers"].append({"name": label, "path": str(path.resolve())})
    return info


@contextlib.contextmanager
def browser_context(session: str = "default", headless: bool = True) -> Iterator[tuple[object, object, str]]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("Playwright is not installed in Norm's Python environment") from exc

    profile = web_root() / "sessions" / validate_session_name(session)
    profile.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    context = None
    errors: list[str] = []
    try:
        selected = ""
        for label, kwargs in (
            ("Microsoft Edge", {"channel": "msedge"}),
            ("Google Chrome", {"channel": "chrome"}),
            ("Playwright Chromium", {}),
        ):
            try:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    headless=bool(headless),
                    accept_downloads=True,
                    viewport={"width": 1440, "height": 1000},
                    locale="en-US",
                    **kwargs,
                )
                selected = label
                break
            except Exception as exc:
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                    context = None
        if context is None:
            raise RuntimeError(
                "No usable Chromium browser was found. Install Microsoft Edge/Chrome or run "
                "'<Norm venv>\\Scripts\\python.exe -m playwright install chromium'. Attempts: "
                + " | ".join(errors)
            )
        yield playwright, context, selected
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        try:
            playwright.stop()
        except Exception:
            pass
