"""R11R7 numeric scope regressions; no Norm runtime starts or task dispatch."""
from pathlib import Path
from types import SimpleNamespace
import ast, json, re

root = Path(__file__).resolve().parents[1]
source = (root / "core/norm_runtime/prompt_worker.py").read_text("utf-8-sig")
tree = ast.parse(source)
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=="PromptWorker")
names = {"_normalize_scope_items", "_scope_items_from_text",
         "_parse_recovery_analysis", "_recovery_scope_items"}
methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(methods)==4
mod = ast.Module(body=[ast.ClassDef(name="PromptWorker", bases=[], keywords=[],
     body=methods, decorator_list=[], type_params=[])],type_ignores=[])
ns={"json":json,"re":re}
exec(compile(ast.fix_missing_locations(mod),"<recovery-test>","exec"), ns)
P=ns["PromptWorker"]

def parse(scope, parent):
    obj = {"useful_points":[], "completed_work":[], "merge_requirements":[],
      "remaining_units":[{"name":"Inspect folder","instruction":"Inspect D:\\\\LOCAL_Share\\\\NinTendo",
        "verify":"Verify original files","scope_items":scope}]}
    return P._parse_recovery_analysis(json.dumps(obj),parent_scope=parent)

assert parse([1],[1])["remaining_units"][0]["scope_items"] == [1]
assert parse([],[])["remaining_units"][0]["scope_items"] == []
for scope,parent in (([],[1]),([r"D:\LOCAL_Share\NinTendo"],[1]),
                     ([r"D:\LOCAL_Share\NinTendo"],[]),
                     (["1"],[1]),([2],[1]),([0],[1]),([True],[1])):
    try: parse(scope,parent)
    except RuntimeError: pass
    else: raise AssertionError(f"incorrect scope accepted {scope!r} against {parent!r}")
print("PASS: [1] remains required for numbered parent and paths rejected")


print("SCOPE TEST SECTION PASSED")
child=SimpleNamespace(metadata={"recovery_scope_items":[], "prompt_origin":"runtime_oversize_recovery_unit", "expected_result_kind":"step_result", "step_name":"Inspect item 1"}, prompt="Check item 1")
assert P()._recovery_scope_items(child)==[]
child.metadata["recovery_scope_items"]=[1]
assert P()._recovery_scope_items(child)==[1]
print("PASS explicit child [] preserved and [1] preserved")
assert "Folder names, file paths, and UNC paths belong in instruction" in source
assert "When no numbered parent scope exists, output scope_items as []" in source
print("ALL R11R7 STRICT RECOVERY SCOPE REGRESSIONS PASS")
