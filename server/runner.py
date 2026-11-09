import json
import os
import re
import socket
import shutil
import signal
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import auth
import config
import browse
import jobs
import store

KEEP_ENV = ('ANTHROPIC_', 'CLAUDE_CODE_', 'OPENAI_', 'CODEX_')


def need_model(meta):
    if not (meta.get('model') or config.MODEL):
        raise jobs.JobError('请先选择或填写模型名（服务器没有默认模型）', 400)


def start(jid, user):
    meta = jobs.owned(jid, user)
    if meta['status'] != 'uploading':
        raise jobs.JobError('任务已提交', 409)
    if not meta['files']:
        raise jobs.JobError('还没有上传任何会话文件', 400)
    need_model(meta)
    return jobs.update(jid, status='queued', message='排队中', queued=int(time.time()))


RUNNING = {}
CANCELLED = set()
KEYS = {}
KEY_RE = re.compile(r'^[\x21-\x7e]{8,400}$')


def key_required():
    return config.USER_KEY == 'required' and config.KEY_VAR not in load_env()


def pop_key(options):
    key = str(options.pop('api_key', '') or '').strip()
    if key and not KEY_RE.match(key):
        raise jobs.JobError('API Key 格式不对：长度 8–400，不能含空格或中文', 400)
    if not key and key_required():
        raise jobs.JobError('请先填写你的 API Key', 400)
    return key


CA_FILES = ('/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/certs/ca-certificates.crt')


def _context():
    for path in CA_FILES:
        if os.path.isfile(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


def check_gateway(base):
    """With SA_GATEWAY_HOSTS set, the user's key may only ever go to an https gateway on that list."""
    if not config.GATEWAY_HOSTS:
        return
    parsed = urlparse(base or '')
    if parsed.scheme != 'https' or parsed.hostname not in config.GATEWAY_HOSTS:
        raise jobs.JobError('服务器配置的模型网关（%s）不在允许列表（%s）内，已拒绝发送你的 API Key，请联系管理员' % (
            parsed.hostname or '未配置', '、'.join(config.GATEWAY_HOSTS)), 503)


PREFLIGHT_SECONDS = 30


def preflight(key, model):
    """One tiny request so a bad key or model name fails at once, not after ten minutes of retries."""
    env = load_env()
    base = env.get('ANTHROPIC_BASE_URL', '').rstrip('/')
    check_gateway(base)
    token = key or env.get(config.KEY_VAR, '')
    name = re.sub(r'\[1m\]$', '', str(model or config.MODEL or '').strip(), flags=re.I)
    if not (base and token and name):
        return
    body = json.dumps({'model': name, 'max_tokens': 1, 'messages': [{'role': 'user', 'content': 'hi'}]}).encode('utf-8')
    request = urllib.request.Request(base + '/v1/messages', data=body, method='POST', headers={
        'Authorization': 'Bearer ' + token, 'x-api-key': token, 'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json'})
    for _ in range(2):
        try:
            urllib.request.urlopen(request, timeout=PREFLIGHT_SECONDS, context=_context()).read()
            return
        except urllib.error.HTTPError as exc:
            text = exc.read(2000).decode('utf-8', 'replace')
            try:
                text = json.loads(text)['error']['message']
            except (ValueError, KeyError, TypeError):
                pass
            text = re.sub(r'\s*\(request id:[^)]*\)', '', str(text))[:160]
            if exc.code in (401, 403):
                raise jobs.JobError('API Key 无效或没有权限（网关返回：%s）' % text, 400)
            raise jobs.JobError('网关拒绝了请求（%d）：%s。请检查模型名「%s」和 API Key' % (exc.code, text, name), 400)
        except socket.timeout:
            continue
        except (urllib.error.URLError, OSError) as exc:
            raise jobs.JobError('连不上模型网关：%s' % getattr(exc, 'reason', exc), 502)
    raise jobs.JobError('网关连上了，但模型「%s」在 %d 秒内没有回应（试了 2 次）。可能这个模型现在很忙或不可用，换一个模型，或稍后再试' % (name, PREFLIGHT_SECONDS), 502)


def keep_key(jid, key):
    if key:
        with jobs.LOCK:
            KEYS[jid] = key


def new_job(user, options):
    key = pop_key(options)
    preflight(key, options.get('model'))
    meta = jobs.create(user, options)
    keep_key(meta['id'], key)
    return meta


def cancel(jid, user):
    jobs.owned(jid, user)
    with jobs.LOCK:
        meta = jobs.get(jid)
        if meta['status'] == 'queued':
            meta.update(status='cancelled', message='已取消', finished=int(time.time()))
            jobs.save(meta)
            KEYS.pop(jid, None)
            shutil.rmtree(str(jobs.job_dir(jid) / 'home'), ignore_errors=True)
            return meta
        if meta['status'] != 'running':
            raise jobs.JobError('任务不在排队或运行中', 409)
        CANCELLED.add(jid)
        proc = RUNNING.get(jid)
    if proc is not None:
        _stop(proc)
    return jobs.get(jid)


def delete(jid, user):
    meta = jobs.owned(jid, user)
    if meta['status'] == 'running':
        raise jobs.JobError('任务正在运行，结束后才能删除', 409)
    KEYS.pop(jid, None)
    shutil.rmtree(str(jobs.job_dir(jid)), ignore_errors=True)


def create_from_store(user, options):
    everyone = bool(options.get('all_users'))
    if everyone and not auth.is_admin(user):
        raise jobs.JobError('只有管理员可以用所有人的会话做分析', 403)
    key = pop_key(options)
    jobs.clean_options(options)
    need_model({'model': options.get('model')})
    preflight(key, options.get('model'))
    meta = jobs.create(user, options)
    jid = meta['id']
    project = str(options.get('project') or '*')[:200]
    days = jobs._clamp(options.get('days'), 1, 3650, 0) if options.get('days') else None
    items = browse.select_all(project, days) if everyone else [(user, k) for k in browse.select(user, project, days)]
    count, sources, size = browse.link_sessions(jid, items)
    if not count:
        shutil.rmtree(str(jobs.job_dir(jid)), ignore_errors=True)
        raise jobs.JobError('没有符合条件的已同步会话', 400)
    if everyone:
        rows = [{'user': u, 'source': k.split('/', 1)[0], 'rel': k.split('/', 1)[1], 'project': store.state(u)['files'][k]['p'],
                 'size': store.state(u)['files'][k]['s']} for u, k in items]
        (jobs.job_dir(jid) / 'owners.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    keep_key(jid, key)
    catalog, roots = browse.job_catalog(jid, sorted({u for u, _ in items}), options.get('project') or None, days)
    return jobs.update(jid, status='queued', message='排队中', queued=int(time.time()), sources=sources,
                       files=count, bytes=size, project=project, days=days or 0, catalog=str(catalog), skills_roots=roots,
                       all_users=everyone)


def tail_log(jid, lines=14):
    path = jobs.job_dir(jid) / 'run.log'
    try:
        size = path.stat().st_size
        with open(str(path), 'rb') as handle:
            handle.seek(max(0, size - 24000))
            text = handle.read().decode('utf-8', 'replace')
    except OSError:
        return ''
    rows = [row.strip() for row in text.replace('\r', '\n').split('\n') if row.strip()]
    return '\n'.join(rows[-lines:])


HINTS = (
    ('ducc login', '服务器上的 claude 要求先登录公司账号，需要管理员处理'),
    ('无效的令牌', 'API Key 无效或已过期，请到「设置」检查'),
    ('Invalid API key', 'API Key 无效，请到「设置」检查'),
    ('unrecognized_model', '网关不认这个模型名，请到「设置」核对'),
)


def explain(text):
    for needle, hint in HINTS:
        if needle in text:
            return hint + '。'
    return ''


def short_tail(jid, lines=4):
    seen, rows = set(), []
    for row in reversed(tail_log(jid, 40).split('\n')):
        if row not in seen:
            seen.add(row)
            rows.append(row)
        if len(rows) >= lines:
            break
    return ' | '.join(reversed(rows))


def report_dir(jid):
    return jobs.job_dir(jid) / 'report'


def load_env():
    values = {}
    if config.ENV_FILE and os.path.isfile(config.ENV_FILE):
        for line in open(config.ENV_FILE, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                key = key.strip()
                if key.startswith(KEEP_ENV):
                    values[key] = value.strip().strip('"\'')
    return values


def prepare_home(extra):
    settings = config.RUNNER_HOME / '.claude' / 'settings.json'
    settings.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(settings), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump({'env': {k: v for k, v in extra.items() if k.startswith('ANTHROPIC_')}}, handle)
    tool = config.RUNNER_HOME / '.skill-audit'
    tool.mkdir(parents=True, exist_ok=True)
    (tool / 'config.json').write_text(json.dumps({'trusted_gateways': config.GATEWAY_HOSTS} if config.GATEWAY_HOSTS else {}),
                                      encoding='utf-8')


def build_env(key=''):
    extra = load_env()
    if key:
        extra.pop(config.KEY_VAR, None)
    prepare_home(extra)
    env = dict(os.environ)
    env.update(extra)
    if key:
        env[config.KEY_VAR] = key
    env['HOME'] = str(config.RUNNER_HOME)
    env['SKILL_AUDIT_HOME'] = str(config.RUNNER_HOME / '.skill-audit')
    for name in ('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'DISABLE_AUTOUPDATER', 'DISABLE_BUG_COMMAND'):
        env.setdefault(name, '1')
    env['PYTHONUNBUFFERED'] = '1'
    if config.EXTRA_PATH:
        env['PATH'] = config.EXTRA_PATH + os.pathsep + env.get('PATH', '')
    return env


def build_command(meta, out):
    home = jobs.job_dir(meta['id']) / 'home'
    cmd = list(config.AUDIT_CMD) + ['history', '--all-projects', '--history-home', str(home),
                                    '--sources', ','.join(sorted(meta['sources'])),
                                    '--limit', str(meta['limit']), '--max-episodes', str(meta['max_episodes']),
                                    '--workers', str(config.WORKERS), '--backend', config.BACKEND,
                                    '--output', str(out)]
    model = meta.get('model') or config.MODEL
    if model:
        cmd += ['--model', model]
    sub = meta.get('subagent_model') or (config.SUBAGENT_MODEL if not meta.get('model') else '')
    if sub:
        cmd += ['--subagent-model', sub]
    if meta.get('effort'):
        cmd += ['--effort', meta['effort']]
    if meta.get('catalog'):
        cmd += ['--skills-catalog', meta['catalog']]
    for root in meta.get('skills_roots') or []:
        cmd += ['--skills-root', root]
    return cmd


def _stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(30):
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def claim(jid):
    with jobs.LOCK:
        meta = jobs.get(jid)
        if not meta or meta['status'] != 'queued':
            return None
        meta.update(status='running', message='分析中', started=int(time.time()))
        jobs.save(meta)
        return meta


def run_job(jid):
    meta = claim(jid)
    if not meta:
        return
    out = report_dir(jid)
    problem = ''
    with jobs.LOCK:
        key = KEYS.pop(jid, '')
    try:
        if not key and key_required():
            raise RuntimeError('API Key 已丢失（服务重启过），请重新提交')
        check_gateway(load_env().get('ANTHROPIC_BASE_URL', ''))
        cmd = build_command(meta, out)
        with open(str(jobs.job_dir(jid) / 'run.log'), 'wb') as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(jobs.job_dir(jid)),
                                    env=build_env(key), start_new_session=True)
            with jobs.LOCK:
                RUNNING[jid] = proc
                asked = jid in CANCELLED
            if asked:
                _stop(proc)
            deadline = time.time() + config.JOB_TIMEOUT
            while proc.poll() is None:
                if time.time() > deadline:
                    _stop(proc)
                    problem = '超过 %d 分钟，已终止' % (config.JOB_TIMEOUT // 60)
                    break
                time.sleep(1)
            code = proc.wait()
        if not problem and code != 0:
            problem = '分析进程退出码 %s' % code
        if not problem and not (out / 'report.html').is_file():
            problem = '没有生成报告'
    except Exception as exc:
        problem = '启动失败：%s' % exc
    with jobs.LOCK:
        RUNNING.pop(jid, None)
        cancelled = jid in CANCELLED
        CANCELLED.discard(jid)
    if not config.KEEP_UPLOADS:
        shutil.rmtree(str(jobs.job_dir(jid) / 'home'), ignore_errors=True)
    if cancelled:
        jobs.update(jid, status='cancelled', message='已取消', finished=int(time.time()))
    elif problem:
        tail = short_tail(jid)
        jobs.update(jid, status='failed', message=(explain(tail_log(jid, 40)) + problem + '：' + tail)[:400],
                    finished=int(time.time()))
    else:
        jobs.update(jid, status='done', message='完成', finished=int(time.time()))


def next_queued():
    waiting = [m for m in jobs.list_jobs(1000) if m['status'] == 'queued']
    waiting.sort(key=lambda m: m.get('queued', m['created']))
    return waiting[0]['id'] if waiting else None


def recover():
    for meta in jobs.list_jobs(1000):
        if meta['status'] == 'running':
            jobs.update(meta['id'], status='failed', message='服务重启，任务被中断，请重新提交',
                        finished=int(time.time()))


def worker():
    while True:
        jid = next_queued()
        if jid:
            run_job(jid)
        else:
            time.sleep(3)


def start_worker():
    recover()
    thread = threading.Thread(target=worker, daemon=True, name='job-worker')
    thread.start()
    return thread
