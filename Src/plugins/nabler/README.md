# Nabler — structured verification and conclusion reporting

Nabler is a built-in Schema-2 plugin available to Norm as **one data-only native
reporting tool**: `plugin_nabler__main__render_report`. It is the single merged
successor to the original Nabler and experimental Nabler Inline variants.

Nabler intentionally separates **executing verifications** from **presenting
verified results**. A report is *not* evidence of a check running.

## Native tool (Norm's conclusion stage)

Call once using observed results, not Python source:

```json
{
  "title": "Release verification",
  "rows": [
    {"name": "Compilation", "value": "Passed", "success": true},
    {"name": "Files scanned", "value": 24},
    {"name": "Integrity", "value": "Mismatch", "status": "FAIL", "detail": "sha256"}
  ],
  "mode": "table",
  "output": "markdown"
}
```

Fields: `title`, `rows`, optional `mode` (`auto`, `checklist`, `table`)
and `output` (`markdown`, `terminal`, `plain`). Row fields: `name` or
`label` or `check` (label only), `value` or `result`, `status` or
`success`, and `detail`. Explicit statuses are `PASS`, `FAIL`, `WARN`,
`INFO`, and `SKIP`. Numeric results without a predicate default to INFO.

`render_report` accepts **data only**. Arbitrary `script` and `context` fields
supplied in a native tool call are never evaluated. Norm must perform the
checks beforehand and must not claim the rendering tool ran any checks.

## Local Python API: three authoring approaches

```python
from nabler import table

a = table("Build checks")

# 1. Plain value (status inferred from type or supplied separately)
a.add_row("Files located", 12, success=lambda count: count >= 10)

# 2. Lambda / zero-argument callable (deferred until render)
a.add_row("Imports", lambda: verify_imports(), success=bool)

# 3. Arbitrary trusted inline Python block (must assign `result`)
a.add_row("Integrity", script="""
    found = discover_files()
    hashes = [verify_hash(path) for path in found]
    result = {"ok": bool(found) and all(hashes), "files": len(found)}
""")

a.show()  # or a.render(output="markdown"), a.as_dict()
```

Rows run once on first render or `as_dict()`; `a.run(force=True)` reruns them.
Exceptions and missing `result` are reported as failed checks. The inline
block sees a shallow snapshot of caller globals/locals when `add_row` is called;
`context={"name": value}` overrides these bindings. A lambda can span lines
but still must be a single expression, whereas `script=` supports assignment,
loops, imports and exception handling.

**Security boundary:** `script=` uses Python `exec()` and is *not sandboxed*.
Only use literal or otherwise trusted local-script source, never model/tool
arguments, downloaded content, or user-provided untrusted text.

## Packaging and rebuild

- Source lives entirely in `plugins/nabler/`; `plugin.json` records the
  deterministic SHA-256 over `src/` for Norm's plugin identity verifier.
- `tabulate==0.9.0` is pinned in both Python dependency manifests; the
  native plugin uses the installed runtime's library even in packed mode.
- The native plugin is discovered from its packaged `plugin.json`; no `.pth`
  is needed for **native** registration.
- For separate standalone Python scripts that import `nabler` from arbitrary
  directories, run `C:\Norm\.venv\Scripts\python.exe C:\Norm\plugins\nabler\install.py`
  after the venv is created. This writes only `nabler-plugin.pth` into that venv.
- Run `tools/test_nabler.py` and `tools/test_requirements_manifest.py` before
  packaging and after a deployment. The standard build does not create `.venv`
  or a compiled EXE inside the portable source archive.

## Selection

Norm recommended original Nabler for user-facing checklist output because the
native paths were equivalent, with Inline useful exclusively for local script
authoring. Version 0.54.3 retains original `plugin_nabler__main__render_report`
while including Inline's additional `script=` / `context=` local API under the
**same** plugin. There is no `plugins/nabler_inline` in this release.
