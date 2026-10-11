"""Deterministic Skill body-read extraction. Never executes historical commands."""
import json
import os
import re
import shlex
import stat
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
import history_reader as reader
import history_review as h
import skill_scope


def blocks(value):
    if isinstance(value, list):
        for v in value: yield from blocks(v)
    elif isinstance(value, dict):
        if value.get('type') in ('function_call', 'custom_tool_call', 'tool_use',
                                  'function_call_output', 'custom_tool_call_output', 'tool_result'):
            yield value
        else:
            for v in value.values():
                if isinstance(v, (dict, list)): yield from blocks(v)


def event_blocks(content, source):
    """Normalize Comate's paired TOOL nodes; never parse user text as tool calls."""
    if source != 'comate':
        yield from blocks(content)
        return
    if content.get('role') != 'assistant':return
    def walk(value, location):
        if isinstance(value,list):
            for i,item in enumerate(value):yield from walk(item,f'{location}/{i}')
        elif isinstance(value,dict):
            if value.get('discard'):return
            if value.get('type') == 'TOOL':
                call=value.get('id')
                result=value.get('result')
                result=result if isinstance(result,dict) else {}
                metadata=result.get('metadata') or {}
                params=decoded(value.get('params',{}))
                if isinstance(params,dict):
                    params=dict(params)
                    if metadata.get('cwd') and not params.get('cwd') and not params.get('workdir'):
                        params['cwd']=metadata['cwd']
                state=value.get('toolState')
                error=state in ('failed','error','rejected','cancelled') or metadata.get('exitCode',0) not in (0,None) or bool(result.get('isError'))
                shared={'call_id':call,'_location':location,'_timestamp':value.get('startTime')}
                request={'type':'function_call','name':value.get('toolName',''),'arguments':params,**shared}
                if request['name']=='skill':
                    output=result.get('output','')
                    # Explicit source directory + returned body constitute an injection.
                    match=re.match(r'\A## Skill: ([^\n]+)\n+\*\*Base directory\*\*: ([^\n]+)\n+(\S[\s\S]*)',output) if isinstance(output,str) else None
                    if match and isinstance(params,dict) and match[1].strip()==params.get('name'):
                        path=path_for(match[2].strip().rstrip('/')+'/SKILL.md',None)
                        if path and '[UNAVAILABLE BLOB]' not in match[3]:
                            request.update(name='read_file',arguments={'path':path},_injection=True)
                yield request
                if ('result' in value and state=='executed') or error:
                    output=result.get('output','')
                    if '[UNAVAILABLE BLOB]' not in str(output):
                        yield {'type':'function_call_output','output':output,'is_error':error,**shared}
                return
            for key in ('children','elements'):
                if key in value:yield from walk(value[key],location+'/'+key)
    yield from walk(content.get('elements',[]),'/elements')


def decoded(value):
    if isinstance(value, str):
        try: return json.loads(value)
        except ValueError: pass
    return value


def path_for(raw, cwd, home=None):
    if not isinstance(raw, str) or not raw.endswith('SKILL.md'): return None
    if raw.startswith('~/') and home:raw=home+raw[1:]
    if any(c in raw for c in ('$','~','*','?','`')): return None
    if not os.path.isabs(raw):
        if not cwd: return None
        raw=os.path.join(cwd,raw)
    return os.path.normpath(raw)


def shell_reads(command, cwd, home=None):
    """Recognize literal cat/head/tail/sed input paths; rg/echo/listings are not reads."""
    found=[]
    # A heredoc contains source/data, not executed shell commands. Remove its body.
    lines=command.splitlines(keepends=True);clean=[];delimiter=None
    for line in lines:
        if delimiter is not None:
            if line.strip()==delimiter:delimiter=None
            continue
        clean.append(line)
        match=re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?",line)
        if match:delimiter=match.group(1)
    command=''.join(clean).replace(chr(92)+chr(10),'')
    # Expand only bounded literal for-lists, never evaluate shell expressions.
    loop=re.search(r'\bfor ([A-Za-z_]\w*) in ([A-Za-z0-9_ ./-]+);\s*do\s+(.*?)\bdone\b',command,re.S)
    if loop and not re.search(r'\b(?:for|while|if|cd)\b',loop[3]) and not any('$'+loop[1] in quoted or '${'+loop[1]+'}' in quoted for quoted in re.findall(r"'[^']*'",loop[3])) and chr(92)+'$' not in loop[3]:
        values=loop[2].split()
        if 0<len(values)<=100:
            body='\n'.join(re.sub(r'\$\{'+loop[1]+r'\}|\$'+loop[1]+r'\b',lambda _:value,loop[3]) for value in values)
            command=command[:loop.start()]+body+command[loop.end():]

    try:
        lexer=shlex.shlex(command,posix=True,punctuation_chars=';&|<>\n')
        lexer.whitespace=' \t\r'
        lexer.whitespace_split=True
        tokens=list(lexer)
    except ValueError:return [(None,'shell', '命令无法解析')] if 'SKILL.md' in command else []
    segments=[];part=[]
    for token in tokens:
        if token in (';','&&','||','|','&','\n'):
            segments.append((part,token));part=[]
        else:part.append(token)
    segments.append((part,''))
    for part,separator in segments:
        if not part:continue
        tool=os.path.basename(part[0])
        if tool=='cd':
            dest=part[1] if len(part)==2 else None
            resolved=path_for(dest.rstrip('/')+'/SKILL.md',cwd,home) if dest else None
            dest=os.path.dirname(resolved) if resolved else None
            # && guarantees execution after a successful cd; same-cwd cd is stable.
            cwd=dest if separator=='&&' or dest==cwd else None
            continue
        if tool not in ('cat','head','tail','sed'):continue
        if tool=='sed' and any(t=='--in-place' or t.startswith('-i') for t in part[1:]):continue
        for i,token in enumerate(part[1:],1):
            if token.endswith('SKILL.md') and not (i and part[i-1] in ('>','>>')):
                # sed program strings are not filename operands.
                if tool=='sed' and (';' in token or token.startswith(('s/','/')) and not os.path.isabs(token)):continue
                resolved=path_for(token,cwd,None if ('"'+token+'"' in command or "'"+token+"'" in command) and token.startswith('~/') else home)
                found.append((resolved,'shell',None if resolved else '动态路径或工作目录无法确认'))
    return found


def command_objects(code):
    """Read literal exec_command argument objects without evaluating JavaScript."""
    for match in re.finditer(r'\bexec_command\s*\(\s*\{',code):
        start=match.end()-1;depth=0;quote=None;escape=False
        for pos in range(start,len(code)):
            char=code[pos]
            if quote:
                if escape:escape=False
                elif char=='\\':escape=True
                elif char==quote:quote=None
                continue
            if char in ('"',"'",'`'):quote=char
            elif char=='{':depth+=1
            elif char=='}':
                depth-=1
                if depth==0:
                    yield code[start:pos+1];break


def requests(block, cwd, home=None):
    name=block.get('name','').lower().split('.')[-1]
    arg=decoded(block.get('arguments',block.get('input',{})))
    if isinstance(arg,dict):
        local=arg.get('workdir',arg.get('cwd',cwd))
        if local and not os.path.isabs(local):local=os.path.join(cwd,local) if cwd else None
        if name in ('read','read_file','read_text_file'):
            raw=arg.get('file_path',arg.get('path',''))
            if isinstance(raw,str) and raw.endswith('SKILL.md'):
                resolved=path_for(raw,local,home)
                return [(resolved,'read',None if resolved else '动态路径或工作目录无法确认')]
        command=arg.get('cmd',arg.get('command'))
        if isinstance(command,str):return shell_reads(command,local,home)
    if isinstance(arg,str):
        if name in ('exec','js') or 'exec_command' in arg:
            # JS orchestrators use quoted cmd fields. Decode string literals, never eval.
            found=[]
            for obj in command_objects(arg):
                literal_field=lambda key: re.search(r'''["']?\b'''+key+r'''["']?'''+r'\s*:\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')\s*(?=[,}])',obj)
                command_match=literal_field('(?:cmd|command)')
                if not command_match:continue
                def literal(match):
                    import ast
                    value=match.group(1)
                    return json.loads(value) if value.startswith('"') else ast.literal_eval(value)
                try:
                    command=literal(command_match)
                    workdir=literal_field('(?:workdir|cwd)')
                    local=literal(workdir) if workdir else None if re.search(r'\b(?:workdir|cwd)\s*:',obj) else cwd
                    if local and not os.path.isabs(local):local=os.path.join(cwd,local) if cwd else None
                except (ValueError,SyntaxError):continue
                found.extend(shell_reads(command,local,home))
            return found
        return shell_reads(arg,cwd,home)
    return []


def output_text(block):
    value=block.get('output',block.get('content',''))
    return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)


def body_names(text):
    names=set()
    def walk(value):
        if isinstance(value,str):
            d=decoded(value)
            if isinstance(d,(dict,list)):walk(d);return
            names.update(re.findall(r'(?:^|\n|\\n)name:\s*[\'\"]?([^\s\\\'\"]+)',value))
        elif isinstance(value,list):
            for v in value:walk(v)
        elif isinstance(value,dict):
            for v in value.values():walk(v)
    walk(text)
    return names


def historical_home(event):
    match=re.match(r'(.+?)/\.(?:codex|claude|comate-engine)/',event['path'])
    return match[1] if match else None


def text_leaves(value):
    if isinstance(value,str):
        parsed=decoded(value)
        if isinstance(parsed,(dict,list)):yield from text_leaves(parsed)
        else:yield value
    elif isinstance(value,list):
        for item in value:yield from text_leaves(item)
    elif isinstance(value,dict):
        for key in ('text','output','content'):
            if key in value:yield from text_leaves(value[key])


def claude_injections(events):
    """Pair an immediate launch result and next injected body; no name-only guessing."""
    found={}
    events=sorted(events,key=lambda e:(e['path'],int(e['location'].split(':')[1]) if e['location'].startswith('line:') else 0))
    for i,event in enumerate(events[:-1]):
        content=h.strict_json(event['text'])
        for result in blocks(content):
            if result.get('type')!='tool_result' or result.get('is_error'):continue
            launch=re.fullmatch(r'Launching skill: ([\w.-]+)',str(result.get('content','')).strip())
            if not launch:continue
            following=events[i+1]
            if following['path']!=event['path']:continue
            value=h.strict_json(following['text'])
            message=value.get('message',value)
            # Claude may insert a re-invocation notice before the injected body.
            notice=message.get('content')
            if isinstance(notice,str) and notice.startswith('(Re-invocation of /'+launch[1]+' ') and i+2<len(events):
                following=events[i+2]
                if following['path']!=event['path']:continue
                value=h.strict_json(following['text']);message=value.get('message',value)
            if message.get('role')!='user':continue
            texts=[b.get('text','') for b in message.get('content',[]) if isinstance(b,dict) and b.get('type')=='text']
            matches=[]
            for text in texts:
                m=re.match(r'\ABase directory for this skill: (/[^\n]+)\n\s*\n(\S[\s\S]*)',text)
                if m and Path(m[1]).name==launch[1] and '[UNAVAILABLE' not in m[2]:matches.append(m)
            if len(matches)==1:
                path=path_for(matches[0][1]+'/SKILL.md',None)
                if path:found[result.get('tool_use_id')]=(launch[1],path,following)
    return found


def single_shell_body(block,cwd,home,linked):
    """Proof for one simple reader only; combined commands require named evidence."""
    arg=decoded(block.get('arguments',block.get('input',{})))
    if isinstance(arg,str):
        objs=list(command_objects(arg))
        if len(objs)!=1:return False
        # Extract literal command using the same non-evaluating object parser.
        match=re.search(r"""["']?\b(?:cmd|command)["']?\s*:\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""",objs[0])
        if not match:return False
        import ast
        try:command=ast.literal_eval(match[1])
        except (ValueError,SyntaxError):return False
    elif isinstance(arg,dict):command=arg.get('cmd',arg.get('command',''))
    else:return False
    # Numeric sed ranges, or cat/head/tail with a single literal operand only.
    pattern=r"\s*(?:cat|head(?: -n \d+)?|tail(?: -n \d+)?|sed -n ['\"]?\d+(?:,\d+)?p['\"]?)\s+([^\s;&|<>]+SKILL\.md)\s*"
    if not re.fullmatch(pattern,command):return False
    if re.search(r'(?:exit_code[\"]?\s*:\s*|Process exited with code |Exit code: )-?[1-9]\d*', '\n'.join(output_text(o) for _,o in linked)):return False
    leaves=[text for _,result in linked for text in text_leaves(result.get('output',result.get('content','')))]
    # A process envelope alone is not file contents.
    for text in leaves:
        if 'Final output:' in text:text=text.split('Final output:',1)[1]
        elif re.match(r'^(?:Chunk ID:|Wall time:)',text) and 'Output:' in text:text=text.split('Output:',1)[1]
        if text.strip() and not re.match(r'^(?:Script (?:completed|running)|Process exited|Exit code:)',text):return True
    return False


def current_skill_states(paths):
    """Snapshot current file availability; never infer deletion from absence."""
    checked_at=datetime.now(timezone.utc).isoformat()
    result={}
    for path in sorted(set(paths)):
        try:
            mode=Path(path).stat().st_mode
            state='present' if stat.S_ISREG(mode) else 'unknown'
            reason=None if state=='present' else '原路径不是普通文件'
        except (FileNotFoundError,NotADirectoryError):
            state,reason='missing','原始 SKILL.md 路径当前不存在，可能已删除、移动或切换分支'
        except OSError as exc:
            state,reason='unknown',type(exc).__name__
        result[path]={'status':state,'checked_at':checked_at,'reason':reason}
    return result


def extract(bundle):
    records=[];uncovered=[];seen=set()
    for session in bundle['sessions']:
        events=list(reader.events(Path(session['manifest'])))
        if session['source'] not in ('codex','claude','comate'):
            uncovered.append({'session':session['key'],'reason':'当前来源尚未支持确定性读取提取；保留来源快照'})
            continue
        injections=claude_injections(events) if session['source']=='claude' else {}
        outputs={}
        for event in events:
            for b in event_blocks(h.strict_json(event['text']),session['source']):
                if b.get('type') in ('function_call_output','custom_tool_call_output','tool_result'):
                    key=b.get('call_id',b.get('tool_use_id'))
                    if key:outputs.setdefault(key,[]).append(({**event,'location':event['location']+b.get('_location','')},b))
        for original_event in events:
            content=h.strict_json(original_event['text'])
            for block_index,b in enumerate(event_blocks(content,session['source'])):
                if b.get('type') not in ('function_call','custom_tool_call','tool_use'):continue
                event={**original_event,'location':original_event['location']+b.get('_location','')}
                call=b.get('call_id',b.get('id'))
                home=historical_home(event)
                targets=requests(b,session.get('project'),home)
                injection=injections.get(call)
                arg=decoded(b.get('input',{}))
                if injection and b.get('name','').lower()=='skill' and isinstance(arg,dict) and arg.get('skill')==injection[0]:
                    targets=[(injection[1],'injection',None)]
                if not targets:
                    if b.get('name','').lower().split('.')[-1] in ('skill','skill_view'):
                        uncovered.append({'session':session['key'],'file':event['path'],'location':event['location'],'reason':'Skill 名称调用缺少可解析正文路径，不推断为读取'})
                    continue
                linked=outputs.get(call,[]) if call else []
                if targets and targets[0][1]=='injection':linked=linked+[(injection[2],{'content':'injected body'})]
                text='\n'.join(output_text(o) for _,o in linked)
                names=body_names(text)
                error_lines=[line for leaf in text_leaves(text) for line in leaf.splitlines() if re.search(r'(?:No such file|Permission denied|FileNotFoundError|Error reading file)',line,re.I)]
                for ordinal,(path,method,reason) in enumerate(targets):
                    identity=(session['source'],session['session_id'],call or event['event_id'],block_index,ordinal,path)
                    if identity in seen:continue
                    seen.add(identity)
                    name=Path(path).parent.name if path else None
                    # An unrelated command's failure must not label every file as failed.
                    failed=any(o.get('is_error') for _,o in linked) if method in ('read','injection') or session['source']=='comate' else False
                    failed=failed or bool(path and any(path in line or '/'+Path(path).parent.name+'/SKILL.md' in line for line in error_lines))
                    if error_lines and method=='shell' and len(targets)==1 and single_shell_body(b,session.get('project'),home,linked):failed=True
                    ambiguous=bool(path and len({p for p,_,_ in targets if p and Path(p).parent.name==name})>1)
                    confirmed=path and linked and not failed and not ambiguous and (name in names or (method in ('read','injection') and text.strip() and not failed) or (len(targets)==1 and not failed and single_shell_body(b,session.get('project'),home,linked)))
                    status='confirmed' if confirmed else 'failed' if failed else 'unconfirmed'
                    when=h.timestamp(b.get('_timestamp')) or h.timestamp(event['timestamp'])
                    stamp=when.isoformat() if when else event['timestamp']
                    records.append({'read_id':h.digest(str(identity)),'skill':path,'name':name,
                        'scope':'project' if path and session.get('project') and path.startswith(session['project'].rstrip('/')+'/') else 'personal' if path and re.search(r'/\.(?:agents|codex|claude|comate)/skills/',path) else 'external' if path else 'unknown',
                        'session_id':session['session_id'],'source':session['source'],'call_id':call,
                        'timestamp':stamp,'time_basis':'tool_start' if h.timestamp(b.get('_timestamp')) else 'message','read_method':'injection' if b.get('_injection') else method,'time_beijing':when.astimezone(timezone(timedelta(hours=8))).isoformat() if when else None,
                        'original_file':event['path'],'request_location':event['location'],
                        'result_locations':[{'file':e['path'],'location':e['location']} for e,_ in linked],
                        'status':status,'reason':None if confirmed else reason or ('读取失败' if failed else '同名 Skill 涉及多个来源路径，合并返回无法确认实际读取来源' if ambiguous else '缺少关联返回或无法逐文件确认正文')})
    records.sort(key=lambda r:(r['timestamp'],r['session_id'],r['request_location'],r['read_id']))
    roots=bundle.get('scope',{}).get('skills_roots',[])
    outside=[r for r in records if r['skill'] and not skill_scope.contains(r['skill'],roots)]
    unresolved=[r for r in records if roots and not r['skill']]
    records=[r for r in records if skill_scope.contains(r['skill'],roots)]
    for row in unresolved:
        uncovered.append({'session':row['session_id'],'file':row['original_file'],'location':row['request_location'],'reason':'读取路径无法解析，不能判断是否属于所选 Skill 目录'})
    counts=Counter(r['skill'] for r in records if r['status']=='confirmed')
    return {'method':'deterministic_body_reads_v1','status':'partial' if uncovered or bundle['coverage']['uncovered'] or any(r['status']=='unconfirmed' for r in records) else 'complete',
        'summary':{'confirmed_reads':sum(counts.values()),'skills':len(counts),'failed_reads':sum(r['status']=='failed' for r in records),'unconfirmed_reads':sum(r['status']=='unconfirmed' for r in records)},
        'excluded_reads':len(outside),'unresolved_scope_reads':unresolved,'skills_roots':roots,
        'current_skill_states':current_skill_states(counts),
        'skills':[{'skill':p,'reads':n} for p,n in sorted(counts.items())], 'reads':records,'uncovered':uncovered,
        'limitations':['统计可识别的正文读取请求；不进行效果分析。未识别的工具格式、Skill 名称调用和无路径注入不推断为成功读取。','单一简单读取可由关联正文确认；合并输出仍依赖逐文件证据。不执行历史命令，复杂控制流和无法证明成功的 cd 保留为未确认。']}
