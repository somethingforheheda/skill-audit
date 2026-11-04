import json
import os
import shutil
import signal
import subprocess
import threading
import time

import config
import browse
import jobs

KEEP_ENV = ('ANTHROPIC_', 'CLAUDE_CODE_', 'OPENAI_', 'CODEX_')


def start(jid, user):
    meta = jobs.owned(jid, user)
    if meta['status'] != 'uploading':
        raise jobs.JobError('任务已提交', 409)
    if not meta['files']:
        raise jobs.JobError('还没有上传任何会话文件', 400)
    return jobs.update(jid, status='queued', message='排队中', queued=int(time.time()))


def delete(jid, user):
    meta = jobs.owned(jid, user)
    if meta['status'] == 'running':
        raise jobs.JobError('任务正在运行，结束后才能删除', 409)
    shutil.rmtree(str(jobs.job_dir(jid)), ignore_errors=True)


def create_from_store(user, options):
    meta = jobs.create(user, options)
    jid = meta['id']
    project = str(options.get('project') or '*')[:200]
    days = jobs._clamp(options.get('days'), 1, 3650, 0) if options.get('days') else None
    count, sources, size = browse.build_home(jid, user, project, days)
    if not count:
        shutil.rmtree(str(jobs.job_dir(jid)), ignore_errors=True)
        raise jobs.JobError('没有符合条件的已同步会话', 400)
    catalog = browse.skills_dir(user)
    return jobs.update(jid, status='queued', message='排队中', queued=int(time.time()), sources=sources,
                       files=count, bytes=size, project=project, days=days or 0,
                       catalog=str(catalog) if catalog else '')


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


def build_env():
    extra = load_env()
    prepare_home(extra)
    env = dict(os.environ)
    env.update(extra)
    env['HOME'] = str(config.RUNNER_HOME)
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
    if config.MODEL:
        cmd += ['--model', config.MODEL]
    if config.SUBAGENT_MODEL:
        cmd += ['--subagent-model', config.SUBAGENT_MODEL]
    if meta.get('catalog'):
        cmd += ['--skills-catalog', meta['catalog']]
    return cmd


def _stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        time.sleep(3)
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def run_job(jid):
    meta = jobs.update(jid, status='running', message='分析中', started=int(time.time()))
    out = report_dir(jid)
    problem = ''
    try:
        cmd = build_command(meta, out)
        with open(str(jobs.job_dir(jid) / 'run.log'), 'wb') as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(jobs.job_dir(jid)),
                                    env=build_env(), start_new_session=True)
            deadline = time.time() + config.JOB_TIMEOUT
            while proc.poll() is None:
                if time.time() > deadline:
                    _stop(proc)
                    problem = '超过 %d 分钟，已终止' % (config.JOB_TIMEOUT // 60)
                    break
                time.sleep(2)
            code = proc.wait()
        if not problem and code != 0:
            problem = '分析进程退出码 %s' % code
        if not problem and not (out / 'report.html').is_file():
            problem = '没有生成报告'
    except Exception as exc:
        problem = '启动失败：%s' % exc
    if not config.KEEP_UPLOADS:
        shutil.rmtree(str(jobs.job_dir(jid) / 'home'), ignore_errors=True)
    if problem:
        tail = tail_log(jid, 3).replace('\n', ' | ')
        jobs.update(jid, status='failed', message=(problem + '：' + tail)[:400], finished=int(time.time()))
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
