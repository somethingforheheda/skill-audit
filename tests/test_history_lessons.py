import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_lessons as lessons
import history_skill_review as review

BODY = '# demo\n\nAlways run the linter before pushing.\nKeep commits small.\n'


def codes(proposal):
    return [f['code'] for f in proposal['flags']]


class ProposalTests(unittest.TestCase):
    def patch(self, **extra):
        return {'action': 'in_place_patch', 'target_file': 'SKILL.md', 'old_string': 'Keep commits small.',
                'new_string': 'Keep commits small - reviewers lose context in large diffs.', **extra}

    def test_in_place_patch_checks_anchor_against_current_text(self):
        ok = lessons.clean_proposal(self.patch(), {'SKILL.md': BODY})
        self.assertEqual(ok['flags'], [])
        missing = lessons.clean_proposal(self.patch(old_string='Not in the file.'), {'SKILL.md': BODY})
        self.assertEqual(codes(missing), ['anchor_missing'])
        spaced = lessons.clean_proposal(self.patch(old_string='Keep  commits\nsmall.'), {'SKILL.md': BODY})
        self.assertEqual(spaced['flags'], [])

    def test_in_place_patch_drops_noops_and_foreign_targets(self):
        self.assertIsNone(lessons.clean_proposal(self.patch(new_string='Keep commits   small.'), {'SKILL.md': BODY}))
        self.assertIsNone(lessons.clean_proposal(self.patch(target_file='AGENTS.md'), {}))
        self.assertIsNone(lessons.clean_proposal(self.patch(new_string=None), {}))
        self.assertIsNone(lessons.clean_proposal({'action': 'rewrite_everything'}, {}))
        self.assertIsNone(lessons.clean_proposal(None, {}))
        rules = lessons.clean_proposal(self.patch(target_file='AGENTS.md', old_string=None), {}, files=lessons.RULE_FILES, support=False)
        self.assertEqual(rules['target_file'], 'AGENTS.md')

    def test_pure_addition_needs_a_valid_position(self):
        add = self.patch(old_string=None, after_line=99)
        self.assertEqual(codes(lessons.clean_proposal(add, {'SKILL.md': BODY})), ['line_out_of_range'])
        self.assertEqual(codes(lessons.clean_proposal(self.patch(old_string=None), {'SKILL.md': BODY})), ['no_position'])
        self.assertEqual(lessons.clean_proposal(self.patch(old_string=None, after_line=3), {'SKILL.md': BODY})['flags'], [])

    def test_lesson_shape_flags(self):
        story = 'On 2026-10-02 PR 4821 broke the build, see ABC-12345.\nUPDATE: the tool is broken now.'
        flagged = lessons.clean_proposal(self.patch(old_string=None, after_line=1, new_string=story), {})
        self.assertEqual(sorted(codes(flagged)), ['appended_update', 'dated', 'incident_ref', 'tool_ban'])
        long = lessons.clean_proposal(self.patch(new_string='\n'.join('rule %d' % i for i in range(41))), {})
        self.assertIn('too_long', codes(long))

    def test_support_files_need_safe_topic_paths_and_a_pointer(self):
        script = {'action': 'add_script', 'target_file': 'scripts/check.py', 'draft_content': 'print(1)', 'pointer': 'Run scripts/check.py first.'}
        self.assertEqual(lessons.clean_proposal(script, {})['flags'], [])
        self.assertEqual(codes(lessons.clean_proposal({**script, 'pointer': None}, {})), ['no_pointer'])
        for bad in ('../scripts/check.py', '/etc/passwd', 'references/check.py', 'scripts/a/b.py', 'check.py'):
            self.assertIsNone(lessons.clean_proposal({**script, 'target_file': bad}, {}), bad)
        self.assertIsNone(lessons.clean_proposal(script, {}, support=False))
        ref = {'action': 'split_to_reference', 'target_file': 'references/error-codes.md', 'draft_content': '| code | meaning |', 'pointer': 'Read references/error-codes.md when a code appears.'}
        self.assertEqual(lessons.clean_proposal(ref, {})['flags'], [])
        self.assertIsNone(lessons.clean_proposal({**ref, 'target_file': 'references/error-codes.txt'}, {}))
        self.assertIn('named_by_incident', codes(lessons.clean_proposal({**ref, 'target_file': 'references/2026-10-02-outage.md'}, {})))


class GuardTests(unittest.TestCase):
    def test_judge_guards_remove_environment_fixes_and_failed_workflows(self):
        value = {'outcome': 'fail', 'reusable_workflow': 'try A then B', 'issues': [
            {'attribution': 'environment', 'fix': 'never use tool X'}, {'attribution': 'skill_gap', 'fix': 'add rule'}]}
        out = lessons.judge_guards(value)
        self.assertEqual((out['reusable_workflow'], out['issues'][0]['fix'], out['issues'][1]['fix']), (None, None, 'add rule'))
        self.assertEqual(out['guards'], ['environment_fix_removed', 'failed_workflow_removed'])
        clean = lessons.judge_guards({'outcome': 'success', 'reusable_workflow': 'w', 'issues': []})
        self.assertNotIn('guards', clean)

    def test_candidate_flags(self):
        row = {'name': 'fix-login-ABC-12345', 'action': 'new', 'steps': ['step'], 'pitfalls': []}
        self.assertEqual(sorted(f['code'] for f in lessons.candidate_flags(row, {'other'})), ['named_by_incident', 'not_kebab'])
        self.assertIn('name_exists', [f['code'] for f in lessons.candidate_flags({'name': 'other', 'action': 'new'}, {'other'})])
        merge = {'name': 'x', 'action': 'merge_into', 'merge_target': 'ghost'}
        self.assertIn('target_missing', [f['code'] for f in lessons.candidate_flags(merge, {'other'})])
        self.assertEqual(lessons.candidate_flags({'name': 'release-check', 'action': 'new', 'pitfalls': ['Pin the version - drift breaks builds.']}, set()), [])


class WiringTests(unittest.TestCase):
    def test_prompts_carry_the_guards_and_no_marker_is_left(self):
        for prompt in (review.JUDGE_PROMPT, review.FINDINGS_PROMPT, review.MINING_PROMPT):
            self.assertNotIn('@@', prompt)
            self.assertIn('未解决的失败', prompt)
        self.assertIn('in_place_patch', review.FINDINGS_PROMPT)
        self.assertIn('教训写法', review.FINDINGS_PROMPT)
        self.assertIn('merge_into', review.MINING_PROMPT)
        self.assertNotIn('"patch"', review.FINDINGS_PROMPT)
        self.assertEqual(review.VERSION, 'episodes-v2')

    def test_check_findings_replaces_legacy_patch_with_a_checked_proposal(self):
        eps = [{'id': 'a', 'session': 's', 'start_event': 1, 'end_event': 9}]
        value = {'findings': [{'title': 't', 'patch': {'after_line': 1, 'text': 'x'}, 'occurrences': [{'episode_id': 'a'}],
                               'proposal': {'action': 'in_place_patch', 'old_string': 'Gone.', 'new_string': 'New rule - because.'}}]}
        out = review.check_findings(value, {'a': {}}, eps, {'SKILL.md': BODY})
        finding = out['findings'][0]
        self.assertNotIn('patch', finding)
        self.assertEqual(codes(finding['proposal']), ['anchor_missing'])

    def test_check_judgment_applies_guards(self):
        e = {'id': 'a', 'start_event': 1, 'end_event': 9}
        out = review.check_judgment({'episode_id': 'a', 'outcome': 'fail', 'reusable_workflow': 'w', 'issues': []}, e)
        self.assertIsNone(out['reusable_workflow'])

    def test_check_mining_flags_candidates(self):
        out = review.check_mining({'candidates': [{'name': 'Bad Name', 'action': 'new'}], 'preferences': []}, set(), {'x'})
        self.assertIn('not_kebab', [f['code'] for f in out['candidates'][0]['flags']])


if __name__ == '__main__':
    unittest.main()
