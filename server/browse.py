import os
import shutil
import time

import config
import store


def project_name(path):
    name = os.path.basename((path or '').rstrip('/'))
    return name or '（未知目录）'


def is_session(key):
    return not key.startswith('skill/')


def summary(user, with_projects=True):
    st = store.state(user)
    sources, projects, files, size = {}, {}, 0, 0
    for key, row in st['files'].items():
        if not is_session(key):
            continue
        source = key.split('/', 1)[0]
        files += 1
        size += row['s']
        sources[source] = sources.get(source, 0) + 1
        if with_projects:
            p = projects.setdefault(project_name(row['p']), {'name': project_name(row['p']), 'files': 0, 'bytes': 0,
                                                              'last': 0, 'sources': {}, 'paths': set()})
            p['files'] += 1
            p['bytes'] += row['s']
            p['last'] = max(p['last'], row['m'])
            p['sources'][source] = p['sources'].get(source, 0) + 1
            if row['p']:
                p['paths'].add(row['p'])
    out = {'user': user, 'files': files, 'bytes': size, 'last': st['last'], 'sources': sources,
           'skills': len(st['skills'])}
    if with_projects:
        rows = sorted(projects.values(), key=lambda p: -p['last'])
        for p in rows:
            p['paths'] = sorted(p['paths'])[:3]
        out['projects'] = rows
    return out


def list_users():
    root = config.DATA / 'users'
    rows = []
    if root.is_dir():
        for entry in root.iterdir():
            if (entry / 'journal.jsonl').is_file():
                try:
                    rows.append(summary(entry.name, False))
                except Exception:
                    continue
    rows.sort(key=lambda r: -r['last'])
    return rows


def select(user, project=None, days=None):
    st = store.state(user)['files']
    since = time.time() - days * 86400 if days else 0
    chosen = []
    for key, row in st.items():
        if not is_session(key) or row['m'] < since:
            continue
        if project and project != '*' and project_name(row['p']) != project:
            continue
        chosen.append(key)
    return chosen


def build_home(jid, user, project=None, days=None):
    import jobs
    home = jobs.job_dir(jid) / 'home'
    base = store.user_dir(user) / 'sessions'
    sources = {}
    count = size = 0
    st = store.state(user)['files']
    for key in select(user, project, days):
        source, rel = key.split('/', 1)
        src = base / source / rel
        if not src.is_file():
            continue
        dst = home.joinpath(*config.SOURCES[source][0].split('/'), *rel.split('/'))
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(str(src), str(dst))
        except OSError:
            shutil.copy2(str(src), str(dst))
        sources[source] = sources.get(source, 0) + 1
        count += 1
        size += st[key]['s']
    return count, sources, size


def skills_dir(user):
    path = store.user_dir(user) / 'skills'
    return path if path.is_dir() else None
