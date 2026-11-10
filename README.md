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

- 每条命令用 `--backend codex|claude-code`（别名 `claude`）选后端，默认 codex；`--model`、`--subagent-model` 覆盖 init 保存的值；`--effort low|medium|high|xhigh|max` 指定思考强度（只有 `claude-code` 后端支持，codex 后端会明确报错而不是悄悄忽略）。模型写全名，不要用 `opus`/`sonnet` 这类会被用户设置重新映射的别名。
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
skill-audit sync init --server http://主机:端口 --user 邮箱前缀   # 把会话历史同步到团队服务器，见「团队同步」
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

流程（默认 `--method episodes`）：

1. **纯脚本采集**：读取本机 `~/.claude/projects`、`~/.codex`、`~/.comate-engine/store`（`--sources`、`--history-home` 可调）中工作目录为当前项目的最近 N 个会话（`--limit`，默认 20；`--days`/`--from`/`--to` 限时间，`--all-projects` 不限项目），脱敏后存为本地快照，确定性统计每次 Skill 正文读取。`--collect-only` 只做这一步，不需要模型。
2. **切出每次 Skill 使用**（脚本）：从触发读取的那条用户消息开始，到之后 6 轮用户发言为止，算出信号：纠正/否定用语、本轮被中断、工具失败、追问轮数，按信号排序。每个 Skill 至少选一次，再按信号补满 `--max-episodes`（默认 60）。
3. **AI 逐段审查**（并行 `--workers`，默认 4）：每段连同 AI 当时读到的 Skill 正文交给模型，判断有没有纠正/返工/打断，并归因为「AI 没按 Skill 做」「Skill 缺内容或写错」「AI 自身问题」「需求变化」「环境」，引用事件号、用户原话和 Skill 原句。
4. **汇总结论**：同一 Skill、同一根因合成一条，核对 Skill 当前正文（已修复的单列），给出插入到第几行之后的补丁草稿；跨 Skill 重复出现的 AI 行为问题单独成条。
5. **挖掘新 Skill**：从全部会话的用户消息摘要中找 ≥2 个会话重复出现、现有 Skill 未覆盖的工作流，给出 name/触发描述/步骤/坑点/验证草稿；反复要求 AI 的行为偏好建议写进 AGENTS.md 等。

程序只检查引用的片段、会话是否存在，不再逐字校验引文：`report.html` 每条结论下列出全部相关会话，聊天记录默认只展开 Skill 读取与问题发生处前后，可「加载上面/下面 10 条」或展开全部，由人判断结论是否成立。报告内嵌会话原文，权限 0600，只在本机查看。

旧版统计优先汇总仍可用 `--method summary`（`--rules-file`、`--attempts`、`--subagent-model` 只对它生效）。

产出在 `~/.skill-audit/reports/<项目>-<时间>-<随机>/`：`report.json`（采集统计）、`report.html`（离线页面）、`analysis.json`/`analysis.md`（AI 建议）、`snapshots/`（会话正文快照）、`analysis-runs/<运行ID>/`（摘要、提示词、草稿、校验错误）。

- 结果为 partial（AI 声明了覆盖缺口）时退出码 2、终端可能标红，属正常，打开 `report.html` 查看。
- 中断或某阶段失败时用 `--resume <工作目录>` 续跑：已完成的逐段审查和阶段结果直接复用，只补跑缺的部分。
- 已有采集报告可复用：`--report <report.json>`；`--prepare-only` 只生成摘要和提示词，不请求模型。
- 频次不代表效果，「确认读取 0 次」也不等于没用过；AI 建议需要人审阅，工具不会修改任何 Skill。
- 费用参考（Opus 级模型实测）：episodes 每段约 $0.2–0.3，174 个会话切出 134 段全审约 $32，汇总+挖掘约 $4；先用 `--prepare-only` 看会切出多少段，再用 `--max-episodes` 控制。summary 方法 30 个会话约 $2–4。260 个会话的快照约 200MB，用 `skill-audit reports` 清理。

## 团队同步（sync）

让团队成员把本机的 AI 会话历史定期同步到一台自建服务器，在那里集中浏览、分析。不运行 `sync` 就不会上传任何会话。

```bash
skill-audit sync init --server http://主机:端口 --user 你的邮箱前缀   # 保存到 ~/.skill-audit/sync.json（0600）
skill-audit sync run --dry-run     # 只列出将要上传的内容
skill-audit sync run               # 先告诉你传多少、确认后上传；--days N 只传最近 N 天，--yes 不再确认
skill-audit sync status            # 服务器上已有多少
skill-audit sync install           # macOS 用 launchd、Linux 用 cron，默认每 4 小时同步一次（--hours 可改）
skill-audit sync uninstall
```

- 同事机器上没有 skill-audit 时，不必访问 GitHub：服务器网页的“团队接入”页给出一条从服务器下载的命令（`curl … /download/skill-audit.pyz`），装好后同步时再自动更新。
- 读取 `~/.claude/projects`、`~/.codex/sessions`、`~/.comate-engine/store`（`--history-home` 可换），只上传服务器上没有或已变化的文件，并且在上传前先算 SHA-256 让服务器核对：内容和服务器上已有的会话相同（改过时间戳、换了文件名、复制了一份）的不再上传，服务器只记一条“同上”的记录；超过 128 MB 的会话工具本身读不了，直接跳过。
- 同时上传这些会话里读过的 Skill 目录（只含小文本文件，每个目录最多 200 个），服务器据此核对补丁草稿所针对的当前正文。只传放在仓库或你自己的 `.agents/skills`、`.claude/skills`、`.codex/skills` 里的 Skill，插件缓存和工具自带的不传；Claude Code 的 Skill 工具只记目录不记 `SKILL.md`，这种也能识别。不想上传 Skill：`sync init --no-skills`（写进 `sync.json`，以后都不传）或 `sync run --no-skills`（只这一次）；重新跑一次不带该参数的 `sync init` 即可恢复。
- `sync run` 开头会问服务器有没有更新的发行包（服务器用 `SA_PYZ` 指向的文件，通过 `/api/sync/version` 和 `/download/skill-audit.pyz` 提供）：有就下载，核对 SHA-256 和 `--version`，替换当前安装并重新运行；失败只提示，不影响同步。`--no-update`、环境变量 `SKILL_AUDIT_NO_UPDATE_CHECK=1`、或 `sync.json` 里写 `"auto_update": false` 可关闭。更新来源就是你配置的服务器，用 http 时同网段的人可以篡改传输内容，所以只适合可信内网，或给服务器配 https；服务器上的发行包要在每次发版后换成新版。
- 身份就是 `--user` 填的 id，没有密码或令牌，只适合在可信内网里使用；传输是否加密取决于服务器地址用 http 还是 https。唯一的例外是服务器用 `SA_ADMINS` 指定的管理员账号：网页登录要密码，同步上传仍只认 id。
- 上传的是**会话原文**，不做脱敏。只把服务器地址设为你信任的机器。

服务端在 `server/`：会话传上来就一直保留，归在上传人名下，每条记录带上传人 id，同一用户内容相同的会话只存一份（重复的只记别名、不占空间、不参与分析）；网页的“手动上传”也一样，“上传并开始分析”是先存到自己名下再分析，普通同事只传不分析，由管理员用自己的 Key 统一分析；`SA_KEEP_UPLOADS=0` 才会在一次性任务结束后删掉任务目录里的会话。纯标准库的 Web 应用（Python 3.9+），收同步、按用户和会话工作目录归类、可从已同步的会话发起 `history` 分析并排队执行、提供报告页。环境变量见 `server/config.py`（`SA_DATA`、`SA_PORT`、`SA_AUDIT_CMD`、`SA_ENV_FILE`、`SA_PYZ` 等）；`python3 server/app.py` 启动，网页上可停止任务、选主模型和子代理模型、选思考强度；`SA_MODEL` 设默认模型，`SA_MODELS`（逗号分隔）给网页一个候选列表；`SA_USER_KEY=required` 要求每个用户自己填 API Key（网关地址仍由 `SA_ENV_FILE` 里的 `ANTHROPIC_BASE_URL` 固定），key 只在分析期间留在服务器内存里并通过环境变量 `SA_KEY_VAR`（默认 `ANTHROPIC_AUTH_TOKEN`）交给分析进程，不写入磁盘；`SA_EXTRA_PATH` 放 `claude` 所在目录。登录层在 `server/auth.py`，默认只校验邮箱前缀格式，接入公司统一登录时替换这一个文件。分析时用 `history --history-home <任务目录> --skills-catalog <任务自己的 Skill 目录> --skills-root <仓库的 skills 目录>`，所以服务器上不需要和用户相同的路径：任务目录里只放所选工作目录所在仓库的 Skill（仓库里一个都没有时才退回用户全局的），报告只统计这些目录里的读取；会话来自别的机器、Skill 又没同步时，状态显示为“未知”而不是“已删除”。API Key、模型、思考强度在网页的“设置”页保存在浏览器里，开始分析时才发给服务器；`SA_GATEWAY_HOSTS`（逗号分隔）限定 Key 只能发往这些 https 主机，创建任务时先发一个 1 token 的请求，Key 或模型名不对立刻报错，网关连上却不回应也会说清楚。`SA_ADMINS`（逗号分隔）指定管理员，登录时还要输入 `data/admin.key` 里的密码（没有这个文件就不能以管理员登录）；管理员多一个“全部会话”页：按目录汇总所有人的会话并显示每个会话属于谁，选目录后可以用所有人的会话做分析，这类任务的报告只有管理员能看。

## 隐私与只读

- 历史内容会进入所选模型服务的上下文，不是纯本地分析：episodes 方法发送被选中的每段 Skill 使用片段（工具输出截断）和各会话的用户消息摘要（每条截断到 160 字）；summary 方法只发送被引用的片段。除此之外，只有你主动运行 `sync` 时才会把会话文件和读过的 Skill 上传到你配置的服务器。
- 联网只有两处：所选后端访问模型服务，以及 `update`/每日版本提示访问 `api.github.com` 与 GitHub Release 下载地址（只读公开信息，不带任何本地数据）。
- 快照、报告、草稿含历史对话原文，按 0700/0600 私有权限保存；脱敏只覆盖已知令牌和常见凭据格式，不保证识别全部敏感信息，分享报告前请自查。
- 只读保证：Codex 用操作系统沙箱 `--sandbox read-only`；Claude Code 只开放 Read/Grep/Glob（history 额外只放行 `analysis-read` 一个命令前缀），以 `--restricted --safe-mode` 隔离用户 hooks、插件、MCP 与 Skill。

## 反馈

请附上终端完整输出，以及 `analysis-runs/<运行ID>/` 下的 `error.json`、`draft-*.txt`（已脱敏，仍建议自查）。

## 开发

- 入口 `skill_audit_cli.py`；后端适配在 `agent_backends/`；历史采集 `history_pipeline.py`、`history_review.py`（来源适配）、`history_extract.py`（读取统计）；Skill 使用片段 `history_episodes.py`、逐段审查/汇总/挖掘 `history_skill_review.py`、旧版汇总与校验 `history_analysis.py`；条例加载 `rulebook.py`。
- 新增后端：在 `agent_backends/` 加一个模块，声明必备保证、可选能力和认证方式并实现 `ready/run`，命令行选项、doctor 与打包自动包含。
- 测试：`python3 -m unittest discover -s tests`（临时夹具，不读真实历史、不请求模型）；`node tests/test_report_status.cjs` 检查 HTML 模板。服务端测试：`cd server && python3 -m unittest discover -s tests`（用假的分析命令，不请求模型）。
- 打包：`python3 build_dist.py` 生成 `dist/skill-audit.pyz` 与 `dist/skill-audit-team.zip`。
- 发版：改 `skill_audit_cli.py` 的 `APP_VERSION` 和下方版本记录，推送后在 GitHub 建 tag `vX.Y.Z` 的 Release，并上传 `skill-audit.pyz`（文件名固定，`update` 按名字找）与 `skill-audit-team.zip`。

## 版本

- **0.17.6**：`sync` 上传前先核对内容哈希，内容相同（时间戳变了、换了文件名、复制了一份）的会话不再上传；服务器按内容去重并记录上传人，重复的不占空间、不参与分析；上传的会话不再在分析后删除；网页手动上传新增“按工作区筛选”“上传前确认并跳过相同会话”“只上传到服务器”（同事不需要 API Key），“上传并开始分析”改为先存到自己名下；分析可以按明确的会话列表发起。
- **0.17.5**：修复 Python 3.9 下提示词写入子进程可能卡死、分析全部失败的问题；服务端新增“设置”页（API Key、模型、思考强度）、分析范围改为“最近 N 天”或“最近 N 个会话”二选一、完成后报告直接嵌在任务页、网站图标、`SA_GATEWAY_HOSTS` 网关白名单和创建任务时的快速预检；报告只统计所选仓库自己的 Skill，会话来自别的机器时不再把 Skill 全部划掉（改显示“状态未知”）；`sync` 只上传仓库或个人 skills 目录里的 Skill，识别 Skill 工具写的目录，新增 `--no-skills`；管理员账号（`SA_ADMINS` + 密码）可以看到所有人的会话和归属并用所有人的会话分析。
- **0.17.4**：`history` 新增 `--effort`（思考强度，仅 claude-code 后端）；服务端网页重做：顶部导航、左边选目录右边固定的发起分析卡片、目录筛选、可复制的接入命令、任务页状态/详情/日志分开，并补了键盘焦点、跳到正文、转义用户数据等细节；网页可停止任务、选模型和思考强度、填自己的 API Key（见服务端说明）；`sync run` 的进度条改成始终只占一行。
- **0.17.3**：`sync run` 查找会话里读过的 Skill 时不再卡住：旧写法的正则遇到几十 MB 的大会话几乎算不完，改为从每个 `SKILL.md` 出现的位置往回找路径，326 个、共 1 GB 的会话约 5 秒扫完；读取工作目录、查找 Skill、上传三个阶段都有进度条（上传按字节显示百分比、速度、剩余时间和当前文件，非终端时每 15 秒一行），Ctrl-C 干净退出，下次只传剩下的。
- **0.17.2**：没有功能变化，只升版本号，用来实测 `sync` 从服务器自动更新（0.17.1 → 0.17.2）。
- **0.17.1**：`sync run` 开头会问服务器有没有更新的发行包，有就下载、核对 SHA-256 和 `--version`、替换当前安装并重新运行，失败只提示不影响同步；`--no-update`、`SKILL_AUDIT_NO_UPDATE_CHECK=1` 或 `sync.json` 里的 `"auto_update": false` 可关闭。服务端新增 `/api/sync/version`，版本号直接从下载用的发行包里读取。
- **0.17.0**：新增 `sync`（init / run / status / install / uninstall）：把本机 Claude Code、Codex、Comate 的会话历史和会话里读过的 Skill 增量同步到团队服务器，可注册 launchd / cron 定时任务；`history` 新增 `--skills-catalog`，只补充 Skill 当前正文来源、不限制会话读取范围；新增 `server/`（标准库 Web 应用）接收同步、按用户和目录浏览、从已同步会话发起分析并排队、提供报告页。
- **0.16.0**：Skill 改进建议按 Hermes 的做法收紧：判断、汇总、挖掘三个提示词加入「不要沉淀」的五类情形、教训写法（祈使句加一句原因，不写单号和日期）和建议动作阶梯；每条结论的补丁换成带检查的 `proposal`（原位改写 / 新增脚本 / 拆进参考文件），脚本只打标记不删除（原句找不到、行号越界、路径不安全、含单号或日期、对工具的否定判断等）；环境类问题不留修改建议，失败片段不留可复用流程；报告展示改写前后对照并可复制；修复未能确认 Skill 名的读取导致审查崩溃的问题。审查结果版本为 `episodes-v2`，旧断点不能续跑。
- **0.15.0**：`history` 默认改为逐次 Skill 使用审查（`--method episodes`）：脚本切片段打信号，AI 逐段判断纠正/返工并归因，汇总为带补丁草稿的 Skill 问题，另挖掘新 Skill 候选与行为偏好；报告每条结论列出全部相关会话并内嵌可分段加载的聊天记录，校验只拦不存在的引用。旧流程保留为 `--method summary`。
- **0.14.0**：「AI 汇总分析」改为按结论分块的卡片，颜色表示严重度（红：淘汰且高优先级，橙：待处理，黄：可改进，绿：保留），先给一句话结论再列问题依据；校验精确到每条证据（校验通过 / 校验不通过 + 原因）。`analysis.json` 的 `validation` 改为 `{checked, passed, status}`，旧报告需重新生成才有逐条标注。
- **0.13.0**：新增 `skill-audit update`（从 GitHub Release 升级，含校验与自检）和每日最多一次的新版本提示。
- **0.12.0**：网关改为可选配置，不内置任何组织地址；新增受信任网关列表；移除已被 0.9 汇总流程取代的逐会话 AI 复盘遗留代码（`check history`、`history-read`、历史复盘条例 JSON、`--jobs`/`--batch-chars`）及 0.8.12 前旧目录回退。
- **0.11.0**：多执行后端（Codex / Claude Code），能力协商与降级，可分别指定主模型和子代理模型；证据比对容忍转义/引号差异，校验一次列出全部错误，续跑先复核草稿。
- **0.10.0**：证据驱动的改进建议（summary-v2）。
- **0.9.0**：脚本统计 + 单个总控 Agent 自主分治，取代逐会话 AI。

## 许可证

[MIT](LICENSE)
