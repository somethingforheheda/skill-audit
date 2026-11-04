"""Skill-use episodes: from the request that led to a Skill read to the user's next turns.

Script only, no model. Each episode carries signals (follow-up corrections, interruptions,
failed tools, re-reads) used to rank which episodes a reviewer or the AI should look at first.
Signals are leads, never conclusions.
"""
import json
import re
from collections import defaultdict

import history_review as h

FOLLOWUPS = 6            # user turns kept after the triggering request
CHAT_CAP, TOOL_CAP, CALL_CAP, BODY_CAP = 3000, 600, 400, 12000
# Phrases a user uses when the agent got it wrong or has to redo something.
CORRECTION = re.compile(
    r'不对|不是(?:这样|这个|我要|让你|说)|错了|搞错|弄错|理解错|没让你|谁让你|别(?:直接|动|改|乱|再|按照)|先别|'
    r'不要(?:再|直接|动|改|复用|用)|我(?:刚|之前|上面|前面|刚才)(?:不是|已经)?(?:说|讲)|我说的是|为什么(?:没|不|要|又|还)|'
    r'怎么(?:又|还|没)|还是(?:不行|没|有问题|报错|失败|不对)|又(?:报错|失败|出现|错|不行)|没(?:有)?按|你(?:没|漏)|漏了|'
    r'重新(?:来|做|改|弄)|回滚|撤销|撤回|不行|有问题|有个(?:大)?问题|失效|不生效|没生效|没效果|仍然|依然|不应该|而不是|'
    r'\bwrong\b|\bnot what\b|\bundo\b|\brevert\b')
INTERRUPT = re.compile(r'^\[Request interrupted by user')
INJECTED = re.compile(r'^\s*(?:<|# AGENTS\.md|Caveat:|Base directory for this skill:|This session is being continued)')


def flat(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(x for x in (flat(v) for v in value) if x)
    if isinstance(value, dict):
        if value.get('type') == 'tool_use':
            return str(value.get('name')) + ' ' + json.dumps(value.get('input'), ensure_ascii=False)
        for key in ('text', 'content', 'output', 'aggregatedOutput'):
            if key in value:
                return flat(value[key])
    return ''


def failed_output(text):
    return bool(re.search(r'"exit_code\\?"?:\s*-?[1-9]|^Script failed|Script error:', text, re.M))


def command_of(item):
    """Readable command for a tool call: the shell command when there is one."""
    raw = item.get('input', item.get('arguments'))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            pass
    if isinstance(raw, dict):
        for key in ('cmd', 'command'):
            if isinstance(raw.get(key), str):
                return raw[key]
        return json.dumps(raw, ensure_ascii=False)
    text = str(raw or '')
    # Codex exec scripts are JS source: pull out the literal cmd strings.
    found = []
    for literal in re.findall(r'\bcmd\s*:\s*("(?:[^"\\]|\\.)*")', text):
        try:
            found.append(json.loads(literal))
        except ValueError:
            found.append(literal)
    return '\n'.join(found) if found else text


def turns(value, source=None):
    """One raw event -> [{kind, text, tool?, failed?}]. kind: user/assistant/call/result/skill/marker/system."""
    if not isinstance(value, dict):
        return []
    typ, role = value.get('type'), value.get('role')
    if role in ('system', 'developer'):
        return [{'kind': 'system', 'text': '系统 / 开发者说明（已省略）'}]
    if typ in ('task_complete', 'turn_aborted', 'compaction', 'contextCompaction'):
        label = {'task_complete': '本轮结束', 'turn_aborted': '本轮被用户中断'}.get(typ, '上下文被压缩')
        return [{'kind': 'marker', 'text': label, 'interrupt': typ == 'turn_aborted'}]
    if typ in ('custom_tool_call', 'function_call'):
        return [{'kind': 'call', 'tool': value.get('name'), 'text': command_of(value)}]
    if typ == 'commandExecution':
        text = flat(value.get('aggregatedOutput'))
        return [{'kind': 'call', 'tool': 'shell', 'text': str(value.get('command'))},
                {'kind': 'result', 'text': text, 'failed': value.get('exitCode') not in (0, None)}]
    if typ in ('custom_tool_call_output', 'function_call_output'):
        text = flat(value.get('output'))
        return [{'kind': 'result', 'text': text, 'failed': failed_output(text)}]
    if typ in ('agentMessage',):
        return [{'kind': 'assistant', 'text': flat(value.get('text'))}]
    if typ in ('webSearch', 'mcpToolCall', 'fileChange', 'subAgentActivity', 'sleep'):
        return [{'kind': 'call', 'tool': typ, 'text': json.dumps(value, ensure_ascii=False)[:CALL_CAP]}]
    if typ == 'skill_listing':
        return [{'kind': 'system', 'text': '可用 Skill 列表（已省略）'}]
    content = value.get('content')
    if typ == 'userMessage':
        text = flat(content)
        text = text.split('## My request:', 1)[-1].strip()
        return [{'kind': 'user', 'text': text}]
    out = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get('type')
            if kind == 'tool_use':
                out.append({'kind': 'call', 'tool': block.get('name'), 'text': command_of(block)})
            elif kind == 'tool_result':
                text = flat(block.get('content'))
                out.append({'kind': 'result', 'text': text, 'failed': bool(block.get('is_error'))})
            elif kind in ('text', 'input_text', 'output_text'):
                out.append({'kind': 'assistant' if role == 'assistant' else 'user', 'text': block.get('text') or ''})
    elif isinstance(content, str):
        out.append({'kind': 'assistant' if role == 'assistant' else 'user', 'text': content})
    for item in out:
        if item['kind'] != 'user':
            continue
        text = item['text']
        if text.startswith('Base directory for this skill:'):
            item.update(kind='skill', text=text)
        elif INTERRUPT.match(text):
            item.update(kind='marker', text='本轮被用户中断', interrupt=True)
        elif re.match(r'^\s*<command-name>', text):
            name = re.search(r'<command-name>([^<]*)', text)
            args = re.search(r'<command-args>([^<]*)', text)
            item.update(text=(name[1] if name else '') + (' ' + args[1] if args and args[1].strip() else ''))
            if (name and name[1] in ('/context', '/compact', '/model', '/clear', '/cost', '/status')):
                item.update(kind='system')
        elif INJECTED.match(text):
            item.update(kind='system', text=text)
    return [x for x in out if x['text'] or x['kind'] in ('result', 'marker')]


ENV_TAGS = re.compile(r'<(in-app-browser-context|environment_context|system-reminder)\b[^>]*>[\s\S]*?</\1>')


def flatten(events):
    """Events -> items in order, each tagged with its event index."""
    items = []
    for index, event in enumerate(events):
        value = h.strict_json(event['text'])
        value = value.get('message', value) if isinstance(value.get('message'), dict) else value
        for item in turns(value):
            item['text'] = h.redact(ENV_TAGS.sub('[环境上下文已省略]', item['text'] or ''))
            item.update(event=index, event_id=event['event_id'], timestamp=event['timestamp'])
            items.append(item)
    return items


def read_events(events, reads):
    """Map each read row to the index of its request event and its result events."""
    where = {(e['path'], e['location']): i for i, e in enumerate(events)}
    def find(path, location):
        while location:
            if (path, location) in where:
                return where[(path, location)]
            location = location.rsplit('/', 1)[0] if '/' in location else ''
        return None
    out = []
    for row in reads:
        at = find(row['original_file'], row['request_location'])
        if at is None:
            continue
        results = {find(x['file'], x['location']) for x in row.get('result_locations') or []} - {None}
        out.append((at, results, row))
    return sorted(out, key=lambda x: x[0])


def signals(items, start, end, read_items):
    after = [x for x in items[start + 1:end + 1]]
    first_read = min(read_items) if read_items else start
    users = [x for x in after if x['kind'] == 'user']
    corrections = []
    for x in users:
        match = CORRECTION.search(x['text'])
        if match:
            corrections.append({'event': x['event'], 'match': match[0], 'text': x['text'][:300]})
    failures = [x['event'] for x in items[first_read:end + 1] if x['kind'] == 'result' and x.get('failed')]
    interrupts = [x['event'] for x in after if x.get('interrupt')]
    calls = sum(x['kind'] == 'call' for x in after)
    score = 3 * len(corrections) + 2 * len(interrupts) + 0.5 * min(len(failures), 6) + 0.5 * max(0, len(users) - 1)
    return {'followups': len(users), 'corrections': corrections, 'interrupts': len(interrupts),
            'failed_tools': len(failures), 'tool_calls': calls, 'score': round(score, 1)}


def build(key, events, reads):
    """Episodes of one session. reads: report rows for this session (any status)."""
    items = flatten(events)
    located = read_events(events, reads)
    users = [i for i, x in enumerate(items) if x['kind'] == 'user']
    first_item = {}
    for i, x in enumerate(items):
        first_item.setdefault(x['event'], i)
    groups = []
    for at, results, row in located:
        item = first_item.get(at)
        if item is None:
            continue
        trigger = max([u for u in users if u <= item], default=None)
        if trigger is None:
            continue
        name = row['name'] or row['skill']
        if groups and groups[-1]['trigger'] == trigger:
            groups[-1]['reads'].append((item, results, row))
            continue
        later = [u for u in users if u > trigger]
        end = later[FOLLOWUPS] - 1 if len(later) > FOLLOWUPS else len(items) - 1
        # A follow-up that reads the same Skills again belongs to the same episode (a re-read).
        if groups and item <= groups[-1]['end'] and name in groups[-1]['names']:
            groups[-1]['reads'].append((item, results, row))
            continue
        if groups and groups[-1]['end'] >= trigger:
            groups[-1]['end'] = trigger - 1
        groups.append({'trigger': trigger, 'end': end, 'names': set(), 'reads': [(item, results, row)]})
        groups[-1]['names'].add(name)
    episodes = []
    for g in groups:
        for _, _, row in g['reads']:
            g['names'].add(row['name'] or row['skill'])
        trigger = items[g['trigger']]
        read_items = [i for i, _, _ in g['reads']]
        skills = {}
        for _, _, row in g['reads']:
            if not (row['skill'] or row['name']):
                continue  # an unconfirmed read that never resolved to a Skill cannot be reviewed
            entry = skills.setdefault(row['skill'] or row['name'], {'name': row['name'], 'path': row['skill'],
                                                                    'reads': 0, 'statuses': []})
            entry['reads'] += 1
            entry['statuses'].append(row['status'])
        if not skills:
            continue
        episodes.append({
            'id': h.digest(key + ':' + trigger['event_id']), 'session': key,
            'request': trigger['text'][:600], 'start_event': trigger['event'], 'end_event': items[g['end']]['event'],
            'started_at': trigger['timestamp'], 'skills': list(skills.values()),
            'read_events': sorted({items[i]['event'] for i in read_items}),
            'read_ids': [row['read_id'] for _, _, row in g['reads']],
            'result_events': sorted({r for _, results, _ in g['reads'] for r in results}),
            'signals': signals(items, g['trigger'], g['end'], read_items),
        })
    return episodes, items


LABEL = {'user': '用户', 'assistant': 'AI', 'call': '工具调用', 'result': '工具返回', 'skill': 'Skill 正文注入',
         'marker': '标记', 'system': '系统'}


def clip(text, cap):
    text = text.strip()
    return text if len(text) <= cap else text[:cap] + f'…（截断，原文 {len(text)} 字）'


def render(items, first, last, reads=()):
    """Readable transcript of events first..last (inclusive) for a model or a person.

    Each line starts with #<event index> so findings can point back at the snapshot.
    """
    lines, reads = [], set(reads)
    for x in items:
        if not first <= x['event'] <= last or x['kind'] == 'system':
            continue
        tag = f"#{x['event']} {LABEL[x['kind']]}"
        if x['kind'] == 'call':
            tag += f"[{x.get('tool')}]"
            body = clip(x['text'], CALL_CAP)
        elif x['kind'] == 'result':
            tag += '（失败）' if x.get('failed') else ''
            # The Skill body itself is shown once from the current file, not from the history.
            body = '（Skill 正文，略）' if x['event'] in reads else clip(x['text'], TOOL_CAP)
        elif x['kind'] == 'skill':
            body = clip(x['text'].split('\n', 1)[0], 200) + '（Skill 正文，略）'
        elif x['kind'] == 'marker':
            body = x['text']
        else:
            body = clip(x['text'], CHAT_CAP)
        lines.append(f'{tag}：{body}')
    return '\n'.join(lines)


def session_digest(key, items, cap=200):
    """One line per user turn plus counts: input for spotting recurring workflows across sessions."""
    users = [x for x in items if x['kind'] == 'user']
    tools = defaultdict(int)
    for x in items:
        if x['kind'] == 'call':
            word = (x['text'].strip().split() or [''])[0][:30] if x.get('tool') in ('exec', 'exec_command', 'Bash', 'shell') else x.get('tool')
            tools[str(word)] += 1
    return {'session': key, 'started_at': items[0]['timestamp'] if items else None,
            'user_turns': len(users), 'tool_calls': sum(tools.values()),
            'top_tools': sorted(tools.items(), key=lambda kv: -kv[1])[:12],
            'requests': [f"#{x['event']} " + clip(x['text'].replace('\n', ' '), cap) for x in users]}
