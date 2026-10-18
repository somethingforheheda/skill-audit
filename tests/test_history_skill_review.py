import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_skill_review as review


def episode(eid, skills, score, start=10, end=40):
    return {'id': eid, 'session': 'codex:s1', 'skills': [{'name': n, 'path': f'/skills/{n}/SKILL.md'} for n in skills],
            'start_event': start, 'end_event': end, 'read_events': [start + 1], 'result_events': [start + 2],
            'signals': {'score': score}, 'started_at': '2026-10-01T00:00:00+00:00', 'request': 'r'}


class SkillReviewTests(unittest.TestCase):
    def test_select_keeps_one_episode_per_skill_before_filling_by_score(self):
        eps = [episode('a', ['x'], 9), episode('b', ['x'], 8), episode('c', ['x'], 7), episode('d', ['y'], 1)]
        chosen = [e['id'] for e in review.select(eps, 2)]
        self.assertEqual(chosen, ['a', 'd'])
        self.assertEqual([e['id'] for e in review.select(eps, 3)], ['a', 'b', 'd'])

    def test_payload_tolerates_prose_around_json(self):
        self.assertEqual(review.payload('好的：\n{"a": 1}\n以上'), {'a': 1})
        with self.assertRaises(ValueError):
            review.payload('没有 JSON')

    def test_check_judgment_rejects_unknown_attribution_and_flags_far_events(self):
        e = episode('a', ['x'], 1)
        ok = review.check_judgment({'episode_id': 'a', 'issues': [{'event': 12, 'attribution': 'skill_gap'},
                                                                   {'event': 999, 'attribution': 'agent_general'}]}, e)
        self.assertEqual([i['event_in_episode'] for i in ok['issues']], [True, False])
        with self.assertRaises(ValueError):
            review.check_judgment({'episode_id': 'a', 'issues': [{'event': 12, 'attribution': 'blame'}]}, e)
        with self.assertRaises(ValueError):
            review.check_judgment({'episode_id': 'other', 'issues': []}, e)

    def test_check_findings_binds_occurrences_to_judged_episodes(self):
        eps = [episode('a', ['x'], 1), episode('b', ['x'], 1)]
        value = {'findings': [
            {'title': 'kept', 'severity': 'extreme', 'occurrences': [{'episode_id': 'a', 'session': 'codex:wrong', 'event': 12},
                                                                       {'episode_id': 'ghost', 'event': 1}]},
            {'title': 'dropped', 'occurrences': [{'episode_id': 'b'}]}]}
        out = review.check_findings(value, {'a': {}}, eps)
        self.assertEqual([f['title'] for f in out['findings']], ['kept'])
        self.assertEqual(out['findings'][0]['occurrences'][0]['session'], 'codex:s1')
        self.assertEqual(out['findings'][0]['severity'], 'medium')
        self.assertEqual(out['dropped_occurrences'], 2)

    def test_check_mining_resolves_session_prefixes(self):
        keys = {'codex:01a0c350-b404', 'claude:8cbdf491-df41'}
        out = review.check_mining({'candidates': [{'name': 'n', 'evidence': [{'session': 'codex:01a0c350'}, {'session': 'codex:zzz'}]}],
                                   'preferences': []}, keys)
        found = [e['found'] for e in out['candidates'][0]['evidence']]
        self.assertEqual(found, [True, False])
        self.assertEqual(out['candidates'][0]['evidence'][0]['session'], 'codex:01a0c350-b404')

    def test_transcript_rows_drop_system_and_cap_long_text(self):
        items = [{'kind': 'system', 'text': 'rules', 'event': 0, 'timestamp': 't'},
                 {'kind': 'result', 'text': 'x' * 2000, 'event': 1, 'timestamp': 't', 'failed': True},
                 {'kind': 'skill', 'text': 'Base directory for this skill: /s\n\nbody', 'event': 2, 'timestamp': 't'}]
        rows = review.transcript(items)
        self.assertEqual([r[0] for r in rows], ['result', 'skill'])
        self.assertEqual((rows[0][4], rows[0][6]), (1, 2000))
        self.assertNotIn('body', rows[1][1])

    def test_skill_catalog_reads_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'demo' / 'SKILL.md'
            path.parent.mkdir()
            path.write_text('---\nname: demo\ndescription: Use when testing.\n  More words.\n---\nbody')
            cat = review.skill_catalog({'scope': {'skills_roots': [d]}}, [])
            self.assertEqual(cat[0]['name'], 'demo')
            self.assertEqual(cat[0]['description'], 'Use when testing. More words.')

    def test_findings_message_prefers_existing_copy_of_a_skill(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'x' / 'SKILL.md'
            path.parent.mkdir()
            path.write_text('---\nname: x\n---\nreal body')
            catalog = [{'name': 'x', 'path': str(path), 'present': True, 'description': ''},
                       {'name': 'x', 'path': '/gone/x/SKILL.md', 'present': False, 'description': ''}]
            judged = {'a': {'issues': [{'attribution': 'skill_gap', 'skill': 'x'}]}}
            groups = review.finding_groups([episode('a', ['x'], 1)], judged)
            text = review.findings_message('x', groups['x'], catalog, {'scope': {}})
            self.assertIn('real body', text)
            self.assertNotIn('未找到', text)

    def test_finding_groups_split_by_skill_and_agent_behaviour(self):
        judged = {'a': {'issues': [{'attribution': 'skill_gap', 'skill': 'x'}, {'attribution': 'agent_general'},
                                   {'attribution': 'environment'}]},
                  'b': {'issues': [{'attribution': 'agent_ignored_skill', 'skill': 'y'}]}}
        groups = review.finding_groups([episode('a', ['x'], 1), episode('b', ['y'], 1)], judged)
        self.assertEqual(sorted(groups), [review.CROSS, 'x', 'y'])
        self.assertEqual([len(r['issues']) for r in groups['x']], [1])
        self.assertEqual(review.dismissed_counts(judged), {'user_change': 0, 'environment': 1, 'unclear': 0})
        self.assertIn('x 1 条', review.overview([{'skill': 'x', 'type': 'skill_gap', 'severity': 'high'}], judged))

    def test_prompts_are_generic(self):
        for text in (review.JUDGE_PROMPT, review.FINDINGS_PROMPT, review.MINING_PROMPT):
            self.assertNotIn('百度', text)
            self.assertIn('不执行', text)


if __name__ == '__main__':
    unittest.main()
