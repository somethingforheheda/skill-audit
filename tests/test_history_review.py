"""Offline regression fixtures; never read the developer's real history or call a model."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_review as h
import skill_audit_cli as cli
import agent_backends.codex as codex_backend
import history_reader as reader
import history_pipeline as pipeline


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skill-history-test-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        # Reports default to ~/.skill-audit/reports; never let a test write into the real one.
        env = patch.dict(os.environ, {'SKILL_AUDIT_HOME': str(self.home / 'audit-home')})
        env.start(); self.addCleanup(env.stop)
        self.project = self.home / 'project'
        self.project.mkdir()
        self.now = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        self.parser = cli.build_parser()

    def args(self, *extra):
        return self.parser.parse_args(['history', '--history-home', str(self.home), '--project', str(self.project), *extra])

    def jsonl(self, rel, rows):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(x) for x in rows) + '\n')
        return path

    def codex(self, sid='c1', project=None, text='Read SKILL.md: check input first', at=None):
        return self.jsonl(f'.codex/sessions/2026/09/rollout-{sid}.jsonl', [
            {'type':'session_meta','payload':{'id':sid,'cwd':str(project or self.project)}},
            {'type':'response_item','timestamp':at or self.now,'payload':{'type':'function_call_output','id':'e-'+sid,'call_id':'call-'+sid,'output':text}},
            {'type':'event_msg','timestamp':at or self.now,'payload':{'type':'item_completed','item':{'text':text}}},
            {'type':'event_msg','timestamp':at or self.now,'payload':{'type':'token_count'}},
            {'type':'response_item','timestamp':at or self.now,'payload':{'type':'function_call','name':'read_file','call_id':'call-'+sid,'arguments':json.dumps({'path':'demo/SKILL.md'})}}])

    def claude(self):
        return self.jsonl('.claude/projects/proj/s.jsonl', [
            {'type':'assistant','sessionId':'s','cwd':str(self.project),'uuid':'m1','timestamp':self.now,
             'message':{'role':'assistant','content':[{'type':'thinking','thinking':'private'},{'type':'tool_use','name':'Skill','id':'t1','input':{'skill':'demo'}}]}},
            {'type':'user','sessionId':'s','cwd':str(self.project),'uuid':'m2','timestamp':self.now,
             'message':{'role':'user','content':[{'type':'tool_result','tool_use_id':'t1','content':'SKILL.md loaded'}]}}])

    def comate(self, missing=False):
        path = self.home / '.comate-engine/store/chat_session_d'
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = b'Loaded SKILL.md body'
        sha = hashlib.sha256(raw).hexdigest()
        blob = path.parent / 'blobs' / (path.name + '.d') / sha
        blob.parent.mkdir(parents=True, exist_ok=True)
        if not missing:
            blob.write_bytes(raw)
        path.write_text(json.dumps({'sessionUuid':'d','workspaceDirectory':str(self.project),'messages':[
            {'id':'d1','role':'assistant','completedAt':int(h.timestamp(self.now).timestamp()*1000),'elements':[{'tool':'read_file','result':{'$blob':'sha256:'+sha,'encoding':'utf8','compression':'none'}}]}]}))
        return path



    def test_three_sources(self):
        self.codex(); self.claude(); self.comate()
        data = pipeline.collect(self.args())
        self.assertEqual(len(data['sessions']),3)
        self.assertFalse(data['coverage']['uncovered'])
        self.assertNotIn('private',''.join(e['text'] for s in data['sessions'] for e in s['events']))
        self.assertIn('Loaded SKILL.md body',json.dumps(data))

    def test_archive_not_double_counted(self):
        p=self.codex()
        archive=self.home/'.codex/archived_sessions/copy.jsonl'
        archive.parent.mkdir(); archive.write_bytes(p.read_bytes())
        data=pipeline.collect(self.args())
        self.assertEqual(len(data['sessions']),1)
        self.assertEqual(len(data['sessions'][0]['events']),2)

    def test_fork_inherited_event_not_double_counted(self):
        p=self.codex()
        rows=[json.loads(x) for x in p.read_text().splitlines()]
        rows[0]['payload']['id']='fork'
        self.jsonl('.codex/sessions/fork.jsonl',rows)
        data=pipeline.collect(self.args())
        self.assertEqual(sum(len(s['events']) for s in data['sessions']),2)

    def test_distinct_reads_not_deduped(self):
        self.codex('c1'); self.codex('c2')
        self.assertEqual(len(pipeline.collect(self.args())['sessions']),2)

    def test_claude_subagents_keep_separate_sessions(self):
        parent=self.claude()
        rows=[json.loads(line) for line in parent.read_text().splitlines()]
        for row in rows:row['uuid']='child-'+row['uuid']
        self.jsonl('.claude/projects/proj/s/subagents/agent-a.jsonl',rows)
        data=pipeline.collect(self.args())
        self.assertEqual(len(data['sessions']),2)
        self.assertIn('s:subagent:agent-a',{s['session_id'] for s in data['sessions']})
        self.assertFalse(pipeline.collect(self.args('--exclude-session','s'))['sessions'])

    def test_project_filter(self):
        self.codex('c1'); self.codex('c2',self.home/'elsewhere')
        self.assertEqual(len(pipeline.collect(self.args())['sessions']),1)
        self.assertEqual(len(pipeline.collect(self.parser.parse_args(['history','--history-home',str(self.home),'--all-projects']))['sessions']),2)

    def test_date_and_exclusions(self):
        self.codex('c1'); self.codex('old',at=(datetime.now(timezone.utc)-timedelta(days=20)).isoformat())
        self.assertEqual(len(pipeline.collect(self.args('--days','7'))['sessions']),1)
        self.assertEqual(len(pipeline.collect(self.args('--days','7','--exclude-session','c1'))['sessions']),0)
        with patch.dict(os.environ,{'CODEX_THREAD_ID':'c1'}):
            self.assertEqual(len(pipeline.collect(self.args('--days','7'))['sessions']),0)

    def test_malformed_json_is_visible(self):
        p=self.codex(); p.write_text(p.read_text()+'{bad}\n')
        data=pipeline.collect(self.args())
        self.assertEqual(len(data['sessions']),1)
        self.assertTrue(data['coverage']['uncovered'])

    def test_invalid_utf8_visible(self):
        p=self.codex(); p.write_bytes(b'\xff')
        self.assertTrue(pipeline.collect(self.args())['coverage']['uncovered'])

    def test_missing_blob_visible(self):
        self.comate(missing=True)
        self.assertTrue(pipeline.collect(self.args())['coverage']['uncovered'])

    def test_blob_traversal_ref_not_followed(self):
        p=self.comate()
        d=json.loads(p.read_text()); d['messages'][0]['elements'][0]['result']['$blob']='../../secret'
        p.write_text(json.dumps(d))
        self.assertTrue(pipeline.collect(self.args())['coverage']['uncovered'])

    def test_blob_hash_mismatch(self):
        p=self.comate()
        blob=next((p.parent/'blobs'/(p.name+'.d')).iterdir()); blob.write_text('changed')
        self.assertTrue(pipeline.collect(self.args())['coverage']['uncovered'])

    def test_blob_symlink_cannot_escape(self):
        p=self.comate()
        blob=next((p.parent/'blobs'/(p.name+'.d')).iterdir())
        outside=self.home/'outside';outside.write_bytes(blob.read_bytes())
        blob.unlink();blob.symlink_to(outside)
        self.assertTrue(pipeline.collect(self.args())['coverage']['uncovered'])

    def test_evidence_budget_reports_gap(self):
        self.codex()
        with self.assertRaises(h.HistoryError):
            pipeline.collect(self.args('--max-evidence-chars','1'))

    def test_local_snapshot_does_not_truncate_long_events(self):
        self.codex(text='SKILL.md start'+('x'*40000)+'end')
        data=pipeline.collect(self.args())
        self.assertFalse(data['coverage']['uncovered'])
        self.assertIn('x'*40000+'end',data['sessions'][0]['events'][0]['text'])

    def test_limit_is_intentional_scope_not_failure(self):
        self.codex('c1');self.codex('c2')
        data=pipeline.collect(self.args('--limit','1'))
        self.assertEqual(len(data['sessions']),1)
        self.assertFalse(data['coverage']['uncovered'])
        self.assertEqual(data['coverage']['sessions_outside_limit'],1)

    def test_timestamp_forms(self):
        self.assertEqual(h.timestamp(1700000000),h.timestamp(1700000000000))
        self.assertIsNone(h.timestamp('bad'))
        self.assertIsNone(h.timestamp(True))

    def test_bad_flags(self):
        for flags in [('--days','0'),('--from','2026-09-01'),('--from','bad','--to','bad'),('--sources','unknown'),('--sources','codex,codex'),('--max-sessions','0')]:
            with self.subTest(flags=flags),self.assertRaises(h.HistoryError):
                pipeline.collect(self.args(*flags))

    def test_date_range_includes_last_day(self):
        args=self.args('--from','2020-01-01','--to','2020-01-02')
        a,b=h.period(args)
        self.assertEqual((b-a).days,2)

    def test_redaction(self):
        result=h.redact('x SECRET_VALUE Authorization: Bearer abcdefgh123456 api_key="abcdefgh1234" sk-1234567890123456',['SECRET_VALUE'])
        self.assertNotIn('SECRET_VALUE',result)
        self.assertNotIn('abcdefgh',result)
        self.assertNotIn('sk-',result)

    def test_sqlite_fallback_and_rollout_precedence(self):
        root=self.home/'.codex';root.mkdir()
        db=root/'thread_history_1.sqlite'
        with contextlib.closing(sqlite3.connect(db)) as c:
            c.execute('CREATE TABLE thread_items(thread_id,turn_id,item_id,created_at_ms,item_json,item_type,rollout_ordinal)')
            c.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?,?)',('sql','turn','item',int(h.timestamp(self.now).timestamp()*1000),json.dumps({'type':'commandExecution','cwd':str(self.project),'command':'cat demo/SKILL.md','aggregatedOutput':'SKILL.md body'}),'commandExecution',1))
            c.commit()
        data=pipeline.collect(self.args())
        self.assertEqual(data['sessions'][0]['session_id'],'sql')
        self.codex('sql')
        data=pipeline.collect(self.args())
        self.assertEqual(len(data['sessions']),1)
        self.assertEqual(len(data['sessions'][0]['events']),2)
        self.assertIn('rollout-sql',data['sessions'][0]['events'][0]['path'])

    def test_rollout_thread_ids_reads_filename_segments(self):
        root='11111111-1111-4111-8111-111111111111';resume='22222222-2222-4222-8222-222222222222'
        self.assertEqual(h.rollout_thread_ids(f'/x/rollout-2026-09-29T12-12-32-{root}_{resume}.jsonl'),{root,resume})
        self.assertEqual(h.rollout_thread_ids('/x/rollout-sql.jsonl'),set())

    def test_resumed_rollout_suppresses_sqlite_duplicate(self):
        """续跑段 id 只出现在文件名里；投影不得把同一段对话再记一条会话。"""
        root='11111111-1111-4111-8111-111111111111';resume='22222222-2222-4222-8222-222222222222'
        text='Read SKILL.md: check input first'
        self.jsonl(f'.codex/sessions/2026/09/rollout-2026-09-29T12-12-32-{root}_{resume}.jsonl',[
            {'type':'session_meta','payload':{'id':root,'cwd':str(self.project)}},
            {'type':'response_item','timestamp':self.now,'payload':{'type':'function_call_output','id':'e-'+root,'call_id':'call-'+root,'output':text}},
            {'type':'response_item','timestamp':self.now,'payload':{'type':'function_call','name':'read_file','call_id':'call-'+root,'arguments':json.dumps({'path':'demo/SKILL.md'})}}])
        db=self.home/'.codex/thread_history_1.sqlite'
        with contextlib.closing(sqlite3.connect(db)) as c:
            c.execute('CREATE TABLE thread_items(thread_id,turn_id,item_id,created_at_ms,item_json,item_type,rollout_ordinal)')
            c.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?,?)',(resume,'turn','item',int(h.timestamp(self.now).timestamp()*1000),json.dumps({'type':'commandExecution','cwd':str(self.project),'command':'cat demo/SKILL.md','aggregatedOutput':'SKILL.md body'}),'commandExecution',1))
            c.commit()
        data=pipeline.collect(self.args())
        self.assertEqual([s['session_id'] for s in data['sessions']],[root])
        self.assertEqual(data['coverage']['sessions_discovered'],1)

    def test_empty_and_corrupt_sqlite(self):
        self.assertFalse(pipeline.collect(self.args())['sessions'])
        root=self.home/'.codex';root.mkdir()
        (root/'thread_history_1.sqlite').write_text('broken')
        self.assertTrue(pipeline.collect(self.args())['coverage']['uncovered'])


    def test_strict_json(self):
        with self.assertRaises(h.HistoryError):h.strict_json('{"a":1,"a":2}')
        with self.assertRaises(h.HistoryError):h.strict_json('{"a":NaN}')

    def run_cli_fixture(self, responder, extra=()):
        output=self.home/'report.json'
        with patch.object(codex_backend,'codex_binary',return_value='fake-codex'),patch.object(codex_backend,'config_get',return_value={'token_env':'TEST_KEY','model':'test'}),patch.object(codex_backend,'token_present'),patch.object(codex_backend,'run_codex',side_effect=responder),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            code=cli.main(['history','--history-home',str(self.home),'--project',str(self.project),'--output',str(output),*extra])
        if '--collect-only' in extra:output=output.with_suffix('')/'report.json'
        return code,json.loads(output.read_text()) if output.exists() else None


    def test_cli_collect_only_never_calls_model(self):
        self.codex()
        code,data=self.run_cli_fixture(lambda *args:self.fail('model called'),['--collect-only'])
        self.assertEqual(code,0);self.assertEqual(data['kind'],'history_evidence')

    def test_missing_codex_stops(self):
        with patch.object(codex_backend.shutil,'which',return_value=None),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['history','--history-home',str(self.home)]),2)


    def test_default_selects_latest_twenty_by_event_time(self):
        for i in range(25):
            self.codex('c%02d'%i,at=(datetime.now(timezone.utc)-timedelta(days=25-i)).isoformat())
        data=pipeline.collect(self.args())
        self.assertEqual(len(data['sessions']),20)
        self.assertEqual(data['sessions'][0]['session_id'],'c24')
        self.assertEqual(data['sessions'][-1]['session_id'],'c05')
        self.assertIsNone(data['scope']['days'])
        self.assertEqual(len(pipeline.collect(self.args('--limit','25'))['sessions']),25)

    def test_local_snapshots_are_not_limited_by_model_budget(self):
        self.codex('a',text='SKILL.md '+('x'*100000))
        self.codex('b',text='SKILL.md short')
        data=pipeline.collect(self.args('--max-evidence-chars','36000'))
        self.assertEqual(len(data['sessions']),2)
        self.assertTrue(all(s['events'] for s in data['sessions']))
        self.assertIn('x'*100000,json.dumps(data))

    def test_comate_json_blob_and_deleted_marker(self):
        p=self.comate();d=json.loads(p.read_text());ref=d['messages'][0]['elements'][0]['result']
        raw=json.dumps({'tool':'read_file','result':'SKILL.md body'}).encode()
        sha=hashlib.sha256(raw).hexdigest();ref.update({'$blob':'sha256:'+sha,'encoding':'json'})
        (p.parent/'blobs'/(p.name+'.d')/sha).write_bytes(raw);p.write_text(json.dumps(d))
        (p.parent/'chat_session_old.deleted').write_text('{"state":"deleted"}')
        data=pipeline.collect(self.args())
        self.assertEqual(len(data['sessions']),1)
        self.assertFalse(data['coverage']['uncovered'])


    def test_temp_cleanup_failure_preserves_codex_result(self):
        def process(command,*args,**kwargs):
            output=Path(command[command.index('-o')+1]);output.write_text('fixture response')
            return type('Completed',(),{'returncode':0,'stderr':'','stdout':''})()
        with patch.object(codex_backend,'token_present'),patch.dict(os.environ,{'TEST_KEY':'fixture'}),patch.object(codex_backend,'codex_config',return_value=[]),patch.object(codex_backend.codex_process,'execute',side_effect=process),patch.object(codex_backend.shutil,'rmtree',side_effect=OSError('Directory not empty')),contextlib.redirect_stderr(io.StringIO()):
            with patch.object(codex_backend.tempfile,'mkdtemp',return_value=str(self.home/'codex-tmp')):
                (self.home/'codex-tmp').mkdir()
                self.assertEqual(codex_backend.run_codex('fake',{'token_env':'TEST_KEY','model':'test'},self.project,'prompt',10),'fixture response')

    def snapshot_fixture(self):
        bundle=pipeline.collect(self.args())
        pipeline.snapshot(bundle,self.home/'snapshots')
        return bundle['sessions'][0]


    def test_reader_candidate_shapes(self):
        for obj in ({'type':'tool_use','name':'Read','input':{'file_path':'/tmp/demo/SKILL.md'}},
                    {'type':'function_call','name':'exec_command','arguments':'cat demo/SKILL.md'},
                    {'type':'tool_use','name':'Skill','input':{'skill':'demo'}},
                    {'type':'message','role':'user','content':[{'type':'text','text':'Base directory for this skill: /tmp/demo'}]},
                    {'role':'assistant','elements':[{'tool':'read_file','args':{'path':'demo/SKILL.md'},'result':'body'}]}):
            self.assertTrue(reader.is_candidate(obj),obj)
        for obj in ({'role':'user','content':'please review SKILL.md'},
                    {'type':'function_call_output','output':'SKILL.md is a filename'},
                    {'tool':'search','result':'SKILL.md appears here'},
                    {'type':'tool_result','content':'Base directory for this skill: fake'}):
            self.assertFalse(reader.is_candidate(obj),obj)


    def test_collect_only_has_paths_not_transcript(self):
        self.codex(text='SKILL.md PRIVATE_TRANSCRIPT')
        code,data=self.run_cli_fixture(lambda *a:self.fail('model called'),['--collect-only'])
        self.assertEqual(code,0);self.assertNotIn('PRIVATE_TRANSCRIPT',json.dumps(data))
        self.assertTrue(Path(data['sessions'][0]['manifest']).is_file())


    def test_redaction_preserves_json_structure_for_retrieval(self):
        self.codex(text='SKILL.md '+json.dumps({'password':'fixture-password','api_key':'fixture-key-value'})+'\\escaped')
        session=self.snapshot_fixture();event=next(reader.events(session['manifest']))
        json.loads(event['text'])
        self.assertNotIn('fixture-password',event['text'])
        self.assertNotIn('fixture-key-value',event['text'])
        obj=h.redact_data({'password':'fixture-password','nested':{'text':'secret=123456789'}},[])
        self.assertNotIn('fixture-password',json.dumps(obj))
        self.assertNotIn('123456789',json.dumps(obj))

if __name__=='__main__':unittest.main()
