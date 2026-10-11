"""Directory union, boundary checks and deterministic extraction."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import skill_scope as scope
import history_extract as extract
import skill_audit_cli as cli

class ScopeTests(unittest.TestCase):
    def test_union_and_boundary(self):
        roots=scope.normalize(['.agents/skills','/personal/skills','.agents/skills'],'/repo')
        self.assertEqual(roots,['/personal/skills','/repo/.agents/skills'])
        self.assertTrue(scope.contains('/repo/.agents/skills/demo/SKILL.md',roots))
        self.assertTrue(scope.contains('/personal/skills/demo/SKILL.md',roots))
        self.assertFalse(scope.contains('/repo/.agents/skills-other/demo/SKILL.md',roots))
        self.assertFalse(scope.contains('/repo/.agents/skills/../../outside/SKILL.md',roots))
        self.assertFalse(scope.contains(None,roots))
    def test_cli_repeatable(self):
        args=cli.build_parser().parse_args(['history','--skills-root','/one','--skills-root','/two'])
        self.assertEqual(args.skills_root,[Path('/one'),Path('/two')])
    def fixture(self, root):
        events=[]
        for i,path in enumerate(['/repo/skills/demo/SKILL.md','/personal/skills/demo/SKILL.md','/other/demo/SKILL.md']):
            for typ in ['function_call','function_call_output']:
                content={'type':typ,'name':'read_file','call_id':str(i),'arguments':json.dumps({'path':path}),'output':'body'}
                events.append({'event_id':str(len(events)),'text':json.dumps(content),'timestamp':'2026-09-01T00:00:00+00:00','path':'/archive.jsonl','location':str(len(events))})
        snapshot=root/'events.jsonl';snapshot.write_text('\n'.join(json.dumps(e) for e in events))
        manifest=root/'session.json';manifest.write_text(json.dumps({'snapshot':str(snapshot),'skills_roots':['/repo/skills','/personal/skills'],'project':'/repo'}))
        session={'manifest':str(manifest),'source':'codex','session_id':'one','key':'codex:one','project':'/repo','skills_roots':['/repo/skills','/personal/skills']}
        return session
    def test_extraction_union_counts_only_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            session=self.fixture(Path(directory))
            report=extract.extract({'scope':{'skills_roots':session['skills_roots']},'coverage':{'uncovered':[]},'sessions':[session]})
            self.assertEqual(report['summary']['confirmed_reads'],2)
            self.assertEqual(report['excluded_reads'],1)
            self.assertEqual(report['status'],'complete')

if __name__=='__main__':unittest.main()
