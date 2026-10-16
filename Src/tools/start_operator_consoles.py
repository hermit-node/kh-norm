"""Idempotent 3-role operator-console launcher with optional WT role fonts."""
from __future__ import annotations
import argparse,configparser,contextlib,json,os,shutil,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/'.venv/Scripts/python.exe'
HOST=ROOT/'tools/operator_console_host.py'
sys.path.insert(0,str(ROOT/'core'))
from norm_runtime.settings import load_path_settings
CONSOLES=(
    ('runtime','Norm Runtime',ROOT/'tools/norm_gui_stream.py'),
    ('replies','Norm Replies',ROOT/'tools/norm_gui_reply.py'),
    ('prompt','Norm Prompt',ROOT/'tools/norm_gui_prompt.py'),
)

@contextlib.contextmanager
def _launch_lock():
    if os.name!='nt':raise RuntimeError('Windows operator consoles only')
    import msvcrt
    state=load_path_settings(ROOT)['state_root'];state.mkdir(parents=True,exist_ok=True)
    path=state/'operator-console-startup.lock'
    with path.open('a+b') as lock:
        lock.seek(0,os.SEEK_END)
        if lock.tell()==0:lock.write(b'0');lock.flush()
        deadline=time.monotonic()+45
        while True:
            try:
                lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                break
            except OSError:
                if time.monotonic()>deadline:raise TimeoutError('Operator launcher busy')
                time.sleep(.1)
        try:yield
        finally:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

def _owned_host_rows():
    """Only exact current-root operator_console_host.py processes."""
    if os.name!='nt':return {}
    script=str(HOST.resolve()).casefold().replace('/','\\')
    exe=str(PYTHON.resolve()).casefold().replace('/','\\')
    query=('Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" '
           '| Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',query],
                          capture_output=True,text=True,timeout=18,check=False,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('Cannot inventory operator hosts (fail closed)')
    data=json.loads(result.stdout.strip() or '[]')
    rows=data if isinstance(data,list) else [data]
    found={key:[] for key,_,_ in CONSOLES}
    for row in rows:
        if not isinstance(row,dict):continue
        cmd=str(row.get('CommandLine') or '').casefold().replace('/','\\')
        image=str(row.get('ExecutablePath') or '').casefold().replace('/','\\')
        if script not in cmd or image!=exe:continue
        for key,title,helper in CONSOLES:
            if (f'"{title.casefold()}"' in cmd
                    and str(helper.resolve()).casefold().replace('/','\\') in cmd):
                found[key].append(int(row['ProcessId']))
    return found

def _settings():
    cfg=configparser.ConfigParser(interpolation=None)
    cfg.read(ROOT/'config/settings.ini',encoding='utf-8-sig')
    s=dict(cfg.items('workspace')) if cfg.has_section('workspace') else {}
    return s

def _launch(title,helper,profile=None):
    if profile:
        wt=shutil.which('wt.exe')
        if not wt:raise RuntimeError('Windows Terminal profile mode enabled but wt.exe missing')
        return subprocess.Popen([wt,'-w','new','new-tab','-p',profile,'--title',title,'-d',str(ROOT),
                                 str(PYTHON),'-u',str(HOST),title,str(helper)],
                                cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return subprocess.Popen([str(PYTHON),'-u',str(HOST),title,str(helper)],
                            cwd=str(ROOT),creationflags=(
                                getattr(subprocess,'CREATE_NEW_CONSOLE',0)
                                |getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profiled',action='store_true')
    parser.add_argument('--unprofiled',action='store_true')
    args=parser.parse_args()
    if args.profiled and args.unprofiled:parser.error('Choose one font mode')
    for required in (PYTHON,HOST,*(entry[2] for entry in CONSOLES)):
        if not required.is_file():raise FileNotFoundError(str(required))
    signal=load_path_settings(ROOT)['state_root']/'operator-console-shutdown.json'
    registry=load_path_settings(ROOT)['state_root']/'operator-consoles.json'
    with _launch_lock():
        existing=_owned_host_rows()
        duplicates={k:v for k,v in existing.items() if len(v)>1}
        if duplicates:raise RuntimeError(f'Duplicate operator host roles already active: {duplicates}; no new sessions started')
        missing=[t for t in CONSOLES if not existing[t[0]]]
        if not missing:
            print('Reusing all 3 existing Norm operator windows.')
            return 0
        mode=_settings().get('profiled_launch','true').strip().lower() in ('true','1','yes','on')
        profile=(args.profiled or mode) and not args.unprofiled
        names={role:f'Norm Workspace {role.title()}' for role,_,_ in CONSOLES}
        if profile:
            sys.path.insert(0,str(ROOT/'tools/workspace'))
            from font_profiles import write_fragment,fragment
            opts=_settings()
            config={}
            for role,_,_ in CONSOLES:
                config[f'{role}_font_face']=opts.get(f'{role}_font_face',
                   {'runtime':'Cascadia Mono','replies':'Consolas','prompt':'Cascadia Code'}[role])
                config[f'{role}_font_size']=int(opts.get(f'{role}_font_size',
                   {'runtime':13,'replies':14,'prompt':16}[role]))
                config[f'{role}_font_weight']=opts.get(f'{role}_font_weight','normal')
            write_fragment(config)
            if not shutil.which('wt.exe'):
                raise RuntimeError('Windows Terminal absent; set profiled_launch=false in [workspace] for plain consoles')
        # Do not reset shutdown signal while any hosts from the prior generation exist.
        # It is cleared only when service is starting and all hosts are absent.
        if not any(existing.values()):signal.unlink(missing_ok=True)
        for role,title,helper in missing:
            _launch(title,helper,names[role] if profile else None)
            if profile:time.sleep(.4)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            current=_owned_host_rows()
            if all(len(current[k])==1 for k,_,_ in CONSOLES):
                rows=[{'title':title,'pid':current[key][0],'helper':str(helper)}
                      for key,title,helper in CONSOLES]
                temp=registry.with_name(registry.name+'.writing')
                temp.write_text(json.dumps({'schema':1,'started_at':datetime.now(timezone.utc).isoformat(),
                                            'consoles':rows},indent=2)+'\n',encoding='utf-8')
                os.replace(temp,registry)
                print('Verified 3/3 distinct operator roles; registry updated.')
                return 0
            if any(len(x)>1 for x in current.values()):
                raise RuntimeError('Duplicate operator host discovered; refusing further launches')
            time.sleep(.5)
        raise RuntimeError('Operator launch incomplete after 30s; no automatic retry/duplicate launch')

if __name__=='__main__':raise SystemExit(main())
