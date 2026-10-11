"""Claude Code backend (`claude -p`), reusing the machine's Claude Code login/gateway settings."""
import json
import os
import re
import shutil
from pathlib import Path

import codex_process
from audit_config import DEFAULT_KEY_ENV, gateway_label, gateway_trusted, known_secrets, load_optional, trusted_hosts
from . import register
from .base import AgentResult, AuditError, AuditTimeout, AuthMode, Backend, error_tail

INSTALL_URL = "https://docs.claude.com/en/docs/claude-code/setup"
READ_TOOLS = ('Read', 'Grep', 'Glob')
# Markers of an enclosing Claude Code session; a nested `claude -p` must not inherit them.
NESTED_ENV = ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_CHILD_SESSION',
              'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_CODE_SESSION_ATTENDED',
              'CLAUDE_CODE_EXECPATH', 'CLAUDE_PID', 'CLAUDE_CODE_SUBAGENT_MODEL')
FOREIGN_SECRETS = ('OPENAI_API_KEY', 'CODEX_API_KEY', DEFAULT_KEY_ENV)
FENCE = re.compile(r'\A```[A-Za-z0-9_-]*\n(.*)\n```\Z', re.S)
DECODER = json.JSONDecoder()


def claude_binary():
    binary = shutil.which('claude')
    if not binary:
        raise AuditError(f"未找到 Claude Code CLI（claude）。请安装并确认 `claude --version` 可运行。安装说明：{INSTALL_URL}")
    return binary


def user_settings_env():
    """ANTHROPIC_* entries from the user's Claude Code settings (where the gateway/login lives)."""
    home = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    try:
        data = json.loads((home / 'settings.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError) as exc:
        raise AuditError(f'Claude Code 用户设置无法读取：{type(exc).__name__}') from exc
    env = data.get('env') if isinstance(data, dict) else None
    return {k: v for k, v in (env or {}).items()
            if isinstance(k, str) and k.startswith('ANTHROPIC_') and isinstance(v, str)}


def child_env(subagent_model, config=None):
    # --restricted ignores user settings files, so forward the gateway/login variables
    # explicitly; like Claude Code itself, settings env wins over the shell.
    env = {**os.environ, **user_settings_env()}
    foreign = (config or {}).get('token_env')   # the Codex gateway token is not Claude's business
    for key in NESTED_ENV + FOREIGN_SECRETS + ((foreign,) if foreign and not foreign.startswith('ANTHROPIC_') else ()):
        env.pop(key, None)
    if subagent_model:
        env['CLAUDE_CODE_SUBAGENT_MODEL'] = subagent_model
    return env


def build_command(binary, task, plan):
    delegation = 'delegation' in plan.granted
    tools = [*READ_TOOLS, *(['Agent'] if delegation else []), *(['Bash'] if task.commands else [])]
    allowed = [*READ_TOOLS, *(['Agent'] if delegation else []), *(f'Bash({prefix} *)' for prefix in task.commands)]
    settings = {'disableAllHooks': True}
    if delegation and plan.subagent_model:
        settings['env'] = {'CLAUDE_CODE_SUBAGENT_MODEL': plan.subagent_model}
    command = [binary, '-p', '--restricted', '--safe-mode', '--strict-mcp-config', '--disable-slash-commands',
               '--no-session-persistence', '--permission-mode', 'dontAsk',
               '--output-format', 'stream-json', '--verbose',
               '--tools', ','.join(tools), '--allowedTools', *allowed,
               '--settings', json.dumps(settings, ensure_ascii=False)]
    for root in task.read_roots:
        command += ['--add-dir', str(root)]
    if plan.model:
        command += ['--model', plan.model]
    return command


def final_event(stdout):
    result = None
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get('type') == 'result':
            result = row
    return result


def strip_fence(text):
    match = FENCE.match(text.strip())
    return match[1].strip() if match else text.strip()


def final_payload(text):
    """Claude models sometimes wrap the requested JSON in prose before and/or after it; keep the JSON.

    The first object/array that starts a line and decodes completely is taken, so ordinary
    prose (or a truncated object) is returned as is and fails validation downstream.
    """
    text = strip_fence(text)
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped[:1] in ('{', '['):
            start = offset + len(line) - len(stripped)
            try:
                _, end = DECODER.raw_decode(text, start)
            except ValueError:
                pass
            else:
                return text[start:end]
        offset += len(line)
    return text


@register
class ClaudeCodeBackend(Backend):
    name = 'claude-code'
    aliases = ('claude',)
    label = 'Claude Code CLI'
    guarantees = {
        'read_only': '工具白名单 Read/Grep/Glob + dontAsk 拒绝其余操作，文件工具限定在工作目录（非操作系统沙箱）',
        'no_write_tools': '--tools 不含 Edit/Write/NotebookEdit',
        'isolated_extensions': '--restricted --safe-mode --strict-mcp-config --disable-slash-commands，disableAllHooks',
        'timeout_cancel': '进程组超时/取消回收',
        'model_select': '--model',
        'redaction': 'ANTHROPIC 令牌与网关令牌',
    }
    capabilities = {
        'delegation': 'Agent 工具',
        'delegation_off': '不提供 Agent 工具',
        'delegation_evidence': 'result.subagent_stats',
        'subagent_model': 'CLAUDE_CODE_SUBAGENT_MODEL（--settings env）',
        'model_attestation': 'result.modelUsage',
        'command_allowlist': 'Bash(<前缀> *) 精确前缀，其余命令与复合命令被拒',
        'trace': 'stream-json 事件流',
        'ephemeral': '--no-session-persistence',
    }
    auth_modes = {'native': AuthMode('复用 Claude Code 本机配置的网关/登录（配置了受信任网关时只允许列表内网关）')}
    default_auth = 'native'

    def locate(self):
        return claude_binary()

    def gateway(self):
        env = {**os.environ, **user_settings_env()}
        return env.get('ANTHROPIC_BASE_URL')

    def ready(self, plan):
        binary = claude_binary()
        config = load_optional()
        url, hosts = self.gateway(), trusted_hosts(config)
        if not gateway_trusted(url, hosts):
            where = gateway_label(url) or 'Anthropic 官方（未配置 ANTHROPIC_BASE_URL）'
            if not plan.selection.allow_any_gateway:
                raise AuditError(f'Claude Code 当前网关（{where}）不在受信任网关列表（{"、".join(hosts)}）中；'
                                 '确需使用请加 --allow-any-gateway')
            plan.notes.append(f'已显式允许受信任列表外的网关：{where}')
        plan = self.resolve_models(plan, config)
        env = child_env(plan.subagent_model if 'delegation' in plan.granted else None, config)
        secrets = [*known_secrets(), *(env.get(k, '') for k in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY'))]
        plan.secrets = tuple(dict.fromkeys(s for s in secrets if s))
        plan.context = {'binary': binary, 'env': env}
        return plan

    def run(self, task, plan):
        import history_review
        command = build_command(plan.context['binary'], task, plan)
        try:
            result = codex_process.execute(command, task.prompt, task.timeout, plan.context['env'],
                                           task.workdir, task.control, label='Claude Code')
        except codex_process.TimedOut as exc:
            raise AuditTimeout(str(exc)) from exc
        secrets = list(plan.secrets)
        if task.trace_path:
            Path(task.trace_path).write_text(history_review.redact(result.stdout, secrets), encoding='utf-8')
            Path(task.trace_path).with_suffix('.stderr.txt').write_text(history_review.redact(result.stderr, secrets), encoding='utf-8')
        event = final_event(result.stdout)
        if event is None:
            raise AuditError(f"Claude Code 退出码 {result.returncode}，没有最终结果；审核未完成。{error_tail(result.stderr or result.stdout, secrets, ignore='[claude-code:unrecognized_model]')}")
        if result.returncode or event.get('is_error') or event.get('subtype') != 'success':
            detail = history_review.redact(str(event.get('result') or event.get('subtype'))[:500], secrets)
            raise AuditError(f"Claude Code 未完成（退出码 {result.returncode}，{event.get('subtype')}）：{detail}")
        stats = event.get('subagent_stats') or {}
        facts = {'delegated': stats.get('spawned', 0), 'permission_denials': len(event.get('permission_denials') or []),
                 'cost_usd': event.get('total_cost_usd'), 'turns': event.get('num_turns')}
        models = list(event.get('modelUsage') or {}) or None
        return AgentResult(final_payload(event.get('result') or ''), plan, models, facts)

    def status(self):
        binary = shutil.which('claude')
        lines = [f"Claude Code CLI：{binary or '未安装（' + INSTALL_URL + '）'}"]
        try:
            url = self.gateway()
            config = load_optional()
            section, hosts = self.section(config), trusted_hosts(config)
        except AuditError as exc:
            return lines + [f'  {exc}']
        trust = ('未限制' if not hosts else '受信任' if gateway_trusted(url, hosts)
                 else '不在受信任列表，运行需 --allow-any-gateway')
        lines += [f"  网关：{gateway_label(url) or 'Anthropic 官方'}（{trust}）",
                  f"  模型：{section.get('model') or 'Claude Code 默认'}；子代理模型：{section.get('subagent_model') or '同主模型'}"]
        return lines

    def ok(self):
        try:
            return bool(shutil.which('claude')) and gateway_trusted(self.gateway(), trusted_hosts(load_optional()))
        except AuditError:
            return False
