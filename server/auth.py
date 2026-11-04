import hashlib
import hmac
import os
import re
import secrets
from http.cookies import SimpleCookie

import config

COOKIE = 'sa_user'
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$')
MAX_AGE = 30 * 24 * 3600


def _secret():
    path = config.DATA / 'secret.key'
    if not path.exists():
        config.DATA.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_hex(32))
    return path.read_text().strip().encode()


def _sign(name):
    return hmac.new(_secret(), name.encode(), hashlib.sha256).hexdigest()[:40]


def valid_name(name):
    return bool(name and NAME_RE.match(name))


def current_user(headers):
    jar = SimpleCookie()
    try:
        jar.load(headers.get('Cookie', ''))
    except Exception:
        return None
    morsel = jar.get(COOKIE)
    if not morsel or '.' not in morsel.value:
        return None
    name, _, sig = morsel.value.rpartition('.')
    if valid_name(name) and hmac.compare_digest(sig, _sign(name)):
        return name
    return None


def _cookie(value, max_age):
    parts = ['%s=%s' % (COOKIE, value), 'Path=/', 'HttpOnly', 'SameSite=Lax', 'Max-Age=%d' % max_age]
    if config.SECURE_COOKIE:
        parts.append('Secure')
    return '; '.join(parts)


def login_cookie(name):
    return _cookie('%s.%s' % (name, _sign(name)), MAX_AGE)


def logout_cookie():
    return _cookie('', 0)
