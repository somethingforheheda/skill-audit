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
  category:'improve',title:'rule correction',confidence:'low',observation:'<script>unsafe</script>',recommendation:'clarify rule',
  skills:[],change_kind:'clarify_boundary',attribution:'skill',
  current_check:[{skill:'/repo/demo/SKILL.md',status:'still_applicable',sha256:'abc'}],
  supporting_sessions:[],counter_evidence:[],target:null,limitation:'bounded evidence',verification:'offline check',
  regression_case:{task:'task',expected:['observable outcome'],forbidden:['bad action'],verification:'compare results'},
  evidence:[{kind:'history',session:'codex:a',event_id:'a4',part:0,quote:'<img onerror=bad>'}]
}]}};
vm.runInContext('D=normalize(input);renderAnalysis();',c);
assert.equal(node('analysis-section').hidden,false);
const analysisHTML=node('c-analysis').innerHTML;
for(const text of ['补充适用边界','Skill内容','当前仍适用','回归案例草稿（尚未运行）','支持证据','反例证据'])assert(analysisHTML.includes(text));
assert(!analysisHTML.includes('<script>unsafe'));
assert(analysisHTML.includes('&lt;script&gt;'));
assert(!analysisHTML.includes('<img onerror=bad>'));
assert(analysisHTML.includes('&lt;img'));
assert(!analysisHTML.includes('校验未通过'));
c.input.analysis.findings[0].validation={status:'failed',errors:['evidence[0] 证据摘录不匹配 <b>x</b>']};
vm.runInContext('renderAnalysis()',c);
const failedHTML=node('c-analysis').innerHTML;
for(const text of ['校验未通过 1 条','校验未通过</span>','证据摘录不匹配','&lt;b&gt;x'])assert(failedHTML.includes(text),text);
c.input.analysis.findings[0].validation={status:'passed'};
vm.runInContext('renderAnalysis()',c);
assert(node('c-analysis').innerHTML.includes('证据已核验'));assert(!node('c-analysis').innerHTML.includes('校验未通过'));
c.input.analysis={overview:'legacy',status:'complete',findings:[{category:'retain',title:'legacy item',evidence:[]}]};
vm.runInContext('D=normalize(input);renderAnalysis();',c);
assert(node('c-analysis').innerHTML.includes('legacy item'));
delete c.input.analysis;
vm.runInContext('D=normalize(input);renderAnalysis();',c);
assert.equal(node('analysis-section').hidden,true);
assert.equal(node('c-analysis').innerHTML,'');
console.log('PASS: v2 analysis cards, legacy compatibility, absent analysis and escaped evidence');
