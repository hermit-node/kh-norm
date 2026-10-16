"""Norm r12: no-clobber, compare-before-replace, temporary-script ownership, terminal output recovery."""
from __future__ import annotations
import json
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from norm_runtime.file_tool_executor import FileToolExecutor
from norm_runtime.artifact_verification import snapshot
from norm_runtime.prompt_worker import PromptWorker

def digest(b):return sha256(b).hexdigest()
def must_fail(fn, kind):
    try:
        fn()
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")

with tempfile.TemporaryDirectory(prefix="norm-r12-file-guard-") as tmp:
    base=Path(tmp)
    workspace=base/'workspace'
    workspace.mkdir()
    t=FileToolExecutor(
        [str(workspace)], backup_root=str(base/'backups'),
        audit_log=str(base/'audit'/'tools.jsonl'), temp_root=str(base/'temp'),
        workspace_root=str(workspace),
    )
    # Isolate native compare/publish behavior without depending on the private writer plugin.
    def write_raw(path,content,count):
        assert len(content.encode('utf-8')) == count
        path.write_text(content,encoding='utf-8')
    t._write_verbatim_temp=write_raw
    f=workspace/'protected.txt'
    r=t._write_file({'path':str(f),'content':'ORIGINAL'})
    assert r['created'] and f.read_text()=='ORIGINAL' and r['prewrite_verified']
    stamp=f.stat().st_mtime_ns
    r2=t._write_file({'path':str(f),'content':'ORIGINAL'})
    assert r2['unchanged'] and r2['operation']=='unchanged' and f.stat().st_mtime_ns==stamp
    checks=snapshot([{'tool':'write_file','result':{'ok':True,**r2}}])
    assert checks and checks[0]['verified'] and checks[0]['verification_method']=='verified_existing_identical_noop'
    must_fail(lambda:t._write_file({'path':str(f),'content':'UNAUTHORIZED'}),FileExistsError)
    assert f.read_text()=='ORIGINAL'
    must_fail(lambda:t._write_file({'path':str(f),'content':'NEW','operation':'replace','replacement_reason':'needed','expected_sha256':'0'*64}),ValueError)
    must_fail(lambda:t._write_file({'path':str(f),'content':'NEW','operation':'replace','expected_sha256':r2['sha256']}),ValueError)
    assert f.read_text()=='ORIGINAL'
    rr=t._write_file({'path':str(f),'content':'REPLACED','operation':'replace','replacement_reason':'user-authorized update','expected_sha256':digest(b'ORIGINAL')})
    assert rr['created'] is False and not rr['unchanged'] and f.read_text()=='REPLACED'
    assert any(p.read_text()=='ORIGINAL' for p in (base/'backups').iterdir()),"original backup absent"
    # A competing writer wins during preparation: create MUST fail, never overwrite.
    contender=workspace/'racing-create.txt'
    def create_race(path,content,count):
        path.write_text(content,encoding='utf8')
        contender.write_text('SOMEONE ELSE',encoding='utf8')
    t._write_verbatim_temp=create_race
    must_fail(lambda:t._write_file({'path':str(contender),'content':'NORM'}),FileExistsError)
    assert contender.read_text()=='SOMEONE ELSE'
    # Replacement stale after validation, before commit: abort, preserve contender.
    racer=workspace/'racing-replace.txt'
    racer.write_text('FIRST',encoding='utf8')
    def replace_race(path,content,count):
        path.write_text(content,encoding='utf8')
        racer.write_text('CONCURRENT EDIT',encoding='utf8')
    t._write_verbatim_temp=replace_race
    must_fail(lambda:t._write_file({'path':str(racer),'content':'NORM REPLACEMENT','operation':'replace',
         'expected_sha256':digest(b'FIRST'),'replacement_reason':'intentional update'}),RuntimeError)
    assert racer.read_text()=='CONCURRENT EDIT'
    t._write_verbatim_temp=write_raw
    # Distinct explicit modification operation still validates current hash.
    mr=t._replace_text({'path':str(f),'old_text':'REPLACED','new_text':'EDITED',
        'expected_sha256':digest(b'REPLACED'),'expected_occurrences':1})
    assert mr['replacements']==1 and f.read_text()=='EDITED'

    # New mid-work script becomes a task-owned ephemeral asset automatically.
    t.task_storage.bind('test-task','step-1')
    scratch=workspace/'probe_docx.py'
    sr=t._write_file({'path':str(scratch),'content':'print("temporary")'})
    assert sr['cleanup_metadata']['temporary_script'] and sr['cleanup_metadata']['retention']=='ephemeral'
    c=t.task_storage.cleanup_candidates(suppressed=False)
    assert any(x['path']==str(scratch) for x in c)
    scratch.write_text('external modification',encoding='utf8')
    assert not any(x['path']==str(scratch) for x in t.task_storage.cleanup_candidates(suppressed=False))
    user_output=workspace/'explicit_user_script.py'
    ur=t._write_file({'path':str(user_output),'content':'print("deliverable")','user_requested':True})
    assert ur['cleanup_metadata']['retention']=='durable'
    assert not any(x['path']==str(user_output) for x in t.task_storage.cleanup_candidates(suppressed=False))
    scratch2=workspace/'probe_working_script.py'
    r=t._write_file({'path':str(scratch2),'content':'print("old")'})
    t._write_file({'path':str(scratch2),'content':'print("new")',
        'operation':'replace','expected_sha256':r['sha256'],'replacement_reason':'iteration on task-owned helper'})
    assert any(x['path']==str(scratch2) for x in t.task_storage.cleanup_candidates(suppressed=False))
    # Alternate public append/insert plugin tools must also pass through
    # the native hash guard (not their unguarded legacy file writers).
    doc=workspace/'notes.txt'
    doc.write_text('OLD',encoding='utf8')
    must_fail(lambda:t._guarded_verbatim_public_call(
        'plugin_verbatim_lines__main__append_text',{'path':str(doc),'content':' NEW'}),ValueError)
    assert doc.read_text()=='OLD'
    append=t._guarded_verbatim_public_call(
        'plugin_verbatim_lines__main__append_text',
        {'path':str(doc),'content':' NEW','expected_sha256':digest(b'OLD'),
         'replacement_reason':'intentional append'})
    assert doc.read_text()=='OLD NEW' and append['mode']=='append'
    must_fail(lambda:t._guarded_verbatim_public_call(
        'plugin_verbatim_lines__main__insert_text',
        {'path':str(doc),'content':'TOP\\n','line':1}),ValueError)
    ins=t._guarded_verbatim_public_call(
        'plugin_verbatim_lines__main__insert_text',
        {'path':str(doc),'content':'TOP\\n','line':1,
         'expected_sha256':digest(b'OLD NEW'),'replacement_reason':'intentional insert'})
    assert doc.read_text().startswith('TOP')
    assert ins['mode']=='insert'
    print('PASS public Verbatim Lines append/insert cannot bypass original hash and explicit replacement authorization')
    # Real model-facing plugin dispatch and its advertised schema must be guarded.
    from shutil import copytree
    plugin_copy=base/'plugs'/'verbatim_lines'
    copytree(ROOT/'plugins'/'verbatim_lines',plugin_copy)
    executor=FileToolExecutor([str(workspace)],backup_root=str(base/'plugin-backups'),
        audit_log=str(base/'plugin-audit.jsonl'),plugin_root=str(base/'plugs'),
        plugin_registry_file=str(base/'plugin-registry.json'))
    available=[x for x in executor.schemas() if x.get('function',{}).get('name','').startswith('plugin_verbatim_lines__')]
    editable=[x for x in available if x['function']['name'].endswith('__append_text')]
    assert editable and 'expected_sha256' in editable[0]['function']['parameters']['properties']
    fname=editable[0]['function']['name']
    plugin_target=workspace/'actual-plugin-edit.txt'
    plugin_target.write_text('ORIGINAL',encoding='utf8')
    refused=executor.execute(fname,{'path':str(plugin_target),'content':' EXTRA'})
    assert not refused['ok'] and plugin_target.read_text()=='ORIGINAL',refused
    accepted=executor.execute(fname,{'path':str(plugin_target),'content':' EXTRA',
        'expected_sha256':digest(b'ORIGINAL'),'replacement_reason':'explicit verified append'})
    assert accepted['ok'] and plugin_target.read_text()=='ORIGINAL EXTRA',accepted
    print('PASS actual plugin tool dispatch guarded and expected hash/reason fields exposed to N2')
    print('PASS atomic create; unchanged no-op; explicit authorized replacement; race aborts; task-owned ephemeral scripts')

    # Last-stage recovery: no reconcile, no repeated sweep, no follow-up verification.
    output=workspace/'missing-output.pdf'
    record={"state":"committed","original_path":str(output),"task_id":"test-task","queued_at":100}
    class FakeRedis:
        def hgetall(self,key):
            return {'recover-me':json.dumps(record)}
    class FakeQueue:
        redis=FakeRedis()
        items_key='items'
        def __init__(self):self.calls=[]
        def restore(self,deletion_id):
            self.calls.append(deletion_id)
            output.write_bytes(b'%PDF-MOCK-RESTORED')
            return {'restored':[{'deletion_id':deletion_id,'path':str(output)}]}
    q=FakeQueue()
    worker=object.__new__(PromptWorker)
    worker._worker_tools=lambda:SimpleNamespace(deletion_queue=q)
    worker._restore_missing_outputs_once('test-task',[str(output)])
    assert q.calls==['recover-me'] and output.read_bytes()==b'%PDF-MOCK-RESTORED'
    print('PASS final-stage restore invoked exactly once, no reconcile/empty/reverification')
print('PASS r12 integrity and cleanup regressions')
