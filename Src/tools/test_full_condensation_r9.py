"""Offline, no database/network writes. Test r9 frozen full-memory 20-item commits."""
from __future__ import annotations
import copy
import datetime as dt
import json
import tempfile
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from norm_runtime.history_maintenance import DeepHistoryMaintainer


class MemoryStore:
    def __init__(self, root, count=45):
        self.root=Path(root)
        self.raw=[{
            'task_id':f'task-{n:03}', 'title':f'Memory {n}', 'status':'completed',
            'started_at':dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)+dt.timedelta(hours=n),
            'updated_at':dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)+dt.timedelta(hours=n),
            'original_request':f'Important request {n}', 'source_task_ids':[],
            'task_kind':'root', 'steps':[{'name':'Step','summary':f'Important detail {n}'}]
        } for n in range(count)]
        self.run=None
        self.deleted=[]
        self.creations=0
        self.call_counts={}
    def active_full_condensation_run(self):return copy.deepcopy(self.run)
    def create_sql_backup(self, directory):
        path=self.root/'full-backup.sql'
        path.write_text('unmodified snapshot; testing only')
        return str(path)
    def deep_history_candidates(self,*args,**kwargs):
        self.creations+=1
        return copy.deepcopy(self.raw)
    def task_history_archive_candidates(self):return []
    def create_full_condensation_run(self, data, backup_path):
        import hashlib
        batches=[]
        for n,groups in enumerate(data,1):
            frozen=json.loads(json.dumps(groups,default=str))
            ids=[row['task_id'] for group in frozen for row in group]
            batches.append({'index':n,'groups':frozen,'status':'pending','results':[],
                            'verification':[],'source_ids':ids,'original_deleted':0})
        self.run={'run_id':'1edc7229-17d6-4b5e-87b5-c40315a95955','total_batches':len(batches),
                  'backup_path':backup_path,'batches':batches}
        return self.active_full_condensation_run()
    def commit_full_condensation_batch(self,*,run_id,batch_index,records,verification,discard_ids):
        b=self.run['batches'][batch_index-1]
        assert b['status']=='pending'
        ids=set(b['source_ids'])
        covered={v['task_id'] for v in verification if v['kind']=='replay' and v['passed']}
        assert covered | set(discard_ids)==ids
        b.update(status='committed',results=json.loads(json.dumps(records,default=str)),
                 verification=copy.deepcopy(verification),groups=None,original_deleted=len(ids))
        self.deleted.extend(b['source_ids'])
        return {'deleted_raw':len(ids),'deleted_compact':0}
    def prune_covered_conversation_history(self,*args,**kwargs):
        return {'messages_deleted':0,'thread_summaries_deleted':0,'memory_links_preserved':0}
    def delete_superseded_memories(self):return 0
    def finish_full_condensation_run(self,run_id):
        assert all(b['status']=='committed' for b in self.run['batches'])
        self.run['status']='completed'
        return True
    def delete_sql_backup(self,path):Path(path).unlink()
    def record_maintenance_note(self,*args,**kwargs):pass


class FullCondensationTests(unittest.TestCase):
    def engine(self,durable,*,fail_at=None,replay_fail=None,discard_at=None):
        maintainer=DeepHistoryMaintainer.__new__(DeepHistoryMaintainer)
        maintainer.durable=durable
        maintainer.config={}
        maintainer.runtime_root=durable.root
        maintainer.queue=maintainer.drain_event=None
        maintainer.n1_gatekeeper=None
        maintainer.seen=[]
        def summarize(group,*,newer_context=None,destructive=False):
            assert destructive
            n=group[0]['task_id']
            maintainer.seen.append(n)
            if n==fail_at:raise RuntimeError('simulated power loss')
            if n==discard_at:return None
            source=group[0]
            return {'primary_task_id':n,'task_date':source['started_at'].date(),
                    'title':source['title'],'status':'completed',
                    'original_request':source['original_request'],
                    'outcome':'Important outcome','lessons':'Important lesson',
                    'future_note':'Future advice','source_task_ids':[row['task_id'] for row in group],
                    '_n1_approved':True}
        maintainer._summarize_group=summarize
        maintainer._replay_one=lambda rec:(rec['primary_task_id']!=replay_fail, {'issues':[]}, 'mock reconstruction')
        maintainer._repair_compact_record=lambda group,record,verdict,replay:record
        maintainer._full_merge_records=lambda records,max_size:(records,{'records_before_merge':len(records)})
        maintainer._relevant_newer_records=lambda group,records,limit:[]
        return maintainer

    def test_restarts_resume_at_first_unfinished_twenty(self):
        with tempfile.TemporaryDirectory() as t:
            store=MemoryStore(t,45)
            first=self.engine(store,fail_at='task-024')
            with self.assertRaisesRegex(RuntimeError,'power loss'):
                first._run_full_chunked(retention_days=30,merge_max_records=6)
            self.assertEqual(len(store.run['batches']),3)
            self.assertEqual([b['status'] for b in store.run['batches']],['committed','pending','pending'])
            self.assertEqual(len(store.deleted),20)
            store.raw.append({'task_id':'new-memory-arrived-during-run'})
            resumed=self.engine(store)
            result=resumed._run_full_chunked(retention_days=30,merge_max_records=6)
            self.assertEqual(result['status'],'success')
            self.assertEqual(len(store.deleted),45)
            self.assertEqual(len(set(store.deleted)),45)
            self.assertEqual(len(resumed.seen),25)
            self.assertEqual(store.creations,1,'resume must use frozen PG inventory, not query live history again')
            self.assertEqual(result['replay_samples_passed'],45)
            self.assertFalse(Path(t,'full-backup.sql').exists())

    def test_failed_reconstruction_never_commits_or_deletes_batch(self):
        with tempfile.TemporaryDirectory() as t:
            store=MemoryStore(t,40)
            worker=self.engine(store,replay_fail='task-035')
            result=worker._run_full_chunked(retention_days=30,merge_max_records=6)
            self.assertEqual(result['status'],'replay_failed')
            self.assertEqual(result['completed_batches'],0)
            self.assertEqual(len(store.deleted),0)
            self.assertTrue(Path(t,'full-backup.sql').exists())
            self.assertEqual([b['status'] for b in store.run['batches']],['pending','pending'])

    def test_n1_unapproved_keep_blocks_prune(self):
        with tempfile.TemporaryDirectory() as t:
            store=MemoryStore(t,21)
            worker=self.engine(store)
            previous=worker._summarize_group
            def bad(group,**kw):
                row=previous(group,**kw)
                if group[0]['task_id']=='task-020':row['_n1_approved']=False
                return row
            worker._summarize_group=bad
            with self.assertRaisesRegex(RuntimeError,'N1 did not explicitly approve'):
                worker._run_full_chunked(retention_days=30,merge_max_records=6)
            self.assertEqual(len(store.deleted),0)

    def test_batches_preserve_group_membership(self):
        groups=[[{'task_id':f't-{i}-a'},{'task_id':f't-{i}-b'}] for i in range(21)]
        batches=DeepHistoryMaintainer._full_source_batches(groups,20)
        self.assertEqual([sum(map(len,b)) for b in batches],[20,20,2])



class IncrementalFreezeTests(unittest.TestCase):
    def test_new_memory_does_not_restart_accepted_weekly_chunks(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as t:
            class Store:
                def __init__(self):
                    self.rows=[{'at':dt.datetime(2026,10,1,tzinfo=dt.timezone.utc)+dt.timedelta(minutes=i),
                                'source':'task_summary','text':f'Memory{i} '+('X'*4300)} for i in range(3)]
                    self.saved=[]
                def background_memory_source(self):return list(self.rows)
                def latest_background_snapshot(self):return None
                def save_background_snapshot(self,summary,source_through):self.saved.append((summary,source_through))
                def keep_latest_background_snapshot(self):pass
            store=Store()
            worker=DeepHistoryMaintainer.__new__(DeepHistoryMaintainer)
            worker.durable=store;worker.state_root=Path(t);worker.config={'consolidation_batch_chars':4000}
            counts={'generated':0}
            def condense(**kw):
                counts['generated']+=1
                if counts['generated']==2:
                    raise RuntimeError('first run interrupted after one committed JSON chunk')
                return f'checked summary {counts["generated"]}', [{'decision':'accept'}]
            worker._condense_plain_with_n1=condense
            with self.assertRaisesRegex(RuntimeError,'interrupted'):
                worker.rebuild_background_snapshot(incremental=True)
            checkpoint=Path(t)/'background-condensation-incremental.json'
            saved=json.loads(checkpoint.read_text())
            self.assertEqual(len(saved['completed_batches']),1)
            self.assertEqual(len(saved['source_chunks']),3)
            store.rows.append({'at':dt.datetime(2026,10,2,tzinfo=dt.timezone.utc),
                               'source':'task_summary','text':'NEW MEMORY AFTER START '+('Y'*4300)})
            seen=[]
            def finished(**kw):
                seen.append(kw['source_text'])
                return 'verified reduced note', [{'decision':'accept'}]
            worker._condense_plain_with_n1=finished
            worker.rebuild_background_snapshot(incremental=True)
            self.assertEqual(len(seen),3)  # remaining 2 batches + final merge
            self.assertTrue(all('NEW MEMORY AFTER START' not in source for source in seen))
            self.assertEqual(store.saved[0][1],dt.datetime(2026,10,1,0,2,tzinfo=dt.timezone.utc))
            self.assertFalse(checkpoint.exists())

if __name__=='__main__':unittest.main(verbosity=2)
