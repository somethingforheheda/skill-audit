from pages import OPTS_HTML, OPTS_JS, esc, shell

USERS_SCRIPT = '''
(async()=>{
  let d;try{d=await (await fetch('/api/users')).json()}catch(e){gid('empty').textContent='加载失败，请刷新重试。';return}
  gid('rows').innerHTML=d.users.map(u=>
    '<tr><td><a href="/u/'+h(u.user)+'">'+h(u.user)+'</a>'+(u.user===d.me?' <span class="badge">我</span>':'')+'</td><td class="num">'+u.files+
    '</td><td class="num">'+mb(u.bytes)+'</td><td>'+h(src(u.sources))+'</td><td class="num">'+u.skills+'</td><td>'+rel(u.last)+'</td></tr>').join('');
  gid('tbl').hidden=!d.users.length;
  gid('empty').hidden=d.users.length>0;
  gid('empty').innerHTML='<b>还没有人同步过</b>按上面的步骤接入后，这里会出现你的名字。';
})();
'''


def cmd(text):
    return '<div class="cmd"><pre>%s</pre><button class="sm" type="button">复制</button></div>' % esc(text)


def connect_box(host, user):
    return ('<div class="card"><h2>接入方法</h2><ol class="steps">'
            '<li><h3>安装或更新 skill-audit</h3><p class="muted small">已装过的话运行下面这条就会升到最新版。</p>%s'
            '<details class="fold" style="border:0;margin:8px 0 0;padding:0"><summary class="small">访问不了 GitHub？从这台服务器下载</summary>%s</details></li>'
            '<li><h3>绑定服务器和你的 id</h3>%s</li>'
            '<li><h3>先上传一次</h3><p class="muted small">会先告诉你要传多少，确认后才开始。</p>%s</li>'
            '<li><h3>设置定时同步</h3><p class="muted small">之后每隔几小时自动同步。新版本会在同步时自动从服务器更新，不用手动升级。</p>%s</li>'
            '</ol></div>') % (
        cmd('skill-audit update'),
        cmd('mkdir -p ~/.local/bin && curl -o ~/.local/bin/skill-audit http://%s/download/skill-audit.pyz && chmod +x ~/.local/bin/skill-audit' % host),
        cmd('skill-audit sync init --server http://%s --user %s' % (host, user)),
        cmd('skill-audit sync run'), cmd('skill-audit sync install'))


def users_page(user, host):
    body = ('<div class="head"><div class="t"><h1>同事同步</h1><p class="muted">会话同步到服务器后，才能在这里选目录做分析。</p></div></div>'
            + connect_box(host, user) +
            '<div class="card flush"><div class="ch"><h2>已同步的同事</h2></div><div class="tw"><table id="tbl" style="margin-top:10px"><thead><tr>'
            '<th>用户</th><th class="num">会话文件</th><th class="num">大小</th><th>来源</th><th class="num">Skill 目录</th><th>最近同步</th></tr></thead>'
            '<tbody id="rows"></tbody></table></div><div class="empty" id="empty">加载中…</div></div>')
    return shell('同事同步', body, user, USERS_SCRIPT, 'users')


USER_SCRIPT = OPTS_JS + '''
const target=document.body.dataset.user, mine=document.body.dataset.me===target;
let chosen=null, projects=[], q='';
function pickBox(){
  const p=gid('pick');if(!p)return;
  if(chosen===null){p.className='pick';p.innerHTML='<span class="muted">还没选。请在左边的表格里点一个目录。</span>'}
  else{p.className='pick on';p.innerHTML='<span class="muted small">已选目录</span><b>'+h(chosen==='*'?'全部目录':chosen)+'</b>'}
  gid('go').disabled=chosen===null;
}
function draw(){
  const rows=projects.filter(p=>!q||(p.name+' '+p.paths.join(' ')).toLowerCase().includes(q));
  gid('rows').innerHTML=rows.map(p=>{const on=p.name===chosen;
    return '<tr class="'+(mine?'row-pick ':'')+(on?'sel':'')+'" data-n="'+encodeURIComponent(p.name)+'"><td><b>'+h(p.name)+'</b><div class="sub">'+
      p.paths.map(x=>h(x)).join('<br>')+'</div></td><td class="num">'+p.files+'</td><td class="num">'+mb(p.bytes)+'</td><td>'+h(src(p.sources))+
      '</td><td>'+rel(p.last)+'</td>'+(mine?'<td><button class="ghost sm" type="button" aria-pressed="'+on+'">'+(on?'已选':'选择')+'</button></td>':'')+'</tr>'}).join('');
  gid('tbl').hidden=!rows.length;gid('empty').hidden=rows.length>0;
  gid('empty').innerHTML=projects.length?'<b>没有匹配的目录</b>换个关键词试试。':
    '<b>还没有同步数据</b>'+(mine?'先到<a href="/users">「同事同步」</a>按步骤接入。':'这位同事还没有同步过。');
}
function choose(v){chosen=v;pickBox();draw()}
async function load(){
  let d;try{d=await (await fetch('/api/users/'+target)).json()}catch(e){gid('empty').textContent='加载失败，请刷新重试。';return}
  gid('head').innerHTML=[['会话文件',d.files],['大小',mb(d.bytes)],['来源',src(d.sources)],['Skill 目录',d.skills],['最近同步',rel(d.last)]].map(x=>
    '<div><dt>'+x[0]+'</dt><dd>'+(x[0]==='最近同步'?x[1]:h(x[1]))+'</dd></div>').join('');
  projects=(d.projects||[]).slice().sort((a,b)=>(b.last||0)-(a.last||0));
  if(mine&&d.files)gid('all').hidden=false;
  draw();
}
gid('q').oninput=e=>{q=e.target.value.trim().toLowerCase();draw()};
if(mine){
  gid('rows').onclick=e=>{const tr=e.target.closest('tr[data-n]');if(tr)choose(decodeURIComponent(tr.dataset.n))};
  gid('all').onclick=()=>choose('*');
  gid('go').onclick=async()=>{
    gid('err').textContent='';gid('go').disabled=true;gid('go').textContent='正在创建任务…';
    const o=Object.assign(readOpts(),{project:chosen,days:+gid('days').value||30,limit:+gid('limit').value||20,
      max_episodes:+gid('episodes').value||30});
    const r=await fetch('/api/analyze',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},H),body:JSON.stringify(o)});
    if(!r.ok){gid('err').textContent=await r.text();gid('go').disabled=false;gid('go').textContent='开始分析';return}
    location.href='/job/'+(await r.json()).id;
  };
  pickBox();loadOpts();
}
load();
'''


def panel():
    return ('<aside class="card panel" aria-label="发起分析"><h2>发起分析</h2>'
            '<p class="muted small">先在左边选目录，再点开始。开始后可以随时停止。</p>'
            '<div class="pick" id="pick" aria-live="polite"></div>'
            '<div class="grid3" style="margin-bottom:14px">'
            '<div class="field"><label for="days">最近天数</label><input type="number" id="days" value="30" min="1" max="3650"></div>'
            '<div class="field"><label for="limit">会话数</label><input type="number" id="limit" value="20" min="1" max="50"></div>'
            '<div class="field"><label for="episodes">审查次数</label><input type="number" id="episodes" value="30" min="1" max="60"></div></div>'
            + OPTS_HTML +
            '<button id="go" disabled style="width:100%">开始分析</button><p id="err" class="err" role="alert"></p></aside>')


def user_page(user, target):
    mine = user == target
    table = ('<div class="card flush"><div class="ch"><h2>按目录分组</h2>'
             '<button id="all" class="ghost sm" type="button" hidden>选全部目录</button>'
             '<input type="text" id="q" placeholder="筛选目录…" aria-label="筛选目录" autocomplete="off" spellcheck="false" style="width:200px"></div>'
             '<div class="tw"><table id="tbl"><thead><tr><th>目录</th><th class="num">文件</th><th class="num">大小</th><th>来源</th><th>最近活动</th>'
             + ('<th></th>' if mine else '') +
             '</tr></thead><tbody id="rows"></tbody></table></div><div class="empty" id="empty">加载中…</div></div>')
    note = '' if mine else '<p class="muted small">这是 %s 的会话，只能查看；分析需要本人登录后发起。</p>' % esc(target)
    body = ('<div class="head"><div class="t"><h1>%s 的会话</h1><p class="muted">按会话所在的工作目录分组。</p></div></div>'
            '<div class="card"><dl class="stat" id="head"><div><dd class="muted">加载中…</dd></div></dl>%s</div>') % (esc(target), note)
    body += ('<div class="split">%s%s</div>' % (table, panel())) if mine else table
    script = ("document.body.dataset.user='%s';document.body.dataset.me='%s';" % (esc(target), esc(user))
              + USER_SCRIPT)
    return shell(target, body, user, script, 'me' if mine else 'users')
