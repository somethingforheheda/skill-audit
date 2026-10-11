"""Fixed offline report renderer. Standard library only; never calls a model."""
import json
import hashlib
import os
from datetime import datetime, timezone
import zipfile
from pathlib import Path

import audit_paths


def template():
    here=Path(__file__)
    if here.is_file():return here.with_name('history_report.html').read_text(encoding='utf-8')
    with zipfile.ZipFile(here.parent) as archive:
        return archive.read('history_report.html').decode('utf-8')


def validate(data):
    if not isinstance(data,dict) or not isinstance(data.get('extraction'),dict):
        raise ValueError('需要 history --collect-only 生成的提取 JSON')
    rows=data['extraction'].get('reads')
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
        raise ValueError('extraction.reads 必须是记录列表')


SESSION_FIELDS = ('session_id','source','parent_session','last_active','candidates','problems','snapshot_sha256')


def render_html(data):
    validate(data)
    # Only presentation fields; do not embed historical bodies or snapshot manifests.
    payload={k:data.get(k) for k in ('schema_version','kind','period','scope','coverage','extraction','analysis')}
    payload['sessions']=[{k:s[k] for k in SESSION_FIELDS if k in s} for s in data.get('sessions',[])]
    encoded=json.dumps(payload,ensure_ascii=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    return template().replace('__SKILL_AUDIT_DATA__',encoded)


def export(data, json_path):
    validate(data)
    path=Path(json_path)
    sidecar=path.parent/'analysis.json'
    if not data.get('analysis') and path.is_file() and sidecar.is_file():
        try:
            analysis=json.loads(sidecar.read_text(encoding='utf-8'))
            if analysis.get('input_sha256')==hashlib.sha256(path.read_bytes()).hexdigest():
                data={**data,'analysis':analysis}
        except (OSError,ValueError,AttributeError):
            pass
    html_path=path.with_suffix('.html') if path.suffix.lower()=='.json' else Path(str(path)+'.html')
    html_path.parent.mkdir(parents=True,exist_ok=True)
    html_path.write_text(render_html(data),encoding='utf-8')
    return html_path


def report_directory(output=None, label=None):
    """默认落在 ~/.skill-audit/reports/<项目>-<时间>-<随机>/，不再用 /tmp。"""
    if output is None:
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        directory=audit_paths.run_directory(label,stamp,os.urandom(2).hex())
        while directory.exists():
            directory=audit_paths.run_directory(label,stamp,os.urandom(2).hex())
    else:
        directory=Path(output).expanduser().absolute()
        if directory.suffix.lower()=='.json':directory=directory.with_suffix('')
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise ValueError(f'报告目录已存在且非空：{directory}；请指定新目录，避免覆盖旧报告')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if audit_paths.reports_root() in directory.parents:
        audit_paths.link_latest(directory)
    return directory


def command(args):
    data=json.loads(args.report.read_text(encoding='utf-8'))
    validate(data)
    directory=report_directory(args.output or args.report.with_suffix(''))
    path=directory/'report.json'
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'报告目录：{directory}')
    print(f'已生成：{path}')
    presentation=data
    sidecar=args.report.parent/'analysis.json'
    if sidecar.is_file():
        try:
            analysis=json.loads(sidecar.read_text(encoding='utf-8'))
            if analysis.get('input_sha256')==hashlib.sha256(args.report.read_bytes()).hexdigest():
                presentation={**data,'analysis':analysis}
        except (OSError,ValueError,AttributeError):
            pass
    print(f'已生成：{export(presentation,path)}')
    return 0
