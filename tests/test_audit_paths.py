"""工具目录划分：默认 ~/.skill-audit，老安装回退，报告不再落 /tmp。"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_paths
import history_export as export
import skill_audit_cli as cli


class PathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skill-audit-home-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)/'audit-home'
        patcher = patch.dict(os.environ, {audit_paths.HOME_ENV: str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_home_follows_env_then_default(self):
        self.assertEqual(audit_paths.home(), self.home)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(audit_paths.HOME_ENV)
            self.assertEqual(audit_paths.home(), Path.home()/'.skill-audit')

    def test_slug_keeps_project_name_only(self):
        self.assertEqual(audit_paths.slug('/Users/x/work/team/example-project'), 'example-project')
        self.assertEqual(audit_paths.slug('v1.2_beta'), 'v1.2_beta')
        # 纯非 ASCII 目录名会被清空，退回 report，保证目录名可移植
        self.assertEqual(audit_paths.slug('中文 名'), 'report')
        self.assertEqual(audit_paths.slug(''), 'report')

    def test_default_report_directory_lands_in_reports_with_latest_link(self):
        directory = export.report_directory(None, label='/work/example-project')
        self.assertEqual(directory.parent, audit_paths.reports_root())
        self.assertTrue(directory.name.startswith('example-project-'))
        self.assertEqual(oct(directory.stat().st_mode)[-3:], '700')
        self.assertEqual((audit_paths.reports_root()/'latest').resolve(), directory.resolve())
        self.assertNotIn('/tmp/', str(directory))

    def test_explicit_output_still_wins_and_refuses_nonempty(self):
        target = Path(self.tmp.name)/'given'
        self.assertEqual(export.report_directory(target.with_suffix('.json')), target)
        (target/'report.json').write_text('keep', encoding='utf-8')
        with self.assertRaises(ValueError):
            export.report_directory(target)
        self.assertEqual((target/'report.json').read_text(encoding='utf-8'), 'keep')


class ReportsCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skill-audit-reports-')
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.dict(os.environ, {audit_paths.HOME_ENV: self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_dir(self, name, reads=3):
        directory = audit_paths.reports_root()/name
        (directory/'snapshots'/'run'/'sessions').mkdir(parents=True)
        (directory/'report.json').write_text(json.dumps(
            {'scope': {'project': '/work/demo'}, 'sessions': [{'session_id': 's'}],
             'extraction': {'summary': {'confirmed_reads': reads}}}), encoding='utf-8')
        (directory/'report.html').write_text('<html></html>', encoding='utf-8')
        (directory/'snapshots'/'run'/'sessions'/'events.jsonl').write_text('x'*2048, encoding='utf-8')
        return directory

    def reports(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['reports', *argv])
        return code, out.getvalue()

    def test_path_and_empty_listing(self):
        code, text = self.reports('--path')
        self.assertEqual(code, 0)
        self.assertEqual(text.strip(), str(audit_paths.reports_root()))
        self.assertIn('暂无报告', self.reports()[1])

    def test_listing_reports_newest_first(self):
        self.run_dir('demo-20260101T000000Z-aa', reads=1)
        self.run_dir('demo-20260202T000000Z-bb', reads=2)
        text = self.reports()[1]
        self.assertLess(text.index('demo-20260202T000000Z-bb'), text.index('demo-20260101T000000Z-aa'))
        self.assertIn('确认读取 2', text)
        self.assertIn('/work/demo', text)

    def test_cross_project_sort_uses_timestamp_not_project_name(self):
        self.run_dir('zebra-20260101T000000Z-aa')
        self.run_dir('alpha-20260202T000000Z-bb')
        text = self.reports('--prune', '--keep', '1')[1]
        self.assertLess(text.index('alpha-'), text.index('zebra-'))
        self.assertIn('zebra-20260101T000000Z-aa  ← 待清理', text)

    def test_rejects_invalid_cleanup_options(self):
        self.assertEqual(self.reports('--prune', '--keep', '-1')[0], 2)
        self.assertEqual(self.reports('--prune', '--keep', '0')[0], 2)
        self.assertEqual(self.reports('--yes')[0], 2)

    def test_prune_is_dry_run_until_confirmed_and_keeps_report_files(self):
        old = self.run_dir('demo-20260101T000000Z-aa')
        new = self.run_dir('demo-20260202T000000Z-bb')
        text = self.reports('--prune', '--keep', '1')[1]
        self.assertIn('待清理', text)
        self.assertIn('试运行', text)
        self.assertTrue((old/'snapshots').is_dir())
        self.reports('--prune', '--keep', '1', '--yes')
        self.assertFalse((old/'snapshots').exists())
        self.assertTrue((old/'report.json').is_file())
        self.assertTrue((new/'snapshots').is_dir())

    def test_prune_all_removes_whole_run(self):
        old = self.run_dir('demo-20260101T000000Z-aa')
        self.run_dir('demo-20260202T000000Z-bb')
        self.reports('--prune', '--keep', '1', '--all', '--yes')
        self.assertFalse(old.exists())


if __name__ == '__main__':
    unittest.main()
