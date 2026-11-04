from pages import shell

INDEX_SCRIPT = '''
const $=s=>document.getElementById(s);
const MAXMB=%(maxmb)d;
const SRC={
  claude:f=>f.name.endsWith('.jsonl'),
  codex:f=>f.name.endsWith('.jsonl'),
  comate:f=>f.name.startsWith('chat_session_')&&!f.name.endsWith('.deleted')};
function picked(){
  const since=Date.now()-(+$('days').value||14)*864e5, out=[];
  for(const s of Object.keys(SRC)){
    for(const f of $('in-'+s).files||[]){
      if(!SRC[s](f)||f.lastModified<since||f.size>MAXMB*1048576||!f.size)continue;
      const parts=(f.webkitRelativePath||f.name).split('/');
      out.push({s:s,f:f,rel:parts.slice(1).join('/')||parts[0]});
    }
  }
  return out;
}
function summary(){
  const p=picked(),mb=p.reduce((a,x)=>a+x.f.size,0)/1048576;
  $('sum').textContent=p.length?('将上传 '+p.length+' 个会话文件，共 '+mb.toFixed(1)+' MB'):'还没有选择文件';
  $('go').disabled=!p.length;
}
for(const s of Object.keys(SRC))$('in-'+s).onchange=summary;
$('days').onchange=summary;
const H={'X-Requested-With':'sa'};
async function go(){
  const p=picked();if(!p.length)return;
  $('go').disabled=true;$('err').textContent='';
  try{
    let r=await fetch('/api/jobs',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},H),
      body:JSON.stringify({limit:+$('limit').value||20})});
    if(!r.ok)throw new Error(await r.text());
    const job=await r.json();let done=0,next=0,fail=null;
    async function lane(){
      while(next<p.length&&!fail){
        const x=p[next++];
        const u='/api/jobs/'+job.id+'/files?source='+x.s+'&path='+encodeURIComponent(x.rel);
        const res=await fetch(u,{method:'PUT',headers:H,body:x.f});
        if(!res.ok){fail=await res.text();return}
        $('bar').style.width=(++done*100/p.length)+'%%';
        $('sum').textContent='上传中 '+done+'/'+p.length;
      }
    }
    await Promise.all([lane(),lane(),lane()]);
    if(fail)throw new Error(fail);
    r=await fetch('/api/jobs/'+job.id+'/start',{method:'POST',headers:H});
    if(!r.ok)throw new Error(await r.text());
    location.href='/job/'+job.id;
  }catch(e){$('err').textContent='失败：'+e.message;$('go').disabled=false}
}
$('go').onclick=go;
const names={uploading:'上传中',queued:'排队中',running:'分析中',done:'已完成',failed:'失败'};
let me='';
async function jobs(){
  const r=await fetch('/api/jobs');if(!r.ok)return;
  const d=await r.json();me=d.me;const only=$('mine').checked;
  $('rows').innerHTML=d.jobs.filter(j=>!only||j.user===me).map(j=>
    '<tr><td><a href="/job/'+j.id+'">'+j.id+'</a></td><td>'+j.user+'</td><td>'+
    new Date(j.created*1000).toLocaleString()+'</td><td><span class="badge s-'+j.status+'">'+names[j.status]+
    '</span></td><td>'+j.files+'</td><td>'+(j.status==='done'?'<a href="/r/'+j.id+'/report.html" target="_blank">报告</a>':'')+
    '</td></tr>').join('')||'<tr><td colspan="6" class="muted">暂无任务</td></tr>';
}
$('mine').onchange=jobs;jobs();setInterval(jobs,5000);summary();
'''

SOURCES = [
    ('claude', 'Claude Code', '~/.claude/projects'),
    ('codex', 'Codex', '~/.codex/sessions'),
    ('comate', 'Comate', '~/.comate-engine/store'),
]


def index_page(user, max_mb):
    rows = ''.join(
        '<div class="row"><b>%s</b><span class="hint">选择文件夹 %s</span>'
        '<input type="file" id="in-%s" webkitdirectory multiple></div>' % (label, path, key)
        for key, label, path in SOURCES)
    body = ('<div class="card"><h2>上传历史会话</h2>'
            '<p class="muted">只需选你实际使用的那一项。隐藏目录在选择框里按 ⌘⇧G（Mac）或直接输入路径（Windows）。'
            '服务器分析完成后会删除你上传的原始会话，只保留报告。</p>%s'
            '<div class="row"><b>范围</b><span>只取最近 <input type="number" id="days" value="14" min="1" max="365" style="width:70px"> 天修改过的，'
            '最多分析 <input type="number" id="limit" value="20" min="1" max="50" style="width:70px"> 个会话</span></div>'
            '<div class="bar"><i id="bar"></i></div>'
            '<p><button id="go" disabled>开始分析</button> <span id="sum" class="muted"></span> <span id="err" class="err"></span></p></div>'
            '<div class="card"><h2>全部任务 <label class="muted" style="font-weight:normal;margin-left:12px">'
            '<input type="checkbox" id="mine"> 只看我的</label></h2>'
            '<table><thead><tr><th>编号</th><th>提交人</th><th>时间</th><th>状态</th><th>文件数</th><th></th></tr></thead>'
            '<tbody id="rows"></tbody></table></div>') % rows
    return shell('Skill 使用复盘', body, user, INDEX_SCRIPT % {'maxmb': max_mb})
