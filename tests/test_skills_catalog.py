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


if __name__ == '__main__':
    unittest.main()
