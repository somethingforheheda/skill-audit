"""Agent backend registry. Adding a backend = adding one module here that calls register().

Modules are discovered lazily on first lookup, so importing ``agent_backends.base`` never
pulls in adapters (they in turn import configuration helpers that need ``base``).
"""
from importlib import import_module, resources

from .base import AuditError, Selection

DEFAULT = 'codex'
_registry = {}
_aliases = {}
_loaded = False


def register(cls):
    from .base import CAPABILITIES, GUARANTEES
    missing = set(GUARANTEES) - set(cls.guarantees)
    unknown = set(cls.capabilities) - set(CAPABILITIES)
    if not cls.name or missing or unknown or cls.default_auth not in cls.auth_modes:
        raise RuntimeError(f'后端 {cls.name or cls.__name__} 声明不完整：缺少 {sorted(missing)}，未知能力 {sorted(unknown)}')
    if cls.name in _registry:
        raise RuntimeError(f'后端重名：{cls.name}')
    _registry[cls.name] = cls
    for alias in cls.aliases:
        _aliases[alias] = cls.name
    return cls


def _discover():
    global _loaded
    if _loaded:
        return
    _loaded = True
    # importlib.resources also enumerates modules inside the zipapp.
    for entry in sorted(resources.files(__name__).iterdir(), key=lambda e: e.name):
        stem = entry.name[:-3] if entry.name.endswith('.py') else None
        if stem and not stem.startswith('_') and stem != 'base':
            import_module(f'{__name__}.{stem}')


def names():
    _discover()
    return sorted(_registry)


def choices():
    _discover()
    return sorted({*_registry, *_aliases})


def get(name=None):
    _discover()
    name = _aliases.get(name or DEFAULT, name or DEFAULT)
    if name not in _registry:
        raise AuditError(f'未知后端 {name}；可选：{"、".join(choices())}')
    return _registry[name]()


def selection(args, **defaults):
    """Build a Selection from argparse-style args; missing attributes fall back to defaults."""
    value = lambda key: getattr(args, key, None) or defaults.get(key)
    return Selection(backend=value('backend'), auth=value('auth'), model=value('model'),
                     subagent_model=value('subagent_model'),
                     # Checkpoints from 0.11 saved the flag under its old name.
                     allow_any_gateway=bool(value('allow_any_gateway') or defaults.get('allow_non_company')))
