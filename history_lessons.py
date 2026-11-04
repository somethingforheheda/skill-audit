"""Guards for Skill improvement proposals: prompt blocks and deterministic checks.

The model proposes; the script never edits a Skill. The prompt blocks say what must not be
written down as a lesson and how an improvement is shaped; the checks below only attach flags
(or drop a proposal that cannot be shown safely) so a person can judge it against the transcript.
"""
import re

ACTIONS = ('in_place_patch', 'add_script', 'split_to_reference')
RULE_FILES = ('AGENTS.md', 'CLAUDE.md')
SUPPORT_DIR = {'add_script': 'scripts', 'split_to_reference': 'references'}
LONG_PROPOSAL_LINES = 40
STEM = r'[A-Za-z][\w]*'
TICKET = re.compile(rf'(?<![\w/])(?:#\d{{3,}}|(?:PR|pr|Pr|issue|Issue)[ -]?#?\d+|{STEM}(?:-{STEM})*-\d{{4,}})(?![\w])')
DATE = re.compile(r'\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b|20\d{2}年\d{1,2}月')
APPENDED = re.compile(r'^\s*(?:UPDATE|Update|更新|补充)\s*[:：]', re.M)
TOOL_BAN = re.compile(r'不可用|用不了|无法使用|已损坏|坏了|does not work|is broken', re.I)
KEBAB = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
ARTIFACT_NAME = re.compile(r'^(?:fix|debug|audit|hotfix)-|-today$')

DO_NOT_CAPTURE = """## 不要把这些写成可沉淀的经验（它们会变成长期自我设限）
- 环境类失败：缺命令、缺依赖、没配置凭证、迁移后路径对不上。最多写成「前置检查 / 安装步骤」，不要写成「某工具不能用」。
- 对工具或功能的否定判断（「X 坏了」「Y 用不了」）：问题修好之后它们还会被一直引用。
- 会话结束前已经恢复的瞬时错误：重试成功时，值得记的是重试办法，不是当初的失败。
- 一次性任务的叙事：不属于一类会反复出现的任务。
- 未解决的失败：会话最终没有走通（AI 试了几种都不行，最后让用户自己查），不要把这些尝试写成步骤或「推荐流程」。只有片段里能看到一条**已经走通**的做法时，才可以只记录那条做法，不记录走不通的尝试。"""

LESSON_SHAPE = """## 教训写法
- 一条规则 = 祈使句 + 一句原因（机制）。例如「改公共函数签名前先全仓搜索符号——手写的替身会复制旧签名，在没跑到的分片上才失败」。不是对本次会话的叙述。
- 不写 PR / 卡片号、日期、提交号、用户原话；规则脱离当次事件也要成立。只有原话本身就是最清楚的表述时才留一小段引用。
- 同一条教训只留一条：先在该 Skill 当前正文里找有没有已写过的相近规则，有就加强或澄清它，不要再写第二遍。
- 不重复环境里已经教过的内容：项目规则文件（AGENTS.md / CLAUDE.md）、工具自己的参数说明。Skill 承载的是流程和坑点，不是代码地图。
- 一条教训只放在一个地方（项目规则 / 这个 Skill / 别的 Skill），不要两边都写。"""

LADDER = """## 建议动作阶梯（选最靠前、能解决问题的一种，写在 `proposal.action`）
1. `in_place_patch`：改 SKILL.md 里误导 AI 的那一句，给出 `old_string`（当前正文逐字，不含行号前缀）和 `new_string`；规则缺失则 `old_string` 为 null，在 `after_line` 之后加入 `new_string`。规则写错、含糊或太弱时必须原位替换原句，不要在它下面追加「上文作废」「更新：」。
2. `add_script`：AI 在 ≥2 次使用中都临时手写同一段复杂命令或解析脚本，并且写错过参数 → 建议做成 `scripts/<名>.<后缀>`，`draft_content` 给草稿全文，`pointer` 写 SKILL.md 里要加的一行指引。
3. `split_to_reference`：SKILL.md 已很长（接近 500 行或 24000 字符），或这条建议自己超过 40 行（完整对照表、错误码表等）→ 放进 `references/<主题>.md`，文件名按主题，不按日期、单号或会话命名；`pointer` 写 SKILL.md 里「什么时候去读它」的一行。
4. 问题不在 Skill 文件里（或证据不足以改）→ `proposal` 为 null，只写 `recommendation`。
常驻规则（每次做这类任务都必须遵守的门槛）整条写在 SKILL.md 里，不要藏进 references/。你只看得到 SKILL.md，不能改已有的 references/ 或 scripts/ 文件，只能建议新增。"""

JUDGE_GUARDS = """## 判断时的限制
- 归因为 `environment`（缺命令、缺凭证、网络、权限）时，`fix` 填 null。
- `outcome` 为 `fail`，或片段结束时仍没有找到可行办法时，`reusable_workflow` 填 null：不要把走不通的尝试当成可复用流程。
- `fix` 写成可泛化的一条规则（祈使句 + 一句原因），不写对工具的否定判断，不写本次的文件名、PR 号、日期。
- 用户纠正的是风格、语气、格式、详略时，同样算纠正；这类偏好也记下来，供后面判断是否需要写进某个 Skill 或项目规则。"""

MINING_GUIDE = """## 沉淀方式（按顺序，选最靠前的）
1. 并入已有 Skill 的一个小节（`action=merge_into`），优先于新建。
2. 内容很长的部分放 references/，反复手写的命令做成 scripts/，在 steps / pitfalls 里注明。
3. 只有现有 Skill 都不覆盖这一类任务时才 `new`。名称必须是工作流的类别，不含卡片号、报错串、功能代号，也不要 fix-X、debug-Y 这类一次性名字；名字只对今天的任务成立，就退回 1 或 2。
`new` 的骨架：何时用（trigger）→ 前置条件（prerequisites）→ 步骤（steps）→ 坑点（pitfalls）→ 验证（verification）。"""


def flag(code, message):
    return {'code': code, 'message': message}


def text_or_none(value):
    return value if isinstance(value, str) and value.strip() else None


def lesson_flags(text):
    flags = []
    if TICKET.search(text):
        flags.append(flag('incident_ref', '含单号 / PR 号，规则应脱离具体事件也成立'))
    if DATE.search(text):
        flags.append(flag('dated', '含日期，疑似事件叙述'))
    if APPENDED.search(text):
        flags.append(flag('appended_update', '像在旧规则下追加「更新」，应原位改写原句'))
    if TOOL_BAN.search(text):
        flags.append(flag('tool_ban', '含对工具的否定判断，修好后会变成过时禁令'))
    return flags


def squash(text):
    return re.sub(r'\s+', ' ', text).strip()


def clean_proposal(value, texts, files=('SKILL.md',), support=True):
    """A usable proposal dict with flags, or None when it cannot be shown or is a no-op."""
    if not isinstance(value, dict) or value.get('action') not in ACTIONS:
        return None
    action, flags = value['action'], []
    target = text_or_none(value.get('target_file')) or ('SKILL.md' if action == 'in_place_patch' else None)
    old, new = text_or_none(value.get('old_string')), text_or_none(value.get('new_string'))
    draft, pointer = text_or_none(value.get('draft_content')), text_or_none(value.get('pointer'))
    line = value.get('after_line') if isinstance(value.get('after_line'), int) and not isinstance(value.get('after_line'), bool) else None
    if action == 'in_place_patch':
        if new is None or target not in files or (old and squash(old) == squash(new)):
            return None
        body = texts.get(target)
        if body is not None:
            if old and old not in body and squash(old) not in squash(body):
                flags.append(flag('anchor_missing', '旧句在当前正文里找不到，采纳前请手动定位'))
            if not old and line is not None and line > len(body.splitlines()):
                flags.append(flag('line_out_of_range', f'第 {line} 行超出当前正文行数'))
        if not old and line is None:
            flags.append(flag('no_position', '没有给出插入位置'))
        if len(new.splitlines()) > LONG_PROPOSAL_LINES:
            flags.append(flag('too_long', f'超过 {LONG_PROPOSAL_LINES} 行，建议拆进 references/'))
        flags += lesson_flags(new)
    else:
        folder = SUPPORT_DIR[action]
        pattern = rf'{folder}/[A-Za-z0-9][\w.-]*' + (r'\.md' if action == 'split_to_reference' else '')
        if not support or draft is None or not target or not re.fullmatch(pattern, target) or '..' in target:
            return None
        if TICKET.search(target) or DATE.search(target):
            flags.append(flag('named_by_incident', '文件名带单号或日期，应按主题命名'))
        if pointer is None:
            flags.append(flag('no_pointer', '没有 SKILL.md 里的指引，新文件不会被读到'))
        if action == 'split_to_reference':
            flags += lesson_flags(draft)
        if pointer:
            flags += lesson_flags(pointer)
    return {'action': action, 'target_file': target, 'old_string': old, 'new_string': new, 'after_line': line,
            'draft_content': draft, 'pointer': pointer, 'flags': flags}


def judge_guards(value):
    """Enforce in code what the judge prompt asks: no fix for environment faults, no workflow from a failed run."""
    notes = []
    for issue in value.get('issues') or []:
        if issue.get('attribution') == 'environment' and issue.get('fix'):
            issue['fix'] = None
            notes.append('environment_fix_removed')
    if value.get('outcome') == 'fail' and value.get('reusable_workflow'):
        value['reusable_workflow'] = None
        notes.append('failed_workflow_removed')
    if notes:
        value['guards'] = notes
    return value


def candidate_flags(row, existing):
    name = row.get('name') if isinstance(row.get('name'), str) else ''
    flags = []
    if not KEBAB.match(name):
        flags.append(flag('not_kebab', 'Skill 名应为小写短横线格式'))
    if TICKET.search(name) or DATE.search(name) or ARTIFACT_NAME.search(name):
        flags.append(flag('named_by_incident', '名字像一次性任务或带单号，应改成类级别名称'))
    if row.get('action') == 'new' and name in existing:
        flags.append(flag('name_exists', '已有同名 Skill，应并入而不是新建'))
    if row.get('action') == 'merge_into' and existing and row.get('merge_target') not in existing:
        flags.append(flag('target_missing', '并入的目标 Skill 不在当前目录里'))
    body = ' '.join(str(x) for x in (row.get('pitfalls') or []) + (row.get('steps') or []))
    return flags + lesson_flags(body)
