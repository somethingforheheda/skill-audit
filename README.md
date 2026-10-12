# skill-audit

Agent Skill（`SKILL.md`）质量审查终端工具：确定性 lint、单 Skill / 跨 Skill 的 AI 语义审核，以及基于本机 Agent 历史会话的 Skill 使用复盘。AI 部分调用你本机已装好的 Claude Code 或 Codex CLI，在只读模式下运行；工具本身不内置任何网关地址或令牌。

## 安装

需要 Python 3.9+，以及已能正常登录使用的 [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) 或 [Codex CLI](https://developers.openai.com/codex/cli)。

```bash
git clone https://github.com/somethingforheheda/skill-audit.git && cd skill-audit
python3 build_dist.py && sh install.sh                                   # 装到 ~/.local/bin/skill-audit
skill-audit init --backend claude-code --model "<模型全名>"            # 保存模型，并自动安装 PyYAML
skill-audit doctor --probe --backend claude --model "<模型全名>"       # 一次小请求，确认可用
```

也可以用 GitHub Release 里的 `skill-audit-team.zip`：解压后运行其中的 `install.sh`，不需要源码。

## 升级

```bash
skill-audit update --check      # 只看有没有新版本
skill-audit update              # 下载最新 Release 并替换自身，配置与报告保留
skill-audit update --version 0.12.0   # 装指定版本（回退用）
```

- 只在你手动运行时才升级。`history`/`review`/`cross`/`doctor` 在终端里运行结束时，最多每天联网查一次，有新版本只打印一行提示；`skill-audit update --notify off` 关闭（也可设环境变量 `SKILL_AUDIT_NO_UPDATE_CHECK=1`）。非终端环境（脚本、Hook）和 `lint` 不检查。
- 下载后先核对 GitHub 记录的 SHA-256，再让新包自己运行一次 `--version` 自检，通过才原子替换；任何一步失败都不改动当前版本。
- `~/.skill-audit/rules/` 里没改过的条例会随新版本更新；改过的原样保留，新版默认条例另存为 `*.new`。
- 只支持 `install.sh` 装出的发行包。源码目录请 `git pull` 后 `python3 build_dist.py && sh install.sh`（会覆盖 rules/ 下的条例，改过的先另存）。0.12.0 及更早版本没有 `update` 命令，需要手动装一次 0.13.0。

## 配置

| 场景 | 命令 |
| --- | --- |
| Claude Code（官方登录或自己的 `ANTHROPIC_BASE_URL`） | `skill-audit init --backend claude-code --model "<模型全名>"` |
| Codex，复用 `codex login` | `skill-audit init --model <模型>` |
| Codex，自定义 OpenAI Responses 兼容网关 | `skill-audit init --model <模型> --base-url https://<网关>/ [--token-env <变量名>]` |
| Codex，直接用 OpenAI API Key | `skill-audit init --model <模型> --base-url https://api.openai.com/ --token-env OPENAI_API_KEY` |
| 只允许走指定网关 | 任意 init 加 `--trusted-gateway <主机名>`（可重复）；`--clear-trusted-gateway` 取消 |

- 每条命令用 `--backend codex|claude-code`（别名 `claude`）选后端，默认 codex；`--model`、`--subagent-model` 覆盖 init 保存的值。模型写全名，不要用 `opus`/`sonnet` 这类会被用户设置重新映射的别名。
- Codex 认证：`--auth gateway` 用 init 保存的网关和令牌，`--auth native` 复用 `codex login`；不指定时有网关配置就用 gateway。旧名 `company`/`chatgpt` 仍可用。
- 网关令牌：默认变量名 `SKILL_AUDIT_API_KEY`。环境变量优先；未设置时读 `~/.skill-audit/credentials.json`（必须 0600，init 会建占位）。令牌只经环境变量传给 CLI，不进命令行参数，trace 与错误输出会脱敏。`config.json` 不含令牌。
- `--base-url` 必须是 HTTPS（localhost 可用 HTTP），不能带账号、查询参数或片段。
- 受信任网关未设置时不限制；设置后 Codex 的 base-url 与 Claude Code 的 `ANTHROPIC_BASE_URL` 都必须是列表内主机，单次放开用 `--allow-any-gateway`，并写入报告备注。
- `skill-audit doctor` 不发请求，显示各后端、网关、模型、令牌来源（不显示值）；`--probe` 实际请求一次。

所有状态在 `~/.skill-audit/`（`SKILL_AUDIT_HOME` 可改）：`config.json`、`credentials.json`、`rules/`、`venv/`（PyYAML）、`reports/`。

## 命令

```bash
skill-audit lint .agents/skills/example                 # 确定性硬性检查（frontmatter、name 格式与目录名、字段长度、500 行上限），默认 JSON，--text 可读
skill-audit review .agents/skills/example --output r.json   # 单 Skill AI 审核
skill-audit cross .agents/skills --output c.json        # 跨 Skill 全量审核（只扫 Git 跟踪且无未提交改动的 Skill）
skill-audit check single r.json --repo-root .           # 复查已有 review/cross 报告的结构与原文定位
skill-audit history --skills-root .agents/skills --limit 30   # 历史会话复盘，见下节
skill-audit reports [--prune --keep 5 [--yes] [--all]]  # 列出 / 清理历史报告，默认试运行
skill-audit probe-agents --backend claude               # 测试后端的原生子代理委派
skill-audit update [--check]                            # 升级本工具，见上节
```

review/cross 会自动校验 AI 输出的 JSON 结构、计数与原文定位；格式通过不代表语义判断正确。`--rules-file` 可临时换条例。

退出码：`0` 完成且无 BLOCK；`1` lint 硬性问题或单 Skill BLOCK（报告仍保存）；`2` 未完成或覆盖不完整（依赖、CLI、网关、格式问题，或 history 结果为 partial）；`130` 用户取消。

## 历史复盘（history）

在要复盘的仓库根目录运行（macOS 建议加 `caffeinate -i` 防睡眠）：

```bash
caffeinate -i skill-audit history --skills-root .agents/skills --limit 30 \
  --backend claude --model "<模型全名>" --subagent-model "<模型全名>" --timeout 1800 --attempts 2
```

流程：

1. **纯脚本采集**：读取本机 `~/.claude/projects`、`~/.codex`、`~/.comate-engine/store`（`--sources`、`--history-home` 可调）中工作目录为当前项目的最近 N 个会话（`--limit`，默认 20；`--days`/`--from`/`--to` 限时间，`--all-projects` 不限项目），脱敏后存为本地快照，确定性统计每次 Skill 正文读取。`--collect-only` 只做这一步，不需要模型。
2. **AI 汇总**：一个总控 Agent（可自行派子代理）读统计摘要，通过只读的 `analysis-read` 工具按需取证，输出改进建议：归因、动作类型、当前正文核对、支持/反例证据、回归案例草稿。
3. **程序校验**（确定性规则，不调用模型）：每条证据必须能在工具返回原文中逐字找到（忽略转义、引号、全半角标点、空白、Markdown 加粗与列表符号差异；页码或事件 ID 写错时按原文自动定位），统计值、会话与 Skill 正文哈希都要对得上。不合格则带着全部错误定向修复，最多 `--attempts` 次；仍不合格的建议**不会删除**，在报告中标记「校验未通过」并列出原因，由人工判断（可能是模型引用不实，也可能是校验误判）。能从证据推出的字段（支持会话、未附正文的当前核对）直接纠正，并在该条「限制」中注明。

产出在 `~/.skill-audit/reports/<项目>-<时间>-<随机>/`：`report.json`（采集统计）、`report.html`（离线页面）、`analysis.json`/`analysis.md`（AI 建议）、`snapshots/`（会话正文快照）、`analysis-runs/<运行ID>/`（摘要、提示词、草稿、校验错误）。

- 结果为 partial（AI 声明了覆盖缺口）时退出码 2、终端可能标红，属正常，打开 `report.html` 查看。
- 出现「未完成分析」时用终端最后一行的 `--resume <工作目录>` 续跑：先按当前规则复核最新草稿，通过则不再请求模型。超时不自动重试。
- 已有采集报告可复用：`--report <report.json>`；`--prepare-only` 只生成摘要和提示词，不请求模型。
- 频次不代表效果，「确认读取 0 次」也不等于没用过；AI 建议需要人审阅，工具不会修改任何 Skill。
- 费用参考（Claude Opus 级模型实测）：30 个会话约 $2–4，260 个会话约 $5。260 个会话的快照约 200MB，用 `skill-audit reports` 清理。

## 隐私与只读

- 被引用的历史片段会进入所选模型服务的上下文，不是纯本地分析；除此之外不上传任何数据。
- 联网只有两处：所选后端访问模型服务，以及 `update`/每日版本提示访问 `api.github.com` 与 GitHub Release 下载地址（只读公开信息，不带任何本地数据）。
- 快照、报告、草稿含历史对话原文，按 0700/0600 私有权限保存；脱敏只覆盖已知令牌和常见凭据格式，不保证识别全部敏感信息，分享报告前请自查。
- 只读保证：Codex 用操作系统沙箱 `--sandbox read-only`；Claude Code 只开放 Read/Grep/Glob（history 额外只放行 `analysis-read` 一个命令前缀），以 `--restricted --safe-mode` 隔离用户 hooks、插件、MCP 与 Skill。

## 反馈

请附上终端完整输出，以及 `analysis-runs/<运行ID>/` 下的 `error.json`、`draft-*.txt`（已脱敏，仍建议自查）。

## 开发

- 入口 `skill_audit_cli.py`；后端适配在 `agent_backends/`；历史采集 `history_pipeline.py`、`history_review.py`（来源适配）、`history_extract.py`（读取统计）；AI 汇总与校验 `history_analysis.py`；条例加载 `rulebook.py`。
- 新增后端：在 `agent_backends/` 加一个模块，声明必备保证、可选能力和认证方式并实现 `ready/run`，命令行选项、doctor 与打包自动包含。
- 测试：`python3 -m unittest discover -s tests`（临时夹具，不读真实历史、不请求模型）；`node tests/test_report_status.cjs` 检查 HTML 模板。
- 打包：`python3 build_dist.py` 生成 `dist/skill-audit.pyz` 与 `dist/skill-audit-team.zip`。
- 发版：改 `skill_audit_cli.py` 的 `APP_VERSION` 和下方版本记录，推送后在 GitHub 建 tag `vX.Y.Z` 的 Release，并上传 `skill-audit.pyz`（文件名固定，`update` 按名字找）与 `skill-audit-team.zip`。

## 版本

- **0.13.0**：新增 `skill-audit update`（从 GitHub Release 升级，含校验与自检）和每日最多一次的新版本提示。
- **0.12.0**：网关改为可选配置，不内置任何组织地址；新增受信任网关列表；移除已被 0.9 汇总流程取代的逐会话 AI 复盘遗留代码（`check history`、`history-read`、历史复盘条例 JSON、`--jobs`/`--batch-chars`）及 0.8.12 前旧目录回退。
- **0.11.0**：多执行后端（Codex / Claude Code），能力协商与降级，可分别指定主模型和子代理模型；证据比对容忍转义/引号差异，校验一次列出全部错误，续跑先复核草稿。
- **0.10.0**：证据驱动的改进建议（summary-v2）。
- **0.9.0**：脚本统计 + 单个总控 Agent 自主分治，取代逐会话 AI。

## 许可证

[MIT](LICENSE)
