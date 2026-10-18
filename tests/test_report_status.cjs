// Run with node: validates the real template's view model and generated detail markup.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../history_report.html'),'utf8');
const script=html.split('<script>')[1].split('</script>')[0].split('\ninitChrome();')[0];
const nodes={};const node=id=>nodes[id]||(nodes[id]={textContent:'{}',innerHTML:'',children:[],appendChild(x){this.children.push(x)},querySelectorAll:()=>[]});
const c=vm.createContext({document:{getElementById:node,createElement:tag=>({tag,children:[],appendChild(x){this.children.push(x)},setAttribute(){}})},console});vm.runInContext(script,c);
const rows=[['build-skill','confirmed'],['ai-docs','unconfirmed'],['missing','failed']].map(([name,status])=>({name,status,skill:'/repo/'+name+'/SKILL.md',source:'codex',session_id:'s',timestamp:'2026-09-29T00:00:00Z'}));
c.input={extraction:{reads:rows,skills:rows.map(r=>({skill:r.skill,reads:1}))},sessions:[{source:'codex',session_id:'s'}]};
vm.runInContext('D=normalize(input);initState();renderReads();renderReads(true);',c);
assert.equal(vm.runInContext('D.reads.length',c),1);
assert.equal(vm.runInContext('D.skills.length',c),1);
assert.equal(vm.runInContext('D.skills[0].name',c),'build-skill');
assert.equal(vm.runInContext('D.sessions[0]._reads.length',c),1);
assert.equal(vm.runInContext('D.reviewReads.length',c),2);
assert(!node('c-reads').innerHTML.includes('ai-docs'));
assert(node('c-review').innerHTML.includes('ai-docs'));
if(process.argv[2]){
 c.input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
 vm.runInContext('D=normalize(input);initState();renderReads();renderReads(true);',c);
 assert.equal(vm.runInContext('D.reads.length',c),c.input.extraction.summary.confirmed_reads);
 assert.equal(vm.runInContext('D.skills.some(s=>s.name==="ai-docs")',c),false);
 assert(!node('c-reads').innerHTML.includes('ai-docs'));
 console.log('Actual report confirmed:',vm.runInContext('D.reads.length',c),'skills:',vm.runInContext('D.skills.length',c));
}
console.log('PASS: confirmed-only catalogue, session tracks, detail markup; separate review records');

c.input.extraction.current_skill_states={'/repo/build-skill/SKILL.md':{status:'missing',checked_at:'2026-09-29T00:00:00Z',reason:'missing'}};
vm.runInContext('D=normalize(input);initState();',c);
assert(vm.runInContext('skillNameHTML("/repo/build-skill/SKILL.md","build-skill")',c).includes('<del'));
assert.equal(vm.runInContext('skillStateBadge("/unknown/SKILL.md")',c),'');
console.log('PASS: missing-path badge and legacy report compatibility');
if(process.argv[2]){
 c.input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
 c.document.createElementNS=(_,tag)=>({tag,attrs:{},children:[],setAttribute(k,v){this.attrs[k]=v},appendChild(child){this.children.push(child)}});
 for(const id of ['sb-body','tl-body']){node(id).children=[];node(id).appendChild=function(child){this.children.push(child)};node(id).clientWidth=640;}
 vm.runInContext('D=normalize(input);initState();bindTip=function(){};tableToggle=function(){};renderSkillBar();renderTimeline();',c);
 const texts=el=>[el.textContent,...(el.children||[]).flatMap(texts)];
 const struck=el=>(el.attrs?.['text-decoration']==='line-through'?1:0)+(el.children||[]).reduce((n,x)=>n+struck(x),0);
 for(const id of ['sb-body','tl-body']){assert.equal(struck(node(id)),4);assert.equal(texts(node(id)).filter(t=>t==='当前不存在').length,0);}
 console.log('PASS: bar SVG and timeline SVG each contain four struck names and no missing labels');
}

c.rows=Array.from({length:25},(_,i)=>i);
vm.runInContext('S={};',c);
assert.equal(vm.runInContext('visibleRows(rows,"sessionsExpanded").length',c),10);
vm.runInContext('S.sessionsExpanded=true;',c);
assert.equal(vm.runInContext('visibleRows(rows,"sessionsExpanded").length',c),25);
assert.equal(vm.runInContext('visibleRows(rows,"readsExpanded").length',c),10);
assert.equal(vm.runInContext('visibleRows(rows.slice(0,3),"readsExpanded").length',c),3);
console.log('PASS: default ten rows, independent expansion and short lists');
if(process.argv[2]){
 vm.runInContext('initState();renderReads();',c);
 assert.equal((node('c-reads').innerHTML.match(/<td class="n">/g)||[]).length,10);
 node('c-reads').children.at(-1).children[0].onclick();
 assert.equal((node('c-reads').innerHTML.match(/<td class="n">/g)||[]).length,406);
 node('c-reads').children.at(-1).children[0].onclick();
 assert.equal((node('c-reads').innerHTML.match(/<td class="n">/g)||[]).length,10);
 console.log('PASS: real detail expand/collapse click handlers 10 -> 406 -> 10');
}

// New analysis cards remain readable with older artifacts and escape all evidence.
c.input={sessions:[],extraction:{reads:[]},analysis:{schema_version:'summary-v2',overview:'fixture',status:'partial',uncovered:['limited'],findings:[{
  category:'improve',title:'rule correction',confidence:'low',priority:'medium',observation:'<script>unsafe</script>',recommendation:'clarify rule',
  skills:[],change_kind:'clarify_boundary',attribution:'skill',
  current_check:[{skill:'/repo/demo/SKILL.md',status:'still_applicable',sha256:'abc'}],
  supporting_sessions:[],counter_evidence:[],target:null,limitation:'bounded evidence',verification:'offline check',
  regression_case:{task:'task',expected:['observable outcome'],forbidden:['bad action'],verification:'compare results'},
  evidence:[{kind:'history',session:'codex:a',event_id:'a4',part:0,quote:'<img onerror=bad>',_check:{ok:true,reason:null}}],
  validation:{checked:1,passed:1,status:'passed'}
}]}};
vm.runInContext('D=normalize(input);renderAnalysis();',c);
assert.equal(node('analysis-section').hidden,false);
const analysisHTML=node('c-analysis').innerHTML;
for(const text of ['补充适用边界','Skill内容','当前仍适用','回归案例草稿（尚未运行）','反例证据'])assert(analysisHTML.includes(text),text);
assert(!analysisHTML.includes('<script>unsafe'));
assert(analysisHTML.includes('&lt;script&gt;'));
assert(!analysisHTML.includes('<img onerror=bad>'));
assert(analysisHTML.includes('&lt;img'));
assert(analysisHTML.includes('校验通过'));
assert(!analysisHTML.includes('校验不通过'));
assert(analysisHTML.includes('问题依据 1/1 校验通过'));
c.input.analysis.findings[0].evidence[0]._check={ok:false,reason:'证据摘录不匹配 <b>x</b>'};
c.input.analysis.findings[0].validation={checked:1,passed:0,status:'failed'};
vm.runInContext('renderAnalysis()',c);
const failedHTML=node('c-analysis').innerHTML;
for(const text of ['校验不通过 1 条结论','ev-badge bad','✕ 校验不通过','证据摘录不匹配','&lt;b&gt;x','问题依据 0/1 校验通过'])assert(failedHTML.includes(text),text);
c.input.analysis.findings[0].evidence[0]._check={ok:true,reason:null};
c.input.analysis.findings[0].validation={checked:1,passed:1,status:'passed'};
vm.runInContext('renderAnalysis()',c);
assert(node('c-analysis').innerHTML.includes('ev-badge ok'));assert(!node('c-analysis').innerHTML.includes('校验不通过'));
// Severity colouring is derived from category+priority, highest risk first.
c.input.analysis.findings[0].category='retire';c.input.analysis.findings[0].priority='high';
vm.runInContext('renderAnalysis()',c);
assert(node('c-analysis').innerHTML.includes('var(--critical)'));
c.input.analysis.findings[0].category='retain';c.input.analysis.findings[0].priority='low';
vm.runInContext('renderAnalysis()',c);
assert(node('c-analysis').innerHTML.includes('var(--good)'));
c.input.analysis={overview:'legacy',status:'complete',findings:[{category:'retain',title:'legacy item',priority:'low',confidence:'low',observation:'o',recommendation:'r',evidence:[]}]};
vm.runInContext('D=normalize(input);renderAnalysis();',c);
assert(node('c-analysis').innerHTML.includes('legacy item'));
delete c.input.analysis;
vm.runInContext('D=normalize(input);renderAnalysis();',c);
assert.equal(node('analysis-section').hidden,true);
assert.equal(node('c-analysis').innerHTML,'');
console.log('PASS: v2 analysis cards, legacy compatibility, absent analysis and escaped evidence');

// Skill-use review: every related session is listed and transcripts render windowed with load buttons.
const host=node('c-analysis'),attrs={};
Object.assign(host,{getAttribute:k=>attrs[k],setAttribute:(k,v)=>{attrs[k]=v},querySelector:()=>null});
const head={textContent:''},hint={textContent:''};
node('analysis-section').querySelector=()=>({querySelector:sel=>sel==='h2'?head:hint});
const txRows=[];for(let i=0;i<40;i++)txRows.push([i===3?'user':i===4?'call':'assistant','m'+i+(i===20?' <b>x</b>':''),i,'2026-10-01T00:00:00Z',0,'',0]);
c.input.transcripts={'codex:s':txRows};
c.input.analysis={kind:'skill_review',overview:'ov',coverage:{sessions:1,episodes:1,judged:1},backend:{model:'m'},
 findings:[{title:'F <i>',type:'skill_gap',severity:'high',confidence:'high',skill:'x',status:'open',observation:'o',root_cause:'r',recommendation:'rec',
   patch:{after_line:3,text:'+rule'},occurrences:[{episode_id:'e1',session:'codex:s',event:20,user_signal:'不对'}]}],
 candidates:[{name:'new-skill',title:'t',action:'new',confidence:'medium',steps:['a'],pitfalls:[],evidence:[{session:'codex:s',request:'m3',found:true},{session:'codex:gone',found:false}]}],
 preferences:[],episodes:[{id:'e1',session:'codex:s',skills:[{name:'x'}],request:'m3',start_event:3,end_event:30,read_events:[4],
   signals:{score:3,followups:2,corrections:[],interrupts:0,failed_tools:0},judgment:{issues:[{event:20,attribution:'skill_gap',what_happened:'w'}]}}]};
vm.runInContext('D=normalize(input);renderAnalysis();',c);
let sr=host.innerHTML;
for(const text of ['F &lt;i&gt;','1 次 · 1 个会话','第 3 行之后','new-skill','部分引用的会话不在本次报告中','逐次 Skill 使用（1）','不对'])assert(sr.includes(text),text);
assert.equal(head.textContent,'AI Skill 使用复盘');
const views=vm.runInContext('SR_VIEWS',c),ids=Object.keys(views);
assert.equal(ids.length,3);  // finding session, candidate session, episode row
const box={innerHTML:'',querySelectorAll:()=>[]};
const findingView=ids.map(k=>views[k]).find(v=>Object.values(v.marks).some(m=>m.label.startsWith('★ 用户：')));
vm.runInContext('srRender',c)(box,findingView);
for(const text of ['触发这次 Skill 使用的请求','读取 Skill：x','★ 用户：不对','加载上面10条','加载下面10条','m20 &lt;b&gt;x'])assert(box.innerHTML.includes(text),text);
assert(!box.innerHTML.includes('>m39<'));
vm.runInContext('renderAnalysis()',c);
assert.equal(host.innerHTML,sr);  // re-render after filters keeps opened transcripts
console.log('PASS: skill review cards list sessions and render windowed transcripts');
