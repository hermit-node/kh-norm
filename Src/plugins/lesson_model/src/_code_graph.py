from __future__ import annotations

import ast
import hashlib
import html
import json
import os
import re
import zipfile
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath
from typing import Any


GRAPH_SCHEMA = "norm.code_graph.v1"
STATIC_LANGUAGES = {"python", "javascript", "typescript", "vue", "svelte"}
EDGE_KINDS = {
    "imports", "calls", "returns", "reads", "writes", "emits", "subscribes",
    "awaits", "inherits", "implements", "passes_data",
}


def _stable_id(kind: str, *parts: object) -> str:
    raw = "|".join([kind, *(str(p) for p in parts)]).encode("utf-8")
    return f"{kind[:1]}_{hashlib.sha1(raw).hexdigest()[:16]}"


def _expr(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return type(node).__name__


def _names(node: ast.AST | None) -> list[str]:
    if node is None:
        return []
    return sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)})[:80]


def _docline(node: ast.AST) -> str:
    try:
        doc = ast.get_docstring(node) or ""
    except Exception:
        doc = ""
    return " ".join(doc.split())[:500]


def _module_name(path: str) -> str:
    p = PurePosixPath(path)
    suffix = p.suffix.lower()
    parts = list(p.with_suffix("").parts) if suffix else list(p.parts)
    if parts and parts[-1] in {"__init__", "index"}:
        parts.pop()
    return ".".join(parts)


def _python_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, list[str], str]:
    args = node.args
    rendered: list[str] = []
    parameter_names: list[str] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults: list[ast.AST | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults):
        parameter_names.append(arg.arg)
        text = arg.arg
        if arg.annotation is not None:
            text += f": {_expr(arg.annotation)}"
        if default is not None:
            text += f" = {_expr(default)}"
        rendered.append(text)
    if args.vararg:
        parameter_names.append(args.vararg.arg)
        rendered.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        rendered.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parameter_names.append(arg.arg)
        text = arg.arg
        if arg.annotation is not None:
            text += f": {_expr(arg.annotation)}"
        if default is not None:
            text += f" = {_expr(default)}"
        rendered.append(text)
    if args.kwarg:
        parameter_names.append(args.kwarg.arg)
        rendered.append("**" + args.kwarg.arg)
    ret = _expr(node.returns) if node.returns is not None else ""
    signature = f"{node.name}({', '.join(rendered)})"
    if ret:
        signature += f" -> {ret}"
    return signature, parameter_names, ret


def _return_summary(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    values: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Return):
            values.append("None" if child.value is None else _expr(child.value))
            if len(values) >= 8:
                break
    if not values:
        return "implicit None / no explicit return observed"
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return " | ".join(unique)[:900]


def _python_file(path: str, text: str) -> dict:
    try:
        tree = ast.parse(text)
    except Exception as exc:
        return {"nodes": [], "calls": [], "imports": [], "inherits": [], "error": f"{type(exc).__name__}: {exc}"}

    module = _module_name(path)
    module_id = _stable_id("node", path, "<module>")
    nodes: list[dict] = [{
        "id": module_id,
        "kind": "file",
        "language": "python",
        "file": path,
        "module": module,
        "symbol": "<module>",
        "qualified_symbol": module,
        "label": PurePosixPath(path).name,
        "line": 1,
        "end_line": max(1, len(text.splitlines())),
        "signature": "",
        "parameters": [],
        "return_annotation": "",
        "return_summary": "",
        "doc": _docline(tree),
        "structural_cluster": str(PurePosixPath(path).parent) or ".",
        "static_confidence": 1.0,
    }]
    calls: list[dict] = []
    imports: list[dict] = []
    inherits: list[dict] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[str] = []
            self.current_id = module_id
            self.class_depth = 0

        def visit_Import(self, node: ast.Import) -> Any:
            for alias in node.names:
                imports.append({
                    "source": self.current_id,
                    "file": path,
                    "line": node.lineno,
                    "module": alias.name,
                    "name": "",
                    "alias": alias.asname or alias.name.split(".")[0],
                    "level": 0,
                })
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
            for alias in node.names:
                imports.append({
                    "source": self.current_id,
                    "file": path,
                    "line": node.lineno,
                    "module": node.module or "",
                    "name": alias.name,
                    "alias": alias.asname or alias.name,
                    "level": int(node.level or 0),
                })
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> Any:
            symbol = ".".join([*self.scope, node.name])
            qsym = f"{module}.{symbol}" if module else symbol
            nid = _stable_id("node", path, symbol)
            nodes.append({
                "id": nid,
                "kind": "class",
                "language": "python",
                "file": path,
                "module": module,
                "symbol": symbol,
                "qualified_symbol": qsym,
                "label": node.name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "signature": "class " + node.name,
                "parameters": [],
                "return_annotation": "",
                "return_summary": "",
                "doc": _docline(node),
                "structural_cluster": str(PurePosixPath(path).parent) or ".",
                "static_confidence": 1.0,
            })
            for base in node.bases:
                inherits.append({"source": nid, "target_text": _expr(base), "file": path, "line": node.lineno})
            prev = self.current_id
            self.current_id = nid
            self.scope.append(node.name)
            self.class_depth += 1
            for child in node.body:
                self.visit(child)
            self.class_depth -= 1
            self.scope.pop()
            self.current_id = prev

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            symbol = ".".join([*self.scope, node.name])
            qsym = f"{module}.{symbol}" if module else symbol
            nid = _stable_id("node", path, symbol)
            signature, params, ret = _python_signature(node)
            kind = "method" if self.class_depth else ("async_function" if isinstance(node, ast.AsyncFunctionDef) else "function")
            nodes.append({
                "id": nid,
                "kind": kind,
                "language": "python",
                "file": path,
                "module": module,
                "symbol": symbol,
                "qualified_symbol": qsym,
                "label": node.name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "signature": signature,
                "parameters": params,
                "return_annotation": ret,
                "return_summary": _return_summary(node),
                "doc": _docline(node),
                "structural_cluster": str(PurePosixPath(path).parent) or ".",
                "static_confidence": 1.0,
            })
            prev = self.current_id
            self.current_id = nid
            self.scope.append(node.name)
            for child in node.body:
                self.visit(child)
            self.scope.pop()
            self.current_id = prev

        def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
            self._function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
            self._function(node)

        def visit_Call(self, node: ast.Call) -> Any:
            args = [_expr(a) for a in node.args[:24]]
            keywords = [{"name": kw.arg or "**", "value": _expr(kw.value)} for kw in node.keywords[:24]]
            vars_found = set()
            for a in node.args:
                vars_found.update(_names(a))
            for kw in node.keywords:
                vars_found.update(_names(kw.value))
            calls.append({
                "source": self.current_id,
                "file": path,
                "line": getattr(node, "lineno", 0),
                "callee": _expr(node.func),
                "arguments": args,
                "keywords": keywords,
                "variable_names": sorted(vars_found)[:80],
                "awaited": isinstance(getattr(node, "parent", None), ast.Await),
            })
            self.generic_visit(node)

    # annotate parent pointers only for await detection
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            try:
                child.parent = parent  # type: ignore[attr-defined]
            except Exception:
                pass
    Visitor().visit(tree)
    return {"nodes": nodes, "calls": calls, "imports": imports, "inherits": inherits, "error": ""}


JS_FN = re.compile(r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)")
JS_ARROW = re.compile(r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>")
JS_IMPORT = re.compile(r"(?m)^\s*import\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]|require\(\s*['\"]([^'\"]+)['\"]\s*\)")
JS_CALL = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(([^;\n]{0,400})\)")


def _js_file(path: str, text: str, language: str) -> dict:
    module = _module_name(path)
    module_id = _stable_id("node", path, "<module>")
    nodes: list[dict] = [{
        "id": module_id, "kind": "file", "language": language, "file": path,
        "module": module, "symbol": "<module>", "qualified_symbol": module,
        "label": PurePosixPath(path).name, "line": 1, "end_line": max(1, len(text.splitlines())),
        "signature": "", "parameters": [], "return_annotation": "",
        "return_summary": "unknown (JavaScript/TypeScript static approximation)", "doc": "",
        "structural_cluster": str(PurePosixPath(path).parent) or ".", "static_confidence": 0.85,
    }]
    spans: list[tuple[int, str]] = []
    for regex in (JS_FN, JS_ARROW):
        for m in regex.finditer(text):
            name = m.group(1)
            params = [x.strip().split(":", 1)[0].strip() for x in m.group(2).split(",") if x.strip()]
            line = text.count("\n", 0, m.start()) + 1
            nid = _stable_id("node", path, name)
            nodes.append({
                "id": nid, "kind": "function", "language": language, "file": path,
                "module": module, "symbol": name, "qualified_symbol": f"{module}.{name}" if module else name,
                "label": name, "line": line, "end_line": line, "signature": f"{name}({', '.join(params)})",
                "parameters": params, "return_annotation": "",
                "return_summary": "unknown (JavaScript/TypeScript static approximation)", "doc": "",
                "structural_cluster": str(PurePosixPath(path).parent) or ".", "static_confidence": 0.72,
            })
            spans.append((line, nid))
    spans.sort()

    imports = []
    for m in JS_IMPORT.finditer(text):
        imported = m.group(1) or m.group(2) or ""
        imports.append({"source": module_id, "file": path, "line": text.count("\n", 0, m.start()) + 1, "module": imported, "name": "", "alias": "", "level": 0})

    calls = []
    for m in JS_CALL.finditer(text):
        callee = m.group(1)
        if callee in {"if", "for", "while", "switch", "function", "catch"}:
            continue
        line = text.count("\n", 0, m.start()) + 1
        source = module_id
        before = [x for x in spans if x[0] <= line]
        if before:
            source = before[-1][1]
        arguments = [x.strip() for x in m.group(2).split(",") if x.strip()][:24]
        variables = sorted({v for arg in arguments for v in re.findall(r"\b[A-Za-z_$][\w$]*\b", arg)})[:80]
        calls.append({"source": source, "file": path, "line": line, "callee": callee, "arguments": arguments, "keywords": [], "variable_names": variables, "awaited": False})
    return {"nodes": nodes, "calls": calls, "imports": imports, "inherits": [], "error": ""}


def _resolve_relative_module(source_file: str, module: str, level: int) -> str:
    source_mod = _module_name(source_file)
    parts = source_mod.split(".")[:-1]
    if level:
        cut = max(0, level - 1)
        if cut:
            parts = parts[: max(0, len(parts) - cut)]
    if module:
        parts.extend([x for x in module.split(".") if x])
    return ".".join(parts)


def _candidate_internal_module(module: str, module_to_file: dict[str, str]) -> str | None:
    if module in module_to_file:
        return module_to_file[module]
    # JS relative paths often carry ./ and extensionless refs.
    normalized = module.replace("/", ".").lstrip(".")
    if normalized in module_to_file:
        return module_to_file[normalized]
    for key, value in module_to_file.items():
        if key.endswith("." + normalized) or normalized.endswith("." + key):
            return value
    return None


def build_graph(scan: dict, max_nodes: int = 5000, include_external: bool = True) -> dict:
    source = Path(scan["source_path"])
    files = {x["path"]: x for x in (scan.get("files") or [])}
    order = [p for p in (scan.get("runtime_order") or []) if p in files and files[p].get("language") in STATIC_LANGUAGES]
    max_nodes = max(50, min(int(max_nodes), 20000))

    nodes: list[dict] = []
    calls: list[dict] = []
    imports: list[dict] = []
    inherits: list[dict] = []
    parse_errors: list[dict] = []

    with zipfile.ZipFile(source, "r") as zf:
        for path in order:
            if len(nodes) >= max_nodes:
                break
            info = files[path]
            if int(info.get("size_bytes") or 0) > 4 * 1024 * 1024:
                parse_errors.append({"file": path, "error": "graph parse skipped: source file > 4 MiB"})
                continue
            try:
                text = zf.read(path).decode("utf-8", errors="replace")
            except Exception as exc:
                parse_errors.append({"file": path, "error": f"{type(exc).__name__}: {exc}"})
                continue
            lang = str(info.get("language") or "")
            result = _python_file(path, text) if lang == "python" else _js_file(path, text, lang)
            nodes.extend(result["nodes"])
            calls.extend(result["calls"])
            imports.extend(result["imports"])
            inherits.extend(result["inherits"])
            if result.get("error"):
                parse_errors.append({"file": path, "error": result["error"]})

    nodes = nodes[:max_nodes]
    node_by_id = {n["id"]: n for n in nodes}
    file_module_id = {n["file"]: n["id"] for n in nodes if n["kind"] == "file"}
    module_to_file = {_module_name(path): path for path in file_module_id}
    by_file_label: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_label: dict[str, list[str]] = defaultdict(list)
    by_qsym: dict[str, str] = {}
    for n in nodes:
        by_file_label[(n["file"], n["label"])].append(n["id"])
        by_label[n["label"]].append(n["id"])
        by_qsym[n["qualified_symbol"]] = n["id"]

    external_nodes: dict[str, dict] = {}
    def external(name: str, kind: str) -> str:
        nid = _stable_id("external", kind, name)
        if nid not in external_nodes:
            external_nodes[nid] = {
                "id": nid, "kind": kind, "language": "external", "file": "", "module": "",
                "symbol": name, "qualified_symbol": name, "label": name, "line": 0, "end_line": 0,
                "signature": "", "parameters": [], "return_annotation": "",
                "return_summary": "external / contract not statically known", "doc": "",
                "structural_cluster": "external", "static_confidence": 0.45,
            }
        return nid

    # Per-file Python import alias table for call resolution.
    alias_table: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    edges: list[dict] = []

    for idx, imp in enumerate(imports):
        source_id = imp["source"]
        source_file = imp["file"]
        module_name = str(imp.get("module") or "")
        if int(imp.get("level") or 0):
            module_name = _resolve_relative_module(source_file, module_name, int(imp.get("level") or 0))
        target_file = _candidate_internal_module(module_name, module_to_file)
        target_id: str | None = None
        confidence = 0.0
        if target_file and target_file in file_module_id:
            if imp.get("name"):
                q = f"{_module_name(target_file)}.{imp['name']}"
                target_id = by_qsym.get(q)
            target_id = target_id or file_module_id[target_file]
            confidence = 0.98
        elif include_external:
            target_id = external(module_name or str(imp.get("name") or "external"), "external_service")
            confidence = 0.8
        if not target_id:
            continue
        alias = str(imp.get("alias") or "")
        if alias:
            alias_table[source_file][alias] = (module_name, str(imp.get("name") or ""))
        edges.append({
            "id": _stable_id("edge", source_id, target_id, "imports", imp["line"], idx),
            "source": source_id, "target": target_id, "kind": "imports", "layer": "structural",
            "file": source_file, "line": imp["line"], "static_confidence": confidence,
            "arguments": [], "keywords": [], "variable_names": [], "bindings": [],
            "expected_response": "module symbols / import side effects",
            "detail": f"import {module_name}" + (f"::{imp.get('name')}" if imp.get("name") else ""),
            "runtime_evidence": [],
        })

    for idx, item in enumerate(inherits):
        source_id = item["source"]
        target_text = str(item.get("target_text") or "")
        candidates = by_label.get(target_text.split(".")[-1], [])
        if len(candidates) == 1:
            target_id, confidence = candidates[0], 0.82
        elif include_external:
            target_id, confidence = external(target_text, "external_type"), 0.45
        else:
            continue
        edges.append({
            "id": _stable_id("edge", source_id, target_id, "inherits", item["line"], idx),
            "source": source_id, "target": target_id, "kind": "inherits", "layer": "structural",
            "file": item["file"], "line": item["line"], "static_confidence": confidence,
            "arguments": [], "keywords": [], "variable_names": [], "bindings": [],
            "expected_response": "inherited interface / behavior", "detail": target_text,
            "runtime_evidence": [],
        })

    for idx, call in enumerate(calls):
        source_id = call["source"]
        if source_id not in node_by_id:
            continue
        source_file = node_by_id[source_id]["file"]
        callee = str(call.get("callee") or "")
        simple = callee.split(".")[-1]
        target_id: str | None = None
        confidence = 0.0

        # same-file symbol
        same = by_file_label.get((source_file, simple), [])
        if len(same) == 1 and same[0] != source_id:
            target_id, confidence = same[0], 0.97

        # imported symbol or imported module alias
        if not target_id:
            head = callee.split(".", 1)[0]
            alias = alias_table.get(source_file, {}).get(head)
            if alias:
                imported_module, imported_name = alias
                tail = callee.split(".", 1)[1] if "." in callee else imported_name
                qsym = ".".join(x for x in [imported_module, tail] if x)
                if qsym in by_qsym:
                    target_id, confidence = by_qsym[qsym], 0.96
                elif imported_name:
                    hits = by_label.get(imported_name, [])
                    if len(hits) == 1:
                        target_id, confidence = hits[0], 0.88

        # unique global label fallback
        if not target_id and len(by_label.get(simple, [])) == 1:
            target_id, confidence = by_label[simple][0], 0.72

        if not target_id and include_external:
            target_id, confidence = external(callee or simple or "unknown_call", "external_call"), 0.42
        if not target_id:
            continue

        target = node_by_id.get(target_id) or external_nodes.get(target_id) or {}
        params = list(target.get("parameters") or [])
        bindings = []
        for i, arg in enumerate(call.get("arguments") or []):
            bindings.append({"parameter": params[i] if i < len(params) else f"arg{i+1}", "expression": arg})
        for kw in call.get("keywords") or []:
            bindings.append({"parameter": kw.get("name") or "**", "expression": kw.get("value")})
        expected = target.get("return_annotation") or target.get("return_summary") or "unknown"
        kind = "awaits" if call.get("awaited") else "calls"
        edges.append({
            "id": _stable_id("edge", source_id, target_id, kind, call["line"], idx),
            "source": source_id, "target": target_id, "kind": kind, "layer": "call",
            "file": source_file, "line": call["line"], "static_confidence": confidence,
            "arguments": call.get("arguments") or [], "keywords": call.get("keywords") or [],
            "variable_names": call.get("variable_names") or [], "bindings": bindings,
            "expected_response": str(expected)[:1200], "detail": callee,
            "runtime_evidence": [],
        })

    nodes.extend(external_nodes.values())
    node_by_id = {n["id"]: n for n in nodes}

    # Apply semantic annotations produced by Norm. These are additive; static evidence remains intact.
    semantics = ((scan.get("code_graph") or {}).get("semantics") or {}) if isinstance(scan.get("code_graph"), dict) else {}
    node_sem = semantics.get("nodes") if isinstance(semantics.get("nodes"), dict) else {}
    edge_sem = semantics.get("edges") if isinstance(semantics.get("edges"), dict) else {}
    cluster_sem = semantics.get("clusters") if isinstance(semantics.get("clusters"), dict) else {}
    extra_edges = semantics.get("extra_edges") if isinstance(semantics.get("extra_edges"), list) else []

    for node in nodes:
        ann = node_sem.get(node["id"]) or {}
        if isinstance(ann, dict):
            for key in ("semantic_role", "intent", "expected_input", "expected_output", "capability_cluster", "notes"):
                if ann.get(key) not in (None, ""):
                    node[key] = ann[key]

    edge_ids = {e["id"] for e in edges}
    for edge in edges:
        ann = edge_sem.get(edge["id"]) or {}
        if isinstance(ann, dict):
            for key in ("data_meaning", "expected_response", "notes"):
                if ann.get(key) not in (None, ""):
                    edge[key] = ann[key]

    for idx, edge in enumerate(extra_edges):
        if not isinstance(edge, dict):
            continue
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        kind = str(edge.get("kind") or "passes_data")
        if source_id not in node_by_id or target_id not in node_by_id or kind not in EDGE_KINDS:
            continue
        eid = str(edge.get("id") or _stable_id("edge", source_id, target_id, kind, "semantic", idx))
        if eid in edge_ids:
            continue
        edges.append({
            "id": eid, "source": source_id, "target": target_id, "kind": kind,
            "layer": "data_flow" if kind in {"reads", "writes", "passes_data", "returns"} else "semantic",
            "file": str(edge.get("file") or ""), "line": int(edge.get("line") or 0),
            "static_confidence": float(edge.get("static_confidence") or 0.0),
            "arguments": list(edge.get("arguments") or []), "keywords": list(edge.get("keywords") or []),
            "variable_names": list(edge.get("variable_names") or []), "bindings": list(edge.get("bindings") or []),
            "expected_response": str(edge.get("expected_response") or ""),
            "data_meaning": str(edge.get("data_meaning") or ""), "notes": str(edge.get("notes") or ""),
            "detail": str(edge.get("detail") or "semantic relationship"), "runtime_evidence": list(edge.get("runtime_evidence") or []),
        })
        edge_ids.add(eid)

    entry_files = {x.get("path") for x in (scan.get("likely_entrypoints") or [])}
    for n in nodes:
        n["entrypoint_hint"] = bool(n.get("file") in entry_files)
        n["capability_cluster"] = str(n.get("capability_cluster") or n.get("structural_cluster") or ".")

    clusters: dict[str, dict] = {}
    for n in nodes:
        key = n["capability_cluster"]
        item = clusters.setdefault(key, {"id": key, "label": key, "node_count": 0, "kind": "capability" if "semantic_role" in n else "structural"})
        item["node_count"] += 1
        ann = cluster_sem.get(key) or {}
        if isinstance(ann, dict):
            item.update({k: v for k, v in ann.items() if v not in (None, "")})

    stats = {
        "node_count": len(nodes), "edge_count": len(edges), "cluster_count": len(clusters),
        "call_edges": sum(1 for e in edges if e["kind"] in {"calls", "awaits"}),
        "import_edges": sum(1 for e in edges if e["kind"] == "imports"),
        "data_flow_edges": sum(1 for e in edges if e["kind"] in {"reads", "writes", "passes_data", "returns"}),
        "external_nodes": sum(1 for n in nodes if str(n["kind"]).startswith("external")),
        "runtime_observed_edges": sum(1 for e in edges if e.get("runtime_evidence")),
    }
    return {
        "schema": GRAPH_SCHEMA,
        "project_name": scan.get("project_name") or "",
        "version_label": scan.get("version_label") or "",
        "scan_id": scan.get("scan_id") or "",
        "source_sha256": scan.get("source_sha256") or "",
        "nodes": nodes,
        "edges": edges,
        "clusters": list(clusters.values()),
        "parse_errors": parse_errors,
        "stats": stats,
        "layers": ["structural", "call", "data_flow", "runtime", "capability", "dependency", "change_impact"],
    }


def _safe_stem(scan: dict) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(scan.get("project_name") or "project")).strip("-") or "project"
    ver = re.sub(r"[^A-Za-z0-9._-]+", "-", str(scan.get("version_label") or "")).strip("-")
    return name + (("-" + ver) if ver else "")


def write_graph_files(scan: dict, graph: dict, output_dir: str = "") -> dict:
    if output_dir:
        directory = Path(output_dir).expanduser().resolve()
    else:
        # state_root/code_memory/sources/<sha>.zip -> state_root/code_memory/maps
        source = Path(scan["source_path"])
        directory = source.parent.parent / "maps"
    directory.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(scan)
    graph_path = directory / f"{stem}.codegraph.json"
    html_path = directory / f"{stem}.topology.html"
    graph_text = json.dumps(graph, indent=2, ensure_ascii=False) + "\n"
    graph_path.write_text(graph_text, encoding="utf-8")
    html_text = _render_html(graph)
    html_path.write_text(html_text, encoding="utf-8")
    return {
        "graph_path": str(graph_path),
        "graph_sha256": hashlib.sha256(graph_text.encode("utf-8")).hexdigest(),
        "html_path": str(html_path),
        "html_sha256": hashlib.sha256(html_text.encode("utf-8")).hexdigest(),
        "format": "standalone HTML + canonical CodeGraph JSON",
        "stats": graph["stats"],
        "parse_errors": graph["parse_errors"][:100],
    }


def _render_html(graph: dict) -> str:
    blob = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape((str(graph.get("project_name") or "Code Topology") + " " + str(graph.get("version_label") or "")).strip())
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Capability Topology</title>
<style>
:root{{--bg:#0a0d12;--panel:#101722;--panel2:#161f2d;--text:#e8eef7;--muted:#91a1b6;--border:#2b3749;--accent:#7fb0ff;--call:#83d4a5;--import:#c59cff;--data:#f0c873;--runtime:#ff8e8e;--external:#687384}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Inter,Segoe UI,Arial,sans-serif}}
header{{display:flex;gap:9px;align-items:center;flex-wrap:wrap;padding:10px 12px;background:#0d131c;border-bottom:1px solid var(--border)}}
header strong{{font-size:16px}} .meta{{color:var(--muted)}} button,input,select{{font:inherit;color:var(--text);background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:8px 10px}} button{{cursor:pointer}} input{{min-width:220px;flex:1}}
#app{{display:grid;grid-template-columns:minmax(0,1fr) 350px;min-height:760px}} #left{{position:relative;min-width:0;overflow:hidden}} #stage{{width:100%;height:760px;display:block;background:radial-gradient(circle at 24% 20%,rgba(127,176,255,.06),transparent 32%),var(--bg)}}
#details{{border-left:1px solid var(--border);background:var(--panel);padding:14px;overflow:auto;max-height:760px}} #details h2{{font-size:15px;margin:0 0 8px}} #details h3{{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:17px 0 5px}} pre{{white-space:pre-wrap;word-break:break-word;margin:0;color:#cbd6e5}} .kv{{display:grid;grid-template-columns:100px minmax(0,1fr);gap:4px 8px}} .kv b{{color:var(--muted);font-weight:600}}
.edge{{fill:none;stroke-width:1.4;opacity:.55;cursor:pointer}} .edge.imports{{stroke:var(--import);stroke-dasharray:5 4}} .edge.calls,.edge.awaits{{stroke:var(--call)}} .edge.passes_data,.edge.reads,.edge.writes,.edge.returns{{stroke:var(--data)}} .edge.runtime{{stroke:var(--runtime);stroke-width:2.6}} .edge.fade{{opacity:.035}} .edge.active{{opacity:1;stroke-width:3.4}}
.node{{cursor:pointer}} .node rect{{fill:#182231;stroke:#39485e;stroke-width:1.2;rx:8}} .node.function rect,.node.method rect,.node.async_function rect{{fill:#14251d;stroke:#315942}} .node.class rect{{fill:#271d34;stroke:#58406e}} .node.external_service rect,.node.external_call rect,.node.external_type rect{{fill:#171a20;stroke:#59616d;stroke-dasharray:4 3}} .node.entry rect{{stroke:var(--data);stroke-width:2.2}} .node text{{fill:var(--text);font-size:10px;pointer-events:none}} .node .small{{fill:var(--muted);font-size:8.5px}} .node.fade{{opacity:.09}} .node.active rect{{stroke:var(--accent);stroke-width:3}}
#hint{{position:absolute;left:12px;bottom:12px;background:rgba(16,23,34,.94);border:1px solid var(--border);border-radius:8px;padding:7px 9px;color:var(--muted);pointer-events:none}} .badge{{display:inline-block;padding:2px 6px;border:1px solid var(--border);border-radius:99px;margin:2px;color:var(--muted)}}
@media(max-width:820px){{#app{{grid-template-columns:1fr}}#details{{border-left:0;border-top:1px solid var(--border);max-height:none}}#stage{{height:620px}}}}
</style>
</head>
<body>
<header>
<strong>{title}</strong><span id="stats" class="meta"></span>
<input id="search" type="search" placeholder="Search function, file, role, cluster…">
<select id="scope"><option value="chain" selected>Full chain</option><option value="direct">Direct neighbors</option><option value="upstream">Upstream</option><option value="downstream">Downstream</option></select>
<select id="layer"><option value="all">All relationships</option><option value="call">Calls / awaits</option><option value="structural">Imports / inheritance</option><option value="data">Data flow</option><option value="observed">Observed runtime only</option></select>
<button id="reset" type="button">Reset</button>
</header>
<div id="app"><div id="left"><svg id="stage" viewBox="0 0 1500 920" aria-label="Interactive executable architecture map"></svg><div id="hint">Select a node or connector. Unrelated code fades. Wheel zoom; drag empty space to pan.</div></div><aside id="details">Select something to inspect its contract and blast radius.</aside></div>
<script>
const G={blob};
const svg=document.getElementById('stage'),details=document.getElementById('details'),search=document.getElementById('search'),scopeSel=document.getElementById('scope'),layerSel=document.getElementById('layer');
document.getElementById('stats').textContent=`${{G.stats.node_count}} nodes · ${{G.stats.edge_count}} edges · ${{G.stats.cluster_count}} clusters`;
const NS='http://www.w3.org/2000/svg',byId=new Map(G.nodes.map(n=>[n.id,n])),out=new Map(),inc=new Map(); for(const n of G.nodes){{out.set(n.id,[]);inc.set(n.id,[])}} for(const e of G.edges){{if(out.has(e.source))out.get(e.source).push(e);if(inc.has(e.target))inc.get(e.target).push(e)}}
const clusters=[...new Set(G.nodes.map(n=>n.capability_cluster||'.'))].sort(),ci=new Map(clusters.map((x,i)=>[x,i])),pos=new Map();
for(let i=0;i<G.nodes.length;i++){{const n=G.nodes[i],c=ci.get(n.capability_cluster||'.')||0,a=(i+c*13)*2.3999632297,r=45+25*Math.sqrt(i+1),cx=750+((c%5)-2)*180,cy=460+((Math.floor(c/5)%5)-2)*120;pos.set(n.id,{{x:cx+Math.cos(a)*r,y:cy+Math.sin(a)*r,vx:0,vy:0}})}}
const shown=G.nodes.slice(0,1800),shownSet=new Set(shown.map(n=>n.id)),edges=G.edges.filter(e=>shownSet.has(e.source)&&shownSet.has(e.target)).slice(0,6000);
for(let t=0;t<130;t++){{for(const e of edges){{const a=pos.get(e.source),b=pos.get(e.target);if(!a||!b)continue;const dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1,target=(e.kind==='calls'||e.kind==='awaits')?110:160,f=(d-target)*.0018;a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f}} for(const n of shown){{const p=pos.get(n.id);p.vx*=.74;p.vy*=.74;p.x+=p.vx;p.y+=p.vy;p.x=Math.max(65,Math.min(1435,p.x));p.y=Math.max(45,Math.min(875,p.y))}}}}
const viewport=document.createElementNS(NS,'g'),edgeLayer=document.createElementNS(NS,'g'),nodeLayer=document.createElementNS(NS,'g');svg.appendChild(viewport);viewport.append(edgeLayer,nodeLayer);const eEls=new Map(),nEls=new Map();
function esc(s){{return String(s??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]))}} function path(e){{const a=pos.get(e.source),b=pos.get(e.target);if(!a||!b)return'';const mx=(a.x+b.x)/2,my=(a.y+b.y)/2,dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1,o=16;return`M ${{a.x}} ${{a.y}} Q ${{mx-dy/d*o}} ${{my+dx/d*o}} ${{b.x}} ${{b.y}}`}}
function layerOK(e){{const v=layerSel.value;if(v==='all')return true;if(v==='call')return e.kind==='calls'||e.kind==='awaits';if(v==='structural')return e.kind==='imports'||e.kind==='inherits'||e.kind==='implements';if(v==='data')return ['passes_data','reads','writes','returns'].includes(e.kind);if(v==='observed')return (e.runtime_evidence||[]).length>0;return true}}
for(const e of edges){{const p=document.createElementNS(NS,'path');p.setAttribute('d',path(e));p.setAttribute('class',`edge ${{e.kind}}${{(e.runtime_evidence||[]).length?' runtime':''}}`);p.dataset.id=e.id;edgeLayer.appendChild(p);eEls.set(e.id,p);p.addEventListener('click',ev=>{{ev.stopPropagation();selectEdge(e)}})}}
for(const n of shown){{const g=document.createElementNS(NS,'g'),p=pos.get(n.id);g.setAttribute('class',`node ${{n.kind}}${{n.entrypoint_hint?' entry':''}}`);g.setAttribute('transform',`translate(${{p.x-58}},${{p.y-19}})`);const r=document.createElementNS(NS,'rect');r.setAttribute('width','116');r.setAttribute('height','38');const t=document.createElementNS(NS,'text');t.setAttribute('x','7');t.setAttribute('y','15');t.textContent=(n.label||'').slice(0,18);const s=document.createElementNS(NS,'text');s.setAttribute('x','7');s.setAttribute('y','29');s.setAttribute('class','small');s.textContent=(n.capability_cluster||n.kind||'').slice(0,20);g.append(r,t,s);nodeLayer.appendChild(g);nEls.set(n.id,g);g.addEventListener('click',ev=>{{ev.stopPropagation();selectNode(n)}})}}
function walk(start,dir){{const seen=new Set([start]),q=[start];while(q.length){{const id=q.shift(),list=dir==='out'?(out.get(id)||[]):(inc.get(id)||[]);for(const e of list){{if(!layerOK(e))continue;const nx=dir==='out'?e.target:e.source;if(!seen.has(nx)){{seen.add(nx);q.push(nx)}}}}}}return seen}}
function scope(id){{const m=scopeSel.value;if(m==='upstream')return walk(id,'in');if(m==='downstream')return walk(id,'out');if(m==='direct'){{const s=new Set([id]);for(const e of [...(out.get(id)||[]),...(inc.get(id)||[])])if(layerOK(e)){{s.add(e.source);s.add(e.target)}}return s}}return new Set([...walk(id,'in'),...walk(id,'out')])}}
function apply(ids,activeEdge=''){{for(const [id,el] of nEls){{el.classList.toggle('fade',!ids.has(id));el.classList.toggle('active',ids.size===1&&ids.has(id))}}for(const e of edges){{const el=eEls.get(e.id),ok=ids.has(e.source)&&ids.has(e.target)&&layerOK(e);el.style.display=layerOK(e)?'':'none';el.classList.toggle('fade',!ok);el.classList.toggle('active',e.id===activeEdge)}}}}
function clear(){{for(const el of nEls.values())el.classList.remove('fade','active');for(const e of edges){{const el=eEls.get(e.id);el.classList.remove('fade','active');el.style.display=layerOK(e)?'':'none'}}details.textContent='Select something to inspect its contract and blast radius.'}}
function selectNode(n){{const ids=scope(n.id);apply(ids);nEls.get(n.id)?.classList.add('active');const a=(inc.get(n.id)||[]).filter(layerOK).length,b=(out.get(n.id)||[]).filter(layerOK).length;details.innerHTML=`<h2>${{esc(n.label)}}</h2><div class="kv"><b>Kind</b><span>${{esc(n.kind)}}</span><b>File</b><span>${{esc(n.file)}}:${{n.line||''}}</span><b>Cluster</b><span>${{esc(n.capability_cluster)}}</span><b>Confidence</b><span>${{Math.round((n.static_confidence||0)*100)}}%</span></div><h3>Signature</h3><pre>${{esc(n.signature||'(module/file)')}}</pre><h3>Inputs</h3><pre>${{esc(n.expected_input||(n.parameters||[]).join(', ')||'not statically known')}}</pre><h3>Expected output</h3><pre>${{esc(n.expected_output||n.return_annotation||n.return_summary||'not statically known')}}</pre>${{n.semantic_role?`<h3>Role</h3><pre>${{esc(n.semantic_role)}}</pre>`:''}}${{n.intent?`<h3>Intent</h3><pre>${{esc(n.intent)}}</pre>`:''}}<h3>Connectivity</h3><pre>${{a}} incoming · ${{b}} outgoing</pre>${{n.doc?`<h3>Doc</h3><pre>${{esc(n.doc)}}</pre>`:''}}`}}
function selectEdge(e){{let ids=new Set([e.source,e.target]);if(scopeSel.value==='chain')ids=new Set([...walk(e.source,'in'),...walk(e.target,'out'),e.source,e.target]);else if(scopeSel.value==='upstream')ids=new Set([...walk(e.source,'in'),e.target]);else if(scopeSel.value==='downstream')ids=new Set([e.source,...walk(e.target,'out')]);apply(ids,e.id);const a=byId.get(e.source)||{{label:e.source}},b=byId.get(e.target)||{{label:e.target}},bind=(e.bindings||[]).map(x=>`${{x.parameter}} ← ${{x.expression}}`).join('\n');details.innerHTML=`<h2>${{esc(a.label)}} → ${{esc(b.label)}}</h2><div class="kv"><b>Relation</b><span>${{esc(e.kind)}}</span><b>Call site</b><span>${{esc(e.file)}}:${{e.line||''}}</span><b>Confidence</b><span>${{Math.round((e.static_confidence||0)*100)}}%</span></div><h3>Parameter binding</h3><pre>${{esc(bind||'(none resolved)')}}</pre><h3>Variables</h3><pre>${{esc((e.variable_names||[]).join(', ')||'(none captured)')}}</pre><h3>Expected response</h3><pre>${{esc(e.expected_response||'unknown')}}</pre>${{e.data_meaning?`<h3>Data meaning</h3><pre>${{esc(e.data_meaning)}}</pre>`:''}}${{(e.runtime_evidence||[]).length?`<h3>Observed runtime</h3><pre>${{esc(JSON.stringify(e.runtime_evidence,null,2))}}</pre>`:''}}<h3>Static detail</h3><pre>${{esc(e.detail||'')}}</pre>`}}
search.addEventListener('input',()=>{{const q=search.value.trim().toLowerCase();if(!q){{clear();return}}const ids=new Set();for(const n of shown){{const h=[n.label,n.symbol,n.file,n.capability_cluster,n.semantic_role,n.intent].filter(Boolean).join(' ').toLowerCase();if(h.includes(q))ids.add(n.id)}}apply(ids);details.innerHTML=`<h2>Search</h2><pre>${{ids.size}} matching nodes</pre>`}});scopeSel.addEventListener('change',clear);layerSel.addEventListener('change',clear);document.getElementById('reset').addEventListener('click',()=>{{search.value='';scopeSel.value='chain';layerSel.value='all';resetTransform();clear()}});svg.addEventListener('click',clear);
let scale=1,tx=0,ty=0,drag=false,lx=0,ly=0;function transform(){{viewport.setAttribute('transform',`translate(${{tx}} ${{ty}}) scale(${{scale}})`)}}function resetTransform(){{scale=1;tx=0;ty=0;transform()}}svg.addEventListener('wheel',ev=>{{ev.preventDefault();const r=svg.getBoundingClientRect(),mx=(ev.clientX-r.left)/r.width*1500,my=(ev.clientY-r.top)/r.height*920,old=scale;scale=Math.max(.2,Math.min(4,scale*(ev.deltaY<0?1.12:.89)));tx=mx-(mx-tx)*(scale/old);ty=my-(my-ty)*(scale/old);transform()}},{{passive:false}});svg.addEventListener('pointerdown',ev=>{{if(ev.target===svg){{drag=true;lx=ev.clientX;ly=ev.clientY;svg.setPointerCapture(ev.pointerId)}}}});svg.addEventListener('pointermove',ev=>{{if(!drag)return;const r=svg.getBoundingClientRect();tx+=(ev.clientX-lx)/r.width*1500;ty+=(ev.clientY-ly)/r.height*920;lx=ev.clientX;ly=ev.clientY;transform()}});svg.addEventListener('pointerup',()=>drag=false);svg.addEventListener('pointercancel',()=>drag=false);clear();
</script>
</body></html>'''
