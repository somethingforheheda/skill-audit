from pages import esc, shell

FMT = '''
const mb=b=>(b/1048576).toFixed(b>1073741824?0:1)+' MB';
const ago=t=>t?new Date(t*1000).toLocaleString():'-';
const src=o=>Object.keys(o||{}).map(k=>({claude:'Claude',codex:'Codex',comate:'Comate'}[k]||k)+' '+o[k]).join(' · ');
const H={'X-Requested-With':'sa'};
'''

USERS_SCRIPT = FMT + '''
(async()=>{
  const d=await (await fetch('/api/users')).json();
  document.getElementById('rows').innerHTML=d.users.map(u=>
    '<tr><td><a href="/u/'+u.user+'">'+u.user+(u.user===d.me?'（我）':'')+'</a></td><td>'+u.files+'</td><td>'+mb(u.bytes)+
    '</td><td>'+src(u.sources)+'</td><td>'+u.skills+'</td><td>'+ago(u.last)+'</td></tr>').join('')||
    '<tr><td colspan="6" class="muted">还没有人同步过</td></tr>';
})();
'''

USER_SCRIPT = FMT + '''
const target=document.body.dataset.user;
async function load(){
  const d=await (await fetch('/api/users/'+target)).json();
  document.getElementById('head').textContent=d.files+' 个会话文件 · '+mb(d.bytes)+' · '+src(d.sources)+' · '+d.skills+' 个 Skill 目录 · 最近同步 '+ago(d.last);
  const mine=document.body.dataset.me===target;
  document.getElementById('rows').innerHTML=(d.projects||[]).map(p=>
    '<tr><td><b>'+p.name+'</b><div class="muted">'+p.paths.join('<br>')+'</div></td><td>'+p.files+'</td><td>'+mb(p.bytes)+
    '</td><td>'+src(p.sources)+'</td><td>'+ago(p.last)+'</td><td>'+(mine?'<button data-p="'+encodeURIComponent(p.name)+'">分析</button>':'')+
    '</td></tr>').join('')||'<tr><td colspan="6" class="muted">还没有同步数据</td></tr>';
  document.getElementById('all').style.display=mine&&d.files?'inline-block':'none';
}
async function analyze(project){
  const days=+document.getElementById('days').value||30,limit=+document.getElementById('limit').value||20;
  const r=await fetch('/api/analyze',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},H),
    body:JSON.stringify({project:project,days:days,limit:limit})});
  if(!r.ok){document.getElementById('err').textContent=await r.text();return}
  location.href='/job/'+(await r.json()).id;
}
document.getElementById('rows').onclick=e=>{const b=e.target.closest('button');if(b)analyze(decodeURIComponent(b.dataset.p))};
document.getElementById('all').onclick=()=>analyze('*');
load();
'''


def connect_box(host):
    return ('<div class="card"><h2>怎么接入</h2><p class="muted">在自己电脑的终端里运行（需要先 <code>skill-audit update</code> 到支持 sync 的版本）：</p>'
            '<pre>skill-audit sync init --server http://%s --user 你的邮箱前缀\n'
            'skill-audit sync run          # 先上传一次，会先告诉你要传多少\n'
            'skill-audit sync install      # 之后每隔几小时自动同步</pre>'
            '<p class="muted">还没有 skill-audit，或版本没有 sync 命令？先下载：</p>'
            '<pre>mkdir -p ~/.local/bin &amp;&amp; curl -o ~/.local/bin/skill-audit http://%s/download/skill-audit.pyz &amp;&amp; chmod +x ~/.local/bin/skill-audit</pre></div>') % (esc(host), esc(host))


def users_page(user, host):
    body = (connect_box(host) +
            '<div class="card"><h2>已同步的同事</h2><table><thead><tr><th>用户</th><th>会话文件</th><th>大小</th><th>来源</th>'
            '<th>Skill 目录</th><th>最近同步</th></tr></thead><tbody id="rows"></tbody></table></div>')
    return shell('同步用户', body, user, USERS_SCRIPT)


def user_page(user, target):
    body = ('<div class="card"><h2>%s 的会话</h2><p id="head" class="muted">加载中…</p>'
            '<p class="muted">按会话所在目录分组。分析只用已同步的会话，完成后在“全部任务”里看报告。</p>'
            '<p>只看最近 <input type="number" id="days" value="30" min="1" max="3650" style="width:70px"> 天，最多分析 '
            '<input type="number" id="limit" value="20" min="1" max="50" style="width:70px"> 个会话 '
            '<button id="all" style="display:none">分析全部目录</button> <span id="err" class="err"></span></p></div>'
            '<div class="card"><table><thead><tr><th>目录</th><th>文件</th><th>大小</th><th>来源</th><th>最近活动</th><th></th></tr></thead>'
            '<tbody id="rows"></tbody></table></div>') % esc(target)
    script = "document.body.dataset.user='%s';document.body.dataset.me='%s';" % (esc(target), esc(user)) + USER_SCRIPT
    return shell(target, body, user, script)
