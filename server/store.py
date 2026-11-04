import json
import os
import re
import shutil
import threading
import time

import auth
import config
import jobs

LOCK = threading.Lock()
CACHE = {}
MB = 1024 * 1024
SKILL_EXT = ('.md', '.txt', '.json', '.yaml', '.yml', '.py', '.sh', '.js', '.ts', '.toml', '.csv')
SKILL_MAX = 256 * 1024
KEY_RE = re.compile(r'^[A-Za-z0-9._-]{1,80}@[0-9a-f]{8}$')


def user_dir(user):
    return config.DATA / 'users' / user


def _journal(user):
    return user_dir(user) / 'journal.jsonl'


def state(user):
    if not auth.valid_name(user):
        raise jobs.JobError('用户名不合法', 400)
    with LOCK:
        if user not in CACHE:
            st = {'files': {}, 'skills': {}, 'bytes': 0, 'last': 0}
            try:
                with open(str(_journal(user)), encoding='utf-8') as handle:
                    for line in handle:
                        try:
                            apply(st, json.loads(line))
                        except ValueError:
                            continue
            except OSError:
                pass
            CACHE[user] = st
        return CACHE[user]


def apply(st, row):
    if 'k' in row:
        old = st['files'].get(row['k'])
        st['bytes'] += row['s'] - (old['s'] if old else 0)
        st['files'][row['k']] = {'s': row['s'], 'm': row['m'], 'p': row.get('p', ''), 't': row.get('t', 0)}
    elif 'skill' in row:
        st['skills'][row['skill']] = {'origin': row.get('origin', ''), 'name': row.get('name', '')}
    st['last'] = max(st['last'], row.get('t', 0))


def append(user, row):
    row['t'] = int(time.time())
    st = state(user)
    with LOCK:
        user_dir(user).mkdir(parents=True, exist_ok=True)
        with open(str(_journal(user)), 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        apply(st, row)


def check(source, rel):
    """Return the storage subpath for an upload, or None when it must be refused."""
    parts = jobs.safe_parts(rel)
    if not parts:
        return None
    if source == 'skill':
        if len(parts) < 2 or not KEY_RE.match(parts[0]) or not parts[-1].lower().endswith(SKILL_EXT):
            return None
        return ('skills',) + tuple(parts)
    if source in config.SOURCES and config.SOURCES[source][1](parts[-1]):
        return ('sessions', source) + tuple(parts)
    return None


def key_of(source, rel):
    return source + '/' + rel


def diff(user, entries):
    st = state(user)['files']
    need, bad = [], []
    for i, e in enumerate(entries):
        try:
            source, rel, size, mt = e['s'], e['rel'], int(e['size']), int(e['mt'])
        except (KeyError, TypeError, ValueError):
            bad.append(i)
            continue
        if not check(source, rel) or size <= 0:
            bad.append(i)
            continue
        have = st.get(key_of(source, rel))
        if not have or have['s'] != size or have['m'] != mt:
            need.append(i)
    return {'need': need, 'bad': bad}


def store_file(user, source, rel, mt, proj, reader, length):
    sub = check(source, rel)
    if not sub:
        raise jobs.JobError('不接受该文件', 400)
    cap = SKILL_MAX if source == 'skill' else config.MAX_FILE_MB * MB
    if length <= 0 or length > cap:
        raise jobs.JobError('文件为空或过大', 413)
    st = state(user)
    old = st['files'].get(key_of(source, rel))
    if st['bytes'] + length - (old['s'] if old else 0) > config.USER_QUOTA_GB * 1024 * MB:
        raise jobs.JobError('已超过个人存储上限 %d GB' % config.USER_QUOTA_GB, 413)
    free = shutil.disk_usage(str(config.DATA)).free
    if free < config.MIN_FREE_GB * 1024 * MB:
        raise jobs.JobError('服务器磁盘空间不足', 507)
    target = user_dir(user).joinpath(*sub)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.part')
    left = length
    try:
        with open(str(tmp), 'wb') as out:
            while left:
                block = reader.read(min(jobs.CHUNK, left))
                if not block:
                    raise jobs.JobError('上传中断', 400)
                out.write(block)
                left -= len(block)
        os.replace(str(tmp), str(target))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    append(user, {'k': key_of(source, rel), 's': length, 'm': mt, 'p': proj[:300]})


def put_skills(user, entries):
    count = 0
    for e in entries[:500]:
        try:
            key, origin, name = str(e['key']), str(e.get('origin', ''))[:400], str(e.get('name', ''))[:120]
        except (KeyError, TypeError):
            continue
        if KEY_RE.match(key):
            append(user, {'skill': key, 'origin': origin, 'name': name})
            count += 1
    return count
