#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v codex >/dev/null 2>&1 && ! command -v claude >/dev/null 2>&1; then
  printf '未找到任何受支持的 Agent CLI，安装已停止。请先安装 Codex（https://developers.openai.com/codex/cli）或 Claude Code（https://docs.claude.com/en/docs/claude-code/setup）\n' >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  printf '未找到 Python 3，安装已停止。\n' >&2
  exit 2
fi
install_dir=${HOME}/.local/bin
mkdir -p "$install_dir"
# Release zip ships the pyz next to this script; a source checkout builds it into dist/.
app="$script_dir/skill-audit.pyz"
[ -f "$app" ] || app="$script_dir/dist/skill-audit.pyz"
if [ ! -f "$app" ]; then
  printf '未找到 skill-audit.pyz；源码目录请先运行 python3 build_dist.py\n' >&2
  exit 2
fi
cp "$app" "$install_dir/skill-audit"
chmod 755 "$install_dir/skill-audit"
audit_home=${SKILL_AUDIT_HOME:-${HOME}/.skill-audit}
rules_dir=${audit_home}/rules
mkdir -p "$rules_dir"
cp "$script_dir/单Skill语义审核条例.json" "$rules_dir/单Skill语义审核条例.json"
cp "$script_dir/全量跨Skill语义审核条例.json" "$rules_dir/全量跨Skill语义审核条例.json"
printf '已安装到 %s/skill-audit\n' "$install_dir"
printf '工具目录：%s（配置、条例、运行时、报告都在这里）\n' "$audit_home"
printf '可编辑条例：%s\n' "$rules_dir"
printf '请确保 %s 在 PATH 中。\n' "$install_dir"
printf '下一步运行 skill-audit init 保存模型（会自动安装 PyYAML），例如：\n'
printf '  skill-audit init --backend claude-code --model <模型全名>\n'
printf '  skill-audit init --model <模型>                                   # Codex，复用 codex login\n'
printf '  skill-audit init --model <模型> --base-url https://<你的网关>/     # Codex，自定义网关\n'
