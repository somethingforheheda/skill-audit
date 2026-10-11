"""Read-only access to local session snapshots: candidate detection, tool-call links, event stream."""
import json
from pathlib import Path
import re

import history_review as h


def is_candidate(content):
    if isinstance(content,list):return any(is_candidate(v) for v in content)
    if not isinstance(content,dict):return False
    typ=str(content.get('type','')).lower()
    name=str(content.get('name',content.get('tool',''))).lower()
    # Tool output mentioning SKILL.md is not a request to read it.
    if typ in ('function_call_output','custom_tool_call_output','tool_result'):
        return False
    if name in ('skill','skill_view') or typ in ('skill','skill_content','invoked_skill'):
        return True
    raw=json.dumps(content,ensure_ascii=False)
    request={k:v for k,v in content.items() if k not in ('output','result','results','tool_result','tool_response')}
    if (name or typ in ('function_call','custom_tool_call','tool_use','commandexecution')) and re.search(r'SKILL\.md|skill_view',json.dumps(request,ensure_ascii=False),re.I):
        return True
    if typ in ('message','text') or content.get('role')=='user':
        # Explicit body injection marker; mere available-skill catalog is excluded.
        if re.search(r'Base directory for this skill:|<skill_content>',raw,re.I):return True
    return any(is_candidate(v) for k,v in content.items() if k not in ('output','result','results') and isinstance(v,(dict,list)))


def identifiers(content):
    if isinstance(content,list):return set().union(*(identifiers(x) for x in content))
    if not isinstance(content,dict):return set()
    found={content[k] for k in ('call_id','tool_use_id') if isinstance(content.get(k),str)}
    if content.get('type')=='tool_use' and isinstance(content.get('id'),str):found.add(content['id'])
    for v in content.values():
        if isinstance(v,(dict,list)):found.update(identifiers(v))
    return found


def events(manifest):
    data=h.strict_json(Path(manifest).read_text(encoding='utf-8'))
    path=Path(data['snapshot'])
    h.require(path.resolve().parent==Path(manifest).resolve().parent,'快照必须位于会话清单目录')
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            event=h.strict_json(line)
            h.require(isinstance(event,dict) and isinstance(event.get('text'),str),'事件格式错误')
            yield event
