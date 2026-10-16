"""0.55.10r1 non-destructive regressions: OS worker ancestry, reversible-trash preservation,
Windows Terminal exit semantics, browser shutdown policy and user budget merge."""
from __future__ import annotations
import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))

def imp(rel,label):
    spec=importlib.util.spec_from_file_location(label, ROOT/rel)
    module=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[label]=module
    spec.loader.exec_module(module)
    return module

shutdown=imp('core/norm_runtime/celery_shutdown.py','r1_worker_inventory')
root=Path('C:/Norm')
src=str(root/'tools/celery_tool_worker.py')
python=str(root/'.venv/Scripts/python.exe')
pythonw=str(root/'.venv/Scripts/pythonw.exe')
rows=[]
for number in range(1,5):
    p=1000+number*10
    rows.extend([
     {'ProcessId':p,'ParentProcessId':99,'CommandLine':f'"{python}" -B "{src}" --id {number}'},
     {'ProcessId':p+1,'ParentProcessId':p,'CommandLine':f'"{pythonw}" -B "{src}" --id {number}'},
    ])
def find_workers(sample):
    fake=SimpleNamespace(returncode=0,stdout=json.dumps(sample))
    with patch.object(shutdown.subprocess,'run',return_value=fake):
        return shutdown._owned_workers(root)
result=find_workers(rows)
assert len(result)==4 and {r['number'] for r in result}==set(range(1,5)),result
print('PASS 8 Windows interpreter process records representing 4 workers count as 4')
extra={'ProcessId':1055,'ParentProcessId':99,'CommandLine':f'"{pythonw}" -B "{src}" --id 1'}
duplicate=find_workers(rows+[extra])
assert len(duplicate)==5 and sorted(x['number'] for x in duplicate)==[1,1,2,3,4],duplicate
print('PASS independent duplicate worker is still detected, not hidden')

source=(ROOT/'core/norm_main.py').read_text('utf8')
tree=ast.parse(source)
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='clear_runtime_redis')
calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call)]
attrs=[n.func.attr for n in calls if isinstance(n.func,ast.Attribute)]
assert 'purge_all' not in attrs and 'flush_db' not in attrs
assert 'deletion_queue' in ast.get_source_segment(source,fn)
print('PASS graceful shutdown does not purge deletion queue or flush trash Redis')

fonts=imp('tools/workspace/font_profiles.py','r1_font_profiles')
cfg={}
for key in ('runtime','replies','prompt'):
  cfg[f'{key}_font_face']='Consolas'
  cfg[f'{key}_font_size']=12
  cfg[f'{key}_font_weight']='normal'
frag=fonts.fragment(cfg)
assert len(frag['profiles'])==3
assert all(p['closeOnExit']=='always' for p in frag['profiles'])
print('PASS Windows Terminal Norm role fragments close tabs on process exit')

browser=(ROOT/'tools/shared_browser/app.py').read_text('utf8')
assert '"visual_pdf_cancel","quit"' in browser
assert '"release","visual_pdf_status","visual_pdf_cancel","quit"' in browser
print('PASS explicit Shared Browser quit allowed during human control and visual PDF capture')

budget=(ROOT/'core/norm_runtime/conversation_service.py').read_bytes()
assert len(budget)>100_000
for expected in (b'num_predict=6900',b'num_predict=9600',b'num_predict=12800',
                 b'num_predict=7600',b'num_predict=7650',b'num_predict=14600',
                 b'num_predict=13200',b'num_predict=27000',b'num_predict=24000',
                 b'num_predict=12400'):
    assert expected in budget,expected
assert b'num_predict=3200' in budget
print('PASS all 11 user-adjusted conversation budgets carried forward; other callsite unchanged')

# Smoke-check source integrity without producing a window, signal or service shutdown.
for filename in ('core/norm_main.py','core/norm_runtime/celery_shutdown.py',
                 'tools/shared_browser/app.py','tools/workspace/font_profiles.py',
                 'core/norm_runtime/conversation_service.py'):
    ast.parse((ROOT/filename).read_text('utf8'))
print('PASS hotfix sources parse; no live services, windows, or trash modified')
