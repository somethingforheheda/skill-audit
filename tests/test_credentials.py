import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import skill_audit_cli as cli
import audit_config as cfg
import agent_backends.codex as codex_backend

CONFIG = {'base_url': 'https://gateway.example.com/', 'model': 'fixture', 'token_env': 'SKILL_AUDIT_FIXTURE_KEY'}
STORED = 'fixture-stored-token-value'


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'SKILL_AUDIT_HOME': str(self.home)})
        self.env.start()
        os.environ.pop(CONFIG['token_env'], None)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def store(self, value=STORED, mode=0o600):
        path = self.home / 'credentials.json'
        path.write_text(json.dumps({CONFIG['token_env']: value}))
        path.chmod(mode)
        return path

    def test_environment_wins_over_file(self):
        self.store()
        with patch.dict(os.environ, {CONFIG['token_env']: 'fixture-env-token'}):
            self.assertEqual(cfg.token_value(CONFIG), ('fixture-env-token', '环境变量'))

    def test_file_used_when_environment_missing(self):
        self.store()
        self.assertEqual(cfg.token_value(CONFIG), (STORED, '令牌文件'))
        cfg.token_present(CONFIG)

    def test_missing_and_placeholder_count_as_unset(self):
        with self.assertRaises(cfg.AuditError):
            cfg.token_present(CONFIG)
        path, created = cfg.ensure_credentials_placeholder(CONFIG['token_env'])
        self.assertTrue(created)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(cfg.token_value(CONFIG), (None, None))
        self.assertEqual(cfg.ensure_credentials_placeholder(CONFIG['token_env']), (path, False))

    @unittest.skipUnless(os.name == 'posix', 'permission bits require POSIX')
    def test_loose_permissions_rejected_but_redaction_survives(self):
        self.store(mode=0o644)
        with self.assertRaisesRegex(cfg.AuditError, 'chmod 600'):
            cfg.token_value(CONFIG)
        self.assertNotIn(STORED, cfg.known_secrets(CONFIG))

    def test_stored_token_reaches_codex_env_not_argv_and_is_redacted(self):
        self.store()
        seen = {}

        def execute(command, prompt, timeout, env, cwd, control):
            seen.update(command=command, env=env)
            Path(command[command.index('-o') + 1]).write_text('OK')
            return SimpleNamespace(returncode=0, stdout='trace ' + STORED, stderr='err ' + STORED)

        with tempfile.TemporaryDirectory() as work, patch.object(codex_backend.codex_process, 'execute', side_effect=execute):
            trace = Path(work) / 'events.jsonl'
            self.assertEqual(codex_backend.run_codex('fake', CONFIG, Path(work), 'prompt', 10, trace_path=trace), 'OK')
            self.assertEqual(seen['env'][CONFIG['token_env']], STORED)
            self.assertFalse(any(STORED in part for part in seen['command']))
            self.assertNotIn(STORED, trace.read_text())
            self.assertNotIn(STORED, trace.with_suffix('.stderr.txt').read_text())
        self.assertIn(STORED, cfg.known_secrets())

    def test_doctor_reports_source_without_value(self):
        self.store()
        (self.home / 'config.json').write_text(json.dumps(CONFIG))
        out = io.StringIO()
        with patch.object(codex_backend.shutil, 'which', return_value='fake-cli'), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['doctor']), 0)
        self.assertIn('来源：令牌文件', out.getvalue())
        self.assertNotIn(STORED, out.getvalue())



class GatewayConfigTests(unittest.TestCase):
    """The gateway is whatever the user configures; nothing is built in."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        env = patch.dict(os.environ, {'SKILL_AUDIT_HOME': str(self.home)}); env.start(); self.addCleanup(env.stop)

    def init(self, *extra):
        with patch.object(codex_backend.shutil, 'which', return_value='fake-cli'), \
                patch.object(cli, 'ensure_yaml_runtime', return_value=('python', False)), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli.main(['init', '--model', 'm', *extra])
        return code, out.getvalue(), cfg.load_optional()

    def test_base_url_rules(self):
        self.assertEqual(cfg.provider_base_url('https://llm.example.org:8443/api/'), 'https://llm.example.org:8443/api/v1')
        self.assertEqual(cfg.provider_base_url('http://localhost:4000'), 'http://localhost:4000/v1')
        for bad in ('http://llm.example.org', 'https://u:p@llm.example.org', 'https://llm.example.org/?a=1', 'ftp://x'):
            with self.assertRaises(cfg.AuditError):
                cfg.provider_base_url(bad)

    def test_legacy_placeholder_counts_as_unset(self):
        path = self.home / 'credentials.json'
        path.write_text(json.dumps({'K': '<在此替换为 某旧网关 key>'})); path.chmod(0o600)
        self.assertEqual(cfg.read_credentials(), {})

    def test_codex_init_without_gateway_uses_codex_login(self):
        code, out, data = self.init()
        self.assertEqual(code, 0)
        self.assertNotIn('base_url', data)
        self.assertEqual(data['backends']['codex']['model'], 'm')
        self.assertIn('复用 codex login', out); self.assertIn('未限制', out)
        self.assertFalse((self.home / 'credentials.json').exists())
        plan = codex_backend.CodexBackend().plan(cli.agent_backends.Selection(), ())
        self.assertEqual(plan.auth, 'native')

    def test_codex_init_gateway_kept_and_trusted_list(self):
        code, out, data = self.init('--base-url', 'https://llm.example.org/', '--token-env', 'MY_KEY')
        self.assertEqual((data['base_url'], data['token_env']), ('https://llm.example.org/', 'MY_KEY'))
        self.assertTrue((self.home / 'credentials.json').exists())
        # Re-running init without --base-url keeps the saved gateway and token variable.
        code, out, data = self.init('--trusted-gateway', 'https://LLM.example.org/x')
        self.assertEqual((data['base_url'], data['token_env'], data['trusted_gateways']),
                         ('https://llm.example.org/', 'MY_KEY', ['llm.example.org']))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, out, data = self.init('--base-url', 'https://other.example.net/')
        self.assertEqual(code, 2); self.assertIn('不在受信任网关列表', err.getvalue())
        self.assertEqual(data['base_url'], 'https://llm.example.org/')
        code, out, data = self.init('--clear-trusted-gateway', '--base-url', 'https://other.example.net/')
        self.assertEqual(code, 0); self.assertNotIn('trusted_gateways', data)


if __name__ == '__main__':
    unittest.main()
