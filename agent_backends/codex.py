"""Codex CLI backend (`codex exec`). The command line is unchanged from the pre-backend tool."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import codex_process
from audit_config import (DEFAULT_KEY_ENV, config_get, credentials_path, ensure_credentials_placeholder,
                          known_secrets, load_optional, provider_base_url, require_trusted, token_present,
                          token_value, trusted_hosts)
from . import register
from .base import AgentResult, AuditError, AuditTimeout, AuthMode, Backend, error_tail

INSTALL_URL = "https://developers.openai.com/codex/cli"
AUTH_ALIASES = {'chatgpt': 'native', 'company': 'gateway'}   # old names stay accepted
PROVIDER = 'skill_audit_gateway'


def codex_binary():
    binary = shutil.which("codex")
    if not binary:
        raise AuditError(
            "未找到 Codex CLI（codex）。审核已停止；请安装 Codex CLI，"
            f"确认 `codex --version` 可运行后重试。安装说明：{INSTALL_URL}"
        )
    return binary


def codex_config(data):
    # The token is supplied by env_key; no secret is passed through argv.
    return [
        "-c", f'model_provider="{PROVIDER}"',
        "-c", f'model_providers.{PROVIDER}.name="skill-audit gateway"',
        "-c", f'model_providers.{PROVIDER}.base_url={json.dumps(provider_base_url(data["base_url"]))}',
        "-c", f'model_providers.{PROVIDER}.env_key={json.dumps(data["token_env"])}',
        "-c", f'model_providers.{PROVIDER}.wire_api="responses"',
        "-c", f"model_providers.{PROVIDER}.requires_openai_auth=false",
        "-c", f"model_providers.{PROVIDER}.request_max_retries=0",
        "-c", f"model_providers.{PROVIDER}.stream_max_retries=0",
        "-c", 'approval_policy="never"',
    ]


def rollout_models(codex_home):
    """Models each thread actually ran with, read from the run's private CODEX_HOME."""
    models = []
    for path in sorted(Path(codex_home).glob("sessions/**/rollout-*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                row = json.loads(line)
                model = row["payload"].get("model") if row.get("type") == "turn_context" else None
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if isinstance(model, str) and model not in models:
                models.append(model)
    return models


def run_codex(binary, data, root, prompt, timeout, control=None, multi_agent=False, auth_mode='gateway', trace_path=None, models_out=None):
    if auth_mode not in ('gateway','chatgpt'):
        raise AuditError('未知认证方式')
    if auth_mode=='gateway':
        token_present(data)
    directory = tempfile.mkdtemp(prefix="skill-audit-codex-")
    try:
        temp = Path(directory)
        codex_home = temp / "codex-home"
        codex_home.mkdir(mode=0o700)
        output = temp / "last-message.txt"
        env = os.environ.copy()
        if auth_mode=='gateway':
            env["CODEX_HOME"] = str(codex_home)
        else:
            # Reuse CLI-managed login. Never copy or print cached credentials.
            env["CODEX_HOME"] = os.environ.get("CODEX_HOME",str(Path.home()/".codex"))
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            if auth_mode=="chatgpt" or key != data["token_env"]:
                env.pop(key, None)
        token = None
        if auth_mode=='gateway':
            # File-stored tokens reach Codex through env_key, never through argv.
            token = token_value(data)[0]
            if token:
                env[data["token_env"]] = token
        if auth_mode=='chatgpt':
            for key in ('OPENAI_BASE_URL','OPENAI_ORG_ID','OPENAI_PROJECT_ID',DEFAULT_KEY_ENV,data.get('token_env') or DEFAULT_KEY_ENV):
                env.pop(key,None)
            status=subprocess.run([binary,'login','status'],env=env,capture_output=True,text=True,timeout=15)
            if status.returncode or 'Logged in using ChatGPT' not in status.stdout+status.stderr:
                raise AuditError('订阅测试需要 codex login 的 ChatGPT 登录；不会回退到 API Key')
        provider=codex_config(data) if auth_mode=='gateway' else ['-c','model_provider="openai"','-c','forced_login_method="chatgpt"','-c','approval_policy="never"']
        command = [binary, "exec", "--ignore-user-config", "--skip-git-repo-check",
                   "--disable", "hooks", "--enable" if multi_agent else "--disable", "multi_agent", "--disable", "apps",
                    "--sandbox", "read-only", "-C", str(root),
                   "-o", str(output), *provider]
        if not multi_agent:command += ['--ephemeral']
        if data.get('model'):command += ['-m',data['model']]
        if multi_agent and data.get('subagent_model'):
            command += ['-c',f'agents.default_subagent_model={json.dumps(data["subagent_model"])}']
        if trace_path:command += ['--json']
        command += ['-']
        try:
            result = codex_process.execute(command, prompt, timeout, env, root, control)
        except codex_process.TimedOut as exc:
            raise AuditTimeout(str(exc)) from exc
        secrets=[*known_secrets(data),token or '']
        if trace_path:
            import history_review
            Path(trace_path).write_text(history_review.redact(result.stdout,secrets),encoding='utf-8')
            Path(trace_path).with_suffix('.stderr.txt').write_text(history_review.redact(result.stderr,secrets),encoding='utf-8')
        if result.returncode:
            raise AuditError(f"Codex 退出码 {result.returncode}；审核未完成。{error_tail(result.stderr or result.stdout, secrets)}")
        if not output.is_file():
            raise AuditError("Codex 未写入最终答复；审核未完成")
        if models_out is not None and auth_mode=='gateway':
            models_out.extend(rollout_models(codex_home))
        return output.read_text(encoding="utf-8").strip()

    finally:
        try:
            shutil.rmtree(directory)
        except OSError:
            print(f"提示：Codex 临时目录暂未清理，已保留调用结果：{directory}", file=sys.stderr)


@register
class CodexBackend(Backend):
    name = 'codex'
    label = 'Codex CLI'
    guarantees = {
        'read_only': '操作系统沙箱 --sandbox read-only',
        'no_write_tools': 'read-only 沙箱拒绝写入',
        'isolated_extensions': '--ignore-user-config --disable hooks/apps（系统托管 hook 仍会运行）',
        'timeout_cancel': '进程组超时/取消回收',
        'model_select': '-m',
        'redaction': '网关令牌及 OpenAI/Codex 环境变量',
    }
    capabilities = {
        'delegation': '--enable multi_agent',
        'delegation_off': '--disable multi_agent',
        'subagent_model': '-c agents.default_subagent_model',
        'model_attestation': '临时 CODEX_HOME 的 rollout 记录（仅 gateway 认证且启用委派时）',
        'command_allowlist': 'read-only 沙箱内可运行任意只读命令（不按前缀精确限制）',
        'trace': '--json 事件流',
        'ephemeral': 'gateway 认证使用临时 CODEX_HOME，运行后删除；native 认证仅关闭委派时 --ephemeral',
    }
    auth_modes = {
        'gateway': AuthMode('skill-audit 注入 init 配置的网关与 token_env 令牌（旧名 company）'),
        'native': AuthMode('复用 codex login 的 ChatGPT 登录（别名 chatgpt）'),
    }
    default_auth = 'gateway'

    def plan(self, selection, needs):
        # Without an explicit --auth: a configured gateway wins, otherwise reuse codex login.
        auth = AUTH_ALIASES.get(selection.auth, selection.auth) or ('gateway' if load_optional().get('base_url') else 'native')
        return super().plan(replace(selection, auth=auth), needs)

    def default_model(self, plan, config):
        return config.get('model') if plan.auth == 'gateway' else None

    def locate(self):
        return codex_binary()

    def configure(self, config, args):
        codex_binary()
        # --base-url is optional: keep a previously saved gateway, or stay on codex login without one.
        url = args.base_url or config.get('base_url')
        self.token_env = None
        if not url:
            for key in ('base_url', 'model', 'token_env'):
                config.pop(key, None)
            return [*super().configure(config, args), "认证：复用 codex login（未配置网关；需要自定义网关时加 --base-url）"]
        provider_base_url(url)
        require_trusted(url, trusted_hosts(config))
        token_env = args.token_env or config.get('token_env') or DEFAULT_KEY_ENV
        if not token_env.isidentifier():
            raise AuditError("token-env 必须是环境变量名")
        config.update(base_url=url, model=args.model, token_env=token_env)
        self.set_section(config, {'subagent_model': args.subagent_model.strip()} if args.subagent_model else {})
        self.token_env = token_env
        return [f"Codex 模型接口：{provider_base_url(url)}/responses", f"模型：{args.model}",
                f"子代理模型：{args.subagent_model or '同主模型'}",
                f"令牌环境变量：{token_env}（配置中未保存令牌）"]

    def after_configure(self, args):
        if not getattr(self, 'token_env', None):
            return []
        credentials, created = ensure_credentials_placeholder(self.token_env)
        return [f"令牌文件：{credentials}（{'已创建占位，' if created else ''}环境变量未设置时读取，须为 0600）"]

    def ready(self, plan):
        binary = codex_binary()
        if plan.auth == 'gateway':
            config = config_get()
            token_present(config)
        else:
            config = load_optional()
        plan = self.resolve_models(plan, config)
        plan.secrets = tuple(known_secrets(config if plan.auth == 'gateway' else None))
        plan.context = {'binary': binary, 'config': config}
        return plan

    def run(self, task, plan):
        multi = 'delegation' in plan.granted
        config = plan.context['config']
        data = {**config, 'model': plan.model} if plan.auth == 'gateway' else {'model': plan.model, 'token_env': config.get('token_env')}
        if multi and plan.subagent_model:
            data['subagent_model'] = plan.subagent_model
        models = []
        text = run_codex(plan.context['binary'], data, task.workdir, task.prompt, task.timeout, task.control,
                         multi_agent=multi, auth_mode='gateway' if plan.auth == 'gateway' else 'chatgpt',
                         trace_path=task.trace_path, models_out=models)
        return AgentResult(text, plan, models or None)

    def status(self):
        binary = shutil.which('codex')
        lines = [f"Codex CLI：{binary or '未安装（' + INSTALL_URL + '）'}"]
        try:
            if not load_optional().get('base_url'):
                return lines + ["  认证：默认复用 codex login（未配置网关；自定义网关用 init --base-url）"]
            data = config_get()
        except AuditError as exc:
            return lines + [f"  网关配置：{exc}"]
        key, source = token_value(data)
        sub = self.section(data).get('subagent_model')
        lines += [f"  网关 Responses 接口：{provider_base_url(data['base_url'])}/responses",
                  f"  模型：{data['model']}；子代理模型：{sub or '同主模型'}",
                  f"  令牌：{data['token_env']}（{'已设置，来源：' + source if key else '未设置'}；令牌文件 {credentials_path()}）"]
        return lines

    def ok(self):
        try:
            if not load_optional().get('base_url'):
                return bool(shutil.which('codex'))
            return bool(shutil.which('codex') and token_value(config_get())[0])
        except AuditError:
            return False
