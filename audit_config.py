"""skill-audit configuration and gateway credentials (shared by the CLI and backends)."""
import json
import os
from urllib.parse import urlparse, urlunparse

import audit_paths
from agent_backends.base import AuditError

DEFAULT_KEY_ENV = "SKILL_AUDIT_API_KEY"
TOKEN_PLACEHOLDER = "<在此替换为网关令牌>"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def config_path():
    return audit_paths.config_file()


def load_optional():
    """The raw config file, or {} when absent; backends without gateway settings use this."""
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"配置文件无法读取：{type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise AuditError("配置文件必须是 JSON 对象")
    return data


def config_get():
    path = config_path()
    if not path.is_file():
        raise AuditError(f"尚未配置网关；先运行 skill-audit init --base-url <网关地址> --model <模型 ID>，"
                         f"或加 --auth native 复用 codex login（配置文件：{path}）")
    data = load_optional()
    if not all(isinstance(data.get(key), str) and data[key] for key in ("base_url", "model", "token_env")):
        raise AuditError("配置缺少 base_url、model 或 token_env；先运行 skill-audit init --base-url <网关地址> --model <模型 ID>")
    provider_base_url(data["base_url"])
    require_trusted(data["base_url"], trusted_hosts(data))
    if not data["token_env"].isidentifier():
        raise AuditError("token_env 必须是环境变量名")
    return data


def save(data):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(path, 0o600)
    return path


def trusted_hosts(config):
    """Optional gateway allowlist (`init --trusted-gateway`); empty means any gateway is accepted."""
    hosts = config.get("trusted_gateways") or []
    if not isinstance(hosts, list) or not all(isinstance(h, str) and h for h in hosts):
        raise AuditError("配置 trusted_gateways 必须是主机名列表")
    return [h.lower() for h in hosts]


def normalize_host(value):
    value = value.strip()
    host = urlparse(value if "://" in value else "https://" + value).hostname
    if not host:
        raise AuditError(f"无法识别的网关主机名：{value}")
    return host.lower()


def gateway_trusted(url, hosts):
    """No allowlist trusts everything; otherwise the URL must be HTTPS on a listed host."""
    if not hosts:
        return True
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname in hosts


def gateway_label(url):
    try:
        return urlparse(url).hostname or url if url else None
    except ValueError:
        return url


def require_trusted(url, hosts):
    if not gateway_trusted(url, hosts):
        raise AuditError(f"网关 {gateway_label(url)} 不在受信任网关列表（{'、'.join(hosts)}）中；"
                         "请改用列表内网关，或重新 init --trusted-gateway 调整列表")


def provider_base_url(base_url):
    try:
        parsed = urlparse(base_url)
        port = parsed.port
    except ValueError as exc:
        raise AuditError("网关地址无法解析") from exc
    local = parsed.hostname in LOCAL_HOSTS
    if (parsed.scheme not in (("https", "http") if local else ("https",)) or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment or port == 0):
        raise AuditError("网关必须是 HTTPS 地址（本机地址可用 HTTP），且不能包含账号、查询参数或片段")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions") or path.endswith("/responses"):
        raise AuditError("请提供 API 基础 URL；Codex 需要 Responses API")
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunparse(parsed._replace(path=path))


def credentials_path():
    return audit_paths.credentials_file()


def read_credentials():
    """User-saved tokens keyed by env var name; placeholders count as unset."""
    path = credentials_path()
    if not path.is_file():
        return {}
    try:
        info = path.stat()
        if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
            raise AuditError(f"令牌文件权限过宽或不属于当前用户，已拒绝读取；请执行 chmod 600 {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"令牌文件无法读取：{type(exc).__name__}") from exc
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise AuditError('令牌文件必须是 {"环境变量名": "令牌"} 形式的 JSON 对象')
    return {k: v.strip() for k, v in data.items() if v.strip() and not is_placeholder(v.strip())}


def is_placeholder(value):
    # Older releases wrote a differently worded placeholder; any "<在此替换为…>" counts as unset.
    return value == TOKEN_PLACEHOLDER or (value.startswith("<在此替换为") and value.endswith(">"))


def ensure_credentials_placeholder(token_env):
    path = credentials_path()
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({token_env: TOKEN_PLACEHOLDER}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(path, 0o600)
    return path, True


def token_value(data):
    """Return (token, source); the environment variable wins over the credentials file."""
    name = data["token_env"]
    if os.environ.get(name):
        return os.environ[name], "环境变量"
    token = read_credentials().get(name)
    return (token, "令牌文件") if token else (None, None)


def known_secrets(data=None):
    """Best-effort redaction list; a broken credentials file must not stop redaction."""
    try:
        stored = list(read_credentials().values())
    except AuditError:
        stored = []
    names = (DEFAULT_KEY_ENV, "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_AUTH_TOKEN",
             "ANTHROPIC_API_KEY", (data or {}).get("token_env", ""))
    return [value for value in [*(os.environ.get(name, "") for name in names if name), *stored] if value]


def token_present(data):
    if not token_value(data)[0]:
        raise AuditError(f"未设置令牌：环境变量 {data['token_env']} 为空，{credentials_path()} 中也没有；"
                         "不会启动审核或回退到 codex login 登录态")
