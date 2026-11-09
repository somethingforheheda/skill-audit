"""Shared lexical historical path scope. Roots form a union, never name matches."""
import os
from pathlib import Path


def normalize(values, project=None):
    base=Path(project).expanduser().absolute() if project else Path.cwd()
    roots=[]
    for value in values or []:
        path=Path(value).expanduser()
        if not path.is_absolute():path=base/path
        root=os.path.normpath(str(path))
        if root not in roots:roots.append(root)
    return sorted(roots)


def contains(path, roots):
    if not roots:return True
    if not isinstance(path,str) or not os.path.isabs(path):return False
    path=os.path.normpath(path)
    return any(os.path.commonpath([path,root])==root for root in roots)


def catalog_copy(path, roots):
    """Synced copy (<name>@<hash>/SKILL.md) of a Skill that was read on another machine, or None."""
    import json,re
    parent=Path(path).parent
    name=re.sub(r'[^A-Za-z0-9._-]','_',parent.name)[:60] or 'skill'
    found=[]
    for root in roots or []:
        try:
            origins=json.loads((Path(root)/'_origins.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):
            origins={}
        try:entries=sorted(Path(root).iterdir())
        except OSError:continue
        for entry in entries:
            if entry.name.startswith(name+'@') and (entry/'SKILL.md').is_file():
                origin=str(origins.get(entry.name,''))
                shared=len(os.path.commonpath([str(parent),origin])) if origin and os.path.isabs(origin) else 0
                found.append((origin==str(parent),shared,entry/'SKILL.md'))
    return max(found,key=lambda x:(x[0],x[1]))[2] if found else None


def readable(path, roots):
    """File to read for a Skill path. With a synced catalog only the catalog is read, never the machine's own files."""
    if not roots or contains(os.path.normpath(path),[os.path.normpath(str(r)) for r in roots]):
        return Path(path)
    return catalog_copy(path,roots)
