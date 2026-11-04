import hashlib
import re
import zipfile

import config

_CACHE = {}
VERSION_RE = re.compile(rb'^APP_VERSION\s*=\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']', re.M)


def pyz_info():
    """Version and checksum of the zipapp served for download, or None when none is configured."""
    path = config.PYZ
    if not path:
        return None
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError:
        return None
    key = (len(data), hashlib.md5(data).hexdigest())
    if _CACHE.get('key') != key:
        version = None
        try:
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                match = VERSION_RE.search(archive.read('skill_audit_cli.py'))
                version = match.group(1).decode() if match else None
        except (zipfile.BadZipFile, KeyError):
            pass
        _CACHE.update(key=key, info={'version': version, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
    return _CACHE['info']
