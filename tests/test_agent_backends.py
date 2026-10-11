"""Backend abstraction contract: callers never change when a backend is added."""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_backends
import agent_backends.claude_code as claude
import agent_backends.codex as codex_backend
import history_analysis as a
import skill_audit_cli as cli
from agent_backends.base import (AgentResult, AgentTask, AuditError, AuthMode, Backend, Need, Selection,
                                 check_models, negotiate)

GATEWAY = 'https://gateway.example.com'


class FakeBackend(Backend):
    """A third backend registered only in tests; supports no delegation at all."""
    name = 'fake-cli'
    aliases = ('fake',)
    label = 'Fake CLI'
    guarantees = {k: 'fixture' for k in ('read_only', 'no_write_tools', 'isolated_extensions',
                                          'timeout_cancel', 'model_select', 'redaction')}
    capabilities = {'command_allowlist': 'fixture', 'trace': 'fixture'}
    auth_modes = {'native': AuthMode('fixture')}
    default_auth = 'native'
    calls = []
    reply = None

    def locate(self):
        return 'fake'

    def ready(self, plan):
        return self.resolve_models(plan, {})

    def run(self, task, plan):
        FakeBackend.calls.append((task, plan))
        return AgentResult(FakeBackend.reply(task), plan, [plan.model] if plan.model else None)

    def ok(self):
        return True


def setUpModule():
    agent_backends.get()   # discover built-in adapters first
    if 'fake-cli' not in agent_backends.names():
        agent_backends.register(FakeBackend)


class RegistryTests(unittest.TestCase):
    def test_builtins_discovered_and_aliases(self):
        self.assertEqual(set(agent_backends.names()) >= {'codex', 'claude-code', 'fake-cli'}, True)
        self.assertEqual(agent_backends.get('claude').name, 'claude-code')
        self.assertEqual(agent_backends.get().name, 'codex')
        with self.assertRaises(AuditError):
            agent_backends.get('nope')

    def test_incomplete_backend_refused(self):
        class Broken(FakeBackend):
            name = 'broken'
            guarantees = {'read_only': 'x'}
        with self.assertRaises(RuntimeError):
            agent_backends.register(Broken)
        self.assertNotIn('broken', agent_backends.names())

    def test_parser_choices_follow_registry(self):
        args = cli.build_parser().parse_args(['review', 'x', '--backend', 'fake'])
        self.assertEqual(args.backend, 'fake')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(['review', 'x', '--subagent-model', 'm'])


class NegotiationTests(unittest.TestCase):
    def test_levels(self):
        fake = FakeBackend()
        granted, degraded = negotiate(fake, (Need('delegation', 'preferred'), Need('trace', 'required')), 'native')
        self.assertEqual((granted, degraded), (frozenset({'trace'}), ('delegation',)))
        with self.assertRaisesRegex(AuditError, '不支持'):
            negotiate(fake, (Need('delegation', 'required'),), 'native')
        # A backend without delegation trivially satisfies "forbidden".
        self.assertEqual(negotiate(fake, (Need('delegation', 'forbidden'),), 'native'), (frozenset(), ()))

    def test_forbidden_needs_off_switch(self):
        class AlwaysDelegates(FakeBackend):
            capabilities = {'delegation': 'always on'}
        with self.assertRaisesRegex(AuditError, '无法关闭'):
            negotiate(AlwaysDelegates(), (Need('delegation', 'forbidden'),), 'native')

    def test_auth_declared_per_backend(self):
        with self.assertRaisesRegex(AuditError, '不支持认证方式 gateway'):
            agent_backends.get('claude-code').plan(Selection(auth='gateway'), ())
        codex = agent_backends.get('codex')
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {'SKILL_AUDIT_HOME': home}):
            self.assertEqual(codex.plan(Selection(auth='chatgpt'), ()).auth, 'native')
            self.assertEqual(codex.plan(Selection(auth='company'), ()).auth, 'gateway')
            # No gateway configured: reuse codex login; a saved gateway becomes the default.
            self.assertEqual(codex.plan(Selection(), ()).auth, 'native')
            Path(home, 'config.json').write_text(json.dumps({'base_url': GATEWAY, 'model': 'm', 'token_env': 'K'}))
            self.assertEqual(codex.plan(Selection(), ()).auth, 'gateway')

    def test_subagent_model_requires_capability(self):
        with self.assertRaisesRegex(AuditError, '不支持「可固定子代理模型」'):
            FakeBackend().plan(Selection(subagent_model='m'), ())

    def test_model_resolution_order(self):
        backend = agent_backends.get('claude-code')
        config = {'model': 'codex-top', 'backends': {'claude-code': {'model': 'cfg-main', 'subagent_model': 'cfg-sub'}}}
        delegate = (Need('delegation', 'preferred'),)
        plan = backend.resolve_models(backend.plan(Selection(), delegate), config)
        self.assertEqual((plan.model, plan.subagent_model), ('cfg-main', 'cfg-sub'))
        plan = backend.resolve_models(backend.plan(Selection(model='cli-main'), delegate), {})
        self.assertEqual((plan.model, plan.subagent_model), ('cli-main', 'cli-main'))
        plan = backend.resolve_models(backend.plan(Selection(model='m', subagent_model='s'), (Need('delegation', 'forbidden'),)), config)
        self.assertEqual((plan.model, plan.subagent_model), ('m', None))
        codex = agent_backends.get('codex')
        plan = codex.resolve_models(codex.plan(Selection(auth='gateway'), delegate), config)
        self.assertEqual((plan.model, plan.subagent_model), ('codex-top', 'codex-top'))

    def test_unexpected_models_reported(self):
        plan = FakeBackend().plan(Selection(model='Opus 4.8[1M]'), ())
        plan.model = 'Opus 4.8[1M]'
        self.assertEqual(check_models(AgentResult('', plan, ['Opus 4.8[1m]'])), [])
        self.assertEqual(check_models(AgentResult('', plan, ['Opus 4.8[1m]', 'Other'])), ['Other'])
        self.assertEqual(check_models(AgentResult('', plan, None)), [])


class CallerContractTests(unittest.TestCase):
    """Review and history run on a new backend without touching caller code."""
    def setUp(self):
        FakeBackend.calls.clear()
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_history_degrades_without_delegation(self):
        skill = self.root / 'skills/example/SKILL.md'
        skill.parent.mkdir(parents=True); skill.write_text('Example skill description.')
        report = {'scope': {'skills_roots': [str(self.root / 'skills')], 'project': str(self.root)},
                  'sessions': [], 'extraction': {'reads': [], 'status': 'complete', 'summary': {}, 'uncovered': []}}
        path = self.root / 'report.json'; path.write_text(json.dumps(report))
        FakeBackend.reply = lambda task: json.dumps({'status': 'complete', 'overview': 'fixture', 'uncovered': [], 'findings': []})
        args = SimpleNamespace(report=path, resume=None, rules_file=None, max_evidence_chars=120000, prepare_only=False,
                               attempts=1, timeout=30, backend='fake', model='fake-model', auth=None,
                               subagent_model=None, allow_any_gateway=False)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(a.run(args, cli), 0)
        task, plan = FakeBackend.calls[0]
        self.assertIn('不支持原生子代理', task.prompt)
        self.assertNotIn('允许自主创建子代理', task.prompt)
        self.assertEqual(len(task.commands), 1); self.assertIn('analysis-read', task.commands[0])
        self.assertEqual(task.trace_path.name, 'events-1.jsonl')
        self.assertIn('退化为单 Agent', err.getvalue())
        artifact = json.loads((self.root / 'analysis.json').read_text())
        self.assertEqual(artifact['coverage']['degraded'], ['delegation'])
        self.assertEqual(artifact['backend']['backend'], 'fake-cli')
        self.assertEqual(artifact['model'], 'fake-model')
        manifest = json.loads(next((self.root / 'analysis-runs').iterdir()).joinpath('manifest.json').read_text())
        self.assertEqual(manifest['selection']['backend'], 'fake')

    def test_review_uses_selected_backend(self):
        (self.root / 'demo').mkdir(); (self.root / 'demo/SKILL.md').write_text('---\nname: demo\n---\n')
        seen = []
        with patch.object(cli, 'validate_generated', side_effect=lambda kind, raw, *rest: seen.append(raw) or
                          {'SKILL_REVIEW_ISSUES': 0, 'issues': [], 'status': 'complete'}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            FakeBackend.reply = lambda task: 'FAKE-REPORT'
            code = cli.main(['review', str(self.root / 'demo'), '--repo-root', str(self.root), '--backend', 'fake', '--model', 'mm'])
        self.assertEqual(code, 0); self.assertEqual(seen, ['FAKE-REPORT'])
        task, plan = FakeBackend.calls[0]
        self.assertEqual((plan.model, task.commands, plan.granted), ('mm', (), frozenset()))


class CodexAdapterTests(unittest.TestCase):
    def test_subagent_model_only_with_multi_agent(self):
        commands = []

        def execute(command, prompt, timeout, env, cwd, control):
            commands.append(command); Path(command[command.index('-o') + 1]).write_text('OK')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        data = {'base_url': GATEWAY, 'token_env': 'SA_FIXTURE_KEY', 'model': 'main', 'subagent_model': 'child'}
        with patch.dict(os.environ, {'SA_FIXTURE_KEY': 'fixture-key'}), \
                patch.object(codex_backend.codex_process, 'execute', side_effect=execute):
            codex_backend.run_codex('fake', data, Path('/tmp'), 'p', 10, multi_agent=True)
            codex_backend.run_codex('fake', data, Path('/tmp'), 'p', 10)
        self.assertIn('agents.default_subagent_model="child"', commands[0])
        self.assertFalse(any('default_subagent_model' in part for part in commands[1]))
        self.assertEqual(commands[0][commands[0].index('-m') + 1], 'main')

    def test_rollout_models_read_from_private_home(self):
        with tempfile.TemporaryDirectory() as home:
            day = Path(home) / 'sessions/2026/10/08'; day.mkdir(parents=True)
            (day / 'rollout-a.jsonl').write_text(json.dumps({'type': 'turn_context', 'payload': {'model': 'main'}}) + '\n')
            (day / 'rollout-b.jsonl').write_text('{bad\n' + json.dumps({'type': 'turn_context', 'payload': {'model': 'child'}}))
            self.assertEqual(codex_backend.rollout_models(home), ['main', 'child'])


class ClaudeAdapterTests(unittest.TestCase):
    def plan(self, needs=(), **selection):
        backend = agent_backends.get('claude-code')
        plan = backend.resolve_models(backend.plan(Selection(**selection), needs), {})
        plan.context = {'binary': 'claude', 'env': {}}
        return plan

    def test_command_read_only_without_delegation(self):
        plan = self.plan((Need('delegation', 'forbidden'),), model='M')
        command = claude.build_command('claude', AgentTask('p', Path('/repo'), 10), plan)
        tools = command[command.index('--tools') + 1].split(',')
        self.assertEqual(tools, ['Read', 'Grep', 'Glob'])
        for flag in ('--restricted', '--safe-mode', '--strict-mcp-config', '--no-session-persistence', '--disable-slash-commands'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(command[command.index('--model') + 1], 'M')
        settings = json.loads(command[command.index('--settings') + 1])
        self.assertEqual(settings, {'disableAllHooks': True})
        self.assertFalse({'Edit', 'Write', 'Bash', 'Agent'} & set(tools))

    def test_command_delegation_and_helper_allowlist(self):
        plan = self.plan((Need('delegation', 'preferred'),), model='M', subagent_model='S')
        task = AgentTask('p', Path('/state'), 10, commands=("/py '/中文 路径/cli.py' analysis-read /m.json",))
        command = claude.build_command('claude', task, plan)
        self.assertEqual(command[command.index('--tools') + 1], 'Read,Grep,Glob,Agent,Bash')
        self.assertIn("Bash(/py '/中文 路径/cli.py' analysis-read /m.json *)", command)
        self.assertNotIn('Bash', command[command.index('--allowedTools') + 1:command.index('--settings')])
        self.assertEqual(json.loads(command[command.index('--settings') + 1])['env'], {'CLAUDE_CODE_SUBAGENT_MODEL': 'S'})

    def test_child_env_forwards_gateway_and_strips_nesting(self):
        with tempfile.TemporaryDirectory() as home:
            Path(home, 'settings.json').write_text(json.dumps({'env': {'ANTHROPIC_BASE_URL': GATEWAY, 'ANTHROPIC_AUTH_TOKEN': 'fixture-token-value',
                                                                        'CLAUDE_CODE_SUBAGENT_MODEL': 'Expensive'}}))
            with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': home, 'CLAUDECODE': '1', 'CLAUDE_CODE_SUBAGENT_MODEL': 'Expensive',
                                         'OPENAI_API_KEY': 'x', 'MY_GATEWAY_KEY': 'y'}):
                env = claude.child_env('Cheap', {'token_env': 'MY_GATEWAY_KEY'})
        self.assertEqual((env['ANTHROPIC_BASE_URL'], env['CLAUDE_CODE_SUBAGENT_MODEL']), (GATEWAY, 'Cheap'))
        for key in ('CLAUDECODE', 'OPENAI_API_KEY', 'MY_GATEWAY_KEY'):
            self.assertNotIn(key, env)

    def test_gateway_unrestricted_by_default(self):
        backend = agent_backends.get('claude-code')
        with tempfile.TemporaryDirectory() as home, patch.object(claude.shutil, 'which', return_value='claude'), \
                patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': home, 'SKILL_AUDIT_HOME': home}):
            os.environ.pop('ANTHROPIC_BASE_URL', None)
            plan = backend.ready(backend.plan(Selection(), ()))
            self.assertEqual(plan.notes, [])
            self.assertTrue(backend.ok())
            with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': 'https://any.example.org'}):
                backend.ready(backend.plan(Selection(), ()))
            self.assertIn('未限制', '\n'.join(backend.status()))

    def test_trusted_gateway_enforced_unless_allowed(self):
        backend = agent_backends.get('claude-code')
        with tempfile.TemporaryDirectory() as home, patch.object(claude.shutil, 'which', return_value='claude'), \
                patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': home, 'SKILL_AUDIT_HOME': home}):
            Path(home, 'config.json').write_text(json.dumps({'trusted_gateways': ['gateway.example.com']}))
            os.environ.pop('ANTHROPIC_BASE_URL', None)
            with self.assertRaisesRegex(AuditError, 'allow-any-gateway'):
                backend.ready(backend.plan(Selection(), ()))
            plan = backend.ready(backend.plan(Selection(allow_any_gateway=True), ()))
            self.assertIn('已显式允许受信任列表外的网关', plan.notes[0])
            with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': 'https://evil.example/gateway.example.com'}):
                self.assertFalse(backend.ok())
            with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': 'http://gateway.example.com'}):
                self.assertFalse(backend.ok())
            with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': GATEWAY + '/'}):
                backend.ready(backend.plan(Selection(), ()))
                self.assertTrue(backend.ok())

    def test_old_allow_flag_still_parsed(self):
        args = cli.build_parser().parse_args(['review', 'x', '--allow-non-company'])
        self.assertTrue(args.allow_any_gateway)
        self.assertTrue(agent_backends.selection(SimpleNamespace(), allow_non_company=True).allow_any_gateway)

    def test_result_parsing(self):
        self.assertEqual(claude.strip_fence('```json\n{"a":1}\n```'), '{"a":1}')
        self.assertEqual(claude.strip_fence('text ```x``` more'), 'text ```x``` more')
        self.assertEqual(claude.final_payload('Here is the result.\n\n{"a": 1}'), '{"a": 1}')
        self.assertEqual(claude.final_payload('Done.\n```json\n{"a": 1}\n```'), '{"a": 1}')
        self.assertEqual(claude.final_payload('Here it is.\n\n{"a": {"b": [1]}}\n\nNote: subagents were denied Bash.'),
                         '{"a": {"b": [1]}}')
        self.assertEqual(claude.final_payload('{"a": 1}\ntrailing note'), '{"a": 1}')
        self.assertEqual(claude.final_payload('OK'), 'OK')
        self.assertEqual(claude.final_payload('note\n{not json'), 'note\n{not json')


FAKE_CLAUDE = '''#!{python}
import json, os, signal, subprocess, sys, time
from pathlib import Path
prompt = sys.stdin.read()
mode = os.environ['FIXTURE_MODE']
Path(os.environ['FIXTURE_ARGS']).write_text(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'prompt': prompt,
    'subagent_env': os.environ.get('CLAUDE_CODE_SUBAGENT_MODEL'), 'nested': os.environ.get('CLAUDECODE')}}))
emit = lambda row: print(json.dumps(row), flush=True)
emit({{'type': 'system', 'subtype': 'init', 'model': 'Fixture'}})
if mode == 'hang':
    child = "import signal,time,os\\nfrom pathlib import Path\\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\\nwhile True:\\n Path(os.environ['FIXTURE_BEAT']).write_text(str(time.time())); time.sleep(.03)"
    subprocess.Popen([sys.executable, '-c', child])
    while True: time.sleep(1)
if mode == 'error':
    emit({{'type': 'result', 'subtype': 'error_max_turns', 'is_error': True, 'result': 'token fixture-secret-token leaked'}})
    sys.exit(1)
print('fixture-secret-token in stderr', file=sys.stderr)
emit({{'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '```json\\n{{"ok": true}}\\n```',
      'modelUsage': {{'Opus 4.8': {{}}}}, 'subagent_stats': {{'spawned': 2}}, 'permission_denials': [], 'total_cost_usd': 0.01}})
'''


@unittest.skipUnless(os.name == 'posix', 'fake CLI and process groups require POSIX')
class ClaudeProcessTests(unittest.TestCase):
    """Real subprocess runs against a fake `claude` executable; no network or credentials."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'bin').mkdir()
        fake = self.root / 'bin/claude'; fake.write_text(FAKE_CLAUDE.format(python=sys.executable)); fake.chmod(0o755)
        (self.root / 'claude-home').mkdir()
        (self.root / 'claude-home/settings.json').write_text(json.dumps({'env': {'ANTHROPIC_BASE_URL': GATEWAY, 'ANTHROPIC_AUTH_TOKEN': 'fixture-secret-token'}}))
        self.env = patch.dict(os.environ, {'PATH': str(self.root / 'bin') + os.pathsep + os.environ['PATH'],
                                           'CLAUDE_CONFIG_DIR': str(self.root / 'claude-home'),
                                           'SKILL_AUDIT_HOME': str(self.root / 'audit'), 'CLAUDECODE': '1',
                                           'FIXTURE_ARGS': str(self.root / 'args.json'), 'FIXTURE_BEAT': str(self.root / 'beat')})
        self.env.start(); self.addCleanup(self.env.stop)
        os.environ.pop('ANTHROPIC_BASE_URL', None)

    def run_backend(self, mode, needs=(), timeout=10, **selection):
        os.environ['FIXTURE_MODE'] = mode
        backend = agent_backends.get('claude-code')
        plan = backend.ready(backend.plan(Selection(**selection), needs))
        return backend.run(AgentTask('PROMPT-TEXT', self.root, timeout, trace_path=self.root / 'events.jsonl'), plan)

    def test_success_parses_result_and_redacts_trace(self):
        result = self.run_backend('ok', (Need('delegation', 'preferred'),), model='Opus 4.8', subagent_model='Opus 4.8')
        self.assertEqual(result.text, '{"ok": true}')
        self.assertEqual((result.models, result.facts['delegated']), (['Opus 4.8'], 2))
        seen = json.loads((self.root / 'args.json').read_text())
        self.assertEqual((seen['prompt'], seen['subagent_env'], seen['nested']), ('PROMPT-TEXT', 'Opus 4.8', None))
        self.assertFalse(any('fixture-secret-token' in part for part in seen['argv']))
        self.assertNotIn('fixture-secret-token', (self.root / 'events.jsonl').read_text())
        self.assertNotIn('fixture-secret-token', (self.root / 'events.stderr.txt').read_text())

    def test_error_result_raises_redacted(self):
        with self.assertRaises(AuditError) as caught:
            self.run_backend('error')
        self.assertIn('error_max_turns', str(caught.exception))
        self.assertNotIn('fixture-secret-token', str(caught.exception))

    def test_timeout_reaps_descendants(self):
        started = time.monotonic()
        with self.assertRaises(cli.AuditTimeout) as caught:
            self.run_backend('hang', timeout=1.5)
        self.assertIn('Claude Code 超时', str(caught.exception))
        self.assertLess(time.monotonic() - started, 6)
        beat = self.root / 'beat'
        if beat.exists():
            old = beat.read_text(); time.sleep(.3)
            self.assertEqual(beat.read_text(), old, 'grandchild survived timeout')

    def test_doctor_probe_through_cli(self):
        os.environ['FIXTURE_MODE'] = 'ok'
        with patch.object(claude, 'final_payload', return_value='OK'), contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['doctor', '--probe', '--backend', 'claude', '--model', 'Opus 4.8'])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn('Claude Code CLI 调用：成功（实际模型：Opus 4.8）', out.getvalue())
        self.assertNotIn('fixture-secret-token', out.getvalue())


if __name__ == '__main__':
    unittest.main()
