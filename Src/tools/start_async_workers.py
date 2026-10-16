"""Idempotent Norm Celery worker launcher, process-identity locked."""
from __future__ import annotations
import contextlib,os,sys,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'core'))
from norm_runtime.celery_tool_executor import CFG,redis
from norm_runtime.celery_shutdown import _owned_workers

@contextlib.contextmanager
def _startup_lock():
    if os.name!='nt':raise RuntimeError('Celery starter is Windows-only')
    import msvcrt
    state=root/'state';state.mkdir(parents=True,exist_ok=True)
    with (state/'celery-startup.lock').open('a+b') as lock:
        lock.seek(0,os.SEEK_END)
        if lock.tell()==0:lock.write(b'0');lock.flush()
        deadline=time.monotonic()+45
        while True:
            try:
                lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                break
            except OSError:
                if time.monotonic()>=deadline:
                    raise TimeoutError('Another Celery launcher holds the lock; refusing duplicate startup')
                time.sleep(.15)
        try:yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

def main():
    if not CFG['enabled']:
        print('Celery disabled: synchronous tools remain available.')
        return 0
    with _startup_lock():
        r=redis()
        try:
            r.ping()
            existing=_owned_workers(root)
            ids=[int(p['number']) for p in existing]
            if any(x<1 or x>int(CFG['count']) for x in ids) or len(ids)!=len(set(ids)):
                raise RuntimeError('Invalid/duplicate Norm worker identities; refusing another worker set')
            missing=[i for i in range(1,int(CFG['count'])+1) if i not in ids]
            if not missing:
                print(f'Celery: reusing {len(ids)} verified Norm-owned worker processes.')
                return 0
            pythonw=root/'.venv/Scripts/pythonw.exe'
            python=root/'.venv/Scripts/python.exe'
            py=pythonw if pythonw.is_file() else python
            script=root/'tools/celery_tool_worker.py'
            if not py.is_file() or not script.is_file():
                raise FileNotFoundError('Celery Python or worker script missing')
            logs=root/'logs';logs.mkdir(parents=True,exist_ok=True)
            for index in missing:
                now=_owned_workers(root)
                if any(w['number']==index for w in now):continue
                with (logs/f'celery-tool-worker-{index}.log').open('ab') as output:
                    subprocess.Popen([str(py),'-B',str(script),'--id',str(index)],
                       cwd=str(root),stdout=output,stderr=output,stdin=subprocess.DEVNULL,
                       creationflags=(0 if py==pythonw else getattr(subprocess,'CREATE_NO_WINDOW',0)),
                       env={**os.environ,'NORM_ROOT':str(root),
                            'VIRTUAL_ENV':str(root/'.venv'),
                            'PATH':str(root/'.venv/Scripts')+os.pathsep+os.environ.get('PATH','')})
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                now=_owned_workers(root)
                if len(now)==CFG['count'] and len({p['number'] for p in now})==CFG['count']:
                    print(f'Celery ready: exactly {len(now)}/{CFG["count"]} Norm-owned processes.')
                    return 0
                time.sleep(.5)
            now=_owned_workers(root)
            print(f'WARNING: {len(now)}/{CFG["count"]} verified Celery processes; synchronous fallback available.')
            return 0
        finally:r.close()

if __name__=='__main__':raise SystemExit(main())
