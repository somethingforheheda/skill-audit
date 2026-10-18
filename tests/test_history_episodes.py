import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_episodes as ep

PATH = '/history/rollout.jsonl'


def event(i, value):
    return {'event_id': f'e{i}', 'timestamp': f'2026-10-01T00:{i:02d}:00+00:00', 'path': PATH,
            'location': f'line:{i}', 'text': json.dumps(value, ensure_ascii=False)}


def user(text):
    return {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}


def assistant(text):
    return {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}


def call(call_id, cmd):
    return {'type': 'custom_tool_call', 'call_id': call_id, 'name': 'exec',
            'input': 'const r = await tools.exec_command({cmd:' + json.dumps(cmd) + '})'}


def output(call_id, text, code=0):
    return {'type': 'custom_tool_call_output', 'call_id': call_id,
            'output': [{'type': 'input_text', 'text': 'Script completed\n'},
                       {'type': 'input_text', 'text': json.dumps({'exit_code': code, 'output': text})}]}


def read(index, name='repo-workflow', status='confirmed', result=None):
    return {'read_id': f'r{index}', 'skill': f'/skills/{name}/SKILL.md', 'name': name, 'status': status,
            'original_file': PATH, 'request_location': f'line:{index}',
            'result_locations': [{'file': PATH, 'location': f'line:{result if result is not None else index + 1}'}]}


class EpisodeTests(unittest.TestCase):
    def session(self):
        rows = [
            {'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': 'secret rules'}]},
            user('# AGENTS.md instructions for /repo\n...'),
            user('帮我把两个仓提交并 push'),
            call('c1', 'cat /skills/repo-workflow/SKILL.md'),
            output('c1', '---\nname: repo-workflow\n---\nbody'),
            call('c2', 'git push'),
            output('c2', 'rejected', code=1),
            assistant('已 push'),
            {'type': 'task_complete'},
            user('不对 你要把所有的操作都告诉我 而不是只告诉我push'),
            assistant('好的'),
            {'type': 'turn_aborted', 'reason': 'interrupted'},
            user('可以 开始吧'),
        ]
        return [event(i, v) for i, v in enumerate(rows)]

    def test_turns_classify_and_hide_injected_context(self):
        items = ep.flatten(self.session())
        kinds = [x['kind'] for x in items]
        self.assertEqual(kinds[:3], ['system', 'system', 'user'])
        self.assertNotIn('secret rules', json.dumps(items, ensure_ascii=False))
        calls = [x for x in items if x['kind'] == 'call']
        self.assertEqual(calls[0]['text'], 'cat /skills/repo-workflow/SKILL.md')
        failed = [x['event'] for x in items if x['kind'] == 'result' and x['failed']]
        self.assertEqual(failed, [6])

    def test_episode_signals(self):
        episodes, _ = ep.build('codex:s', self.session(), [read(3)])
        self.assertEqual(len(episodes), 1)
        e = episodes[0]
        self.assertEqual((e['start_event'], e['read_events'], e['result_events']), (2, [3], [4]))
        self.assertEqual(e['request'], '帮我把两个仓提交并 push')
        s = e['signals']
        self.assertEqual((s['followups'], s['interrupts'], s['failed_tools']), (2, 1, 1))
        self.assertEqual([c['event'] for c in s['corrections']], [9])
        self.assertGreater(s['score'], 3)

    def test_reads_in_same_turn_share_an_episode(self):
        events = self.session()
        episodes, _ = ep.build('codex:s', events, [read(3), read(5, 'release-push', result=6)])
        self.assertEqual(len(episodes), 1)
        self.assertEqual(sorted(k['name'] for k in episodes[0]['skills']), ['release-push', 'repo-workflow'])

    def test_next_trigger_closes_previous_episode(self):
        events = self.session()
        events += [event(13, call('c3', 'cat /skills/app-build/SKILL.md')), event(14, output('c3', 'body'))]
        episodes, _ = ep.build('codex:s', events, [read(3), read(13, 'app-build')])
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]['end_event'], 11)
        self.assertEqual(episodes[1]['start_event'], 12)

    def test_claude_blocks_and_interrupt_marker(self):
        rows = [
            {'role': 'user', 'content': 'review 这段代码'},
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Skill', 'input': {'skill': 'code-review'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'Launching skill: code-review'}]},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'Base directory for this skill: /skills/code-review\n\nbody'}]},
            {'role': 'user', 'content': [{'type': 'text', 'text': '[Request interrupted by user]'}]},
            {'role': 'user', 'content': '我没让你直接改 先给我方案'},
        ]
        items = ep.flatten([event(i, v) for i, v in enumerate(rows)])
        self.assertEqual([x['kind'] for x in items], ['user', 'call', 'result', 'skill', 'marker', 'user'])
        episodes, _ = ep.build('claude:s', [event(i, v) for i, v in enumerate(rows)], [read(1, 'code-review', result=3)])
        s = episodes[0]['signals']
        self.assertEqual((s['interrupts'], len(s['corrections'])), (1, 1))

    def test_render_hides_skill_body_and_numbers_events(self):
        episodes, items = ep.build('codex:s', self.session(), [read(3)])
        text = ep.render(items, 0, 12, episodes[0]['result_events'])
        self.assertIn('#2 用户：帮我把两个仓提交并 push', text)
        self.assertIn('#4 工具返回：（Skill 正文，略）', text)
        self.assertIn('#6 工具返回（失败）', text)
        self.assertNotIn('secret rules', text)
        self.assertNotIn('AGENTS.md', text)


if __name__ == '__main__':
    unittest.main()
