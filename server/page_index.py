from pages import OPTS_HTML, OPTS_JS, esc, shell

INDEX_SCRIPT = '''
const MAXMB=%(maxmb)d;
const SRC={
  claude:f=>f.name.endsWith('.jsonl'),
  codex:f=>f.name.endsWith('.jsonl'),
  comate:f=>f.name.startsWith('chat_session_')&&!f.name.endsWith('.deleted')};
function picked(){
  const since=Date.now()-(+gid('days').value||14)*864e5, out=[];
  for(const s of Object.keys(SRC)){
    for(const f of gid('in-'+s).files||[]){
      if(!SRC[s](f)||f.lastModified<since||f.size>MAXMB*1048576||!f.size)continue;
      const parts=(f.webkitRelativePath||f.name).split('/');
      out.push({s:s,f:f,rel:parts.slice(1).join('/')||parts[0]});
    }
  }
  return out;
}
function summary(){
  const p=picked(),size=p.reduce((a,x)=>a+x.f.size,0);
  gid('sum').textContent=p.length?('将上传 '+p.length+' 个会话文件，共 '+mb(size)):'还没有选择文件';
  gid('go').disabled=!p.length;
}
for(const s of Object.keys(SRC))gid('in-'+s).onchange=summary;
gid('days').onchange=summary;
async function go(){
  const p=picked();if(!p.length)return;
  gid('go').disabled=true;gid('err').textContent='';gid('barbox').hidden=false;
  try{
    let r=await fetch('/api/jobs',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},H),
      body:JSON.stringify(Object.assign({limit:+gid('limit').value||20},readOpts()))});
    if(!r.ok)throw new Error(await r.text());
    const job=await r.json();let done=0,next=0,fail=null;
    async function lane(){
      while(next<p.length&&!fail){
        const x=p[next++];
        const u='/api/jobs/'+job.id+'/files?source='+x.s+'&path='+encodeURIComponent(x.rel);
        const res=await fetch(u,{method:'PUT',headers:H,body:x.f});
        if(!res.ok){fail=await res.text();return}
        gid('bar').style.width=(++done*100/p.length)+'%%';
        gid('sum').textContent='上传中 '+done+'/'+p.length;
      }
    }
    await Promise.all([lane(),lane(),lane()]);
    if(fail)throw new Error(fail);
    r=await fetch('/api/jobs/'+job.id+'/start',{method:'POST',headers:H});
    if(!r.ok)throw new Error(await r.text());
    location.href='/job/'+job.id;
  }catch(e){gid('err').textContent='失败：'+e.message;gid('go').disabled=false}
}
gid('go').onclick=go;
let me='',only=false,last=[];
function draw(){
  const rows=last.filter(j=>!only||j.user===me);
  gid('rows').innerHTML=rows.map(j=>
    '<tr><td>'+badge(j.status)+'</td><td><a href="/job/'+h(j.id)+'" class="mono">'+h(j.id)+'</a>'+
    (j.project?'<div class="sub">'+h(j.project==='*'?'全部目录':j.project)+'</div>':'')+'</td><td>'+h(j.user)+'</td><td>'+rel(j.created)+
    '</td><td class="num">'+j.files+'</td><td>'+(j.status==='done'?'<a href="/r/'+h(j.id)+'/report.html" target="_blank" rel="noopener">打开报告</a>':'')+
    '</td></tr>').join('');
  gid('empty').hidden=rows.length>0;gid('tbl').hidden=!rows.length;
  gid('empty').innerHTML=only?'<b>你还没有提交过分析</b>点右上角「发起分析」，选一个目录就能开始。':'<b>还没有任务</b>点右上角「发起分析」开始第一次。';
}
async function jobs(){
  try{const r=await fetch('/api/jobs');if(!r.ok)return;const d=await r.json();me=d.me;last=d.jobs;draw()}catch(e){}
}
function setOnly(v){only=v;gid('f-all').setAttribute('aria-pressed',!v);gid('f-mine').setAttribute('aria-pressed',v);draw()}
gid('f-all').onclick=()=>setOnly(false);gid('f-mine').onclick=()=>setOnly(true);
jobs();setInterval(()=>{if(!document.hidden)jobs()},5000);summary();
'''

SOURCES = [
    ('claude', 'Claude Code', '~/.claude/projects'),
    ('codex', 'Codex', '~/.codex/sessions'),
    ('comate', 'Comate', '~/.comate-engine/store'),
]


def index_page(user, max_mb):
    pickers = ''.join(
        '<div class="field"><label for="in-%s">%s</label><input type="file" id="in-%s" webkitdirectory multiple>'
        '<p class="hint">选择文件夹 <code>%s</code></p></div>' % (key, label, key, path)
        for key, label, path in SOURCES)
    manual = ('<div class="card"><details class="fold" style="border:0;margin:0;padding:0"><summary>没装同步工具？手动上传会话文件</summary>'
              '<p class="muted small">只需选你实际使用的那一项。隐藏目录在选择框里按 ⌘⇧G（Mac），或直接输入路径（Windows）。'
              '分析完成后服务器会删除你上传的原始会话，只保留报告。</p>%s'
              '<div class="grid3"><div class="field"><label for="days">只取最近（天）</label><input type="number" id="days" value="14" min="1" max="365"></div>'
              '<div class="field"><label for="limit">最多会话数</label><input type="number" id="limit" value="20" min="1" max="50"></div></div>'
              + OPTS_HTML +
              '<div id="barbox" hidden class="bar" style="margin-bottom:12px"><i id="bar"></i></div>'
              '<div class="actions"><button id="go" disabled>上传并开始分析</button><span id="sum" class="muted small" aria-live="polite"></span>'
              '<span id="err" class="err" role="alert"></span></div></details></div>') % pickers
    body = ('<div class="head"><div class="t"><h1>分析任务</h1><p class="muted">团队里的分析任务和报告。'
            '分析的是已经同步到服务器上的会话。</p></div>'
            '<div class="actions"><a class="btn" href="/u/' + esc(user) + '">发起分析</a></div></div>'
            '<div class="card flush"><div class="ch"><h2>全部任务</h2><div class="seg" role="group" aria-label="筛选">'
            '<button id="f-all" aria-pressed="true">全部</button><button id="f-mine" aria-pressed="false">我的</button></div></div>'
            '<div class="tw"><table id="tbl" style="margin-top:10px"><thead><tr><th>状态</th><th>任务</th><th>提交人</th><th>创建</th>'
            '<th class="num">文件</th><th>报告</th></tr></thead><tbody id="rows"></tbody></table></div>'
            '<div class="empty" id="empty">加载中…</div></div>' + manual)
    return shell('分析任务', body, user, INDEX_SCRIPT % {'maxmb': max_mb} + OPTS_JS + 'loadOpts();', 'jobs')
