import html

import auth
from icon import ICON_SVG
from style import CSS

COMMON_JS = '''
const gid=s=>document.getElementById(s);
const h=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mb=b=>{const v=b/1048576;return v>=1024?(v/1024).toFixed(1)+'\\u00a0GB':v.toFixed(v>=100?0:1)+'\\u00a0MB'};
const ago=t=>t?new Date(t*1000).toLocaleString('zh-CN',{hour12:false}):'-';
const rel=t=>{if(!t)return '-';const d=Date.now()/1000-t;
  const s=d<60?'刚刚':d<3600?Math.floor(d/60)+'\\u00a0分钟前':d<86400?Math.floor(d/3600)+'\\u00a0小时前':
    new Date(t*1000).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'});
  return '<time title="'+h(ago(t))+'">'+s+'</time>'};
const SRC_NAME={claude:'Claude',codex:'Codex',comate:'Comate'};
const src=o=>Object.keys(o||{}).map(k=>(SRC_NAME[k]||k)+'\\u00a0'+o[k]).join(' · ')||'-';
const STATUS={uploading:'上传中',queued:'排队中',running:'分析中',done:'已完成',failed:'失败',cancelled:'已取消'};
const badge=s=>'<span class="badge s-'+h(s)+'">'+h(STATUS[s]||s)+'</span>';
const H={'X-Requested-With':'sa'};
const MODEL_OK=v=>!v||/^[A-Za-z0-9][A-Za-z0-9._:\\/\\[\\] -]{0,80}$/.test(v);
document.addEventListener('click',e=>{
  const b=e.target.closest('.cmd button');if(!b)return;
  const text=b.parentNode.querySelector('pre').textContent;
  const was=b.textContent,done=()=>{b.textContent='已复制';setTimeout(()=>{b.textContent=was},1500)};
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(text).then(done)}
  else{const t=document.createElement('textarea');t.value=text;t.style.position='fixed';t.style.opacity='0';
    document.body.appendChild(t);t.select();try{document.execCommand('copy');done()}catch(x){}t.remove()}
});
'''

OPTS_HTML = (
    '<div class="field"><label for="o-key">API Key <span id="o-keyreq" class="muted small"></span></label>'
    '<input type="password" id="o-key" autocomplete="new-password" spellcheck="false" placeholder="填写你自己的 key…">'
    '<p class="hint">只用于你发起的分析。保存在这个浏览器里；服务器不写入磁盘，分析期间只放在内存里。</p></div>'
    '<div class="field"><label for="o-model">模型</label>'
    '<input type="text" id="o-model" list="o-models" placeholder="填写模型名…" autocomplete="off" spellcheck="false">'
    '<datalist id="o-models"></datalist></div>'
    '<div class="field"><label for="o-sub">子代理模型</label>'
    '<input type="text" id="o-sub" placeholder="留空则与主模型相同…" autocomplete="off" spellcheck="false"></div>'
    '<div class="field"><label for="o-effort">思考强度</label><select id="o-effort"><option value="">默认</option>'
    '<option value="low">低 low</option><option value="medium">中 medium</option><option value="high">高 high</option>'
    '<option value="xhigh">很高 xhigh</option><option value="max">最大 max</option></select>'
    '<p class="hint">越高越准，也越慢、越贵。</p></div>')

SCOPE_HTML = (
    '<fieldset class="scope"><legend>分析范围（二选一）</legend>'
    '<div class="opt on"><input type="radio" name="scope" id="scope-days" checked aria-label="按天数">'
    '<span>最近</span><input type="number" id="days" value="30" min="1" max="3650" aria-label="天数"><span>天内的全部会话</span></div>'
    '<div class="opt"><input type="radio" name="scope" id="scope-count" aria-label="按会话数">'
    '<span>最近</span><input type="number" id="limit" value="20" min="1" max="500" aria-label="会话数"><span>个会话</span></div></fieldset>')

SAVED_HTML = '<div class="saved" id="saved" aria-live="polite"></div>'

OPTS_JS = """
function scopeInit(){
  const rows=document.querySelectorAll('.scope .opt');
  const set=r=>{for(const x of rows){const on=x===r;x.classList.toggle('on',on);x.querySelector('input[type=radio]').checked=on}};
  for(const r of rows){r.addEventListener('focusin',()=>set(r));r.addEventListener('click',()=>set(r))}
  set(rows[0]);
}
function readScope(){return gid('scope-days').checked?{days:+gid('days').value||30}:{limit:+gid('limit').value||20}}
const EFFORT_NAME={'':'默认',low:'低',medium:'中',high:'高',xhigh:'很高',max:'最大'};
const getOpt=k=>localStorage.getItem('sa-'+k)||'';
let CFG={};
function optsProblem(){
  if(CFG.key_required&&!getOpt('key'))return '还没保存 API Key，请到「设置」保存。';
  if(!getOpt('model')&&!CFG.default_model)return '还没填模型名，请到「设置」填写。';
  for(const k of ['model','sub'])if(!MODEL_OK(getOpt(k)))return '模型名「'+getOpt(k)+'」格式不对：只能含字母、数字、空格和 . _ : / [ ] -，请到「设置」改。';
  return '';
}
function renderSaved(){
  const box=gid('saved');if(!box)return;
  const bad=optsProblem();
  const bits=['模型 '+(getOpt('model')||CFG.default_model||'未填'),'子代理 '+(getOpt('sub')||'同主模型'),
    '强度 '+(EFFORT_NAME[getOpt('effort')]||'默认'),'Key '+(getOpt('key')?'已保存':(CFG.key_required?'未填':'用服务器的'))];
  box.className='saved'+(bad?' warn':'');
  box.innerHTML='<div class="sv"><b>分析设置</b><a href="/settings">'+(bad?'去设置':'修改')+'</a></div><div class="muted small">'+h(bits.join(' · '))+'</div>'+
    (bad?'<div class="err small">'+h(bad)+'</div>':'');
}
async function loadOpts(){
  try{CFG=await (await fetch('/api/config')).json()}catch(e){}
  renderSaved();
}
function readOpts(){return {api_key:getOpt('key'),model:getOpt('model'),subagent_model:getOpt('sub'),effort:getOpt('effort')}}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def shell(title, body, user=None, script='', active=''):
    def link(key, href, label):
        return '<a href="%s"%s>%s</a>' % (href, ' aria-current="page"' if key == active else '', label)
    nav = right = ''
    if user:
        nav = ('<nav aria-label="主导航">%s%s%s%s%s</nav>' % (
            link('jobs', '/', '分析任务'), link('me', '/u/' + esc(user), '我的会话'), link('users', '/users', '团队接入'), link('all', '/all', '全部会话') if auth.is_admin(user) else '', link('settings', '/settings', '设置')))
        right = ('<div class="who"><span>%s</span><form method="post" action="/logout" style="margin:0">'
                 '<button class="ghost sm">退出</button></form></div>') % esc(user)
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light">'
            '<meta name="theme-color" content="#0d2f27">'
            '<link rel="icon" type="image/svg+xml" href="/favicon.svg"><link rel="alternate icon" type="image/png" href="/favicon.ico">'
            '<link rel="apple-touch-icon" href="/apple-touch-icon.png"><title>%s · Skill 使用复盘</title><style>%s</style>'
            '<a class="skip" href="#main">跳到正文</a>'
            '<header class="top"><div class="in"><a class="brand" href="/">%s<span>Skill 使用复盘</span></a>%s%s</div></header>'
            '<main id="main">%s</main><script>%s%s</script></html>') % (esc(title), CSS, ICON_SVG.replace('<svg ', '<svg class="logo" aria-hidden="true" ', 1), nav, right, body, COMMON_JS, script)


def login_page(error='', name='', ask_password=False):
    password = ('<div class="field"><label for="p">管理员密码</label><input type="password" id="p" name="password" '
                'autocomplete="current-password" autofocus></div>') if ask_password else ''
    body = ('<div class="card login"><h1>登录</h1>'
            '<p class="muted">输入公司邮箱前缀即可进入，仅办公网内可访问。</p>'
            '<form method="post" action="/login"><div class="field"><label for="u">邮箱前缀</label>'
            '<input type="text" id="u" name="user" value="%s" placeholder="例如 zhangsan" %s autocomplete="username" '
            'spellcheck="false" autocapitalize="none"></div>%s'
            '<p class="err" role="alert">%s</p><button>进入</button></form></div>') % (
        esc(name), '' if ask_password else 'autofocus', password, esc(error))
    return shell('登录', body)


JOB_SCRIPT = '''
const id=document.body.dataset.id;
const EFFORT={'':'默认',low:'低',medium:'中',high:'高',xhigh:'很高',max:'最大'};
async function loadOwners(){
  const r=await fetch('/api/jobs/'+id+'/owners');if(!r.ok)return;
  const rows=(await r.json()).sessions;if(!rows.length)return;
  const by={};rows.forEach(x=>by[x.user]=(by[x.user]||0)+1);
  gid('ownsum').textContent=Object.entries(by).sort((a,b)=>b[1]-a[1]).map(o=>o[0]+' '+o[1]).join(' · ');
  gid('ownbox').hidden=false;
  const draw=()=>{const q=gid('oq').value.trim().toLowerCase();
    gid('owners').innerHTML='<table><thead><tr><th>同事</th><th>来源</th><th>文件</th><th>工作目录</th><th class="num">大小</th></tr></thead><tbody>'+
      rows.filter(x=>!q||(x.user+' '+x.rel+' '+x.project).toLowerCase().includes(q)).map(x=>'<tr><td><b>'+h(x.user)+'</b></td><td>'+h(x.source)+
        '</td><td class="mono small">'+h(x.rel.split('/').pop())+'</td><td class="small">'+h(x.project)+'</td><td class="num">'+mb(x.size)+'</td></tr>').join('')+'</tbody></table>'};
  gid('oq').oninput=draw;draw();
}
async function tick(){
  let r;try{r=await fetch('/api/jobs/'+id)}catch(e){gid('state').textContent='网络不通，稍后自动重试…';setTimeout(tick,5000);return}
  if(!r.ok){gid('state').textContent='任务不存在或已删除。';return}
  const j=await r.json();
  gid('state').innerHTML=badge(j.status)+(j.message&&(j.status==='failed'||!['排队中','分析中','完成','已取消'].includes(j.message))?' <span>'+h(j.message)+'</span>':'');
  const kv=[['提交人',j.user],['会话文件',j.files+'\\u00a0个'],['大小',mb(j.bytes)],['创建',ago(j.created)]];
  if(j.project)kv.push(['目录',j.project==='*'?'全部目录':j.project]);
  if(j.model)kv.push(['模型',j.model]);
  if(j.subagent_model)kv.push(['子代理',j.subagent_model]);
  if(j.all_users)kv.push(['范围','所有同事的会话']);
  kv.push(['思考强度',EFFORT[j.effort||'']||j.effort]);
  if(j.all_users&&!gid('ownbox').dataset.on){gid('ownbox').dataset.on='1';loadOwners()}
  gid('meta').innerHTML=kv.map(x=>'<li><span>'+x[0]+'</span> '+h(x[1])+'</li>').join('');
  gid('log').textContent=j.log||'';gid('logbox').hidden=!j.log;
  if(j.status==='done'&&!gid('reportframe').dataset.on){gid('reportframe').dataset.on='1';gid('reportframe').src='/r/'+id+'/report.html';gid('reportbox').hidden=false}
  gid('del').hidden=!(j.mine&&j.status!=='running'&&j.status!=='queued');
  gid('stop').hidden=!(j.mine&&(j.status==='running'||j.status==='queued'));
  if(['queued','running','uploading'].includes(j.status))setTimeout(tick,3000);
}
gid('stop').onclick=async()=>{if(!confirm('停止这个任务？已经花掉的模型费用不会退回。'))return;
  gid('stop').disabled=true;const r=await fetch('/api/jobs/'+id+'/cancel',{method:'POST',headers:H});
  if(!r.ok)alert(await r.text());gid('stop').disabled=false;tick()};
gid('del').onclick=async()=>{if(!confirm('删除这个任务和它的报告？'))return;
  await fetch('/api/jobs/'+id+'/delete',{method:'POST',headers:H});location.href='/'};
tick();
'''


def job_page(jid, user):
    body = ('<div class="head"><div class="t"><h1>分析任务 <span class="mono">%s</span></h1>'
            '<p class="muted"><a href="/">返回任务列表</a></p></div>'
            '<div class="actions"><button id="stop" class="danger" hidden>停止</button><button id="del" class="ghost" hidden>删除</button></div></div>'
            '<div class="card"><h2>状态</h2><div id="state" aria-live="polite">加载中…</div><ul class="kv" id="meta"></ul></div>'
            '<div class="card flush" id="reportbox" hidden><div class="ch"><h2>分析报告</h2>'
            '<a class="small" href="/r/%s/report.html" target="_blank" rel="noopener">在新窗口打开</a></div>'
            '<iframe id="reportframe" class="report" title="分析报告"></iframe></div>'
            '<div class="card flush" id="ownbox" hidden><div class="ch"><h2>会话归属</h2>'
            '<input type="text" id="oq" placeholder="按同事或文件名筛选…" aria-label="筛选会话" autocomplete="off" spellcheck="false" style="width:220px"></div>'
            '<p class="muted small" id="ownsum" style="margin:0 16px 8px"></p><div class="tw" id="owners"></div></div>'
            '<div class="card" id="logbox" hidden><h2>运行日志（最近几行）</h2><pre class="log" id="log" tabindex="0"></pre></div>') % (esc(jid), esc(jid))
    return shell('任务 ' + jid, body, user, "document.body.dataset.id='%s';" % esc(jid) + JOB_SCRIPT, 'jobs')
