"""Nabler: deferred local verification checks and data-only native report rendering.

Python-authored Table rows may use a value, callable, or trusted script block.
The model-facing adapter from_records() never executes code submitted as data.
"""
from __future__ import annotations

import inspect
import json
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from tabulate import tabulate
except ModuleNotFoundError as exc:
    if exc.name != "tabulate":
        raise
    # A PyInstaller-packed norm.exe may not include venv dependencies.
    # Nabler loads its pinned pure-Python dependency from the sibling venv.
    import importlib.util
    import sys

    _module_path = (
        Path(__file__).resolve().parents[3]
        / ".venv" / "Lib" / "site-packages" / "tabulate" / "__init__.py"
    )
    if not _module_path.is_file():
        raise
    _spec = importlib.util.spec_from_file_location(
        "_nabler_external_tabulate", _module_path,
        submodule_search_locations=[str(_module_path.parent)],
    )
    if _spec is None or _spec.loader is None:
        raise
    _dependency = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _dependency
    _spec.loader.exec_module(_dependency)
    tabulate = _dependency.tabulate

_UNSET = object()
_STATUSES = ("PASS", "FAIL", "WARN", "INFO", "SKIP")
_GLYPHS = {"PASS": "✓", "FAIL": "✗", "WARN": "!", "INFO": "·", "SKIP": "–"}


def _display(value: Any) -> str:
    if isinstance(value, subprocess.CompletedProcess):
        out = (str(value.stdout or "") or str(value.stderr or "")).strip()
        return f"exit={value.returncode}" + (f"; {out[:180]}" if out else "")
    if value is None:
        return "—"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _infer(value: Any, rule: Any) -> str:
    if callable(rule):
        return "PASS" if bool(rule(value)) else "FAIL"
    if isinstance(rule, bool):
        return "PASS" if rule else "FAIL"
    if isinstance(rule, str) and rule.strip().upper() in _STATUSES:
        return rule.strip().upper()
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    if isinstance(value, subprocess.CompletedProcess):
        return "PASS" if value.returncode == 0 else "FAIL"
    if isinstance(value, dict):
        for key in ("ok", "success", "passed"):
            if isinstance(value.get(key), bool):
                return "PASS" if value[key] else "FAIL"
        explicit = str(value.get("status", "")).upper()
        if explicit in _STATUSES:
            return explicit
    return "INFO"


@dataclass
class _Row:
    name: str
    check: Any = None
    success: Any = None
    detail: str = ""
    done: bool = False
    result: Any = field(default_factory=lambda: _UNSET)
    status: str = "INFO"


class Table:
    """Render a result set; execute local deferred checks only when first rendered.

    >>> report = Table("Release verification")
    >>> report.add_row("Compilation", lambda: True)
    >>> report.add_row("Integrity", script="result = all([True, True])")
    >>> report.render(output="markdown")  # doctest: +ELLIPSIS
    'Release verification...'
    """

    def __init__(self, title: str = "", *, mode: str = "auto"):
        self.title = str(title)
        self.mode = mode
        self._rows: list[_Row] = []

    def add_row(
        self,
        name: str,
        check: Any = None,
        success: Any = None,
        detail: str = "",
        *,
        script: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> "Table":
        """Accept values, lambdas, or a trusted multiline Python script.

        For script=, assign the variable `result` in the block. Ordinary
        imports, loops, if, try/except and local variables are supported.
        Caller locals/globals are copied on registration (shallowly); use
        context= for explicit overrides. This is not a sandbox. NEVER pass
        untrusted text to script=.
        """
        if script is not None:
            if check is not None:
                raise ValueError("provide check or script, not both")
            if not isinstance(script, str) or not script.strip():
                raise ValueError("script must be nonempty Python source")
            if context is not None and not isinstance(context, dict):
                raise TypeError("context must be a dictionary")
            caller = inspect.currentframe().f_back
            try:
                scope = dict(caller.f_globals) if caller else {}
                if caller:
                    scope.update(caller.f_locals)
                if context is not None:
                    scope.update(context)
            finally:
                del caller
            source = textwrap.dedent(script)

            def execute_script() -> Any:
                namespace = dict(scope)
                namespace["result"] = _UNSET
                exec(compile(source, f"<Nabler: {name}>", "exec"), namespace, namespace)
                answer = namespace["result"]
                if answer is _UNSET:
                    raise ValueError("inline verification script must assign 'result'")
                return answer

            check = execute_script
        elif context is not None:
            raise ValueError("context is only used with script=")
        self._rows.append(_Row(str(name), check, success, str(detail)))
        return self

    add = add_row

    def run(self, *, force: bool = False) -> "Table":
        for row in self._rows:
            if row.done and not force:
                continue
            try:
                row.result = row.check() if callable(row.check) else row.check
                row.status = _infer(row.result, row.success)
            except Exception as exc:
                row.result = f"{type(exc).__name__}: {exc}"
                row.status = "FAIL"
            row.done = True
        return self

    def as_dict(self) -> dict[str, Any]:
        self.run()
        rows = [
            {"name": r.name, "value": _display(r.result), "status": r.status, "detail": r.detail}
            for r in self._rows
        ]
        counts = {s: sum(r["status"] == s for r in rows) for s in _STATUSES}
        return {"title": self.title, "rows": rows, "counts": counts}

    def render(self, *, mode: str | None = None, output: str = "terminal") -> str:
        data = self.as_dict()
        rows = data["rows"]
        if not rows:
            return self.title or "(empty report)"
        mode = (mode or self.mode).strip().lower()
        output = output.strip().lower()
        if mode == "auto":
            mode = "checklist" if all(not r["detail"] for r in rows) and len(rows) <= 2 else "table"
        if mode not in {"table", "checklist"}:
            raise ValueError("mode must be auto, table, or checklist")
        if output not in {"terminal", "markdown", "plain"}:
            raise ValueError("output must be terminal, markdown, or plain")
        if mode == "checklist":
            lines = [
                f"{_GLYPHS[r['status']]} {r['name']}: {r['value']}"
                + (f" — {r['detail']}" if r["detail"] else "")
                for r in rows
            ]
            text = "\n".join(lines)
        else:
            use_detail = any(r["detail"] for r in rows)
            content = [
                [_GLYPHS[r["status"]] + " " + r["status"], r["name"], r["value"]]
                + ([r["detail"]] if use_detail else [])
                for r in rows
            ]
            headers = ["Status", "Check", "Result"] + (["Detail"] if use_detail else [])
            tablefmt = {"terminal": "rounded_grid", "markdown": "github", "plain": "simple"}[output]
            if output == "markdown":
                content = [[str(cell).replace("\n", "<br>") for cell in row] for row in content]
            else:
                from textwrap import fill
                widths = [None, 38, 75] + ([75] if use_detail else [])
                content = [
                    [fill(str(cell), width=width, break_long_words=False) if width else str(cell)
                     for cell, width in zip(row, widths)]
                    for row in content
                ]
            text = tabulate(content, headers=headers, tablefmt=tablefmt, disable_numparse=True)
        return (self.title + "\n" if self.title else "") + text

    def show(self, *, mode: str | None = None, output: str = "terminal") -> None:
        text = self.render(mode=mode, output=output)
        if output == "terminal":
            try:
                from rich.console import Console
                Console(highlight=False, markup=False).print(text, soft_wrap=False)
                return
            except ImportError:
                pass
        print(text)

    def __str__(self) -> str:
        return self.render()


def table(title: str = "", *, mode: str = "auto") -> Table:
    """Convenience constructor."""
    return Table(title, mode=mode)


def from_records(title: str, rows: list[dict[str, Any]], *, mode: str = "auto") -> Table:
    """DATA ONLY. Ignore any script/check strings as code (never execute them)."""
    report = Table(title, mode=mode)
    if not isinstance(rows, list):
        raise ValueError("rows must be a list of report objects")
    for record in rows:
        if not isinstance(record, dict):
            raise ValueError("each report row must be an object")
        name = str(record.get("name") or record.get("label") or record.get("check") or "")
        if not name:
            raise ValueError("report rows require name or label")
        value = record.get("value", record.get("result"))
        status = record.get("status", record.get("success"))
        if not (isinstance(status, bool) or (isinstance(status, str) and status.upper() in _STATUSES)):
            status = None
        report.add_row(name, value, success=status, detail=str(record.get("detail") or ""))
    return report
