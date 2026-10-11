"""Offline extraction tests: no models, credentials, or real histories."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import history_extract as x
import skill_audit_cli as cli
import agent_backends.codex as codex_backend

class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def extract(self, payloads, source="codex"):
        events=[]
        for i,p in enumerate(payloads):
            events.append({'event_id':str(i),'timestamp':'2026-09-01T01:00:00+00:00','path':'/original/rollout.jsonl','location':f'line:{i+1}','text':json.dumps(p)})
        snapshot=self.root/'events.jsonl';snapshot.write_text('\n'.join(json.dumps(e) for e in events))
        manifest=self.root/'session.json';manifest.write_text(json.dumps({'snapshot':str(snapshot)}))
        return x.extract({'coverage':{'uncovered':[]},'sessions':[{'key':'codex:s','session_id':'s','source':source,'project':'/repo','manifest':str(manifest)}]})
    def request(self,id,cmd):
        return {'type':'function_call','name':'exec_command','call_id':id,'arguments':json.dumps({'cmd':cmd})}
    def result(self,id,body='---\nname: demo\ndescription: fixture\n---\nbody'):
        return {'type':'function_call_output','call_id':id,'output':body}
    def test_repeated_reads_and_duplicate_archive(self):
        a=self.request('a','cat .agents/skills/demo/SKILL.md');b=self.result('a')
        d=self.extract([a,b,a,b,self.request('b','cat .agents/skills/demo/SKILL.md'),self.result('b')])
        self.assertEqual(d['summary']['confirmed_reads'],2)
        self.assertEqual(d['reads'][0]['time_beijing'],'2026-09-01T09:00:00+08:00')
        self.assertEqual(d['reads'][0]['original_file'],'/original/rollout.jsonl')
    def test_multiple_same_path_one_command(self):
        d=self.extract([self.request('a','cat demo/SKILL.md demo/SKILL.md'),self.result('a')])
        self.assertEqual(d['summary']['confirmed_reads'],2)
    def test_newlines_and_search_not_reads(self):
        items=x.shell_reads('cat a/SKILL.md\nrg name b/SKILL.md\ncat c/SKILL.md','/repo')
        self.assertEqual([i[0] for i in items],['/repo/a/SKILL.md','/repo/c/SKILL.md'])
        self.assertEqual(x.shell_reads('echo a/SKILL.md; ls b/SKILL.md','/repo'),[])
    def test_partial_and_failure(self):
        d=self.extract([self.request('a','cat demo/SKILL.md'),self.result('a','No such file or directory'),self.request('b','cat unknown/SKILL.md')])
        self.assertEqual(d['summary']['confirmed_reads'],0)
        self.assertEqual(d['summary']['failed_reads'],1)
        self.assertEqual(d['summary']['unconfirmed_reads'],1)
    def test_wrapper_and_escaped_output(self):
        req={'type':'custom_tool_call','name':'functions.exec','call_id':'a','input':'text(await tools.exec_command({cmd:"cat demo/SKILL.md"}));'}
        d=self.extract([req,self.result('a',json.dumps({'output':'---\nname: demo\ndescription: fixture\n---'}))])
        self.assertEqual(d['summary']['confirmed_reads'],1)
    def test_workdir_and_variables(self):
        req={'name':'exec_command','arguments':json.dumps({'cmd':'cat demo/SKILL.md','workdir':'sub'})}
        self.assertEqual(x.requests(req,'/repo')[0][0],'/repo/sub/demo/SKILL.md')
        self.assertIsNone(x.shell_reads('cat $ROOT/demo/SKILL.md','/repo')[0][0])
    def test_claude_read_and_result_pair(self):
        d=self.extract([{'type':'tool_use','name':'Read','id':'a','input':{'file_path':'/repo/demo/SKILL.md'}},{'type':'tool_result','tool_use_id':'a','content':'actual partial body'}])
        self.assertEqual(d['summary']['confirmed_reads'],1)
    def test_successful_read_with_error_examples_is_not_failure(self):
        d=self.extract([{'type':'tool_use','name':'Read','id':'a','input':{'file_path':'/repo/demo/SKILL.md'}},{'type':'tool_result','tool_use_id':'a','content':'Skill instructions: example error ENOENT: no such file or directory, open missing.txt'}])
        self.assertEqual(d['summary']['confirmed_reads'],1)
    def test_wrapper_literal_workdir(self):
        req={'type':'custom_tool_call','name':'functions.exec','call_id':'a','input':'tools.exec_command({cmd:"cat demo/SKILL.md",workdir:"/actual"})'}
        d=self.extract([req,self.result('a')])
        self.assertEqual(d['summary']['confirmed_reads'],1)
        self.assertEqual(d['reads'][0]['skill'],'/actual/demo/SKILL.md')

    def test_wrapper_separate_workdirs(self):
        code='tools.exec_command({cmd:"cat demo/SKILL.md",workdir:"/first"});tools.exec_command({cmd:"cat demo/SKILL.md",workdir:"/second"})'
        self.assertEqual([r[0] for r in x.requests({'name':'functions.exec','input':code},'/repo')],['/first/demo/SKILL.md','/second/demo/SKILL.md'])

    def test_heredoc_source_not_a_read(self):
        command="cat > script.py <<'PY'\ncat demo/SKILL.md\nprint('unbalanced quote)\nPY\ncat actual/SKILL.md"
        self.assertEqual([r[0] for r in x.shell_reads(command,'/repo')],['/repo/actual/SKILL.md'])

    def comate(self, ident='a', name='read_file', params=None, result=None, **extra):
        return {'id':ident,'type':'TOOL','toolName':name,'params':params or {'file_path':'demo/SKILL.md'},
                'toolState':'executed','result':result or {'output':'body'},**extra}

    def test_comate_repeated_injection_and_location(self):
        output='## Skill: demo\n\n**Base directory**: /repo/demo\n\n# Demo body'
        a=self.comate(name='skill',params={'name':'demo'},result={'output':output},startTime=1790586460212)
        b={**a,'id':'b'}
        message={'role':'assistant','elements':[{'children':[a,b]}]}
        d=self.extract([message,message],source='comate')
        self.assertEqual(d['summary']['confirmed_reads'],2)
        self.assertEqual(d['reads'][0]['request_location'],'line:1/elements/0/children/0')
        self.assertEqual(d['reads'][0]['time_basis'],'tool_start')
        self.assertEqual(d['reads'][0]['read_method'],'injection')
        self.assertEqual(d['reads'][0]['skill'],'/repo/demo/SKILL.md')

    def test_comate_read_shell_failure_missing_and_discard(self):
        nodes=[self.comate(),self.comate('b','run_command',{'command':'cat demo/SKILL.md'},
              {'output':'name: demo','metadata':{'cwd':'/other','exitCode':0}}),
              self.comate('c',result={'output':'name: demo','metadata':{'exitCode':1}}),
              self.comate('d',toolState='pending'),self.comate('e',discard=True),
              self.comate('f',result={'output':'[UNAVAILABLE BLOB]'})]
        d=self.extract([{'role':'assistant','elements':[{'children':nodes}]}],source='comate')
        self.assertEqual(d['summary'],{'confirmed_reads':2,'skills':2,'failed_reads':1,'unconfirmed_reads':2})
        self.assertTrue(all(r['time_basis']=='message' for r in d['reads']))

    def test_comate_catalogue_user_text_and_name_only(self):
        d=self.extract([{'role':'user','elements':[self.comate()]},
            {'role':'assistant','elements':[self.comate(name='skill',params={'name':'demo'})]}],source='comate')
        self.assertEqual(d['summary']['confirmed_reads'],0)
        self.assertEqual(len(d['uncovered']),1)

    def test_quoted_js_keys_and_partial_body(self):
        req={'type':'custom_tool_call','name':'functions.exec','call_id':'a',
             'input':'text(await tools.exec_command({"cmd":"sed -n \'40,60p\' demo/SKILL.md","workdir":"/actual"}))'}
        d=self.extract([req,self.result('a','Section body without frontmatter')])
        self.assertEqual(d['summary']['confirmed_reads'],1)
        self.assertEqual(d['reads'][0]['skill'],'/actual/demo/SKILL.md')

    def test_shell_path_resolution_without_execution(self):
        self.assertEqual(x.shell_reads('cat ~/.agents/skills/demo/SKILL.md','/repo','/historical')[0][0],'/historical/.agents/skills/demo/SKILL.md')
        self.assertIsNone(x.shell_reads("cat '~/demo/SKILL.md'",'/repo','/historical')[0][0])
        self.assertEqual(x.shell_reads('cd /other && cat demo/SKILL.md','/repo')[0][0],'/other/demo/SKILL.md')
        self.assertIsNone(x.shell_reads('cd /other; cat demo/SKILL.md','/repo')[0][0])
        self.assertEqual(x.shell_reads('cd /repo; cat demo/SKILL.md','/repo')[0][0],'/repo/demo/SKILL.md')
        self.assertEqual([r[0] for r in x.shell_reads('for f in a b; do cat .agents/skills/$f/SKILL.md; done','/repo')],['/repo/.agents/skills/a/SKILL.md','/repo/.agents/skills/b/SKILL.md'])
        self.assertEqual(x.shell_reads("sed -i 's/a/b/' demo/SKILL.md",'/repo'),[])

    def test_no_false_confirmation_of_empty_or_mixed_output(self):
        for command,output in [('cat demo/SKILL.md','Process exited with code 0\nFinal output:\n'),
            ('cat demo/SKILL.md; echo done','done'),('cat demo/SKILL.md','Process exited with code 1\nFinal output:\nfailed')]:
            self.assertEqual(self.extract([self.request('a',command),self.result('a',output)])['summary']['confirmed_reads'],0)

    def test_unrelated_failure_does_not_mark_partial_read_failed(self):
        d=self.extract([self.request('a',"sed -n '20,30p' demo/SKILL.md; cat other.txt"),self.result('a','section body\ncat: other.txt: No such file or directory')])
        self.assertEqual(d['summary']['failed_reads'],0)
        self.assertEqual(d['summary']['unconfirmed_reads'],1)

    def test_claude_injection_repeated_and_reinvoked(self):
        records=[]
        for call in ('a','b'):
            records.extend([{'type':'tool_use','name':'Skill','id':call,'input':{'skill':'demo'}},
                {'role':'user','content':[{'type':'tool_result','tool_use_id':call,'content':'Launching skill: demo'}]},
                {'role':'user','content':'(Re-invocation of /demo — already loaded)'},
                {'role':'user','content':[{'type':'text','text':'Base directory for this skill: /repo/demo\n\n# Actual skill body'}]}])
        d=self.extract(records,source='claude')
        self.assertEqual(d['summary']['confirmed_reads'],2)
        self.assertEqual(d['reads'][0]['read_method'],'injection')
        self.assertEqual(len(d['reads'][0]['result_locations']),2)

    def test_claude_injection_uses_file_order_not_timestamp_order(self):
        events=[{'path':'/history.jsonl','location':'line:2','text':json.dumps({'role':'user','content':[{'type':'tool_result','tool_use_id':'a','content':'Launching skill: demo'}]})},
            {'path':'/history.jsonl','location':'line:3','text':json.dumps({'role':'user','content':[{'type':'text','text':'Base directory for this skill: /repo/demo\n\n# Body'}]})}]
        self.assertEqual(x.claude_injections(events[::-1])['a'][1],'/repo/demo/SKILL.md')

    def test_literal_loop_with_echo_and_quoted_sed(self):
        cmd='for f in a b; do echo "--- $f ---"; sed -n \'1,20p\' .agents/skills/$f/SKILL.md | sed -n \'/^---$/,/^---$/p\'; done'
        self.assertEqual([r[0] for r in x.shell_reads(cmd,'/repo')],['/repo/.agents/skills/a/SKILL.md','/repo/.agents/skills/b/SKILL.md'])

    def test_claude_unrelated_body_not_paired(self):
        d=self.extract([{'type':'tool_use','name':'Skill','id':'a','input':{'skill':'demo'}},
            {'role':'user','content':[{'type':'tool_result','tool_use_id':'a','content':'Launching skill: demo'}]},
            {'role':'user','content':[{'type':'text','text':'Base directory for this skill: /repo/other\n\n# Other body'}]}],source='claude')
        self.assertEqual(d['summary']['confirmed_reads'],0)
        self.assertEqual(len(d['uncovered']),1)

    def test_same_name_fallback_does_not_confirm_both_sources(self):
        d=self.extract([self.request('a',"cat /personal/demo/SKILL.md 2>/dev/null || cat /repo/demo/SKILL.md"),self.result('a')])
        self.assertEqual(d['summary']['confirmed_reads'],0)
        self.assertEqual(d['summary']['unconfirmed_reads'],2)
        self.assertTrue(all('多个来源' in r['reason'] for r in d['reads']))

    def test_current_skill_state_snapshot(self):
        present=self.root/'SKILL.md';present.write_text('body')
        missing=self.root/'gone/SKILL.md'
        states=x.current_skill_states([str(present),str(missing)])
        self.assertEqual(states[str(present)]['status'],'present')
        self.assertEqual(states[str(missing)]['status'],'missing')
        self.assertTrue(states[str(missing)]['checked_at'])
        with patch.object(Path,'stat',side_effect=PermissionError('denied')):
            self.assertEqual(x.current_skill_states([str(present)])[str(present)]['status'],'unknown')

    def test_collect_only_needs_no_codex_or_config(self):
        output=self.root/'out.json'
        with patch.object(codex_backend,'codex_binary',side_effect=AssertionError('must not call')),patch.object(codex_backend,'config_get',side_effect=AssertionError('must not load')),patch.object(codex_backend,'run_codex',side_effect=AssertionError('no AI')):
            code=cli.main(['history','--collect-only','--history-home',str(self.root),'--all-projects','--sources','codex','--output',str(output)])
        self.assertEqual(code,0)
        self.assertIn('extraction',json.loads((output.with_suffix('')/'report.json').read_text()))

if __name__=='__main__':unittest.main()
