from __future__ import annotations
import contextlib, importlib.util, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def test_ps1_routing():
    ps=(ROOT/'norm-ssh.ps1').read_text('utf-8-sig')
    assert "function Ensure-NormTerminals {" in ps
    assert '    Ensure-NormTerminals\n    while ($true)' in ps
    assert "'t' { Ensure-NormTerminals }" in ps
    assert "$StartCandidates = @('Run-Norm-Terminals.bat')" in ps
    assert ps.index('if ($CheckOnly) {') < ps.index('    Ensure-NormTerminals\n    while ($true)')
    assert ps.count('function Ensure-NormTerminals {')==1
    assert ps.count('    Ensure-NormTerminals\n    while ($true)')==1

def test_operator_role_reuse():
    sys.path.insert(0,str(ROOT/'core'))
    from norm_runtime import settings
    spec=importlib.util.spec_from_file_location('norm_start_operator_r10a',ROOT/'tools/start_operator_consoles.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    roles=m.CONSOLES
    names=[row[0] for row in roles]
    assert names==['runtime','replies','prompt']
    with tempfile.TemporaryDirectory(prefix='norm_roles_r10a_') as tmp:
        tmp=Path(tmp)
        state=tmp/'state'
        state.mkdir()
        py=tmp/'python.exe';py.write_bytes(b'placeholder')
        host=tmp/'host.py';host.write_text('pass')
        helpers=[]
        for role,title,path in roles:
            p=tmp/(role+'.py');p.write_text('pass')
            helpers.append((role,title,p))
        m.CONSOLES=tuple(helpers);m.ROOT=tmp;m.PYTHON=py;m.HOST=host
        m.load_path_settings=lambda _: {'state_root':state}
        m._launch_lock=lambda:contextlib.nullcontext()
        m._settings=lambda: {'profiled_launch':'false'}
        def run_case(initial,expected,has_duplicate=False):
            events=[]
            current={k:list(v) for k,v in initial.items()}
            def inventory(): return {k:list(v) for k,v in current.items()}
            def fake_launch(title,helper,profile=None):
                role=next(k for k,t,p in m.CONSOLES if t==title)
                events.append(role)
                current[role].append(400+len(events))
            m._owned_host_rows=inventory
            m._launch=fake_launch
            old_argv=sys.argv
            sys.argv=['start_operator_consoles.py','--unprofiled']
            try:
                if has_duplicate:
                    try:m.main()
                    except RuntimeError as exc:assert 'Duplicate operator host' in str(exc)
                    else:raise AssertionError('Duplicate role accepted')
                else:
                    assert m.main()==0
            finally:sys.argv=old_argv
            assert events==expected,(events,expected)
            return events
        run_case({'runtime':[11],'replies':[12],'prompt':[13]},[])
        run_case({'runtime':[11],'replies':[],'prompt':[13]},['replies'])
        run_case({'runtime':[],'replies':[],'prompt':[]},['runtime','replies','prompt'])
        run_case({'runtime':[11,14],'replies':[],'prompt':[13]},[],True)
    print('PASS: all present=0 launches; one missing=1; none=3; duplicate=blocked')

if __name__=='__main__':
    test_ps1_routing()
    test_operator_role_reuse()
    print('PASS: healthy PS1 launch, T menu, duplicate prevention, no changes to role launcher')
