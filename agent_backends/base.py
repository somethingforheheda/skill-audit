"""Backend-neutral execution protocol: scenarios describe needs, adapters build CLI calls."""
from dataclasses import dataclass, field, replace
from pathlib import Path


class AuditError(Exception):
    pass


class AuditTimeout(AuditError):
    pass


# Every backend must state how it meets these; registration refuses a backend missing any.
GUARANTEES = {
    'read_only': '模型不能修改文件',
    'no_write_tools': '不暴露写文件/编辑工具',
    'isolated_extensions': '不加载用户 hooks、插件、MCP、Skill',
    'timeout_cancel': '超时与 Ctrl-C 回收整个进程组',
    'model_select': '可显式指定主模型',
    'redaction': '提供秘密列表，trace 与错误输出脱敏',
}

# Optional capabilities; a backend maps each one it has to how it is achieved.
CAPABILITIES = {
    'delegation': '原生子代理',
    'delegation_off': '可强制关闭子代理',
    'delegation_evidence': 'CLI 可证实实际发生了委派',
    'subagent_model': '可固定子代理模型',
    'model_attestation': '运行后可报告实际使用的模型',
    'command_allowlist': '可执行指定的只读命令入口',
    'trace': '运行事件落盘',
    'ephemeral': '不写入本机会话历史',
}

LEVELS = ('required', 'preferred', 'forbidden')


@dataclass(frozen=True)
class Need:
    capability: str
    level: str


@dataclass(frozen=True)
class AuthMode:
    description: str


@dataclass(frozen=True)
class Selection:
    """What the user asked for on the command line; None means "use the default"."""
    backend: str = None
    auth: str = None
    model: str = None
    subagent_model: str = None
    allow_any_gateway: bool = False   # bypass the optional trusted-gateway list


@dataclass
class Plan:
    backend: str
    auth: str
    granted: frozenset
    degraded: tuple
    selection: Selection
    model: str = None
    subagent_model: str = None
    secrets: tuple = ()
    notes: list = field(default_factory=list)
    context: dict = field(default_factory=dict)   # adapter-private, never serialized

    def record(self):
        return {'backend': self.backend, 'auth': self.auth, 'model': self.model,
                'subagent_model': self.subagent_model if 'delegation' in self.granted else None,
                'degraded': list(self.degraded), 'notes': list(self.notes)}


@dataclass
class AgentTask:
    prompt: str
    workdir: Path
    timeout: int
    read_roots: tuple = ()
    commands: tuple = ()      # exact command prefixes the agent may run (read-only helpers)
    trace_path: Path = None
    control: object = None


@dataclass
class AgentResult:
    text: str
    plan: Plan
    models: list = None       # models the CLI reports as used; None when unattested
    facts: dict = field(default_factory=dict)


def negotiate(backend, needs, auth):
    """Return (granted, degraded) or raise before anything starts."""
    have = set(backend.capabilities)
    granted, degraded = set(), []
    for need in needs:
        if need.level not in LEVELS or need.capability not in CAPABILITIES:
            raise AuditError(f'未知能力要求：{need}')
        if need.level == 'forbidden':
            if need.capability in have and need.capability + '_off' not in have:
                raise AuditError(f'{backend.label} 无法关闭「{CAPABILITIES[need.capability]}」')
        elif need.capability in have:
            granted.add(need.capability)
        elif need.level == 'required':
            raise AuditError(f'{backend.label} 不支持「{CAPABILITIES[need.capability]}」，本命令无法在该后端运行')
        else:
            degraded.append(need.capability)
    return frozenset(granted), tuple(degraded)


class Backend:
    name = ''
    aliases = ()
    label = ''
    guarantees = {}
    capabilities = {}
    auth_modes = {}
    default_auth = ''

    def plan(self, selection, needs):
        """Static: no process, no CLI lookup, no credentials. Safe for --prepare-only."""
        auth = selection.auth or self.default_auth
        if auth not in self.auth_modes:
            options = '；'.join(f'{name}：{mode.description}' for name, mode in self.auth_modes.items())
            raise AuditError(f'{self.label} 不支持认证方式 {auth}。可选 {options}')
        needs = tuple(needs)
        if selection.subagent_model:
            needs += (Need('subagent_model', 'required'),)
        granted, degraded = negotiate(self, needs, auth)
        return Plan(self.name, auth, granted, degraded, selection)

    def section(self, config):
        value = (config.get('backends') or {}).get(self.name) or {}
        return value if isinstance(value, dict) else {}

    def set_section(self, config, section):
        backends = config.get('backends') if isinstance(config.get('backends'), dict) else {}
        backends = {k: v for k, v in backends.items() if k != self.name}
        if section:
            backends[self.name] = section
        if backends:
            config['backends'] = backends
        else:
            config.pop('backends', None)

    def locate(self):
        """Path of the CLI, or AuditError with install instructions."""
        raise NotImplementedError

    def configure(self, config, args):
        """`init --backend <name>`: update config in place, return lines to print."""
        self.locate()
        section = {'model': args.model.strip()}
        if args.subagent_model:
            section['subagent_model'] = args.subagent_model.strip()
        self.set_section(config, section)
        return [f"模型：{section['model']}", f"子代理模型：{section.get('subagent_model') or '同主模型'}"]

    def after_configure(self, args):
        return []

    def default_model(self, plan, config):
        return None

    def resolve_models(self, plan, config):
        section = self.section(config)
        model = plan.selection.model or section.get('model') or self.default_model(plan, config)
        sub = None
        if 'delegation' in plan.granted:
            sub = plan.selection.subagent_model or section.get('subagent_model') or model
            if 'subagent_model' not in self.capabilities:
                plan.notes.append(f'{self.label} 无法固定子代理模型，子代理可能使用 CLI 默认模型')
                sub = None
            elif sub is None:
                plan.notes.append('未指定主模型，子代理模型未固定，由 CLI 自行决定')
        return replace(plan, model=model, subagent_model=sub)

    def ready(self, plan):
        """Locate the CLI, load configuration, resolve models and check credentials."""
        raise NotImplementedError

    def run(self, task, plan):
        raise NotImplementedError

    def status(self):
        """Lines for doctor; must never print secret values."""
        return []

    def ok(self):
        """Whether doctor considers this backend ready to run (no model request)."""
        return False


def error_tail(raw, secrets, ignore=()):
    """Last few lines of CLI output for an error message, redacted."""
    import history_review
    lines = [x for x in (raw or '').strip().splitlines() if not x.startswith(ignore)]
    return history_review.redact(' | '.join(lines[-3:])[:700], secrets)


def check_models(result):
    """Warn-level facts: reported models outside the requested ones."""
    plan = result.plan
    if result.models is None or plan.model is None:
        return []
    allowed = {plan.model.lower(), (plan.subagent_model or plan.model).lower()}
    return [m for m in result.models if m.lower() not in allowed]
