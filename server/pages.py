import html

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
document.addEventListener('click',e=>{
  const b=e.target.closest('.cmd button');if(!b)return;
  const text=b.parentNode.querySelector('pre').textContent;
  const done=()=>{b.textContent='已复制';setTimeout(()=>{b.textContent='复制'},1500)};
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(text).then(done)}
  else{const t=document.createElement('textarea');t.value=text;t.style.position='fixed';t.style.opacity='0';
    document.body.appendChild(t);t.select();try{document.execCommand('copy');done()}catch(x){}t.remove()}
});
'''

OPTS_HTML = (
    '<div class="field"><label for="o-key">API Key <span id="o-keyreq" class="muted small"></span></label>'
    '<input type="password" id="o-key" autocomplete="new-password" spellcheck="false" placeholder="填写你自己的 key…">'
    '<label class="check small" for="o-remember"><input type="checkbox" id="o-remember"> 在这个浏览器里记住</label>'
    '<p class="hint">只用于这次分析。服务器只在分析期间放在内存里，不写入磁盘。</p></div>'
    '<div class="field"><label for="o-model">模型</label>'
    '<input type="text" id="o-model" list="o-models" placeholder="填写模型名…" autocomplete="off" spellcheck="false">'
    '<datalist id="o-models"></datalist></div>'
    '<div class="field"><label for="o-sub">子代理模型</label>'
    '<input type="text" id="o-sub" placeholder="留空则与主模型相同…" autocomplete="off" spellcheck="false"></div>'
    '<div class="field"><label for="o-effort">思考强度</label><select id="o-effort"><option value="">默认</option>'
    '<option value="low">低 low</option><option value="medium">中 medium</option><option value="high">高 high</option>'
    '<option value="xhigh">很高 xhigh</option><option value="max">最大 max</option></select>'
    '<p class="hint">越高越准，也越慢、越贵。</p></div>')

OPTS_JS = '''
async function loadOpts(){
  try{
    const c=await (await fetch('/api/config')).json();
    gid('o-models').innerHTML=c.models.map(m=>'<option value="'+h(m)+'">').join('');
    if(c.default_model)gid('o-model').placeholder='默认 '+c.default_model;
    gid('o-keyreq').textContent=c.key_required?'（必填）':'（选填，不填则用服务器自己的）';
  }catch(e){}
  const kk=localStorage.getItem('sa-key');if(kk){gid('o-key').value=kk;gid('o-remember').checked=true}
  for(const k of ['model','sub','effort']){const v=localStorage.getItem('sa-'+k);if(v)gid('o-'+k).value=v}
}
function readOpts(){
  const o={api_key:gid('o-key').value.trim(),model:gid('o-model').value.trim(),subagent_model:gid('o-sub').value.trim(),effort:gid('o-effort').value};
  if(gid('o-remember').checked&&o.api_key)localStorage.setItem('sa-key',o.api_key);else localStorage.removeItem('sa-key');
  localStorage.setItem('sa-model',o.model);localStorage.setItem('sa-sub',o.subagent_model);localStorage.setItem('sa-effort',o.effort);
  return o;
}
'''


def esc(value):
    return html.escape(str(value), quote=True)


def shell(title, body, user=None, script='', active=''):
    def link(key, href, label):
        return '<a href="%s"%s>%s</a>' % (href, ' aria-current="page"' if key == active else '', label)
    nav = right = ''
    if user:
        nav = ('<nav aria-label="主导航">%s%s%s</nav>' % (
            link('jobs', '/', '分析任务'), link('me', '/u/' + esc(user), '我的会话'), link('users', '/users', '同事同步')))
        right = ('<div class="who"><span>%s</span><form method="post" action="/logout" style="margin:0">'
                 '<button class="ghost sm">退出</button></form></div>') % esc(user)
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light">'
            '<meta name="theme-color" content="#0d2f27"><title>%s · Skill 使用复盘</title><style>%s</style>'
            '<a class="skip" href="#main">跳到正文</a>'
            '<header class="top"><div class="in"><a class="brand" href="/">Skill 使用复盘</a>%s%s</div></header>'
            '<main id="main">%s</main><script>%s%s</script></html>') % (esc(title), CSS, nav, right, body, COMMON_JS, script)


def login_page(error='', name=''):
    body = ('<div class="card login"><h1>登录</h1>'
            '<p class="muted">输入公司邮箱前缀即可进入，仅办公网内可访问。</p>'
            '<form method="post" action="/login"><div class="field"><label for="u">邮箱前缀</label>'
            '<input type="text" id="u" name="user" value="%s" placeholder="例如 zhangsan" autofocus autocomplete="username" '
            'spellcheck="false" autocapitalize="none"></div>'
            '<p class="err" role="alert">%s</p><button>进入</button></form></div>') % (esc(name), esc(error))
    return shell('登录', body)


JOB_SCRIPT = '''
const id=document.body.dataset.id;
const EFFORT={'':'默认',low:'低',medium:'中',high:'高',xhigh:'很高',max:'最大'};
async function tick(){
  let r;try{r=await fetch('/api/jobs/'+id)}catch(e){gid('state').textContent='网络不通，稍后自动重试…';setTimeout(tick,5000);return}
  if(!r.ok){gid('state').textContent='任务不存在或已删除。';return}
  const j=await r.json();
  gid('state').innerHTML=badge(j.status)+(j.message&&(j.status==='failed'||!['排队中','分析中','完成','已取消'].includes(j.message))?' <span>'+h(j.message)+'</span>':'');
  const kv=[['提交人',j.user],['会话文件',j.files+'\\u00a0个'],['大小',mb(j.bytes)],['创建',ago(j.created)]];
  if(j.project)kv.push(['目录',j.project==='*'?'全部目录':j.project]);
  if(j.model)kv.push(['模型',j.model]);
  if(j.subagent_model)kv.push(['子代理',j.subagent_model]);
  kv.push(['思考强度',EFFORT[j.effort||'']||j.effort]);
  gid('meta').innerHTML=kv.map(x=>'<li><span>'+x[0]+'</span> '+h(x[1])+'</li>').join('');
  gid('log').textContent=j.log||'';gid('logbox').hidden=!j.log;
  gid('report').hidden=j.status!=='done';
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
            '<div class="actions"><a id="report" class="btn" hidden href="/r/%s/report.html" target="_blank" rel="noopener">打开报告</a>'
            '<button id="stop" class="danger" hidden>停止</button><button id="del" class="ghost" hidden>删除</button></div></div>'
            '<div class="card"><h2>状态</h2><div id="state" aria-live="polite">加载中…</div><ul class="kv" id="meta"></ul></div>'
            '<div class="card" id="logbox" hidden><h2>运行日志（最近几行）</h2><pre class="log" id="log" tabindex="0"></pre></div>') % (esc(jid), esc(jid))
    return shell('任务 ' + jid, body, user, "document.body.dataset.id='%s';" % esc(jid) + JOB_SCRIPT, 'jobs')
