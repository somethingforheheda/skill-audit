import html

CSS = '''
*{box-sizing:border-box}body{margin:0;background:#f5f6f2;color:#202d28;font:14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif}
header{background:#fff;border-bottom:1px solid #e1e5dd;padding:12px 24px;display:flex;align-items:center;gap:16px}
header h1{font-size:17px;margin:0;flex:1}header a,header button{color:#245d45}
main{max-width:980px;margin:24px auto;padding:0 16px}
.card{background:#fff;border:1px solid #e1e5dd;border-radius:10px;padding:18px 20px;margin-bottom:18px}
.card h2{font-size:15px;margin:0 0 10px}.muted{color:#79837b;font-size:12.5px}
.row{display:flex;gap:12px;align-items:center;padding:8px 0;border-top:1px solid #eef0ea}.row:first-of-type{border-top:0}
.row b{width:120px}.row .hint{flex:1;color:#79837b;font-size:12.5px;word-break:break-all}
button,.btn{background:#245d45;color:#fff;border:0;border-radius:6px;padding:7px 16px;cursor:pointer;font:inherit}
button.ghost{background:#fff;color:#245d45;border:1px solid #c9d3c6}button:disabled{opacity:.5;cursor:default}
input[type=text],input[type=number]{border:1px solid #c9d3c6;border-radius:6px;padding:6px 10px;font:inherit}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 6px;border-top:1px solid #eef0ea;font-size:13px}
th{color:#79837b;font-weight:500;border-top:0}
.badge{padding:1px 8px;border-radius:10px;font-size:12px;background:#eef0ea}
.s-done{background:#e0f0e5;color:#1d6b3a}.s-failed{background:#fbe3e0;color:#a3321f}
.s-running{background:#e3ecfb;color:#1f4fa3}.s-queued{background:#fdf0d5;color:#8a5a00}
pre{background:#202d28;color:#dfe8e2;border-radius:8px;padding:12px;overflow:auto;max-height:320px;font-size:12px;white-space:pre-wrap}
.bar{height:8px;background:#e7eee4;border-radius:4px;overflow:hidden;margin:10px 0}.bar i{display:block;height:100%;width:0;background:#245d45}
a{color:#245d45}.err{color:#a3321f}
'''


def esc(value):
    return html.escape(str(value), quote=True)


def shell(title, body, user=None, script=''):
    right = ''
    if user:
        right = ('<span class="muted">%s</span><form method="post" action="/logout" style="margin:0">'
                 '<button class="ghost">退出</button></form>') % esc(user)
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title><style>%s</style>'
            '<header><h1><a href="/" style="text-decoration:none">Skill 使用复盘</a></h1><a href="/users">同事同步</a>%s</header>'
            '<main>%s</main><script>%s</script></html>') % (esc(title), CSS, right, body, script)


def login_page(error='', name=''):
    body = ('<div class="card" style="max-width:420px;margin:60px auto"><h2>登录</h2>'
            '<p class="muted">输入你的公司邮箱前缀即可，仅在办公网内可访问。后续会接入公司统一登录。</p>'
            '<form method="post" action="/login"><p><input type="text" name="user" value="%s" placeholder="邮箱前缀，如 zhangsan" '
            'autofocus style="width:100%%"></p><p class="err">%s</p><button>进入</button></form></div>') % (esc(name), esc(error))
    return shell('登录', body)


JOB_SCRIPT = '''
const id=document.body.dataset.id||location.pathname.split('/').pop();
const $=s=>document.getElementById(s);
const names={uploading:'上传中',queued:'排队中',running:'分析中',done:'已完成',failed:'失败'};
async function tick(){
  const r=await fetch('/api/jobs/'+id);if(!r.ok){$('state').textContent='任务不存在或已删除';return}
  const j=await r.json();
  $('state').innerHTML='<span class="badge s-'+j.status+'">'+names[j.status]+'</span> '+(j.message||'');
  $('meta').textContent=j.user+' · '+j.files+' 个文件 · '+(j.bytes/1048576).toFixed(1)+' MB · '+new Date(j.created*1000).toLocaleString();
  $('log').textContent=j.log||'';$('logbox').style.display=j.log?'block':'none';
  $('report').style.display=j.status==='done'?'inline-block':'none';
  $('del').style.display=(j.mine&&j.status!=='running')?'inline-block':'none';
  if(j.status==='queued'||j.status==='running'||j.status==='uploading')setTimeout(tick,3000);
}
$('del').onclick=async()=>{if(!confirm('删除这个任务和它的报告？'))return;
  await fetch('/api/jobs/'+id+'/delete',{method:'POST',headers:{'X-Requested-With':'sa'}});location.href='/'};
tick();
'''


def job_page(jid, user):
    body = ('<div class="card"><h2>任务 %s</h2><p id="state" class="muted">加载中…</p><p id="meta" class="muted"></p>'
            '<p><a id="report" class="btn" style="display:none;text-decoration:none" href="/r/%s/report.html" target="_blank">打开报告</a> '
            '<button id="del" class="ghost" style="display:none">删除</button> <a href="/">返回首页</a></p></div>'
            '<div class="card" id="logbox" style="display:none"><h2>运行日志（最近几行）</h2><pre id="log"></pre></div>') % (esc(jid), esc(jid))
    return shell('任务 ' + jid, body, user, "document.body.dataset.id='%s';" % esc(jid) + JOB_SCRIPT)
