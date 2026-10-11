import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import history_analysis as a
import skill_audit_cli as cli
import agent_backends.codex as codex_backend

class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.skill=self.root/'skills/example/SKILL.md'
        self.skill.parent.mkdir(parents=True);self.skill.write_text('Example skill description with concrete instructions.')
        self.report={'scope':{'skills_roots':[str(self.root/'skills')],'project':str(self.root)},
            'sessions':[], 'extraction':{'reads':[], 'status':'complete','summary':{},'uncovered':[]}}
        self.path=self.root/'report.json';self.path.write_text(json.dumps(self.report))
    def args(self,**kwargs):
        return SimpleNamespace(report=self.path,resume=None,rules_file=None,max_evidence_chars=120000,prepare_only=False,attempts=2,timeout=30,**kwargs)
    def result(self):
        return {'status':'complete','overview':'Bounded fixture analysis','uncovered':[],'findings':[]}
    def test_prepare_no_model_and_inventory(self):
        args=self.args();args.prepare_only=True
        with patch.object(codex_backend,'codex_binary',side_effect=AssertionError('must not start')):
            self.assertEqual(a.run(args,cli),0)
        state=next((self.root/'analysis-runs').iterdir())
        summary=a.read_json(state/'summary.json')
        self.assertEqual(summary['skills'][0]['reads'],0)
        self.assertEqual(summary['skills'][0]['current']['status'],'present')
    def test_single_agent_and_immutable_input(self):
        original=self.path.read_bytes()
        with patch.object(codex_backend,'codex_binary',return_value='fake'),patch.object(codex_backend,'config_get',return_value={'model':'fixture','token_env':'NO_REAL_KEY'}),patch.object(codex_backend,'token_present'),patch.object(codex_backend,'run_codex',return_value=json.dumps(self.result())) as run:
            self.assertEqual(a.run(self.args(),cli),0)
        self.assertEqual(run.call_count,1);self.assertTrue(run.call_args.kwargs['multi_agent'])
        self.assertEqual(original,self.path.read_bytes())
        self.assertTrue((self.root/'analysis.json').exists())
        self.assertIn('Bounded fixture analysis',(self.root/'report.html').read_text())
    def test_timeout_not_retried(self):
        with patch.object(codex_backend,'codex_binary',return_value='fake'),patch.object(codex_backend,'config_get',return_value={'model':'fixture','token_env':'NO_REAL_KEY'}),patch.object(codex_backend,'token_present'),patch.object(codex_backend,'run_codex',side_effect=cli.AuditTimeout('timeout')) as run:
            self.assertEqual(a.run(self.args(),cli),2)
        self.assertEqual(run.call_count,1);self.assertFalse((self.root/'analysis.json').exists())
    def test_invalid_output_repaired(self):
        with patch.object(codex_backend,'codex_binary',return_value='fake'),patch.object(codex_backend,'config_get',return_value={'model':'fixture','token_env':'NO_REAL_KEY'}),patch.object(codex_backend,'token_present'),patch.object(codex_backend,'run_codex',side_effect=['{}',json.dumps(self.result())]) as run:
            self.assertEqual(a.run(self.args(),cli),0)
        self.assertEqual(run.call_count,2)
        self.assertIn('上次校验错误',run.call_args.args[3])
    def test_evidence_and_false_statistics(self):
        summary=a.make_summary(self.report)
        f={'attribution':'skill','change_kind':'clarify_boundary','current_check':[{'skill':str(self.skill),'status':'still_applicable','sha256':summary['skills'][0]['current']['sha256']}],'supporting_sessions':[],'counter_evidence':[],'target':None,'regression_case':{'task':'fixture','expected':['observable behavior'],'forbidden':[],'verification':'fixture'},'category':'improve','skills':[str(self.skill)],'priority':'high','title':'fixture','observation':'fixture','recommendation':'fixture','confidence':'low','limitation':'fixture','verification':'fixture','evidence':[{'kind':'skill','path':str(self.skill),'start':1,'quote':'Example skill description'}]}
        result=self.result();result['findings']=[f]
        a.validate(result,self.report,summary)
        f['evidence'][0]['quote']='invented evidence'
        with self.assertRaises(ValueError):a.validate(result,self.report,summary)
        # Collapsed whitespace still matches; fabricated or ellipsis-joined text does not.
        f['evidence'][0]['quote']='Example  skill\n description'
        a.validate(result,self.report,summary)
        f['evidence'][0]['quote']='Example ... description'
        with self.assertRaises(ValueError):a.validate(result,self.report,summary)
        f['evidence'][0]['quote']='...skill description with…'
        a.validate(result,self.report,summary)
        f['evidence']=[{'kind':'statistic','skill':str(self.skill),'metric':'reads','value':99}]
        with self.assertRaises(ValueError):a.validate(result,self.report,summary)
        f['evidence']=[None]
        with self.assertRaises(ValueError):a.validate(result,self.report,summary)
    def test_bounded_and_whitelist(self):
        self.skill.write_text('x'*5000)
        summary=a.make_summary(self.report)
        self.assertLessEqual(len(a.file_piece(summary,'skill',str(self.skill),1)['text']),2400)
        with self.assertRaises(ValueError):a.file_piece(summary,'skill','/etc/passwd',1)
        self.assertNotIn('secret',str(a.clean_history({'role':'developer','content':'secret'})))
    def test_resume_rejects_changed_input(self):
        args=self.args();args.prepare_only=True;a.run(args,cli)
        args.resume=next((self.root/'analysis-runs').iterdir())
        self.path.write_text('{}')
        with self.assertRaises(ValueError):a.run(args,cli)

    def snapshots(self):
        self.report['sessions']=[]
        self.report['extraction']['reads']=[]
        for session_id in ('a','b'):
            folder=self.root/session_id;folder.mkdir()
            events=[]
            content=[
                {'role':'developer','content':'secret correction not a task'},
                {'role':'user','content':'please perform the task'},
                {'type':'function_call','name':'Read','arguments':'SKILL.md'},
                {'role':'assistant','content':'work completed, known mistake was fixed'},
                {'role':'user','content':'wrong approach: use the root command'},
                {'role':'assistant','content':'the corrected task is now successful'}]
            for index,value in enumerate(content):
                events.append({'event_id':session_id+str(index),'text':json.dumps(value),
                    'path':'/original/'+session_id,'location':'line:'+str(index+1),
                    'timestamp':'2026-09-29T10:00:00Z'})
            snapshot=folder/'events.jsonl';snapshot.write_text('\n'.join(json.dumps(x) for x in events))
            manifest=folder/'session.json';manifest.write_text(json.dumps({'snapshot':str(snapshot)}))
            self.report['sessions'].append({'key':'codex:'+session_id,'source':'codex','session_id':session_id,
                'manifest':str(manifest),'snapshot_sha256':a.digest_file(snapshot),'last_active':'2026-09-30T00:00:00Z'})
            for number in range(3):
                self.report['extraction']['reads'].append({'source':'codex','session_id':session_id,
                    'skill':str(self.skill),'status':'confirmed','timestamp':'2026-09-29T10:00:00Z',
                    'read_id':session_id+str(number),'original_file':'/original/'+session_id,
                    'request_location':'line:3','result_locations':[]})
        self.report['extraction']['summary']={'confirmed_reads':6,'failed_reads':0,'unconfirmed_reads':0,'skills':1}
        self.path.write_text(json.dumps(self.report))
        return a.make_summary(self.report)

    def finding(self,summary):
        row=next(x for x in summary['skills'] if x['skill']==str(self.skill))
        return {'category':'improve','skills':[str(self.skill)],'priority':'high','title':'fixture',
                'observation':'fixture','recommendation':'fixture','confidence':'low','limitation':'fixture',
                'verification':'fixture','attribution':'skill','change_kind':'clarify_boundary',
                'current_check':[{'skill':str(self.skill),'status':'still_applicable','sha256':row['current']['sha256']}],
                'supporting_sessions':[],'counter_evidence':[],'target':None,
                'regression_case':{'task':'fixture','expected':['observable behavior'],'forbidden':['bad command'],'verification':'fixture'},
                'evidence':[{'kind':'skill','path':str(self.skill),'start':1,'quote':'Example skill description'}]}

    def test_repeated_reads_preserved_and_sessions_distinct(self):
        summary=self.snapshots();row=summary['skills'][0]
        self.assertEqual(row['reads'],6);self.assertEqual(row['sessions'],2)
        self.assertEqual(row['top_session_share'],.5)
        self.assertEqual(row['recent_reads']['7'],6)
        self.assertEqual(row['weeks'],{'2026-09-28':6})
        self.assertEqual(summary['sessions_with_confirmed_reads'],2)
        self.assertNotIn('installation_date',row)

    def test_search_and_window_bounded_clean_and_citable(self):
        summary=self.snapshots()
        args=SimpleNamespace(session='codex:a',query='wrong approach',limit=1,offset=0)
        result=a.search_events(self.report,args)
        self.assertEqual(result['total'],1);self.assertEqual(result['matches'][0]['event_id'],'a4')
        args.query='secret correction'
        self.assertEqual(a.search_events(self.report,args)['total'],0)
        window=a.read_window(self.report,'a0')
        self.assertEqual(len(window['events']),6)
        self.assertTrue(all(len(e['text'])<=400 for e in window['events']))
        self.assertNotIn('secret',str(window))
        args=SimpleNamespace(path=str(self.skill),limit=1,offset=0)
        linked=a.skill_sessions(self.report,summary,args)
        self.assertEqual(linked['total'],2);self.assertEqual(linked['next_offset'],1)
        self.assertEqual(linked['sessions'][0]['confirmed'],3)
        with self.assertRaises(ValueError):a.search_events(self.report,SimpleNamespace(session='outside',query='wrong',limit=1,offset=0))

    def test_evidence_and_current_hash_tampering_rejected(self):
        summary=self.snapshots();f=self.finding(summary)
        result=self.result();result['findings']=[f]
        a.validate(result,self.report,summary)
        for field,value in [('attribution','made-up'),('regression_case',None),('current_check',[])]:
            altered=copy.deepcopy(result);altered['findings'][0][field]=value
            with self.assertRaises(ValueError):a.validate(altered,self.report,summary)
        self.skill.write_text('changed content')
        with self.assertRaisesRegex(ValueError,'正文已改变'):a.validate(result,self.report,summary)

    def test_new_candidate_needs_two_actual_sessions_not_reads(self):
        summary=self.snapshots();f=self.finding(summary)
        f.update(category='add',change_kind='add',skills=[],current_check=[],target={'name':'new-candidate'})
        e={'kind':'history','session':'codex:a','event_id':'a4','part':0,'quote':'wrong approach: use the root command'}
        f['evidence']=[e,dict(e,event_id='a5',quote='the corrected task is now successful')]
        f['supporting_sessions']=['codex:a']
        result=self.result();result['findings']=[f]
        with self.assertRaisesRegex(ValueError,'两个不同会话'):a.validate(result,self.report,summary)
        f['evidence'].append(dict(e,session='codex:b',event_id='b4'))
        f['supporting_sessions'].append('codex:b')
        a.validate(result,self.report,summary)
        f['supporting_sessions'].append('codex:a')
        with self.assertRaises(ValueError):a.validate(result,self.report,summary)

    def test_already_fixed_not_actionable_counter_evidence_validated(self):
        summary=self.snapshots();f=self.finding(summary);result=self.result();result['findings']=[f]
        f['current_check'][0]['status']='already_fixed'
        with self.assertRaisesRegex(ValueError,'全部已修复'):a.validate(result,self.report,summary)
        f.update(category='retain',change_kind='none',regression_case=None)
        a.validate(result,self.report,summary)
        f['counter_evidence']=[{'kind':'history','session':'codex:a','event_id':'a5','part':0,'quote':'the corrected task is now successful'}]
        a.validate(result,self.report,summary)
        f['counter_evidence'][0]['quote']='invented result'
        with self.assertRaises(ValueError):a.validate(result,self.report,summary)

    def test_helpers_reject_modified_snapshot_and_summary(self):
        self.snapshots()
        args=self.args();args.prepare_only=True;a.run(args,cli)
        state=next((self.root/'analysis-runs').iterdir())
        query=SimpleNamespace(manifest=state/'manifest.json',action='search',session='codex:a',query='wrong',limit=1,offset=0)
        self.assertEqual(a.lookup(query)['total'],1)
        (state/'summary.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'摘要已改变'):a.lookup(query)
        manifest=json.loads((self.root/'a/session.json').read_text())
        Path(manifest['snapshot']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'快照已改变'):a.history_piece(self.report,'codex:a','a4',0)

    def test_fake_model_nonempty_findings_saved_and_rendered(self):
        summary=self.snapshots();result=self.result();result['findings']=[self.finding(summary)]
        original=self.path.read_bytes()
        with patch.object(codex_backend,'codex_binary',return_value='fake'),patch.object(codex_backend,'config_get',return_value={'model':'fixture','token_env':'NO_REAL_KEY'}),patch.object(codex_backend,'token_present'),patch.object(codex_backend,'run_codex',return_value=json.dumps(result)):
            self.assertEqual(a.run(self.args(),cli),0)
        self.assertEqual(self.path.read_bytes(),original)
        artifact=a.read_json(self.root/'analysis.json')
        self.assertEqual(artifact['schema_version'],'summary-v2')
        self.assertEqual(artifact['findings'][0]['change_kind'],'clarify_boundary')
        self.assertIn('回归案例草稿',(self.root/'analysis.md').read_text())
        self.assertIn('clarify_boundary',(self.root/'report.html').read_text())

    def test_merge_requires_preserved_capabilities_and_entrypoint_plan(self):
        summary=self.snapshots()
        second=self.root/'skills/second/SKILL.md';second.parent.mkdir();second.write_text('Second skill description preserves an independent entrypoint.')
        summary=a.make_summary(self.report);f=self.finding(summary)
        f.update(category='merge',change_kind='merge',skills=[str(self.skill),str(second)])
        row=next(x for x in summary['skills'] if x['skill']==str(second))
        f['current_check'].append({'skill':str(second),'status':'still_applicable','sha256':row['current']['sha256']})
        f['evidence'].append({'kind':'skill','path':str(second),'start':1,'quote':'Second skill description'})
        f['target']={'destination':str(self.skill),'preserved_capabilities':['independent command entrypoint'],'entrypoint_plan':'keep an explicit route'}
        result=self.result();result['findings']=[f]
        a.validate(result,self.report,summary)
        f['target']['entrypoint_plan']=''
        with self.assertRaises(ValueError):a.validate(result,self.report,summary)

    def test_neighbour_skill_cited_and_all_errors_reported(self):
        summary=self.snapshots()
        other=self.root/'skills/other/SKILL.md';other.parent.mkdir();other.write_text('Other skill triggers on the same words.')
        summary=a.make_summary(self.report);f=self.finding(summary)
        f['evidence'].append({'kind':'skill','path':str(other),'start':1,'quote':'triggers on the same words'})
        result=self.result();result['findings']=[f]
        a.validate(result,self.report,summary)
        bad=copy.deepcopy(f);bad['title']='second';bad['evidence'][0]['quote']='invented evidence'
        worse=copy.deepcopy(f);worse['title']='third';worse['priority']='urgent'
        result['findings']=[f,bad,worse]
        with self.assertRaises(ValueError) as caught:a.validate(result,self.report,summary)
        self.assertIn('第2条「second」evidence[0] 证据摘录不匹配',str(caught.exception))
        self.assertIn('第3条「third」优先级',str(caught.exception))

    def patched(self,**kwargs):
        from contextlib import ExitStack
        stack=ExitStack()
        for name,value in (('codex_binary','fake'),('config_get',{'model':'fixture','token_env':'NO_REAL_KEY'}),('token_present',None)):
            stack.enter_context(patch.object(codex_backend,name,return_value=value))
        return stack,stack.enter_context(patch.object(codex_backend,'run_codex',**kwargs))

    def test_resume_revalidates_latest_draft_without_model(self):
        stack,run=self.patched(side_effect=['{}','{"status":"complete"}'])
        with stack:self.assertEqual(a.run(self.args(),cli),2)
        state=next((self.root/'analysis-runs').iterdir())
        # A draft that fails only a rule relaxed later is accepted on resume with no model call.
        (state/'draft-2.txt').write_text(json.dumps(self.result()))
        args=self.args();args.resume=state
        stack,run=self.patched(side_effect=AssertionError('must not run'))
        with stack:self.assertEqual(a.run(args,cli),0)
        artifact=a.read_json(self.root/'analysis.json')
        self.assertEqual(artifact['backend']['draft'],'draft-2.txt')
        self.assertIn('未重新请求模型',artifact['coverage']['notes'][-1])

    def test_resume_repairs_with_errors_and_keeps_numbering(self):
        stack,run=self.patched(return_value='{}')
        args=self.args();args.attempts=1
        with stack:self.assertEqual(a.run(args,cli),2)
        state=next((self.root/'analysis-runs').iterdir())
        args.resume=state
        stack,run=self.patched(return_value=json.dumps(self.result()))
        with stack:self.assertEqual(a.run(args,cli),0)
        prompt=run.call_args.args[3]
        self.assertIn('上次校验错误：分析输出顶层字段错误',prompt);self.assertIn(str(state/'draft-1.txt'),prompt)
        self.assertTrue((state/'draft-1.txt').exists() and (state/'draft-2.txt').exists())
        self.assertEqual(a.read_json(state/'attempt-2.json')['draft'],'draft-2.txt')

    def test_quote_matches_decoded_history_text(self):
        summary=self.snapshots();f=self.finding(summary)
        # Event text is a JSON line whose payload holds escaped tool output (two escape levels).
        nested={'type':'function_call_output','output':json.dumps({'output':'面板仍会列出“等待提交”的文件\n- 构建失败 → 使用 `build-skill`'},ensure_ascii=False)}
        folder=self.root/'a';rows=[json.loads(x) for x in (folder/'events.jsonl').read_text().splitlines()]
        rows[3]['text']=json.dumps(nested,ensure_ascii=False)
        (folder/'events.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows))
        self.report['sessions'][0]['snapshot_sha256']=a.digest_file(folder/'events.jsonl')
        e={'kind':'history','session':'codex:a','event_id':'a3','part':0,'quote':'面板仍会列出"等待提交"的文件\n- 构建失败 → 使用 build-skill'}
        f['evidence'].append(e);f['supporting_sessions']=['codex:a'];result=self.result();result['findings']=[f]
        a.validate(result,self.report,summary)
        e['quote']='面板仍会列出"等待提交"的目录\n- 构建失败 → 使用 build-skill'
        with self.assertRaises(ValueError):a.validate(result,self.report,summary)
