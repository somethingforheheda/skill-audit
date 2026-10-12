"""`skill-audit update`: replace the installed zipapp with the latest GitHub Release, plus a once-a-day version hint.

Nothing here updates silently: the hint only prints a line, and the binary changes only when the user runs `update`.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

import audit_config as cfg
import audit_paths
import rulebook
from agent_backends.base import AuditError

REPO = "somethingforheheda/skill-audit"
API = f"https://api.github.com/repos/{REPO}/releases"
ASSET = "skill-audit.pyz"
# Release assets are served from github.com and redirected to *.githubusercontent.com.
DOWNLOAD_HOSTS = ("github.com", "githubusercontent.com")
MAX_BYTES = 20 * 1024 * 1024
CHECK_EVERY = 24 * 3600
HINT_TIMEOUT = 3
HINT_COMMANDS = ("history", "review", "cross", "doctor")
DISABLE_ENV = "SKILL_AUDIT_NO_UPDATE_CHECK"
CONFIG_KEY = "update_check"


def _open(req, timeout):
    return request.urlopen(req, timeout=timeout)


def parse_version(text):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text).strip())
    return tuple(int(part) for part in match.groups()) if match else None


def _allowed_host(url):
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == name or host.endswith("." + name) for name in DOWNLOAD_HOSTS)


def fetch_release(version=None, timeout=15):
    """The latest Release (or the one tagged `version`) as {version, url, digest, page}."""
    path = f"/tags/v{version}" if version else "/latest"
    req = request.Request(API + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "skill-audit"})
    try:
        with _open(req, timeout) as response:
            data = json.loads(response.read(2_000_000).decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code == 404:
            raise AuditError(f"GitHub 上没有找到{'版本 ' + version if version else '已发布的 Release'}（{REPO}）") from exc
        if exc.code in (403, 429):
            raise AuditError("GitHub 接口暂时限流，请稍后再试") from exc
        raise AuditError(f"查询 GitHub Release 失败：HTTP {exc.code}") from exc
    except (error.URLError, OSError, TimeoutError) as exc:
        raise AuditError(f"无法连接 GitHub：{getattr(exc, 'reason', exc)}") from exc
    except (ValueError, UnicodeError) as exc:
        raise AuditError("GitHub 返回的 Release 信息无法解析") from exc
    tag = data.get("tag_name") if isinstance(data, dict) else None
    parsed = parse_version(tag or "")
    if parsed is None:
        raise AuditError(f"Release 标签不是 x.y.z 版本号：{tag!r}")
    asset = next((a for a in data.get("assets") or [] if isinstance(a, dict) and a.get("name") == ASSET), None)
    url = asset.get("browser_download_url") if asset else None
    if not isinstance(url, str) or not _allowed_host(url):
        raise AuditError(f"Release {tag} 没有可用的 {ASSET}；请到 {data.get('html_url') or 'GitHub'} 手动下载")
    digest = asset.get("digest")
    digest = digest.split(":", 1)[1].lower() if isinstance(digest, str) and digest.startswith("sha256:") else None
    return {"version": ".".join(map(str, parsed)), "url": url, "digest": digest, "page": data.get("html_url")}


def download(url, dest, timeout=60):
    req = request.Request(url, headers={"User-Agent": "skill-audit"})
    try:
        with _open(req, timeout) as response:
            if not _allowed_host(response.geturl()):
                raise AuditError("下载被重定向到了非 GitHub 地址，已中止")
            total = 0
            with open(dest, "wb") as out:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise AuditError("下载文件超过大小上限，已中止")
                    out.write(chunk)
    except (error.URLError, OSError, TimeoutError) as exc:
        raise AuditError(f"下载失败：{getattr(exc, 'reason', exc)}") from exc


def verify(path, version, digest):
    """Checksum (when GitHub reports one), zip structure, and a real `--version` run of the candidate."""
    if digest:
        actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if actual != digest:
            raise AuditError("下载文件的 SHA-256 与 Release 记录不一致，已中止，当前版本未改动")
    if not zipfile.is_zipfile(path):
        raise AuditError("下载文件不是有效的发行包，当前版本未改动")
    with zipfile.ZipFile(path) as archive:
        if "__main__.py" not in archive.namelist():
            raise AuditError("下载文件缺少入口，当前版本未改动")
    env = dict(os.environ, **{DISABLE_ENV: "1"})
    try:
        result = subprocess.run([sys.executable, str(path), "--version"], capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError(f"新版本无法运行（{type(exc).__name__}），当前版本未改动") from exc
    if result.returncode or result.stdout.strip() != f"skill-audit {version}":
        raise AuditError("新版本自检失败（--version 输出不符），当前版本未改动")


def installed_target():
    """The zipapp this process runs from; a source checkout cannot be updated this way."""
    path = Path(sys.argv[0])
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise AuditError("当前不是 install.sh 安装的发行包（疑似源码运行）；源码目录请 git pull 后重新 python3 build_dist.py && sh install.sh")
    return Path(os.path.realpath(path))


def _embedded(archive_path, name):
    try:
        with zipfile.ZipFile(archive_path) as archive:
            return archive.read(name)
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def refresh_rules(old_app, new_app):
    """Installed rulebooks shadow the embedded ones: refresh untouched copies, keep edited ones."""
    notes = []
    for name in rulebook.FILES.values():
        installed = audit_paths.rules_dir() / name
        if not installed.is_file():
            continue
        new = _embedded(new_app, name)
        if new is None:
            continue
        current = installed.read_bytes()
        if current == new:
            continue
        if current == _embedded(old_app, name):
            installed.write_bytes(new)
            notes.append(f"条例已随新版本更新：{name}")
        else:
            side = installed.with_name(name + ".new")
            side.write_bytes(new)
            notes.append(f"你修改过 {name}，已保留；新版默认条例另存为 {side}，需要时自行合并")
    return notes


def _state_path():
    return audit_paths.home() / "update-check.json"


def _save_state(latest):
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"checked_at": time.time(), "latest": latest}), encoding="utf-8")


def _load_state():
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def run(args, current):
    if args.notify:
        data = cfg.load_optional()
        data[CONFIG_KEY] = args.notify == "on"
        cfg.save(data)
        print(f"新版本提示：{'已开启' if args.notify == 'on' else '已关闭'}")
        return 0
    release = fetch_release(args.version)
    latest = release["version"]
    print(f"当前版本：{current}\n{'目标' if args.version else '最新'}版本：{latest}")
    if not args.version:
        _save_state(latest)
    if not args.version and parse_version(latest) <= parse_version(current):
        print("已是最新版本。")
        return 0
    if args.check:
        print(f"有新版本，运行 skill-audit update 升级。{('说明：' + release['page']) if release['page'] else ''}")
        return 0
    target = installed_target()
    fd, tmp_name = tempfile.mkstemp(prefix=".skill-audit-update-", dir=target.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        print("正在下载……", flush=True)
        download(release["url"], tmp)
        verify(tmp, latest, release["digest"])
        print("SHA-256 已核对。" if release["digest"] else "Release 未提供校验值，已做可运行性检查。")
        rule_notes = refresh_rules(target, tmp)
        tmp.chmod(0o755)
        os.replace(tmp, target)
    except PermissionError as exc:
        raise AuditError(f"没有写入 {target.parent} 的权限，当前版本未改动") from exc
    finally:
        tmp.unlink(missing_ok=True)
    _save_state(latest)
    print(f"已更新：{current} → {latest}（{target}）", *rule_notes, sep="\n")
    if release["page"]:
        print(f"更新说明：{release['page']}")
    return 0


def maybe_hint(command, current):
    """Print a one-line hint when a newer Release exists. Network at most once a day; never raises."""
    try:
        if command not in HINT_COMMANDS or os.environ.get(DISABLE_ENV) or not sys.stderr.isatty():
            return
        if cfg.load_optional().get(CONFIG_KEY) is False:
            return
        if not zipfile.is_zipfile(sys.argv[0]):
            return
        state = _load_state()
        latest = state.get("latest")
        if time.time() - float(state.get("checked_at") or 0) > CHECK_EVERY:
            try:
                latest = fetch_release(timeout=HINT_TIMEOUT)["version"]
            except AuditError:
                pass
            _save_state(latest)
        if parse_version(latest or "") and parse_version(latest) > parse_version(current):
            print(f"提示：skill-audit {latest} 已发布（当前 {current}），运行 skill-audit update 升级；"
                  f"不想看到此提示：skill-audit update --notify off", file=sys.stderr)
    except Exception:
        return
