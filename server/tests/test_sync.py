import argparse
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path

import test_web  # noqa: F401  (sets the environment and temp data dir)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
import browse  # noqa: E402
import config  # noqa: E402
import jobs  # noqa: E402
import runner  # noqa: E402
import store  # noqa: E402
import sync_client  # noqa: E402
import sync_scan  # noqa: E402


def make_home(base):
    home = Path(base) / 'home'
    skill = home / '.claude' / 'skills' / 'demo-skill'
    (skill / 'references').mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: demo-skill\n---\nbody\n', encoding='utf-8')
    (skill / 'references' / 'a.md').write_text('ref', encoding='utf-8')
    (skill / 'big.md').write_bytes(b'x' * (300 * 1024))
    claude = home / '.claude' / 'projects' / '-work-app'
    claude.mkdir(parents=True)
    rows = [{'type': 'user', 'cwd': '/work/app-client', 'message': {'role': 'user', 'content': 'hi'}},
            {'type': 'assistant', 'message': {'content': 'reading %s' % (skill / 'SKILL.md')}}]
    (claude / 's1.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')
    codex = home / '.codex' / 'sessions' / '2026' / '11' / '04'
    codex.mkdir(parents=True)
    meta = {'type': 'session_meta', 'payload': {'id': 'c1', 'cwd': '/work/other-repo'}}
    (codex / 'rollout-1.jsonl').write_text(json.dumps(meta) + '\n', encoding='utf-8')
    comate = home / '.comate-engine' / 'store'
    comate.mkdir(parents=True)
    (comate / 'chat_session_a').write_text(json.dumps({'workspaceDirectory': '/work/app-client', 'messages': []}), encoding='utf-8')
    (comate / 'chat_session_a.deleted').write_text('x', encoding='utf-8')
    return home


class SyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_dirs()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.server.daemon_threads = True
        cls.url = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        runner.start_worker()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.home = make_home(self.tmp.name)
        os.environ['SKILL_AUDIT_HOME'] = str(Path(self.tmp.name) / 'audit')
        self.addCleanup(self.tmp.cleanup)

    def args(self, **extra):
        base = dict(server=self.url, user='carol', days=None, history_home=str(self.home), yes=True, dry_run=False, quiet=True, hours=4)
        base.update(extra)
        return argparse.Namespace(**base)

    def test_scan_and_projects(self):
        found, skipped = sync_scan.scan(self.home)
        self.assertEqual(sorted(r['s'] for r in found), ['claude', 'codex', 'comate'])
        self.assertEqual(skipped, [])
        by = {r['s']: sync_scan.peek_project(r['s'], r['abs']) for r in found}
        self.assertEqual(by, {'claude': '/work/app-client', 'codex': '/work/other-repo', 'comate': '/work/app-client'})
        self.assertEqual(sync_scan.scan(self.home, days=0.0001, now=time.time() + 86400)[0], [])

    def test_init_validates_user_and_server(self):
        with self.assertRaises(sync_client.SyncError):
            sync_client.cmd_init(self.args(user='../x'))
        with self.assertRaises(sync_client.SyncError):
            sync_client.cmd_init(self.args(server='ftp://x'))
        self.assertEqual(sync_client.cmd_init(self.args()), 0)
        self.assertEqual(sync_client.load_config()['user'], 'carol')

    def test_run_uploads_once_then_only_changes(self):
        sync_client.cmd_init(self.args())
        self.assertEqual(sync_client.cmd_run(self.args()), 0)
        info = browse.summary('carol')
        self.assertEqual((info['files'], info['skills']), (3, 1))
        self.assertEqual(sorted(p['name'] for p in info['projects']), ['app-client', 'other-repo'])
        stored = store.user_dir('carol') / 'skills'
        names = sorted(p.relative_to(stored).as_posix().split('/', 1)[1] for p in stored.rglob('*') if p.is_file())
        self.assertEqual(names, ['SKILL.md', 'references/a.md'])
        found, _ = sync_scan.scan(self.home)
        client = sync_client.Client(self.url, 'carol')
        self.assertEqual(client.diff(found), [])
        target = self.home / '.claude' / 'projects' / '-work-app' / 's1.jsonl'
        target.write_text(target.read_text() + '{"type":"user"}\n', encoding='utf-8')
        found, _ = sync_scan.scan(self.home)
        self.assertEqual([r['s'] for r in client.diff(found)], ['claude'])
        sync_client.cmd_run(self.args())
        self.assertEqual(client.diff(sync_scan.scan(self.home)[0]), [])

    def test_server_refuses_bad_users_and_paths(self):
        for who in ('', '../x', 'a b'):
            with self.assertRaises(sync_client.SyncError):
                sync_client.Client(self.url, who).status()
        client = sync_client.Client(self.url, 'dave')
        row = {'s': 'claude', 'rel': '../evil.jsonl', 'size': 5, 'mt': 1}
        self.assertEqual(client.post('/api/sync/diff', {'files': [row]})['bad'], [0])
        os.makedirs(self.tmp.name, exist_ok=True)
        path = Path(self.tmp.name) / 'x.jsonl'
        path.write_text('12345')
        with self.assertRaises(sync_client.SyncError):
            client.upload({'s': 'claude', 'rel': '../evil.jsonl', 'size': 5, 'mt': 1, 'abs': str(path)})

    def test_analyze_from_store_hardlinks_and_passes_catalog(self):
        sync_client.cmd_init(self.args(user='erin', quiet=True))
        sync_client.cmd_run(self.args(user='erin'))
        meta = runner.create_from_store('erin', {'project': 'app-client', 'limit': 5})
        self.assertEqual(meta['files'], 2)
        self.assertTrue(meta['catalog'].endswith('skills'))
        for _ in range(100):
            row = jobs.get(meta['id'])
            if row['status'] in ('done', 'failed'):
                break
            time.sleep(0.2)
        self.assertEqual(row['status'], 'done', row)
        text = (runner.report_dir(meta['id']) / 'report.html').read_text(encoding='utf-8')
        self.assertIn('--skills-catalog', text)
        with self.assertRaises(jobs.JobError):
            runner.create_from_store('erin', {'project': 'nope'})
        self.assertEqual(store.state('erin')['bytes'] > 0, True)


if __name__ == '__main__':
    unittest.main()
