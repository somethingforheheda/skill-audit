import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_skill_review as review


class CatalogTests(unittest.TestCase):
    def test_catalog_root_supplies_text_for_skills_read_on_another_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / 'demo-skill@12345678'
            skill.mkdir()
            (skill / 'SKILL.md').write_text('---\nname: demo-skill\ndescription: Demo.\n---\nbody\n', encoding='utf-8')
            episodes = [{'skills': [{'name': 'demo-skill', 'path': '/Users/someone/.claude/skills/demo-skill/SKILL.md'}]}]
            plain = review.latest_copies(review.skill_catalog({'scope': {}}, episodes))
            self.assertFalse(plain['demo-skill']['present'])
            report = {'scope': {'catalog_roots': [tmp]}}
            found = review.latest_copies(review.skill_catalog(report, episodes))['demo-skill']
            self.assertTrue(found['present'])
            self.assertEqual(Path(found['path']).parent.name, 'demo-skill@12345678')

    def test_skill_read_elsewhere_counts_as_present_when_a_synced_copy_exists(self):
        import json
        import history_extract as extract
        with tempfile.TemporaryDirectory() as tmp:
            for key in ('demo-skill@aaaaaaaa', 'demo-skill@bbbbbbbb'):
                (Path(tmp) / key).mkdir()
                (Path(tmp) / key / 'SKILL.md').write_text('x', encoding='utf-8')
            (Path(tmp) / '_origins.json').write_text(json.dumps({
                'demo-skill@aaaaaaaa': '/Users/a/.agents/skills/demo-skill',
                'demo-skill@bbbbbbbb': '/Users/b/work/demo-skill'}), encoding='utf-8')
            gone = '/Users/b/work/demo-skill/SKILL.md'
            other = '/Users/c/nothing/SKILL.md'
            states = extract.current_skill_states([gone, other], [tmp])
            self.assertEqual(states[gone]['status'], 'present')
            self.assertEqual(states[other]['status'], 'unknown')
            self.assertEqual(extract.current_skill_states([gone])[gone]['status'], 'missing')
            import skill_scope
            self.assertEqual(skill_scope.catalog_copy(gone, [tmp]).parent.name, 'demo-skill@bbbbbbbb')
            link = '/Users/a/.claude/skills/demo-skill/SKILL.md'
            self.assertEqual(skill_scope.catalog_copy(link, [tmp]).parent.name, 'demo-skill@aaaaaaaa')

    def test_scanner_finds_skills_the_skill_tool_only_names_by_directory(self):
        import sync_scan
        text = 'x\\n"text":"Base directory for this skill: /Users/me/.claude/skills/watch\\n\\n# Watch"'
        self.assertEqual(sync_scan.skill_paths_in(text), {'/Users/me/.claude/skills/watch/SKILL.md'})
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / 'skills' / 'watch'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('x', encoding='utf-8')
            session = Path(tmp) / 's.jsonl'
            session.write_text('Base directory for this skill: %s\\n' % skill, encoding='utf-8')
            self.assertEqual(list(sync_scan.skill_dirs_in([str(session)], Path(tmp))), [str(skill.resolve())])

    def test_with_a_catalog_the_machines_own_skill_files_are_never_read(self):
        import history_extract as extract
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / 'own' / 'sneaky'
            own.mkdir(parents=True)
            (own / 'SKILL.md').write_text('---\nname: sneaky\n---\nlocal\n', encoding='utf-8')
            empty = Path(tmp) / 'catalog'
            empty.mkdir()
            path = str(own / 'SKILL.md')
            self.assertEqual(extract.current_skill_states([path], [str(empty)])[path]['status'], 'unknown')
            self.assertEqual(extract.current_skill_states([path])[path]['status'], 'present')
            found = review.skill_catalog({'scope': {'catalog_roots': [str(empty)]}}, [{'skills': [{'name': 'sneaky', 'path': path}]}])
            self.assertEqual([c['present'] for c in found], [False])

    def test_only_skills_in_a_skills_folder_of_a_repository_or_home_are_uploaded(self):
        import sync_scan
        keep = ['/Users/me/.agents/skills/watch', '/Users/me/work/app/.claude/skills/ohyo-build', 'C:\\Users\\me\\.codex\\skills\\x']
        drop = ['/Users/me/.codex/plugins/cache/o/figma/17.0.0/skills/figma-use', '/Users/me/.codex/skills/.system/imagegen',
                '/Users/me/.codex/memories/skills/note', '/Users/me/other/skills/x']
        self.assertEqual([sync_scan.own_skill(p) for p in keep], [True] * 3)
        self.assertEqual([sync_scan.own_skill(p) for p in drop], [False] * 4)


if __name__ == '__main__':
    unittest.main()
