"""Local side of team sync: find AI session files and the Skills they read."""
import hashlib
import json
import os
import re
from pathlib import Path

MAX_SESSION = 128 * 1024 * 1024
SKILL_EXT = ('.md', '.txt', '.json', '.yaml', '.yml', '.py', '.sh', '.js', '.ts', '.toml', '.csv')
SKILL_FILE_MAX = 256 * 1024
SKILL_FILES_MAX = 200
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv'}


def roots(home):
    return {'claude': home / '.claude' / 'projects', 'codex': home / '.codex' / 'sessions',
            'comate': home / '.comate-engine' / 'store'}


def accepts(source, name):
    if source in ('claude', 'codex'):
        return name.endswith('.jsonl')
    return name.startswith('chat_session_') and not name.endswith('.deleted')


def scan(home, days=None, now=None):
    """Session files as dicts {s, rel, abs, size, mt}; oversized files are returned in `skipped`."""
    import time
    since = (now or time.time()) - days * 86400 if days else 0
    found, skipped = [], []
    for source, root in roots(home).items():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob('*') if source != 'comate' else root.glob('chat_session_*')):
            try:
                if not path.is_file() or not accepts(source, path.name):
                    continue
                info = path.stat()
            except OSError:
                continue
            if info.st_size == 0 or info.st_mtime < since:
                continue
            row = {'s': source, 'rel': path.relative_to(root).as_posix(), 'abs': str(path),
                   'size': info.st_size, 'mt': int(info.st_mtime)}
            (skipped if info.st_size > MAX_SESSION else found).append(row)
    return found, skipped


def peek_project(source, path, lines=300):
    """Working directory of a session, read from its first lines (cheap, no full parse)."""
    try:
        if source == 'comate':
            with open(path, encoding='utf-8', errors='replace') as handle:
                match = re.search(r'"workspaceDirectory"\s*:\s*"([^"]*)"', handle.read(4 * 1024 * 1024))
            return match.group(1) if match else ''
        with open(path, encoding='utf-8', errors='replace') as handle:
            for number, line in enumerate(handle):
                if number >= lines:
                    break
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                payload = item.get('payload') if isinstance(item.get('payload'), dict) else {}
                cwd = item.get('cwd') or payload.get('cwd')
                if cwd:
                    return str(cwd)
    except OSError:
        pass
    return ''


STOP = set(' \t\r\n"\'\\<>|*?,;()[]{}')
NEEDLE = 'SKILL.md'


def skill_paths_in(text):
    """Absolute or ~/ paths ending in SKILL.md, found by walking back from each occurrence (linear time)."""
    found, at = set(), text.find(NEEDLE)
    while at != -1:
        start = at
        while start > 0 and text[start - 1] not in STOP and at - start < 400:
            start -= 1
        candidate = text[start:at + len(NEEDLE)]
        if candidate.startswith(('/', '~/')) and candidate.endswith('/' + NEEDLE):
            found.add(candidate)
        at = text.find(NEEDLE, at + 1)
    return found


def skill_dirs_in(paths, home, tick=None):
    """Skill directories whose SKILL.md is mentioned in the given session files and still exists here."""
    found = {}
    for number, path in enumerate(paths, 1):
        if tick:
            tick(number, len(paths))
        try:
            with open(path, encoding='utf-8', errors='replace') as handle:
                text = handle.read()
        except OSError:
            continue
        if NEEDLE not in text:
            continue
        for raw in skill_paths_in(text):
            candidate = Path(os.path.expanduser(raw) if raw.startswith('~') else raw)
            try:
                if candidate.is_file():
                    found[str(candidate.parent.resolve())] = candidate.parent
            except OSError:
                continue
    return found


def skill_key(directory):
    digest = hashlib.sha1(str(directory).encode('utf-8')).hexdigest()[:8]
    name = re.sub(r'[^A-Za-z0-9._-]', '_', directory.name)[:60] or 'skill'
    return '%s@%s' % (name, digest)


def skill_files(directory):
    """Small text files of a Skill directory as dicts, bounded in count and size."""
    rows = []
    for path in sorted(directory.rglob('*')):
        if len(rows) >= SKILL_FILES_MAX:
            break
        try:
            relative = path.relative_to(directory)
            if (SKIP_DIRS & set(relative.parts)) or len(relative.parts) > 4 or not path.is_file():
                continue
            info = path.stat()
        except (OSError, ValueError):
            continue
        if path.name.lower().endswith(SKILL_EXT) and 0 < info.st_size <= SKILL_FILE_MAX:
            rows.append({'s': 'skill', 'rel': '%s/%s' % (skill_key(directory), relative.as_posix()), 'abs': str(path),
                         'size': info.st_size, 'mt': int(info.st_mtime)})
    return rows
