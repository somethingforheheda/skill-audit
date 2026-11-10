import hashlib
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
            st = {'files': {}, 'skills': {}, 'bytes': 0, 'last': 0, 'hashes': {}}
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
        if old:
            st['bytes'] -= old.get('b', old['s'])
            if old.get('h') and st['hashes'].get(old['h']) == row['k']:
                del st['hashes'][old['h']]
        held = 0 if row.get('dup') else row['s']
        st['bytes'] += held
        entry = {'s': row['s'], 'm': row['m'], 'p': row.get('p', ''), 't': row.get('t', 0), 'b': held}
        if row.get('h'):
            entry['h'] = row['h']
            if not row.get('dup'):
                st['hashes'][row['h']] = row['k']
        if row.get('dup'):
            entry['dup'] = row['dup']
        st['files'][row['k']] = entry
    elif 'skill' in row:
        st['skills'][row['skill']] = {'origin': row.get('origin', ''), 'name': row.get('name', '')}
    st['last'] = max(st['last'], row.get('t', 0))


def append(user, row):
    row['t'] = int(time.time())
    row['u'] = user
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
    key = key_of(source, rel)
    old = st['files'].get(key)
    if st['bytes'] + length - (old.get('b', old['s']) if old else 0) > config.USER_QUOTA_GB * 1024 * MB:
        raise jobs.JobError('已超过个人存储上限 %d GB' % config.USER_QUOTA_GB, 413)
    free = shutil.disk_usage(str(config.DATA)).free
    if free < config.MIN_FREE_GB * 1024 * MB:
        raise jobs.JobError('服务器磁盘空间不足', 507)
    target = user_dir(user).joinpath(*sub)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.part')
    left = length
    digest = hashlib.sha256()
    try:
        with open(str(tmp), 'wb') as out:
            while left:
                block = reader.read(min(jobs.CHUNK, left))
                if not block:
                    raise jobs.JobError('上传中断', 400)
                out.write(block)
                digest.update(block)
                left -= len(block)
        content = digest.hexdigest()
        row = {'k': key, 's': length, 'm': mt, 'p': proj[:300], 'h': content}
        if source != 'skill':
            twin = st['hashes'].get(content)
            if twin and twin != key:
                tmp.unlink()
                return append(user, dict(row, dup=twin))
            if old and old.get('h') == content and not old.get('dup'):
                tmp.unlink()
                return append(user, row)
        os.replace(str(tmp), str(target))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    append(user, row)


def have(user, entries, record=True):
    """Which of the entries (with their SHA-256) still have to be uploaded; same content under another name is only noted."""
    st = state(user)
    need = []
    for i, e in enumerate(entries):
        try:
            source, rel, size, mt, content = e['s'], e['rel'], int(e['size']), int(e['mt']), str(e['h'])
        except (KeyError, TypeError, ValueError):
            need.append(i)
            continue
        key = key_of(source, rel)
        if not check(source, rel) or size <= 0 or source == 'skill' or not re.match(r'^[0-9a-f]{64}$', content):
            need.append(i)
            continue
        mine = st['files'].get(key)
        twin = st['hashes'].get(content)
        if mine and mine.get('h') == content and not mine.get('dup'):
            row = {'k': key, 's': size, 'm': mt, 'p': mine.get('p', ''), 'h': content}
        elif twin and twin != key:
            row = {'k': key, 's': size, 'm': mt, 'p': (mine or {}).get('p', ''), 'h': content, 'dup': twin}
        else:
            need.append(i)
            continue
        if record and (not mine or mine['s'] != size or mine['m'] != mt or mine.get('dup') != row.get('dup')):
            append(user, row)
    return {'need': need}


def resolve(user, key):
    row = state(user)['files'].get(key)
    return row['dup'] if row and row.get('dup') else key


def dedupe_user(user):
    """Hash what is already stored and mark later copies of the same content; no file is removed. Returns (hashed, duplicates)."""
    st = state(user)
    todo = sorted((k for k, r in st['files'].items() if 'h' not in r and not r.get('dup') and k.split('/', 1)[0] in config.SOURCES),
                  key=lambda k: (st['files'][k].get('t', 0), k))
    hashed = dupes = 0
    for key in todo:
        row = st['files'][key]
        source, rel = key.split('/', 1)
        path = user_dir(user).joinpath(*check(source, rel))
        try:
            digest = hashlib.sha256()
            with open(str(path), 'rb') as handle:
                for block in iter(lambda: handle.read(1 << 20), b''):
                    digest.update(block)
        except OSError:
            continue
        content = digest.hexdigest()
        twin = st['hashes'].get(content)
        out = {'k': key, 's': row['s'], 'm': row['m'], 'p': row.get('p', ''), 'h': content}
        if twin and twin != key:
            out['dup'] = twin
            dupes += 1
        append(user, out)
        hashed += 1
    return hashed, dupes


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
