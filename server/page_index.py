import auth
from pages import OPTS_JS, SAVED_HTML, SCOPE_HTML, esc, shell

INDEX_SCRIPT = r'''
const MAXMB=%(maxmb)d, ME='%(me)s';
const SRC={
  claude:f=>f.name.endsWith('.jsonl'),
  codex:f=>f.name.endsWith('.jsonl'),
  comate:f=>f.name.startsWith('chat_session_')&&!f.name.endsWith('.deleted')};
const WS=new WeakMap();
let wsOn=null;
function eligible(){
  const out=[];
  for(const s of Object.keys(SRC)){
    for(const f of gid('in-'+s).files||[]){
      if(!SRC[s](f)||f.size>MAXMB*1048576||!f.size)continue;
      const parts=(f.webkitRelativePath||f.name).split('/');
      out.push({s:s,f:f,rel:parts.slice(1).join('/')||parts[0]});
    }
  }
  return out;
}
function picked(){
  const byDays=gid('scope-days').checked;
  const since=byDays?Date.now()-(+gid('days').value||30)*864e5:0;
  let out=eligible().filter(x=>x.f.lastModified>=since);
  if(wsOn)out=out.filter(x=>wsOn.has(WS.get(x.f)||''));
  if(byDays)return out;
  return out.sort((a,b)=>b.f.lastModified-a.f.lastModified).slice(0,+gid('limit').value||20);
}
function unique(p){
  const best=new Map();
  for(const x of p){
    const k=x.s+':'+x.f.name,o=best.get(k);
    if(!o||x.f.lastModified>o.f.lastModified||(x.f.lastModified===o.f.lastModified&&x.f.size>o.f.size))best.set(k,x);
  }
  return p.filter(x=>best.get(x.s+':'+x.f.name)===x);
}
function summary(){
  const p=picked(),size=p.reduce((a,x)=>a+x.f.size,0);
  gid('sum').textContent=p.length?('将上传 '+p.length+' 个会话文件，共 '+mb(size)+(wsOn?'（已按工作区筛选）':'')):(wsOn?'所选工作区下没有符合范围的会话':'还没有选择文件');
  gid('go').disabled=gid('go-up').disabled=!p.length;
}
async function peek(s,f){
  try{
    if(s==='comate'){
      const m=(await f.slice(0,4194304).text()).match(/"workspaceDirectory"\s*:\s*"([^"]*)"/);
      if(!m)return '';
      try{return JSON.parse('"'+m[1]+'"')}catch(e){return m[1]}
    }
    for(const line of (await f.slice(0,524288).text()).split('\n').slice(0,300)){
      let o;try{o=JSON.parse(line)}catch(e){continue}
      const c=o&&typeof o==='object'&&(o.cwd||(o.payload&&o.payload.cwd));
      if(c)return String(c);
    }
  }catch(e){}
  return '';
}
function resetFilter(){
  wsOn=null;gid('ws-box').hidden=true;gid('cf').hidden=true;
  const n=eligible().length;
  gid('ws-btn').disabled=!n;
  gid('ws-note').textContent=n?('已选 '+n+' 个会话文件。想只传某几个工作区（目录）的会话，点左边的按钮'):'先在上面选好文件夹，这里才能筛选工作区';
  summary();
}
function drawWs(groups){
  const q=gid('ws-q').value.trim().toLowerCase();
  gid('ws-list').innerHTML=groups.filter(g=>!q||g.key.toLowerCase().includes(q)).map(g=>
    '<label class="wsrow"><input type="checkbox" data-k="'+encodeURIComponent(g.key)+'"'+(wsOn.has(g.key)?' checked':'')+'><span><b>'+
    h(g.key?g.key.split(/[\\/]/).filter(Boolean).pop()||g.key:'（未知工作区）')+'</b><span class="sub">'+h(g.key)+'</span></span><span class="muted small">'+
    g.n+' 个 · '+mb(g.size)+'</span></label>').join('')||'<p class="muted small">没有匹配的工作区。</p>';
}
async function openWs(){
  const all=eligible();gid('ws-btn').disabled=true;
  let n=0,next=0;
  async function lane(){
    while(next<all.length){
      const x=all[next++];
      if(!WS.has(x.f))WS.set(x.f,await peek(x.s,x.f));
      gid('ws-note').textContent='正在读取工作区 '+(++n)+'/'+all.length;
    }
  }
  await Promise.all([lane(),lane(),lane(),lane()]);
  const by=new Map();
  for(const x of all){const k=WS.get(x.f)||'',g=by.get(k)||{key:k,n:0,size:0};g.n++;g.size+=x.f.size;by.set(k,g)}
  const groups=[...by.values()].sort((a,b)=>b.n-a.n);
  wsOn=new Set(groups.map(g=>g.key));
  gid('ws-note').textContent='共 '+groups.length+' 个工作区，勾选要上传的';
  gid('ws-box').hidden=false;gid('ws-btn').disabled=false;drawWs(groups);summary();
  gid('ws-list').onchange=e=>{const k=decodeURIComponent(e.target.dataset.k);e.target.checked?wsOn.add(k):wsOn.delete(k);summary()};
  gid('ws-q').oninput=()=>drawWs(groups);
  gid('ws-all').onclick=()=>{groups.forEach(g=>wsOn.add(g.key));drawWs(groups);summary()};
  gid('ws-none').onclick=()=>{wsOn.clear();drawWs(groups);summary()};
}
gid('ws-btn').onclick=openWs;
gid('ws-clear').onclick=resetFilter;
for(const s of Object.keys(SRC))gid('in-'+s).onchange=resetFilter;
for(const id of ['days','limit','scope-days','scope-count'])gid(id).onchange=()=>{gid('cf').hidden=true;summary()};
let queued=[],mode='analyze';
function confirmSum(){
  const dup=gid('cf-dup').checked,p=dup?unique(queued):queued;
  gid('cf-sum').textContent='将上传 '+p.length+' 个会话文件，共 '+mb(p.reduce((a,x)=>a+x.f.size,0))+'。'+(mode==='store'?'传到服务器后由管理员统一分析，不需要 API Key。':'');
}
function go(m){
  const p=picked();if(!p.length)return;
  mode=m;
  const bad=m==='analyze'?optsProblem():'';gid('err').textContent=bad;if(bad)return;
  queued=p;const same=p.length-unique(p).length;
  gid('cf-dup-row').hidden=!same;gid('cf-dup').checked=true;
  gid('cf-dup-note').textContent=same?('发现 '+same+' 个相同的会话（同来源、同文件名），勾选后只传其中最新的一份'):'';
  gid('cf-none').hidden=!!same;confirmSum();
  gid('cf-ok').textContent=m==='store'?'确定上传':'确定上传并分析';
  gid('cf').hidden=false;gid('go').disabled=gid('go-up').disabled=true;gid('cf').scrollIntoView({block:'nearest'});
}
function back(){gid('cf').hidden=true;summary()}
async function storeUpload(p){
  const SH={'X-SA-User':ME};
  gid('cf-ok').disabled=gid('cf-back').disabled=true;gid('err').textContent='';gid('barbox').hidden=false;
  try{
    const proj=new Map();let n=0,nx=0;
    async function look(){
      while(nx<p.length){
        const x=p[nx++];
        if(!WS.has(x.f))WS.set(x.f,await peek(x.s,x.f));
        gid('cf-sum').textContent='正在读取工作区 '+(++n)+'/'+p.length;
      }
    }
    await Promise.all([look(),look(),look(),look()]);
    const rows=p.map(x=>({s:x.s,rel:x.rel,size:x.f.size,mt:Math.floor(x.f.lastModified/1000)}));
    let need=[];
    for(let i=0;i<rows.length;i+=1500){
      const r=await fetch('/api/sync/diff',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},SH),body:JSON.stringify({files:rows.slice(i,i+1500)})});
      if(!r.ok)throw new Error(await r.text());
      need=need.concat((await r.json()).need.map(k=>i+k));
    }
    let done=0,next=0,fail=null;
    async function lane(){
      while(next<need.length&&!fail){
        const k=need[next++],x=p[k],row=rows[k];
        const u='/api/sync/file?'+new URLSearchParams({source:x.s,path:x.rel,mt:row.mt,proj:WS.get(x.f)||''});
        const res=await fetch(u,{method:'PUT',headers:Object.assign({'Content-Type':'application/octet-stream'},SH),body:x.f});
        if(!res.ok){fail=await res.text();return}
        gid('bar').style.width=(++done*100/need.length)+'%%';
        gid('cf-sum').textContent='上传中 '+done+'/'+need.length;
      }
    }
    await Promise.all([lane(),lane(),lane()]);
    if(fail)throw new Error(fail);
    if(mode==='analyze'){
      gid('cf-sum').textContent='上传完成，正在创建分析任务…';
      const r=await fetch('/api/analyze',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},H),
        body:JSON.stringify(Object.assign(readOpts(),{project:'*',keys:p.map(x=>x.s+'/'+x.rel)},readScope()))});
      if(!r.ok)throw new Error('会话已经传好，但创建分析失败：'+await r.text());
      location.href='/job/'+(await r.json()).id;return;
    }
    gid('cf-sum').innerHTML='上传完成：新传 '+done+' 个，服务器上已有 '+(p.length-need.length)+' 个，已跳过。管理员可以在「全部会话」里看到，你也可以在<a href="/u/'+ME+'">我的会话</a>里查看。';
    gid('cf-ok').hidden=true;gid('cf-back').disabled=false;gid('cf-back').textContent='继续上传别的';
  }catch(e){gid('err').textContent='失败：'+e.message;gid('cf-ok').disabled=false;gid('cf-back').disabled=false}
}
async function upload(){
  const p=gid('cf-dup').checked?unique(queued):queued;if(!p.length)return;
  return storeUpload(p);
}
gid('cf-dup').onchange=confirmSum;gid('cf-ok').onclick=upload;
gid('cf-back').onclick=()=>{gid('cf-ok').hidden=false;gid('cf-ok').disabled=false;gid('cf-back').textContent='返回修改';gid('barbox').hidden=true;back()};
gid('go').onclick=()=>go('analyze');gid('go-up').onclick=()=>go('store');
let me='',only=false,last=[];
function draw(){
  const rows=last.filter(j=>!only||j.user===me);
  gid('rows').innerHTML=rows.map(j=>
    '<tr><td>'+badge(j.status)+'</td><td><a href="/job/'+h(j.id)+'" class="mono">'+h(j.id)+'</a>'+
    (j.project?'<div class="sub">'+h(j.project==='*'?'全部目录':j.project)+'</div>':'')+'</td><td>'+h(j.user)+'</td><td>'+rel(j.created)+
    '</td><td class="num">'+j.files+'</td><td>'+(j.status==='done'?'<a href="/job/'+h(j.id)+'">查看报告</a>':'')+
    '</td></tr>').join('');
  gid('empty').hidden=rows.length>0;gid('tbl').hidden=!rows.length;
  gid('empty').innerHTML=only?'<b>你还没有提交过分析</b>点右上角「发起分析」，选一个目录就能开始。':'<b>还没有任务</b>点右上角「发起分析」开始第一次。';
}
async function jobs(){
  try{const r=await fetch('/api/jobs');if(!r.ok)return;const d=await r.json();me=d.me;last=d.jobs;draw()}catch(e){}
}
function setOnly(v){only=v;gid('f-all').setAttribute('aria-pressed',!v);gid('f-mine').setAttribute('aria-pressed',v);draw()}
gid('f-all').onclick=()=>setOnly(false);gid('f-mine').onclick=()=>setOnly(true);
scopeInit();jobs();setInterval(()=>{if(!document.hidden)jobs()},5000);summary();
'''

SOURCES = [
    ('claude', 'Claude Code', '~/.claude/projects'),
    ('codex', 'Codex', '~/.codex/sessions'),
    ('comate', 'Comate', '~/.comate-engine/store'),
]


WS_HTML = ('<div class="field"><div class="actions" style="gap:10px"><button id="ws-btn" type="button" class="ghost sm" disabled>按工作区筛选</button>'
           '<span class="hint" id="ws-note">先在上面选好文件夹</span></div></div>'
           '<div id="ws-box" hidden class="wsbox"><div class="actions" style="gap:8px;margin-bottom:8px"><b>只上传勾选的工作区</b>'
           '<input type="text" id="ws-q" placeholder="筛选工作区…" aria-label="筛选工作区" autocomplete="off" spellcheck="false" style="width:170px">'
           '<button id="ws-all" type="button" class="ghost sm">全选</button><button id="ws-none" type="button" class="ghost sm">全不选</button>'
           '<button id="ws-clear" type="button" class="ghost sm">不筛选</button></div><div id="ws-list" class="wslist"></div></div>')


def index_page(user, max_mb):
    admin = auth.is_admin(user)
    pickers = ''.join(
        '<div class="field"><label for="in-%s">%s</label><input type="file" id="in-%s" webkitdirectory multiple>'
        '<p class="hint">选择这个文件夹：点“选择文件”，按 ⌘⇧G，粘贴路径，回车</p>'
        '<div class="cmd"><pre>%s</pre><button class="sm" type="button">复制路径</button></div></div>' % (key, label, key, path)
        for key, label, path in SOURCES)
    intro = ('只需选你实际使用的那一项。浏览器不允许网页替你自动选中文件夹，所以每一项都有“复制路径”：Mac 上点“选择文件”，'
             '在弹出的窗口里按 ⌘⇧G，粘贴路径，回车，再点右下角的“上传”（窗口里文件是灰的，正常，这里只选文件夹）；'
             '也可以按 ⌘⇧. 显示隐藏文件夹，从用户目录一层层点进去。Windows 直接把路径输进地址栏。')
    if admin:
        intro += ('两个按钮都会把会话存到你名下并一直保留（和同步工具一样），服务器上内容相同的会话只留一份；“只上传到服务器”不分析，“上传并开始分析”传完立刻用你的 Key 分析。')
    else:
        intro += '上传到服务器后由管理员统一分析，你不需要填 API Key。'
    manual = ('<div class="card"><details class="fold" style="border:0;margin:0;padding:0"><summary>没装同步工具？手动上传会话文件</summary>'
              '<p class="muted small">' + intro + '</p>%s'
              + WS_HTML + SCOPE_HTML + ('' if admin else '<div hidden>') + SAVED_HTML + ('' if admin else '</div>') +
              '<div id="cf" hidden class="confirm" role="group" aria-label="确认上传"><b>确认上传</b><p id="cf-sum" aria-live="polite"></p>'
              '<label id="cf-dup-row" class="wsrow" hidden><input type="checkbox" id="cf-dup" checked><span>跳过相同的会话'
              '<span class="sub" id="cf-dup-note"></span></span></label><p id="cf-none" class="muted small">没有相同的会话。</p>'
              '<div class="actions"><button id="cf-ok" type="button">确定上传</button><button id="cf-back" type="button" class="ghost">返回修改</button></div></div>'
              '<div id="barbox" hidden class="bar" style="margin-bottom:12px"><i id="bar"></i></div>'
              '<div class="actions"><button id="go" disabled type="button"%s>上传并开始分析</button>'
              '<button id="go-up" disabled type="button"%s>只上传到服务器</button><span id="sum" class="muted small" aria-live="polite"></span>'
              '<span id="err" class="err" role="alert"></span></div></details></div>') % (
        pickers, '' if admin else ' hidden', ' class="ghost"' if admin else '')
    body = ('<div class="head"><div class="t"><h1>分析任务</h1><p class="muted">团队里的分析任务和报告。'
            '分析的是已经同步到服务器上的会话。</p></div>'
            '<div class="actions"><a class="btn" href="/u/' + esc(user) + '">发起分析</a></div></div>'
            '<div class="card flush"><div class="ch"><h2>全部任务</h2><div class="seg" role="group" aria-label="筛选">'
            '<button id="f-all" aria-pressed="true">全部</button><button id="f-mine" aria-pressed="false">我的</button></div></div>'
            '<div class="tw"><table id="tbl" style="margin-top:10px"><thead><tr><th>状态</th><th>任务</th><th>提交人</th><th>创建</th>'
            '<th class="num">文件</th><th>报告</th></tr></thead><tbody id="rows"></tbody></table></div>'
            '<div class="empty" id="empty">加载中…</div></div>' + manual)
    return shell('分析任务', body, user, INDEX_SCRIPT % {'maxmb': max_mb, 'me': user} + OPTS_JS + 'loadOpts();', 'jobs')
