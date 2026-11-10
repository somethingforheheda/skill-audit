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
        self.assertEqual((info['files'], len(store.state('carol')['skills'])), (3, 1))
        self.assertEqual(info['skills'], 0)
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
        self.assertTrue(Path(meta['catalog']).is_dir())
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

    def test_no_skills_option_is_saved_and_skips_the_skill_upload(self):
        sync_client.cmd_init(self.args(user='gina', quiet=True, no_skills=True))
        self.assertIs(sync_client.load_config()['skills'], False)
        sync_client.cmd_run(self.args(user='gina', no_skills=True))
        self.assertEqual(store.state('gina')['skills'], {})
        self.assertGreater(len(store.state('gina')['files']), 0)
        sync_client.cmd_init(self.args(user='gina', quiet=True))
        self.assertNotIn('skills', sync_client.load_config())


class DedupeTests(SyncBase):
    def project_file(self):
        return self.home / '.claude' / 'projects' / '-work-app' / 's1.jsonl'

    def test_same_content_with_a_new_timestamp_is_not_uploaded_again(self):
        sync_client.cmd_init(self.args(user='dora', quiet=True))
        sync_client.cmd_run(self.args(user='dora', quiet=True))
        before = dict(store.state('dora')['files'])
        later = time.time() + 5000
        os.utime(str(self.project_file()), (later, later))
        self.assertEqual(sync_client.cmd_run(self.args(user='dora', quiet=True)), 0)
        after = store.state('dora')['files']
        row = after['claude/-work-app/s1.jsonl']
        self.assertEqual(row['m'], int(later))
        self.assertEqual(row['t'] >= before['claude/-work-app/s1.jsonl']['t'], True)
        self.assertEqual(sorted(after), sorted(before))
        self.assertEqual(sync_client.plan(self.args(user='dora'), sync_client.Client(self.url, 'dora'), self.home)[0], [])

    def test_a_copy_under_another_name_is_recorded_but_neither_stored_nor_counted(self):
        sync_client.cmd_init(self.args(user='emil', quiet=True))
        sync_client.cmd_run(self.args(user='emil', quiet=True))
        size = store.state('emil')['bytes']
        copy = self.project_file().with_name('s1-copy.jsonl')
        copy.write_bytes(self.project_file().read_bytes())
        sync_client.cmd_run(self.args(user='emil', quiet=True))
        files = store.state('emil')['files']
        self.assertEqual(files['claude/-work-app/s1-copy.jsonl']['dup'], 'claude/-work-app/s1.jsonl')
        self.assertFalse((store.user_dir('emil') / 'sessions' / 'claude' / '-work-app' / 's1-copy.jsonl').exists())
        self.assertEqual(store.state('emil')['bytes'], size)
        self.assertEqual(browse.summary('emil')['files'], 3)
        self.assertNotIn('claude/-work-app/s1-copy.jsonl', browse.select('emil'))
        self.assertEqual(sync_client.plan(self.args(user='emil'), sync_client.Client(self.url, 'emil'), self.home)[0], [])

    def test_a_browser_style_upload_of_known_content_is_dropped_on_the_server(self):
        sync_client.cmd_init(self.args(user='fay', quiet=True))
        sync_client.cmd_run(self.args(user='fay', quiet=True))
        client = sync_client.Client(self.url, 'fay')
        path = self.project_file()
        client.upload({'s': 'claude', 'rel': '-work-app/other-name.jsonl', 'size': path.stat().st_size, 'mt': 1, 'abs': str(path)})
        row = store.state('fay')['files']['claude/-work-app/other-name.jsonl']
        self.assertEqual(row['dup'], 'claude/-work-app/s1.jsonl')
        self.assertFalse((store.user_dir('fay') / 'sessions' / 'claude' / '-work-app' / 'other-name.jsonl').exists())

    def test_rows_carry_the_uploader_and_old_data_can_be_hashed_and_deduplicated(self):
        user = 'gus'
        base = store.user_dir(user) / 'sessions' / 'claude' / 'p'
        base.mkdir(parents=True)
        for name, text in (('a.jsonl', 'same\n'), ('b.jsonl', 'same\n'), ('c.jsonl', 'other\n')):
            (base / name).write_text(text, encoding='utf-8')
            store.append(user, {'k': 'claude/p/' + name, 's': len(text), 'm': 5, 'p': '/w/x'})
        self.assertEqual(store.dedupe_user(user), (3, 1))
        files = store.state(user)['files']
        self.assertEqual(files['claude/p/b.jsonl']['dup'], 'claude/p/a.jsonl')
        self.assertNotIn('dup', files['claude/p/a.jsonl'])
        self.assertEqual(store.state(user)['bytes'], 5 + 6)
        self.assertEqual(store.dedupe_user(user), (0, 0))
        self.assertTrue(all(json.loads(line)['u'] == user for line in (store.user_dir(user) / 'journal.jsonl').read_text().splitlines()))

    def test_analysis_by_explicit_keys_resolves_copies_and_keeps_the_sessions(self):
        sync_client.cmd_init(self.args(user='hana', quiet=True))
        sync_client.cmd_run(self.args(user='hana', quiet=True))
        copy = self.project_file().with_name('dup.jsonl')
        copy.write_bytes(self.project_file().read_bytes())
        sync_client.cmd_run(self.args(user='hana', quiet=True))
        meta = runner.create_from_store('hana', {'keys': ['claude/-work-app/dup.jsonl', 'claude/-work-app/s1.jsonl', 'claude/nope.jsonl']})
        self.assertEqual(meta['files'], 1)
        for _ in range(100):
            row = jobs.get(meta['id'])
            if row['status'] in ('done', 'failed'):
                break
            time.sleep(0.2)
        self.assertTrue((config.JOBS / meta['id'] / 'home').is_dir())
        self.assertTrue((store.user_dir('hana') / 'sessions' / 'claude' / '-work-app' / 's1.jsonl').is_file())


class JobCatalogTests(unittest.TestCase):
    def put(self, user, key, origin):
        folder = store.user_dir(user) / 'skills' / key
        (folder / 'refs').mkdir(parents=True, exist_ok=True)
        (folder / 'SKILL.md').write_text('---\nname: x\n---\n', encoding='utf-8')
        (folder / 'refs' / 'a.md').write_text('a', encoding='utf-8')
        store.append(user, {'skill': key, 'origin': origin, 'name': key.split('@')[0]})

    def test_repository_skills_win_over_global_ones_and_plugins_never_count(self):
        config.ensure_dirs()
        user = 'frank'
        store.append(user, {'k': 'claude/p/s1.jsonl', 's': 1, 'm': int(time.time()), 'p': '/Users/me/work/app-client'})
        store.append(user, {'k': 'claude/p/s2.jsonl', 's': 1, 'm': int(time.time()), 'p': '/Users/me/work/other'})
        self.put(user, 'mine@11111111', '/Users/me/.agents/skills/mine')
        self.put(user, 'both@22222222', '/Users/me/.agents/skills/both')
        self.put(user, 'both@33333333', '/Users/me/work/app-client/.agents/skills/both')
        self.put(user, 'sibling@44444444', '/Users/me/work/other/.agents/skills/sibling')
        self.put(user, 'figma@55555555', '/Users/me/.codex/plugins/cache/x/figma/1/skills/figma')
        self.put(user, 'imagegen@66666666', '/Users/me/.codex/skills/.system/imagegen')
        store.append(user, {'k': 'claude/p/s3.jsonl', 's': 1, 'm': int(time.time()), 'p': '/Users/me'})
        self.assertEqual(browse.skill_rank('C:\\Users\\me\\.agents\\skills\\w', set()), 1)
        self.assertEqual(browse.summary(user, False)['skills'], 2)
        catalog, roots = browse.job_catalog('abcabcabcabc', [user], 'app-client')
        names = sorted(p.name for p in catalog.iterdir() if p.is_dir())
        self.assertEqual(names, ['both@33333333'])
        self.assertTrue((catalog / 'both@33333333' / 'refs' / 'a.md').is_file())
        self.assertEqual(sorted(json.loads((catalog / '_origins.json').read_text(encoding='utf-8'))), names)
        self.assertIn('/Users/me/work/app-client/.agents/skills', roots)
        self.assertIn('/Users/me/work/app-client/.claude/skills', roots)
        self.assertNotIn('/Users/me/.agents/skills', roots)
        everything, _ = browse.job_catalog('defdefdefdef', [user], None)
        self.assertEqual(sorted(p.name for p in everything.iterdir() if p.is_dir()),
                         ['both@33333333', 'sibling@44444444'])

    def test_global_skills_are_the_fallback_when_the_repository_has_none(self):
        config.ensure_dirs()
        user = 'ivy'
        store.append(user, {'k': 'claude/p/s1.jsonl', 's': 1, 'm': int(time.time()), 'p': '/Users/ivy/work/app'})
        self.put(user, 'mine@11111111', '/Users/ivy/.agents/skills/mine')
        catalog, roots = browse.job_catalog('123123123123', [user], 'app')
        self.assertEqual([p.name for p in catalog.iterdir() if p.is_dir()], ['mine@11111111'])
        self.assertIn('/Users/ivy/.agents/skills', roots)
        self.assertIn('/Users/ivy/.claude/skills', roots)


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
        self.assertIn('[=', text)
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

    def test_every_frame_fits_one_row_even_with_wide_characters(self):
        import unittest.mock as mock
        for columns in (40, 80, 100, 160):
            out = TtyBuffer()
            bar = sync_client.Progress('查找读过的 Skill', 1 << 30, size=True, out=out)
            with mock.patch.object(sync_client.shutil, 'get_terminal_size', return_value=os.terminal_size((columns, 24))):
                for step in range(1, 11):
                    bar.last = 0
                    bar.update(step * (100 << 20), '(%d/326) 中文文件名-很长很长很长很长很长.jsonl' % step)
            frames = [f for f in out.getvalue().split('\r') if f]
            self.assertEqual(len(frames), 10)
            for frame in frames:
                text = frame.replace('\x1b[K', '')
                self.assertNotIn('\n', text)
                self.assertLessEqual(sum(sync_client.cells(c) for c in text), columns - 2, (columns, text))
        self.assertEqual(sync_client.fit('上传ab', 5), '上传a')

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
        sync_client.cmd_init(self.args(user='ivor'))
        out = TtyBuffer()
        with contextlib.redirect_stdout(out):
            code = sync_client.cmd_run(self.args(user='ivor', quiet=False))
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
