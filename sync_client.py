"""Team sync client: upload local AI-session history and the Skills it read to a team server."""
import http.client
import io
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
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


def save_config(server, user, skills=True):
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump({'server': server, 'user': user, **({} if skills else {'skills': False})}, handle, ensure_ascii=False)


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
            conn = self.cls(self.host, timeout=self.timeout, blocksize=65536)
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

    def upload(self, row, project='', on_bytes=None):
        query = urllib.parse.urlencode({'source': row['s'], 'path': row['rel'], 'mt': row['mt'], 'proj': project})
        with open(row['abs'], 'rb') as handle:
            data = handle.read(row['size'])
        if len(data) != row['size']:
            raise SyncError('文件在读取时发生变化：' + row['abs'])
        body = Feed(data, on_bytes) if on_bytes else data
        return self.request('PUT', '/api/sync/file?' + query, body, len(data), ctype='application/octet-stream')


class Feed(io.BytesIO):
    """Request body that reports how many bytes have been handed to the socket; a retry rewinds the count."""

    def __init__(self, data, on_bytes):
        super().__init__(data)
        self.on_bytes, self.sent = on_bytes, 0

    def read(self, size=-1):
        chunk = super().read(size)
        self.sent += len(chunk)
        self.on_bytes(len(chunk))
        return chunk

    def seek(self, pos, whence=0):
        if pos == 0 and whence == 0 and self.sent:
            self.on_bytes(-self.sent)
            self.sent = 0
        return super().seek(pos, whence)


def cells(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1


def fit(text, columns):
    """Cut text so it occupies at most `columns` terminal cells (Chinese and emoji take two)."""
    used, out = 0, []
    for char in text:
        width = cells(char)
        if used + width > columns:
            break
        used += width
        out.append(char)
    return ''.join(out)


class Progress:
    """One-line progress bar on a terminal; on a pipe it prints a short line now and then instead."""

    def __init__(self, label, total, size=False, enabled=True, out=None):
        self.label, self.total, self.size, self.enabled = label, max(total, 1), size, enabled
        self.out = out or sys.stdout
        self.tty = self.out.isatty()
        self.start = self.last = time.time()
        self.note = ''

    def amount(self, value):
        return human(value) if self.size else str(value)

    def update(self, done, note=''):
        if not self.enabled:
            return
        now = time.time()
        finished = done >= self.total
        if not finished and now - self.last < (0.1 if self.tty else 15):
            return
        self.last = now
        done = max(0, min(done, self.total))
        ratio = done / self.total
        elapsed = max(now - self.start, 1e-6)
        rate = done / elapsed
        left = (self.total - done) / rate if rate and done else None
        tail = ''
        if self.size and done:
            tail = '  %s/s' % human(rate)
        if left is not None and not finished:
            tail += '  剩余 %d:%02d' % divmod(int(left), 60)
        if self.tty:
            columns = shutil.get_terminal_size((100, 20)).columns
            width = max(8, min(24, columns - 56))
            filled = int(width * ratio)
            arrow = '>' if filled < width else '='
            bar = ('=' * max(filled - 1, 0) + (arrow if filled else '')).ljust(width)
            line = '%s [%s] %3d%%  %s/%s%s  %s' % (self.label, bar, ratio * 100,
                                                  self.amount(done), self.amount(self.total), tail, note)
            self.out.write('\r' + fit(line, columns - 2) + '\x1b[K')
        else:
            self.out.write('%s %3d%%  %s/%s%s\n' % (self.label, ratio * 100, self.amount(done), self.amount(self.total), tail))
        self.out.flush()

    def finish(self):
        if self.enabled and self.tty:
            self.out.write('\n')
            self.out.flush()


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
    skills = not getattr(args, 'no_skills', False)
    save_config(server, user, skills)
    print('已保存：%s @ %s（服务器上已有 %d 个会话文件）%s' % (
        user, server, status.get('files', 0), '' if skills else '；以后同步不再上传 Skill'))
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


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def drop_known(args, client, need, dry=False):
    """Hash what looks new or changed and let the server say which of it it already holds (same content under another name or timestamp)."""
    if not need:
        return need, 0
    bar = Progress('核对内容是否重复', len(need), enabled=not args.quiet)
    for number, r in enumerate(need, 1):
        try:
            r['h'] = sha256_of(r['abs'])
        except OSError:
            r['h'] = ''
        bar.update(number)
    bar.finish()
    keep = set()
    try:
        for start in range(0, len(need), 500):
            chunk = need[start:start + 500]
            reply = client.post('/api/sync/have', {'files': [{'s': r['s'], 'rel': r['rel'], 'size': r['size'], 'mt': r['mt'], 'h': r['h']} for r in chunk],
                                                   'dry': dry})
            keep.update(start + i for i in reply.get('need', []))
    except SyncError as exc:
        say(args, '服务器不支持内容核对，按文件时间上传：%s' % exc)
        return need, 0
    kept = [r for i, r in enumerate(need) if i in keep]
    return kept, len(need) - len(kept)


def plan(args, client, home):
    files, skipped = sync_scan.scan(home, args.days)
    return client.diff(files), len(files), skipped


def cmd_run(args):
    cfg = load_config()
    client = Client(cfg['server'], cfg['user'])
    auto_update(args, client, cfg)
    home = Path(os.path.expanduser(args.history_home)) if args.history_home else Path.home()
    need, total, skipped = plan(args, client, home)
    need, same = drop_known(args, client, need, args.dry_run)
    size = sum(r['size'] for r in need)
    say(args, '本机共 %d 个会话文件，需要上传 %d 个（%s）%s%s' % (
        total, len(need), human(size), '，另有 %d 个超过 128 MB 的已跳过' % len(skipped) if skipped else '',
        '，%d 个内容和服务器上已有的相同，不重复上传' % same if same else ''))
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
    show = not args.quiet
    projects = {}
    bar = Progress('读取工作目录', len(need), enabled=show)
    for number, r in enumerate(need, 1):
        projects[r['abs']] = sync_scan.peek_project(r['s'], r['abs'])
        bar.update(number)
    bar.finish()
    skills = 0 if getattr(args, 'no_skills', False) or cfg.get('skills') is False else skills_phase(args, client, need, home)
    failed = done = 0
    bar = Progress('上传', size, size=True, enabled=show)
    sent = [0]

    def count(n):
        sent[0] += n
        bar.update(sent[0], current[0])
    current = ['']
    try:
        for number, r in enumerate(need, 1):
            current[0] = '(%d/%d) %s' % (number, len(need), r['rel'].rsplit('/', 1)[-1][:28])
            try:
                client.upload(r, projects.get(r['abs'], ''), count)
                done += 1
            except SyncError as exc:
                failed += 1
                bar.finish()
                say(args, '失败：%s/%s：%s' % (r['s'], r['rel'], exc))
            sent[0] = sum(x['size'] for x in need[:number])
            bar.update(sent[0], current[0])
    except KeyboardInterrupt:
        bar.finish()
        say(args, '已中断：本次已上传 %d 个，下次运行只会传剩下的。' % done)
        return 130
    bar.finish()
    say(args, '完成：会话 %d 个成功、%d 个失败；Skill 文件 %d 个。' % (done, failed, skills))
    return 1 if failed else 0


def skills_phase(args, client, need, home):
    bar = Progress('查找读过的 Skill', len(need), enabled=not args.quiet)
    dirs = sync_scan.skill_dirs_in([r['abs'] for r in need], home, lambda n, total: bar.update(n))
    bar.finish()
    rows, entries = [], []
    for directory in (d for d in dirs.values() if sync_scan.own_skill(d)):
        rows += sync_scan.skill_files(directory)
        entries.append({'key': sync_scan.skill_key(directory), 'origin': str(directory), 'name': directory.name})
    if not rows:
        return 0
    say(args, '会话里读过 %d 个 Skill 目录：%s' % (len(entries), '、'.join(sorted(e['name'] for e in entries))[:300]))
    missing = client.diff(rows)
    bar = Progress('上传 Skill 文件', len(missing), enabled=not args.quiet)
    for number, r in enumerate(missing, 1):
        client.upload(r)
        bar.update(number)
    bar.finish()
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
