"""Team sync client: upload local AI-session history and the Skills it read to a team server."""
import http.client
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

import audit_paths
import self_update
import sync_scan
from agent_backends.base import AuditError

USER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$')
BATCH = 1500
RETRIES = 3


class SyncError(Exception):
    pass


def config_file():
    return audit_paths.home() / 'sync.json'


def load_config():
    try:
        data = json.loads(config_file().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise SyncError('还没有配置，先运行：skill-audit sync init --server http://主机:端口 --user 你的邮箱前缀')
    if not data.get('server') or not USER_RE.match(data.get('user', '')):
        raise SyncError('sync.json 内容不完整，请重新运行 sync init')
    return data


def save_config(server, user):
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump({'server': server, 'user': user}, handle, ensure_ascii=False)


def normalize_server(value):
    value = value.strip().rstrip('/')
    if '://' not in value:
        value = 'http://' + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise SyncError('服务器地址应形如 http://主机:端口')
    return '%s://%s' % (parsed.scheme, parsed.netloc)


class Client:
    def __init__(self, server, user, timeout=60):
        parsed = urllib.parse.urlparse(server)
        self.cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        self.host, self.user, self.timeout = parsed.netloc, user, timeout

    def request(self, method, path, body=None, length=None, ctype='application/json'):
        last = None
        for attempt in range(RETRIES):
            conn = self.cls(self.host, timeout=self.timeout)
            headers = {'X-SA-User': self.user}
            if body is not None:
                headers['Content-Type'] = ctype
                headers['Content-Length'] = str(length if length is not None else len(body))
            try:
                if hasattr(body, 'seek'):
                    body.seek(0)
                conn.request(method, path, body=body, headers=headers)
                res = conn.getresponse()
                data = res.read()
                if res.status < 400:
                    return json.loads(data.decode('utf-8')) if data else {}
                last = '%s %s' % (res.status, data.decode('utf-8', 'replace')[:200])
                if res.status < 500:
                    raise SyncError(last)
            except (OSError, http.client.HTTPException) as exc:
                last = '%s: %s' % (type(exc).__name__, exc)
            finally:
                conn.close()
            time.sleep(2 * (attempt + 1))
        raise SyncError('请求失败：' + str(last))

    def fetch(self, path, limit=self_update.MAX_BYTES):
        conn = self.cls(self.host, timeout=self.timeout)
        try:
            conn.request('GET', path, headers={'X-SA-User': self.user})
            res = conn.getresponse()
            data = res.read(limit + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise SyncError('%s: %s' % (type(exc).__name__, exc))
        finally:
            conn.close()
        if res.status != 200 or len(data) > limit:
            raise SyncError('下载失败：HTTP %s' % res.status)
        return data

    def post(self, path, value):
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        return self.request('POST', path, raw)

    def status(self):
        return self.request('GET', '/api/sync/status')

    def diff(self, rows):
        need = []
        for start in range(0, len(rows), BATCH):
            chunk = rows[start:start + BATCH]
            reply = self.post('/api/sync/diff', {'files': [{'s': r['s'], 'rel': r['rel'], 'size': r['size'], 'mt': r['mt']} for r in chunk]})
            need += [chunk[i] for i in reply.get('need', [])]
        return need

    def upload(self, row, project=''):
        query = urllib.parse.urlencode({'source': row['s'], 'path': row['rel'], 'mt': row['mt'], 'proj': project})
        with open(row['abs'], 'rb') as handle:
            body = handle.read(row['size'])
        if len(body) != row['size']:
            raise SyncError('文件在读取时发生变化：' + row['abs'])
        return self.request('PUT', '/api/sync/file?' + query, body, ctype='application/octet-stream')


def human(size):
    return '%.1f MB' % (size / 1048576) if size < 1 << 30 else '%.2f GB' % (size / (1 << 30))


def say(args, text, end='\n'):
    if not getattr(args, 'quiet', False):
        print(text, end=end, flush=True)


def cmd_init(args):
    user = (args.user or '').strip().lower()
    if not USER_RE.match(user):
        raise SyncError('用户 id 应为 2-32 位的字母、数字、点、下划线、短横线（你的邮箱前缀）')
    server = normalize_server(args.server or '')
    status = Client(server, user, 15).status()
    save_config(server, user)
    print('已保存：%s @ %s（服务器上已有 %d 个会话文件）' % (user, server, status.get('files', 0)))
    return 0


REEXEC_ENV = 'SKILL_AUDIT_SYNC_UPDATED'


def restart(target):
    os.execv(sys.executable, [sys.executable, str(target)] + sys.argv[1:])


def auto_update(args, client, cfg, restart=restart):
    """Replace this zipapp with the server's newer build; any problem is reported and syncing carries on."""
    if (getattr(args, 'no_update', False) or cfg.get('auto_update') is False
            or os.environ.get(self_update.DISABLE_ENV) or os.environ.get(REEXEC_ENV)):
        return False
    try:
        info = client.request('GET', '/api/sync/version')
    except SyncError:
        return False
    latest, digest = info.get('version'), info.get('sha256')
    import skill_audit_cli
    current = skill_audit_cli.APP_VERSION
    if not latest or not digest or not self_update.parse_version(latest) or \
            self_update.parse_version(latest) <= self_update.parse_version(current):
        return False
    tmp = None
    try:
        target = self_update.installed_target()
        data = client.fetch('/download/skill-audit.pyz')
        fd, name = tempfile.mkstemp(prefix='.skill-audit-update-', dir=str(target.parent))
        tmp = Path(name)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        self_update.verify(tmp, latest, digest)
        self_update.refresh_rules(target, tmp)
        tmp.chmod(0o755)
        os.replace(str(tmp), str(target))
        tmp = None
    except (SyncError, AuditError, OSError) as exc:
        print('自动更新失败，继续用当前版本同步：%s' % exc, file=sys.stderr)
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
    say(args, '已从服务器更新：%s → %s' % (current, latest))
    os.environ[REEXEC_ENV] = '1'
    try:
        restart(target)
    except OSError:
        pass
    return True


def plan(args, client, home):
    files, skipped = sync_scan.scan(home, args.days)
    return client.diff(files), len(files), skipped


def cmd_run(args):
    cfg = load_config()
    client = Client(cfg['server'], cfg['user'])
    auto_update(args, client, cfg)
    home = Path(os.path.expanduser(args.history_home)) if args.history_home else Path.home()
    need, total, skipped = plan(args, client, home)
    size = sum(r['size'] for r in need)
    say(args, '本机共 %d 个会话文件，需要上传 %d 个（%s）%s' % (
        total, len(need), human(size), '，另有 %d 个超过 128 MB 的已跳过' % len(skipped) if skipped else ''))
    if args.dry_run:
        for r in need[:20]:
            say(args, '  %s/%s  %s' % (r['s'], r['rel'], human(r['size'])))
        return 0
    if need and not args.yes:
        if not sys.stdin.isatty():
            raise SyncError('非交互运行需要加 --yes')
        if input('继续上传到 %s ？[y/N] ' % cfg['server']).strip().lower() != 'y':
            print('已取消')
            return 1
    if not need:
        return 0
    projects = {}
    for r in need:
        projects[r['abs']] = sync_scan.peek_project(r['s'], r['abs'])
    skills = skills_phase(args, client, need, home)
    failed = 0
    for number, r in enumerate(need, 1):
        try:
            client.upload(r, projects.get(r['abs'], ''))
        except SyncError as exc:
            failed += 1
            say(args, '\n失败：%s/%s：%s' % (r['s'], r['rel'], exc))
        say(args, '\r上传 %d/%d' % (number, len(need)), end='')
    say(args, '')
    say(args, '完成：会话 %d 个成功、%d 个失败；Skill 文件 %d 个。' % (len(need) - failed, failed, skills))
    return 1 if failed else 0


def skills_phase(args, client, need, home):
    dirs = sync_scan.skill_dirs_in([r['abs'] for r in need], home)
    rows, entries = [], []
    for directory in dirs.values():
        rows += sync_scan.skill_files(directory)
        entries.append({'key': sync_scan.skill_key(directory), 'origin': str(directory), 'name': directory.name})
    if not rows:
        return 0
    say(args, '会话里读过 %d 个 Skill 目录：%s' % (len(entries), '、'.join(sorted(e['name'] for e in entries))[:300]))
    missing = client.diff(rows)
    for r in missing:
        client.upload(r)
    client.post('/api/sync/skills', {'entries': entries})
    return len(missing)


def cmd_status(args):
    cfg = load_config()
    status = Client(cfg['server'], cfg['user'], 15).status()
    print('%s @ %s' % (cfg['user'], cfg['server']))
    print('服务器上：%d 个会话文件，%s，%d 个 Skill 目录，最近同步 %s' % (
        status.get('files', 0), human(status.get('bytes', 0)), status.get('skills', 0),
        time.strftime('%Y-%m-%d %H:%M', time.localtime(status['last'])) if status.get('last') else '从未'))
    return 0


PLIST = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>%(label)s</string>
<key>ProgramArguments</key><array>%(arguments)s</array>
<key>StartInterval</key><integer>%(seconds)d</integer>
<key>RunAtLoad</key><true/>
<key>StandardOutPath</key><string>%(log)s</string>
<key>StandardErrorPath</key><string>%(log)s</string>
</dict></plist>
'''
LABEL = 'com.skill-audit.sync'
CRON_MARK = '# skill-audit sync'


def run_command():
    import shutil
    exe = shutil.which('skill-audit') or os.path.abspath(sys.argv[0])
    return [exe, 'sync', 'run', '--yes', '--quiet']


def plist_text(command, hours, log):
    from xml.sax.saxutils import escape
    return PLIST % {'label': LABEL, 'seconds': hours * 3600, 'log': escape(str(log)),
                    'arguments': ''.join('<string>%s</string>' % escape(c) for c in command)}


def cron_line(command, hours, log):
    import shlex
    return '0 */%d * * * %s >> %s 2>&1 %s' % (hours, ' '.join(shlex.quote(c) for c in command), shlex.quote(str(log)), CRON_MARK)


def cmd_install(args):
    import subprocess
    load_config()
    log = audit_paths.home() / 'sync.log'
    command = run_command()
    if sys.platform == 'darwin':
        path = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plist_text(command, args.hours, log), encoding='utf-8')
        target = 'gui/%d' % os.getuid()
        subprocess.run(['launchctl', 'bootout', target + '/' + LABEL], capture_output=True)
        result = subprocess.run(['launchctl', 'bootstrap', target, str(path)], capture_output=True, text=True)
        if result.returncode:
            raise SyncError('launchctl 加载失败：' + result.stderr.strip())
        print('已安装：每 %d 小时同步一次，日志 %s' % (args.hours, log))
        return 0
    if sys.platform.startswith('linux'):
        current = subprocess.run(['crontab', '-l'], capture_output=True, text=True).stdout
        kept = [line for line in current.splitlines() if CRON_MARK not in line]
        text = '\n'.join(kept + [cron_line(command, args.hours, log)]) + '\n'
        subprocess.run(['crontab', '-'], input=text, text=True, check=True)
        print('已写入 crontab：每 %d 小时同步一次，日志 %s' % (args.hours, log))
        return 0
    raise SyncError('当前系统暂不支持自动安装，请用系统的任务计划程序定时运行：' + ' '.join(command))


def cmd_uninstall(args):
    import subprocess
    if sys.platform == 'darwin':
        path = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
        subprocess.run(['launchctl', 'bootout', 'gui/%d/%s' % (os.getuid(), LABEL)], capture_output=True)
        if path.exists():
            path.unlink()
    elif sys.platform.startswith('linux'):
        current = subprocess.run(['crontab', '-l'], capture_output=True, text=True).stdout
        kept = [line for line in current.splitlines() if CRON_MARK not in line]
        subprocess.run(['crontab', '-'], input='\n'.join(kept) + '\n', text=True, check=True)
    print('已移除定时同步')
    return 0


ACTIONS = {'init': cmd_init, 'run': cmd_run, 'status': cmd_status, 'install': cmd_install, 'uninstall': cmd_uninstall}


def cmd_sync(args):
    try:
        return ACTIONS[args.action](args)
    except SyncError as exc:
        print('错误：%s' % exc, file=sys.stderr)
        return 2
