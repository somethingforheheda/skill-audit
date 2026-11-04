import json
import os
import re
import shutil
import threading
import time
import uuid

import config

LOCK = threading.Lock()
ID_RE = re.compile(r'^[0-9a-f]{12}$')
MB = 1024 * 1024
CHUNK = 256 * 1024


class JobError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def job_dir(jid):
    return config.JOBS / jid


def _meta_path(jid):
    return job_dir(jid) / 'job.json'


def get(jid):
    if not ID_RE.match(jid or ''):
        return None
    try:
        return json.loads(_meta_path(jid).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def save(meta):
    path = _meta_path(meta['id'])
    tmp = path.with_name('job.json.tmp')
    meta['updated'] = int(time.time())
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    os.replace(str(tmp), str(path))


def update(jid, **fields):
    with LOCK:
        meta = get(jid)
        if meta:
            meta.update(fields)
            save(meta)
        return meta


def list_jobs(limit=200):
    rows = []
    if config.JOBS.is_dir():
        for entry in config.JOBS.iterdir():
            meta = get(entry.name)
            if meta:
                rows.append(meta)
    rows.sort(key=lambda m: m['created'], reverse=True)
    return rows[:limit]


def _clamp(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def create(user, options):
    jid = uuid.uuid4().hex[:12]
    job_dir(jid).mkdir(parents=True)
    now = int(time.time())
    meta = {'id': jid, 'user': user, 'created': now, 'updated': now, 'status': 'uploading',
            'limit': _clamp(options.get('limit'), 1, 50, 20),
            'max_episodes': _clamp(options.get('max_episodes'), 1, config.MAX_EPISODES, config.MAX_EPISODES),
            'sources': {}, 'files': 0, 'bytes': 0, 'message': ''}
    save(meta)
    return meta


def owned(jid, user):
    meta = get(jid)
    if not meta:
        raise JobError('任务不存在', 404)
    if meta['user'] != user:
        raise JobError('只能操作自己的任务', 403)
    return meta


def safe_parts(rel):
    if not isinstance(rel, str) or not rel or len(rel) > 400 or '\\' in rel or '\0' in rel:
        return None
    parts = rel.split('/')
    if any(p in ('', '.', '..') or len(p) > 200 for p in parts) or len(parts) > 12:
        return None
    return parts


def save_file(jid, user, source, rel, reader, length):
    meta = owned(jid, user)
    if meta['status'] != 'uploading':
        raise JobError('任务已提交，不能再上传', 409)
    if source not in config.SOURCES:
        raise JobError('未知来源', 400)
    base, accept = config.SOURCES[source]
    parts = safe_parts(rel)
    if not parts or not accept(parts[-1]):
        raise JobError('不接受该文件', 400)
    if length <= 0 or length > config.MAX_FILE_MB * MB:
        raise JobError('文件为空或超过 %d MB' % config.MAX_FILE_MB, 413)
    if shutil.disk_usage(str(config.DATA)).free < config.MIN_FREE_GB * 1024 * MB:
        raise JobError('服务器磁盘空间不足', 507)
    with LOCK:
        meta = get(jid)
        if meta['bytes'] + length > config.MAX_JOB_MB * MB:
            raise JobError('本次上传总量超过 %d MB' % config.MAX_JOB_MB, 413)
        meta['bytes'] += length
        save(meta)
    home = job_dir(jid) / 'home'
    target = home.joinpath(*base.split('/'), *parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.part')
    left = length
    try:
        with open(str(tmp), 'wb') as out:
            while left:
                block = reader.read(min(CHUNK, left))
                if not block:
                    raise JobError('上传中断', 400)
                out.write(block)
                left -= len(block)
        os.replace(str(tmp), str(target))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        with LOCK:
            meta = get(jid)
            meta['bytes'] -= length
            save(meta)
        raise
    with LOCK:
        meta = get(jid)
        meta['files'] += 1
        meta['sources'][source] = meta['sources'].get(source, 0) + 1
        save(meta)
    return meta
