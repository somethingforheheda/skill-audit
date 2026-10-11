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
