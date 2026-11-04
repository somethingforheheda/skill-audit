import argparse
import hashlib
import io
import json
import os
import sys
import threading
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

import test_web  # noqa: F401  (sets the environment and temp data dir)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
import browse  # noqa: E402
import config  # noqa: E402
import jobs  # noqa: E402
import runner  # noqa: E402
import self_update  # noqa: E402
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


class SyncBase(unittest.TestCase):
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


class SyncTests(SyncBase):
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


def fake_pyz(version):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr('__main__.py', 'import sys\nprint("skill-audit %s")\n' % version)
        archive.writestr('skill_audit_cli.py', 'APP_VERSION = "%s"\n' % version)
    return buf.getvalue()


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_dirs()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.server.daemon_threads = True
        cls.url = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        import tempfile
        from unittest import mock
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ['SKILL_AUDIT_HOME'] = str(Path(self.tmp.name) / 'audit')
        os.environ.pop(sync_client.REEXEC_ENV, None)
        self.addCleanup(os.environ.pop, sync_client.REEXEC_ENV, None)
        self.served = Path(self.tmp.name) / 'served.pyz'
        self.installed = Path(self.tmp.name) / 'skill-audit'
        self.installed.write_bytes(fake_pyz('0.0.1'))
        patcher = mock.patch.object(config, 'PYZ', str(self.served))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(self_update, 'installed_target', lambda: self.installed)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = sync_client.Client(self.url, 'frank')
        self.args = argparse.Namespace(no_update=False, quiet=True)

    def test_server_reports_and_serves_the_configured_build(self):
        with self.assertRaises(sync_client.SyncError):
            self.client.request('GET', '/api/sync/version')
        data = fake_pyz('9.9.9')
        self.served.write_bytes(data)
        info = self.client.request('GET', '/api/sync/version')
        self.assertEqual(info, {'version': '9.9.9', 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
        self.assertEqual(self.client.fetch('/download/skill-audit.pyz'), data)

    def test_newer_server_build_replaces_the_install_and_restarts(self):
        data = fake_pyz('9.9.9')
        self.served.write_bytes(data)
        calls = []
        self.assertTrue(sync_client.auto_update(self.args, self.client, {}, restart=calls.append))
        self.assertEqual(self.installed.read_bytes(), data)
        self.assertEqual(calls, [self.installed])
        self.assertEqual(os.environ.get(sync_client.REEXEC_ENV), '1')
        self.assertFalse(sync_client.auto_update(self.args, self.client, {}, restart=calls.append))
        self.assertEqual(len(calls), 1)

    def test_no_update_when_not_newer_or_switched_off(self):
        self.served.write_bytes(fake_pyz('0.0.1'))
        self.assertFalse(sync_client.auto_update(self.args, self.client, {}, restart=lambda t: None))
        self.served.write_bytes(fake_pyz('9.9.9'))
        for args, cfg in ((argparse.Namespace(no_update=True, quiet=True), {}), (self.args, {'auto_update': False})):
            self.assertFalse(sync_client.auto_update(args, self.client, cfg, restart=lambda t: None))
        self.assertEqual(self.installed.read_bytes(), fake_pyz('0.0.1'))

    def test_wrong_checksum_or_broken_download_leaves_the_install_alone(self):
        before = self.installed.read_bytes()
        good = fake_pyz('9.9.9')
        self.served.write_bytes(good)
        bad = dict(self.client.request('GET', '/api/sync/version'), sha256='0' * 64)

        class Lying(sync_client.Client):
            def request(self, method, path, *a, **k):
                return bad if path == '/api/sync/version' else super().request(method, path, *a, **k)

        lying = Lying(self.url, 'frank')
        self.assertFalse(sync_client.auto_update(self.args, lying, {}, restart=lambda t: self.fail('restarted')))
        self.assertEqual(self.installed.read_bytes(), before)
        self.served.write_bytes(b'not a zip')
        self.assertFalse(sync_client.auto_update(self.args, self.client, {}, restart=lambda t: self.fail('restarted')))
        self.assertEqual(self.installed.read_bytes(), before)
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir() if p.name.startswith('.skill-audit-update-')], [])


class TtyBuffer(io.StringIO):
    def isatty(self):
        return True


class ProgressTests(unittest.TestCase):
    def test_bar_on_a_terminal_shows_percent_and_bytes(self):
        out = TtyBuffer()
        bar = sync_client.Progress('上传', 4 * 1048576, size=True, out=out)
        bar.update(1048576, 'a.jsonl')
        bar.last = 0
        bar.update(4 * 1048576)
        bar.finish()
        text = out.getvalue()
        self.assertIn('\r', text)
        self.assertIn('100%', text)
        self.assertIn('4.0 MB/4.0 MB', text)
        self.assertIn('█', text)
        self.assertTrue(text.endswith('\n'))

    def test_bar_on_a_pipe_prints_sparse_plain_lines(self):
        out = io.StringIO()
        bar = sync_client.Progress('读取', 10, out=out)
        for n in range(1, 11):
            bar.update(n)
        self.assertEqual(len(out.getvalue().strip().splitlines()), 1)
        self.assertIn('100%', out.getvalue())
        quiet = io.StringIO()
        sync_client.Progress('x', 1, enabled=False, out=quiet).update(1)
        self.assertEqual(quiet.getvalue(), '')

    def test_feed_counts_sent_bytes_and_rewinds_on_retry(self):
        seen = []
        feed = sync_client.Feed(b'x' * 100, seen.append)
        feed.read(60)
        feed.read(60)
        feed.seek(0)
        self.assertEqual(sum(seen), 0)
        feed.read(100)
        self.assertEqual(sum(seen), 100)

    def test_walk_back_finds_skill_paths_in_linear_time(self):
        noise = '/' + 'ab/' * 2000000
        started = time.time()
        found = sync_scan.skill_paths_in(noise + ' "/tmp/x/SKILL.md" ~/y/SKILL.md')
        self.assertLess(time.time() - started, 2)
        self.assertEqual(found, {'/tmp/x/SKILL.md', '~/y/SKILL.md'})


class VisibleProgressTests(SyncBase):
    def test_run_reports_each_phase_and_the_bytes_sent(self):
        import contextlib
        sync_client.cmd_init(self.args(user='gina'))
        out = TtyBuffer()
        with contextlib.redirect_stdout(out):
            code = sync_client.cmd_run(self.args(user='gina', quiet=False))
        text = out.getvalue()
        self.assertEqual(code, 0)
        for part in ('读取工作目录', '查找读过的 Skill', '上传', '100%', '完成：会话 3 个成功'):
            self.assertIn(part, text)

    def test_upload_reports_exactly_the_file_size(self):
        sync_client.cmd_init(self.args(user='hank'))
        client = sync_client.Client(self.url, 'hank')
        row = [r for r in sync_scan.scan(self.home)[0] if r['s'] == 'claude'][0]
        seen = []
        client.upload(row, '/w/p', seen.append)
        self.assertEqual(sum(seen), row['size'])


if __name__ == '__main__':
    unittest.main()
