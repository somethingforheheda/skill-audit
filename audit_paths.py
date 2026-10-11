"""Single place where skill-audit decides which directories it owns.

报告与快照原先落在 /tmp，系统清理会连证据一起删掉。这里把工具的全部长期
状态收进一个用户目录：默认 ~/.skill-audit，可用 SKILL_AUDIT_HOME 覆盖。

    ~/.skill-audit/
    ├── config.json          网关配置（不含令牌）
    ├── credentials.json     可选令牌文件（0600；环境变量优先）
    ├── rules/               可编辑条例
    ├── venv/                PyYAML 专用运行时
    └── reports/
        ├── latest -> <最近一次运行目录>
        └── <项目>-<UTC 时间>-<随机>/
            ├── report.json  证据索引（小，长期留）
            ├── report.html  离线报告页面
            └── snapshots/<run_id>/  会话快照正文及 AI 断点、草稿（含历史对话原文）
"""

import os
import re
from pathlib import Path

HOME_ENV = "SKILL_AUDIT_HOME"
DEFAULT_DIRNAME = ".skill-audit"


def home():
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_DIRNAME


def config_file():
    return home() / "config.json"


def credentials_file():
    return home() / "credentials.json"


def rules_dir():
    return home() / "rules"


def venv_dir():
    return home() / "venv"


def reports_root():
    return home() / "reports"


def slug(text, fallback="report"):
    name = re.sub(r"[^0-9A-Za-z._-]+", "-", Path(str(text or "")).name).strip("-.")
    return (name or fallback)[:48]


def run_directory(label, stamp, token):
    return reports_root() / f"{slug(label)}-{stamp}-{token}"


def link_latest(directory):
    """reports/latest 指向最近一次运行；不支持符号链接时静默跳过。"""
    link = reports_root() / "latest"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(Path(directory).name)
    except OSError:
        return None
    return link
