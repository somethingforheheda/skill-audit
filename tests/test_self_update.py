"""skill-audit update：发行包替换、校验、条例刷新与每日版本提示（全部离线，GitHub 请求由假响应代替）。"""
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import zipapp
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib import error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_paths
import rulebook
import self_update as su
from agent_backends.base import AuditError

RULE_NAMES = list(rulebook.FILES.values())


class Response(io.BytesIO):
    def __init__(self, body, url="https://github.com/x"):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


def build_pyz(directory, name, version, rules="new"):
    stage = Path(directory) / (name + "-src")
    stage.mkdir()
    (stage / "__main__.py").write_text(f"print('skill-audit {version}')\n", encoding="utf-8")
    for rule in RULE_NAMES:
        (stage / rule).write_text(f"{rules}-{rule}", encoding="utf-8")
    target = Path(directory) / name
    zipapp.create_archive(stage, target, interpreter="/usr/bin/env python3")
    return target


def release_json(version, url="https://github.com/o/r/releases/download/v1/skill-audit.pyz", digest=None):
    asset = {"name": su.ASSET, "browser_download_url": url}
    if digest:
        asset["digest"] = "sha256:" + digest
    return json.dumps({"tag_name": "v" + version, "html_url": "https://github.com/o/r/releases/tag/v" + version,
                       "assets": [asset]}).encode()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="skill-audit-update-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.home = self.dir / "home"
        patcher = patch.dict(os.environ, {audit_paths.HOME_ENV: str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(su.DISABLE_ENV, None)
        self.installed = build_pyz(self.dir, "installed.pyz", "0.12.0", rules="old")
        argv = patch.object(sys, "argv", [str(self.installed)])
        argv.start()
        self.addCleanup(argv.stop)

    def serve(self, version="0.13.0", payload=None, digest="auto", url=None):
        """Patch the network: API JSON first, then the asset bytes."""
        payload = payload if payload is not None else build_pyz(self.dir, f"new-{version}.pyz", version).read_bytes()
        if digest == "auto":
            digest = hashlib.sha256(payload).hexdigest()
        calls = []

        def fake(req, timeout):
            calls.append(req.full_url)
            if req.full_url.startswith(su.API):
                return Response(release_json(version, digest=digest, **({"url": url} if url else {})))
            return Response(payload)
        return patch.object(su, "_open", fake), calls

    def args(self, **kw):
        base = dict(check=False, version=None, notify=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def run_update(self, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            code = su.run(self.args(**kw), "0.12.0")
        return code, out.getvalue()

    def test_parse_version(self):
        self.assertEqual(su.parse_version("v0.13.0"), (0, 13, 0))
        self.assertGreater(su.parse_version("0.13.0"), su.parse_version("0.9.12"))
        self.assertIsNone(su.parse_version("0.13"))
        self.assertIsNone(su.parse_version("latest"))

    def test_update_replaces_installed_app_and_keeps_it_executable(self):
        patcher, _ = self.serve()
        with patcher:
            code, out = self.run_update()
        self.assertEqual(code, 0)
        self.assertIn("0.12.0 → 0.13.0", out)
        self.assertIn("SHA-256 已核对", out)
        self.assertEqual(su.installed_target(), self.installed.resolve())
        self.assertIn(b"skill-audit 0.13.0", zipapp_main(self.installed))
        self.assertTrue(os.access(self.installed, os.X_OK))
        self.assertEqual([p.name for p in self.dir.glob(".skill-audit-update-*")], [])

    def test_already_latest_downloads_nothing(self):
        patcher, calls = self.serve(version="0.12.0")
        with patcher:
            code, out = self.run_update()
        self.assertEqual(code, 0)
        self.assertIn("已是最新版本", out)
        self.assertEqual(len(calls), 1)

    def test_check_only_never_downloads(self):
        before = self.installed.read_bytes()
        patcher, calls = self.serve()
        with patcher:
            _, out = self.run_update(check=True)
        self.assertIn("有新版本", out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.installed.read_bytes(), before)

    def test_pinned_version_can_roll_back(self):
        patcher, calls = self.serve(version="0.11.0")
        with patcher:
            _, out = self.run_update(version="0.11.0")
        self.assertIn("0.12.0 → 0.11.0", out)
        self.assertTrue(calls[0].endswith("/tags/v0.11.0"))

    def test_checksum_mismatch_leaves_current_version_untouched(self):
        before = self.installed.read_bytes()
        patcher, _ = self.serve(digest="0" * 64)
        with patcher, self.assertRaisesRegex(AuditError, "SHA-256"):
            self.run_update()
        self.assertEqual(self.installed.read_bytes(), before)
        self.assertEqual([p.name for p in self.dir.glob(".skill-audit-update-*")], [])

    def test_candidate_that_reports_another_version_is_rejected(self):
        before = self.installed.read_bytes()
        wrong = build_pyz(self.dir, "wrong.pyz", "0.99.0").read_bytes()
        patcher, _ = self.serve(payload=wrong, digest=None)
        with patcher, self.assertRaisesRegex(AuditError, "自检失败"):
            self.run_update()
        self.assertEqual(self.installed.read_bytes(), before)

    def test_non_zip_download_is_rejected(self):
        patcher, _ = self.serve(payload=b"<html>not a zip</html>", digest=None)
        with patcher, self.assertRaisesRegex(AuditError, "不是有效的发行包"):
            self.run_update()

    def test_release_asset_must_come_from_github(self):
        patcher, _ = self.serve(url="https://evil.example.com/skill-audit.pyz")
        with patcher, self.assertRaisesRegex(AuditError, "没有可用的"):
            self.run_update()

    def test_redirect_to_other_host_is_rejected(self):
        payload = build_pyz(self.dir, "x.pyz", "0.13.0").read_bytes()

        def fake(req, timeout):
            if req.full_url.startswith(su.API):
                return Response(release_json("0.13.0"))
            return Response(payload, url="https://evil.example.com/a.pyz")
        with patch.object(su, "_open", fake), self.assertRaisesRegex(AuditError, "重定向"):
            self.run_update()

    def test_source_checkout_is_refused(self):
        with patch.object(sys, "argv", [str(self.dir / "missing.py")]), self.assertRaisesRegex(AuditError, "源码"):
            su.installed_target()
        script = self.dir / "run.py"
        script.write_text("print(1)\n", encoding="utf-8")
        with patch.object(sys, "argv", [str(script)]), self.assertRaisesRegex(AuditError, "源码"):
            su.installed_target()

    def test_http_errors_become_readable_messages(self):
        def missing(req, timeout):
            raise error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO())

        def limited(req, timeout):
            raise error.HTTPError(req.full_url, 403, "rl", {}, io.BytesIO())

        def offline(req, timeout):
            raise error.URLError("offline")
        for fake, text in ((missing, "没有找到"), (limited, "限流"), (offline, "无法连接")):
            with patch.object(su, "_open", fake), self.assertRaisesRegex(AuditError, text):
                su.fetch_release()

    def test_untouched_rules_follow_the_new_version_and_edited_rules_are_kept(self):
        rules = audit_paths.rules_dir()
        rules.mkdir(parents=True)
        untouched, edited = RULE_NAMES
        (rules / untouched).write_text(f"old-{untouched}", encoding="utf-8")
        (rules / edited).write_text("my own edits", encoding="utf-8")
        patcher, _ = self.serve()
        with patcher:
            _, out = self.run_update()
        self.assertEqual((rules / untouched).read_text(encoding="utf-8"), f"new-{untouched}")
        self.assertEqual((rules / edited).read_text(encoding="utf-8"), "my own edits")
        self.assertEqual((rules / (edited + ".new")).read_text(encoding="utf-8"), f"new-{edited}")
        self.assertIn("你修改过", out)

    def test_notify_switch_only_changes_config(self):
        code, out = self.run_update(notify="off")
        self.assertEqual(code, 0)
        self.assertIn("已关闭", out)
        self.assertIs(json.loads((self.home / "config.json").read_text(encoding="utf-8"))[su.CONFIG_KEY], False)


class Terminal(io.StringIO):
    def isatty(self):
        return True


class HintTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="skill-audit-hint-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = patch.dict(os.environ, {audit_paths.HOME_ENV: str(self.dir / "home")})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(su.DISABLE_ENV, None)
        app = build_pyz(self.dir, "app.pyz", "0.12.0")
        argv = patch.object(sys, "argv", [str(app)])
        argv.start()
        self.addCleanup(argv.stop)
        self.fetches = 0

    def hint(self, latest="0.13.0", command="history", fail=False, tty=True):
        def fetch(version=None, timeout=15):
            self.fetches += 1
            if fail:
                raise AuditError("offline")
            return {"version": latest}
        err = Terminal() if tty else io.StringIO()
        with patch.object(su, "fetch_release", fetch), redirect_stderr(err):
            su.maybe_hint(command, "0.12.0")
        return err.getvalue()

    def test_hint_printed_once_per_day_network_but_every_time_from_cache(self):
        first = self.hint()
        self.assertIn("0.13.0 已发布", first)
        self.assertIn("skill-audit update", first)
        self.assertIn("0.13.0 已发布", self.hint())
        self.assertEqual(self.fetches, 1)

    def test_cache_expires_after_a_day(self):
        self.hint()
        path = self.dir / "home" / "update-check.json"
        path.write_text(json.dumps({"checked_at": time.time() - su.CHECK_EVERY - 5, "latest": "0.12.0"}), encoding="utf-8")
        self.assertIn("0.13.0", self.hint())
        self.assertEqual(self.fetches, 2)

    def test_up_to_date_and_offline_stay_silent(self):
        self.assertEqual(self.hint(latest="0.12.0"), "")
        (self.dir / "home" / "update-check.json").unlink()
        self.assertEqual(self.hint(fail=True), "")
        # 离线也记下检查时间，避免每次命令都等超时。
        self.assertEqual(self.hint(fail=True), "")
        self.assertEqual(self.fetches, 2)

    def test_non_interactive_commands_and_opt_outs_never_touch_the_network(self):
        self.assertEqual(self.hint(command="lint"), "")
        self.assertEqual(self.hint(command="update"), "")
        with patch.dict(os.environ, {su.DISABLE_ENV: "1"}):
            self.assertEqual(self.hint(), "")
        (self.dir / "home").mkdir(exist_ok=True)
        (self.dir / "home" / "config.json").write_text(json.dumps({su.CONFIG_KEY: False}), encoding="utf-8")
        self.assertEqual(self.hint(), "")
        (self.dir / "home" / "config.json").unlink()
        self.assertEqual(self.hint(tty=False), "")
        self.assertEqual(self.fetches, 0)


def zipapp_main(path):
    import zipfile
    with zipfile.ZipFile(path) as archive:
        return archive.read("__main__.py")


if __name__ == "__main__":
    unittest.main()
