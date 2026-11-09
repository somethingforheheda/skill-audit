"""The CLI prompt must reach a child that starts slowly, whatever the Python version."""
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_process

READ_ALL = 'import sys,time; time.sleep(0.8); sys.stdout.write(str(len(sys.stdin.read())))'


class PromptDeliveryTests(unittest.TestCase):
    def test_a_prompt_bigger_than_a_pipe_reaches_a_child_that_starts_slowly(self):
        prompt = 'x' * 300000 + '\n'
        out = codex_process.execute([sys.executable, '-c', READ_ALL], prompt, 30, dict(os.environ), '.')
        self.assertEqual((out.returncode, out.stdout), (0, str(len(prompt.encode('utf-8')))))

    def test_non_ascii_prompt_survives(self):
        prompt = '中文提示 ' * 50000
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        out = codex_process.execute([sys.executable, '-c', READ_ALL], prompt, 30, env, '.')
        self.assertEqual(out.stdout, str(len(prompt)))

    def test_a_child_that_never_finishes_is_timed_out(self):
        with self.assertRaises(codex_process.TimedOut):
            codex_process.execute([sys.executable, '-c', 'import time; time.sleep(30)'], 'x', 1, dict(os.environ), '.')


if __name__ == '__main__':
    unittest.main()
