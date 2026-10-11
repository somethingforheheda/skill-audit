"""Real subprocess regression tests, using a local fake Codex; no network or credentials."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import codex_process

SOURCE=Path(__file__).resolve().parents[1]
ENTRY=Path(os.environ.get('SKILL_AUDIT_TEST_APP',str(SOURCE/'skill_audit_cli.py')))


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='skill-audit-process-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def wait_for(self,condition,timeout=8):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            if condition():return
            time.sleep(.05)
        self.fail('local fixture did not become ready')

    def test_process_normal_completion_and_timeout(self):
        result=codex_process.execute([sys.executable,'-c','import sys; print(sys.stdin.read())'],'hello',2,os.environ.copy(),self.root)
        self.assertEqual(result.stdout.strip(),'hello');self.assertEqual(result.returncode,0)
        started=time.monotonic()
        with self.assertRaises(codex_process.TimedOut):
            codex_process.execute([sys.executable,'-c','import time; time.sleep(30)'],'',.2,os.environ.copy(),self.root)
        self.assertLess(time.monotonic()-started,3)

    def test_cancel_before_start_never_spawns(self):
        control=codex_process.Control();control.cancel()
        with self.assertRaises(codex_process.Cancelled):
            codex_process.execute(['nonexistent-fixture-binary'],'',1,os.environ.copy(),self.root,control)

    @unittest.skipUnless(os.name=='posix','process group signals require POSIX')
    def test_single_ctrl_c_preserves_checkpoint_and_stops_descendants(self):
        bin_dir=self.root/'bin';bin_dir.mkdir()
        conf=self.root/'audit-home';conf.mkdir(parents=True)
        (conf/'config.json').write_text(json.dumps({'base_url':'https://gateway.example.com/v1','token_env':'SKILL_AUDIT_TEST_KEY','model':'fixture'}))
        history=self.root/'history/.codex/sessions';history.mkdir(parents=True)
        for number in range(4):
            rows=[{'type':'session_meta','payload':{'id':'fixture-'+str(number),'cwd':str(self.root)}},
                  {'type':'response_item','timestamp':datetime.now(timezone.utc).isoformat(),'payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'unrelated fixture request'}]}}]
            (history/(str(number)+'.jsonl')).write_text('\n'.join(json.dumps(x) for x in rows))
        log=self.root/'starts.jsonl'; heartbeat=self.root/'heartbeat';child_ready=self.root/'child-ready'
        fake=bin_dir/'codex'
        fake.write_text('#!'+sys.executable+'\n'+'''import json,os,sys,subprocess,time,shlex
from pathlib import Path
root=Path(sys.argv[sys.argv.index('-C')+1]);prompt=sys.stdin.read()
assert '--enable' in sys.argv and sys.argv[sys.argv.index('--enable')+1]=='multi_agent'
assert (root/'summary.json').exists()
log=Path(os.environ['FIXTURE_LOG'])
count=len(log.read_text().splitlines()) if log.exists() else 0
with log.open('a') as stream:stream.write(json.dumps({'root':str(root),'prompt':prompt})+'\\n')
if os.getenv('FIXTURE_RESUME'):
    output=Path(sys.argv[sys.argv.index('-o')+1])
    result={'status':'complete','uncovered':[],'overview':'fixture','findings':[]}
    output.write_text(json.dumps(result));sys.exit(0)
child="""import os,signal,time
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
Path(os.environ['FIXTURE_READY']).write_text(str(os.getpid()))
while True:
 Path(os.environ['FIXTURE_HEARTBEAT']).write_text(str(time.time()))
 time.sleep(.03)
"""
subprocess.Popen([sys.executable,'-c',child])
while True:time.sleep(1)
''')
        fake.chmod(0o755)
        env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ.get('PATH',''),
             'SKILL_AUDIT_HOME':str(self.root/'audit-home'),'SKILL_AUDIT_TEST_KEY':'fixture-not-a-real-key',
             'FIXTURE_LOG':str(log),'FIXTURE_READY':str(child_ready),'FIXTURE_HEARTBEAT':str(heartbeat)}
        report=self.root/'report.json'
        command=[sys.executable,str(ENTRY),'history','--history-home',str(self.root/'history'),
                 '--project',str(self.root),'--output',str(report),'--timeout','30','--attempts','3']
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        try:
            self.wait_for(lambda:(child_ready.exists() and heartbeat.exists()) or proc.poll() is not None)
            if proc.poll() is not None:
                out,err=proc.communicate()
                self.fail('fixture exited early: '+err)
            self.assertEqual(len(log.read_text().splitlines()),1)
            started=time.monotonic();proc.send_signal(signal.SIGINT)
            stdout,stderr=proc.communicate(timeout=6)
            self.assertEqual(proc.returncode,130,stderr)
            self.assertNotIn('Traceback',stderr);self.assertNotIn('第 2 次',stderr)
            self.assertLess(time.monotonic()-started,5)
            self.assertEqual(len(log.read_text().splitlines()),1,'queued tasks started after cancellation')
            old=heartbeat.read_text();time.sleep(.2)
            self.assertEqual(heartbeat.read_text(),old,'grandchild survived cancellation')
            report=report.with_suffix('')/'report.json'
            before=report.read_bytes()
            data=json.loads(before);self.assertEqual(len(data['sessions']),4)
            state=next((report.parent/'analysis-runs').iterdir())
            self.assertTrue((state/'cancelled.json').exists())
            resumed=subprocess.run([sys.executable,str(ENTRY),'history','--resume',str(state)],env={**env,'FIXTURE_RESUME':'1'},capture_output=True,text=True,timeout=10)
            self.assertEqual(resumed.returncode,0,resumed.stderr)
            self.assertEqual(before,report.read_bytes())
            self.assertTrue((report.parent/'analysis.json').exists())
            self.assertEqual(len(log.read_text().splitlines()),2,'one overall agent per attempt, no per-session fanout')
        finally:
            if proc.poll() is None:proc.kill();proc.communicate()
            # Emergency fixture-only cleanup should an assertion reveal a leak.
            if child_ready.exists():
                try:os.kill(int(child_ready.read_text()),signal.SIGKILL)
                except ProcessLookupError:pass


if __name__=='__main__':unittest.main()
