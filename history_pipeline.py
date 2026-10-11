"""Deterministic collection and durable snapshot preparation; no model scheduling."""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import audit_config
import history_export
import history_extract
import history_reader as reader
import history_review as h
import skill_scope


def focus(rows, source, session, path, start, end, secrets):
    events, hits, links, errors = [], set(), set(), []
    for location, native_id, raw_time, content in rows:
        when = h.timestamp(raw_time)
        if when is None:
            h.notice(errors, source, f'{location} 缺少有效时间，无法筛选', path)
            continue
        if not start <= when < end:
            continue
        text = json.dumps(h.redact_data(content,secrets), ensure_ascii=False)
        eid = h.digest(f'{source}:{session}:{native_id or (when.isoformat()+text)}')
        event = {'event_id': eid, 'timestamp': when.isoformat(), 'path': str(path), 'location': location, 'text': text}
        ids = reader.identifiers(content)
        events.append((event, ids))
        if reader.is_candidate(content):
            hits.add(eid)
            links.update(ids)
    # Keep both tool request and result even when only one mentions SKILL.md.
    hits.update(event['event_id'] for event, ids in events if ids & links)
    seen, unique = set(), []
    for event, _ in events:
        if event['event_id'] not in seen:
            unique.append(event)
            seen.add(event['event_id'])
    unique.sort(key=lambda e: e['timestamp'])
    return unique, sorted(hits), errors



def collect(args, secrets=()):
    start, end = h.period(args)
    home = args.history_home.expanduser().resolve()
    h.require(home.is_dir(), 'history-home 目录不存在')
    h.require(args.limit > 0, 'limit 必须大于 0')
    h.require(args.max_evidence_chars >= 36000, '汇总证据预算至少为 36000 字符')
    allowed_roots=skill_scope.normalize(getattr(args,'skills_root',None),args.project)
    sources = args.sources.split(',')
    h.require(set(sources) <= set(h.SOURCES) and len(set(sources)) == len(sources), 'sources 应为 codex,claude,comate 的不重复组合')
    project = None if args.all_projects else (args.project or Path.cwd()).expanduser().resolve()
    excluded = set(args.exclude_session)
    if os.getenv('CODEX_THREAD_ID'):
        excluded.add(os.environ['CODEX_THREAD_ID'])
    roots = {'codex': home/'.codex', 'claude': home/'.claude/projects', 'comate': home/'.comate-engine/store'}
    selected, discovered, rollout_ids, source_status, scan_errors = {}, set(), set(), [], []
    unknown_projects=[]
    for source in sources:
        root = roots[source]
        if not root.exists():
            source_status.append({'source':source,'status':'not_installed','files':0})
            continue
        if source == 'codex':
            paths = sorted(set((root/'sessions').rglob('*.jsonl')) | set((root/'archived_sessions').rglob('*.jsonl')))
        elif source == 'claude':
            paths = sorted(root.rglob('*.jsonl'))
        else:
            paths = sorted(p for p in root.glob('chat_session_*') if p.is_file() and not p.name.endswith('.deleted'))
        source_status.append({'source':source,'status':'available','files':len(paths)})
        def entries():
            for path in paths:
                path=path.resolve()  # Keep original archive locations for symlinked sample sets.
                try:
                    meta, rows, errors = h.read_session(source, path)
                    if source == 'codex' and any(h.timestamp(r[2]) and start <= h.timestamp(r[2]) < end for r in rows):
                        # 同时登记文件名里的续跑段 id，否则续跑会话会被 SQLite 投影重复计一次。
                        rollout_ids.add(meta['session_id'])
                        rollout_ids.update(h.rollout_thread_ids(path))
                    yield path, meta, rows, errors
                except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
                    # Unknown scope cannot be silently equated with no usage.
                    h.notice(scan_errors, source, f'发现阶段无法读取：{type(exc).__name__}', path)
            if source == 'codex':
                db = root/'thread_history_1.sqlite'
                for meta, rows, errors in h.projection_sessions(db, start, end, rollout_ids, scan_errors):
                    yield db, meta, rows, errors
        for path, meta, rows, errors in entries():
            if meta['session_id'] in excluded or meta.get('parent_session') in excluded:
                continue
            if project and (not meta['project'] or Path(meta['project']).expanduser().resolve() != project):
                if not meta['project']:
                    times=[h.timestamp(r[2]) for r in rows if h.timestamp(r[2]) and start <= h.timestamp(r[2]) < end]
                    if times:unknown_projects.append((max(times),source,str(path)))
                continue
            events, hits, problems = focus(rows, source, meta['session_id'], path, start, end, secrets)
            if not events:
                if errors or problems:
                    scan_errors.extend(errors + problems)
                continue
            key = f"{source}:{meta['session_id']}"
            discovered.add(key)
            if key in selected:
                old = selected[key]
                ids = {e['event_id'] for e in old['events']}
                events = old['events'] + [e for e in events if e['event_id'] not in ids]
                hits = sorted(set(hits) | set(old['candidates']))
                errors = old['problems'] + errors
            events.sort(key=lambda e:e['timestamp'])
            selected[key] = {**meta,'key':key,'events':events,'candidates':hits,'problems':errors+problems,
                             'last_active':events[-1]['timestamp'],'skills_roots':allowed_roots}
            # Bound retained sessions while scanning older files. Sorting uses event time, not filenames.
            ordered = sorted(selected, key=lambda k:(selected[k]['last_active'], k), reverse=True)
            for discard in ordered[args.limit:]:
                del selected[discard]
    sessions = sorted(selected.values(), key=lambda s:(s['last_active'],s['key']), reverse=True)
    seen_origins=set()
    for session in sessions:
        retained=[]
        for event in session['events']:
            origin=h.digest(session['source']+event['timestamp']+event['text'])
            if origin not in seen_origins:
                retained.append(event);seen_origins.add(origin)
        session['events']=retained
        keep={e['event_id'] for e in retained}
        session['candidates']=[x for x in session['candidates'] if x in keep]
    cutoff=h.timestamp(sessions[-1]['last_active']) if len(sessions)==args.limit else start
    problems=[]
    for issue in scan_errors:
        try:recent=datetime.fromtimestamp(Path(issue['path']).stat().st_mtime,timezone.utc)>=cutoff
        except OSError:recent=True
        if recent:problems.append(issue)
    for active,source,path in unknown_projects:
        if active >= cutoff:h.notice(problems,source,'近期会话缺少项目归属，无法确认是否属于所选项目',path)
    for session in sessions:
        problems.extend(x for x in session['problems'] if x not in problems)
    return {'schema_version':'1.1','kind':'history_evidence',
            'period':{'from':start.isoformat(),'to_exclusive':end.isoformat(),'timezone':str(datetime.now().astimezone().tzinfo)},
            'scope':{'project':str(project) if project else None,'sources':sources,'excluded_sessions':sorted(excluded),
                     'skills_roots':allowed_roots,'limit':args.limit,'selection':'latest_activity','days':args.days},
            'coverage':{'sources':source_status,'uncovered':problems,'sessions_discovered':len(discovered),
                        'sessions_outside_limit':max(0,len(discovered)-len(sessions))},'sessions':sessions}


def snapshot(bundle, state):
    for session in bundle['sessions']:
        directory=state/'sessions'/h.digest(session['key'])
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=directory/'events.jsonl'
        origins=sorted({e['path'] for e in session['events']})
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            for event in session.pop('events'):
                stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        session['snapshot_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        session['manifest']=str(directory/'session.json')
        atomic(Path(session['manifest']), {'session':session['key'], 'original_paths':origins,
               'snapshot':str(path),'skills_roots':session.get('skills_roots',[]),'project':session.get('project'),'period':bundle['period'], 'problems':session['problems']})
        session['events']=[]
    return bundle


def verify_snapshots(bundle):
    for session in bundle['sessions']:
        manifest=h.strict_json(Path(session['manifest']).read_text())
        path=Path(manifest['snapshot'])
        h.require(hashlib.sha256(path.read_bytes()).hexdigest()==session['snapshot_sha256'],
                  '会话快照已改变，不能复用断点，请开始新复盘')


def atomic(path, data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=path.with_suffix(path.suffix+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)
    temp.replace(path)


def run(args,cli):
    """Script-only collection (`history --collect-only`, also the first step of a full history run)."""
    secrets=audit_config.known_secrets()
    if args.resume:
        h.require(not args.state_dir,'resume 不能与 state-dir 同用')
        state=args.resume.expanduser().resolve()
        manifest=h.strict_json((state/'manifest.json').read_text())
        bundle=manifest['bundle'];run_id=manifest['run_id']
        saved_roots=bundle['scope'].get('skills_roots',[])
        requested=getattr(args,'skills_root',None)
        roots=skill_scope.normalize(requested,args.project or bundle['scope'].get('project')) if requested is not None else saved_roots
        bundle['scope']['skills_roots']=roots
        for session in bundle['sessions']:session['skills_roots']=roots
        print('恢复固定会话快照；范围沿用断点，不重新筛选。',file=sys.stderr)
    else:
        run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+os.urandom(4).hex()
        state=(args.state_dir or args.report_directory/'snapshots'/run_id).expanduser().resolve()
        h.require(not state.exists() or not any(state.iterdir()),'新运行的 state-dir 必须为空；恢复请用 --resume')
        state.mkdir(parents=True,exist_ok=True,mode=0o700)
        bundle=snapshot(collect(args,secrets),state)
        atomic(state/'manifest.json',{'run_id':run_id,'bundle':bundle})
    verify_snapshots(bundle)
    if args.resume:
        state=args.report_directory/'snapshots'/run_id
        for session in bundle['sessions']:
            session['events']=list(reader.events(Path(session['manifest'])))
        bundle=snapshot(bundle,state)
        atomic(state/'manifest.json',{'run_id':run_id,'bundle':bundle})
    bundle['extraction']=history_extract.extract(bundle)
    cli.emit(bundle,args.output)
    if args.output:
        print(f'可视化报告：{history_export.export(bundle,args.output)}',file=sys.stderr)
    summary=bundle['extraction']['summary']
    print(f"纯脚本提取已完成，扫描 {len(bundle['sessions'])} 个会话：确认读取 {summary['confirmed_reads']} 次，历史读取失败 {summary['failed_reads']} 次，待核对 {summary['unconfirmed_reads']} 次；未请求模型。",file=sys.stderr)
    if bundle['extraction']['status']!='complete':
        print('覆盖不完整（退出码 2），报告已生成；历史读取失败计数不表示本次程序异常。',file=sys.stderr)
        reasons={}
        for issue in bundle['extraction']['uncovered']:
            reasons.setdefault(issue['reason'],[]).append(issue['session'])
        for reason,sessions in reasons.items():
            print(f"未覆盖：{reason}（{len(sessions)} 条线索，涉及 {len(set(sessions))} 个会话；具体位置见 JSON）",file=sys.stderr)
    return 0 if bundle['extraction']['status']=='complete' else 2
