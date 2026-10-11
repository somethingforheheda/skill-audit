import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import skill_audit_cli as cli
import agent_backends.codex as codex_backend

class SubscriptionProbeTests(unittest.TestCase):
    def test_chatgpt_auth_never_uses_gateway_or_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def execute(command,prompt,timeout,env,cwd,control):
                self.assertEqual(env['CODEX_HOME'],'/fixture/codex')
                for key in ('OPENAI_API_KEY','CODEX_API_KEY','SKILL_AUDIT_API_KEY','OPENAI_BASE_URL'):
                    self.assertNotIn(key,env)
                self.assertIn('model_provider="openai"',command)
                self.assertIn('forced_login_method="chatgpt"',command)
                self.assertNotIn('model_provider="skill_audit_gateway"',command)
                self.assertNotIn('--ephemeral',command)
                self.assertIn('--enable',command);self.assertIn('multi_agent',command)
                Path(command[command.index('-o')+1]).write_text('fixture')
                return SimpleNamespace(returncode=0,stdout='{"type":"fixture"}\n',stderr='')
            with patch.dict(os.environ,{'CODEX_HOME':'/fixture/codex','OPENAI_API_KEY':'fixture','CODEX_API_KEY':'fixture','SKILL_AUDIT_API_KEY':'fixture','OPENAI_BASE_URL':'https://invalid'}),patch.object(codex_backend.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='',stderr='Logged in using ChatGPT')),patch.object(codex_backend.codex_process,'execute',side_effect=execute):
                self.assertEqual(codex_backend.run_codex('fake',{},root,'test',10,multi_agent=True,auth_mode='chatgpt',trace_path=root/'events.jsonl'),'fixture')
    def test_reject_api_key_login_no_model_call(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(codex_backend.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='Logged in using an API key',stderr='')),patch.object(codex_backend.codex_process,'execute') as run:
            with self.assertRaises(cli.AuditError):
                codex_backend.run_codex('fake',{},Path(temp),'test',10,auth_mode='chatgpt')
            run.assert_not_called()
