import json
import os
import re
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
        if not is_session(key) or row.get('dup'):
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
           'skills': len(pick_skills([user], {row['p'].replace('\\', '/').rstrip('/') for key, row in st['files'].items()
                                            if is_session(key) and row['p']}))}
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
        if not is_session(key) or row['m'] < since or row.get('dup'):
            continue
        if project and project != '*' and project_name(row['p']) != project:
            continue
        chosen.append(key)
    return chosen


def link_sessions(jid, items):
    """Hard-link the chosen (user, key) sessions into the job's own history home."""
    import jobs
    home = jobs.job_dir(jid) / 'home'
    sources = {}
    count = size = 0
    for user, key in items:
        source, rel = key.split('/', 1)
        src = store.user_dir(user) / 'sessions' / source / rel
        dst = home.joinpath(*config.SOURCES[source][0].split('/'), *rel.split('/'))
        if not src.is_file() or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(str(src), str(dst))
        except OSError:
            shutil.copy2(str(src), str(dst))
        sources[source] = sources.get(source, 0) + 1
        count += 1
        size += store.state(user)['files'][key]['s']
    return count, sources, size


def build_home(jid, user, project=None, days=None):
    return link_sessions(jid, [(user, key) for key in select(user, project, days)])


def user_names():
    root = config.DATA / 'users'
    return sorted(e.name for e in root.iterdir() if (e / 'journal.jsonl').is_file()) if root.is_dir() else []


def select_all(project=None, days=None):
    return [(user, key) for user in user_names() for key in select(user, project, days)]


def session_rows(project=None, days=None):
    """Every synced session with the colleague it belongs to, newest first."""
    rows = []
    for user, key in select_all(project, days):
        row = store.state(user)['files'][key]
        rows.append({'user': user, 'source': key.split('/', 1)[0], 'rel': key.split('/', 1)[1], 'project': row['p'],
                     'size': row['s'], 'm': row['m']})
    rows.sort(key=lambda r: -r['m'])
    return rows


def summary_all():
    """Like summary() but over every colleague; each directory also says whose sessions are in it."""
    users = user_names()
    out = {'user': '*', 'files': 0, 'bytes': 0, 'last': 0, 'sources': {}, 'users': len(users)}
    projects = {}
    paths = set()
    for user in users:
        one = summary(user)
        out['files'] += one['files']
        out['bytes'] += one['bytes']
        out['last'] = max(out['last'], one['last'])
        for source, n in one['sources'].items():
            out['sources'][source] = out['sources'].get(source, 0) + n
        for p in one['projects']:
            row = projects.setdefault(p['name'], {'name': p['name'], 'files': 0, 'bytes': 0, 'last': 0, 'sources': {}, 'paths': [], 'owners': {}})
            row['files'] += p['files']
            row['bytes'] += p['bytes']
            row['last'] = max(row['last'], p['last'])
            row['owners'][user] = row['owners'].get(user, 0) + p['files']
            for source, n in p['sources'].items():
                row['sources'][source] = row['sources'].get(source, 0) + n
            row['paths'] = (row['paths'] + [x for x in p['paths'] if x not in row['paths']])[:3]
        for key, row in store.state(user)['files'].items():
            if is_session(key) and row['p']:
                paths.add(row['p'].replace('\\', '/').rstrip('/'))
    out['skills'] = len(pick_skills(users, paths))
    out['projects'] = sorted(projects.values(), key=lambda p: -p['last'])
    return out


GLOBAL_SKILLS = re.compile(r'^(?P<home>/root|(?:[A-Za-z]:)?/(?:Users|home)/[^/]+)/\.(?:agents|claude|codex)/skills/[^/]+$')
SKILLS_ROOT = re.compile(r'^(.*?)/\.(?:agents|claude|codex)/skills/')
HOME_DIR = re.compile(r'^(?:/root|(?:[A-Za-z]:)?/(?:Users|home)/[^/]+)$')


def skill_rank(origin, projects):
    """2: Skill kept in a repository that contains a selected working directory; 1: the user's own global Skill; 0: the rest (plugins, bundled system Skills)."""
    path = origin.replace('\\', '/')
    if GLOBAL_SKILLS.match(path):
        return 1
    found = SKILLS_ROOT.match(path)
    root = found.group(1) if found else ''
    if root and not HOME_DIR.match(root) and any(p == root or p.startswith(root + '/') for p in projects):
        return 2
    return 0


def skill_roots(origin):
    """Skill directories next to a Skill's home, so a symlinked .claude/skills path counts as the same place."""
    found = SKILLS_ROOT.match(origin.replace('\\', '/'))
    base = found.group(1) if found else ''
    return ['%s/.%s/skills' % (base, kind) for kind in ('agents', 'claude', 'codex')] if base else []


def pick_skills(users, projects):
    """name -> (user, key, origin): Skills kept in the repositories of the given working directories, else the users' global ones."""
    ranked = {}
    for user in users:
        for key, row in store.state(user)['skills'].items():
            rank = skill_rank(row.get('origin', ''), projects)
            if rank:
                ranked.setdefault(rank, []).append((row.get('name') or key, key, user, row.get('origin', '')))
    return {name: (user, key, origin) for name, key, user, origin in sorted(ranked.get(2) or ranked.get(1) or [])}


def job_catalog(jid, users, project=None, days=None, items=None):
    """Hard-linked copies of the Skills this job should look at, plus the directories reads are limited to."""
    import jobs
    projects = set()
    for user in users:
        files = store.state(user)['files']
        keys = [k for u, k in items if u == user] if items is not None else select(user, project, days)
        projects |= {files[k]['p'].replace('\\', '/').rstrip('/') for k in keys if files[k]['p']}
    best = pick_skills(users, projects)
    catalog = jobs.job_dir(jid) / 'catalog'
    catalog.mkdir(parents=True, exist_ok=True)
    origins, roots = {}, set()
    for user, key, origin in best.values():
        source = store.user_dir(user) / 'skills' / key
        for folder, _, names in os.walk(str(source)):
            target = catalog / key / os.path.relpath(folder, str(source))
            target.mkdir(parents=True, exist_ok=True)
            for name in names:
                try:
                    os.link(os.path.join(folder, name), str(target / name))
                except OSError:
                    shutil.copy2(os.path.join(folder, name), str(target / name))
        origins[key] = origin
        roots.update(skill_roots(origin))
    (catalog / '_origins.json').write_text(json.dumps(origins, ensure_ascii=False), encoding='utf-8')
    return catalog, sorted(roots)
