from pages import OPTS_HTML, shell

SETTINGS_SCRIPT = '''
const FIELDS=['key','model','sub','effort'];
async function init(){
  try{
    const c=await (await fetch('/api/config')).json();
    gid('o-models').innerHTML=c.models.map(m=>'<option value="'+h(m)+'">').join('');
    if(c.default_model)gid('o-model').placeholder='默认 '+c.default_model;
    gid('o-keyreq').textContent=c.key_required?'（必填）':'（选填，不填则用服务器自己的）';
  }catch(e){}
  for(const k of FIELDS)gid('o-'+k).value=localStorage.getItem('sa-'+k)||'';
}
function say(text,bad){const m=gid('msg');m.textContent=text;m.className=bad?'err small':'muted small'}
gid('save').onclick=()=>{
  const key=gid('o-key').value.trim();
  if(key&&(/[^\\x21-\\x7e]/.test(key)||key.length<8||key.length>400)){say('API Key 格式不对：长度 8–400，不能含空格或中文。',true);return}
  for(const k of ['model','sub'])if(!MODEL_OK(gid('o-'+k).value.trim())){say('模型名格式不对：只能含字母、数字、空格和 . _ : / [ ] -，并以字母或数字开头。',true);return}
  for(const k of FIELDS){const v=gid('o-'+k).value.trim();if(v)localStorage.setItem('sa-'+k,v);else localStorage.removeItem('sa-'+k)}
  say('已保存在这个浏览器里。回到「我的会话」选目录就能直接开始分析。',false);
};
gid('clear').onclick=()=>{
  for(const k of FIELDS){localStorage.removeItem('sa-'+k);gid('o-'+k).value=''}
  say('已清除。',false);
};
init();
'''


def settings_page(user):
    body = ('<div class="head"><div class="t"><h1>分析设置</h1><p class="muted">填一次就行。之后在「我的会话」里选好目录，直接点开始。</p></div></div>'
            '<div class="card" style="max-width:560px"><h2>API Key 和模型</h2>' + OPTS_HTML +
            '<div class="actions"><button id="save" type="button">保存</button>'
            '<button id="clear" class="ghost" type="button">清除已保存的</button>'
            '<span id="msg" class="muted small" role="status" aria-live="polite"></span></div></div>')
    return shell('分析设置', body, user, SETTINGS_SCRIPT, 'settings')
