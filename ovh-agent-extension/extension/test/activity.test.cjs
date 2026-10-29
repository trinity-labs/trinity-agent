const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const {JSDOM} = require('jsdom');

function browser(env={}) {
  const root = path.resolve(__dirname, '..');
  const nativeRequire = createRequire(path.join(root, 'extension.js'));
  const vscode = {workspace:{workspaceFolders:[{uri:{fsPath:root}}]}, Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)}, env:{language:'fr'}};
  const scope = {require:name=>name==='vscode'?vscode:nativeRequire(name), module:{exports:{}}, process:{env:{...process.env,...env,OVH_AGENT_LANG:'fr'}}, console, setTimeout, clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider;',scope);
  const provider = new scope.module.exports.Provider(root, {});
  const dom = new JSDOM(provider.getHtml({asWebviewUri:x=>x,cspSource:'local'}), {runScripts:'outside-only',url:'https://test.invalid'});
  const w = dom.window, intervals = new Map(), sent = [];
  let now = Date.UTC(2026,9,17,15,40), timerId = 0;
  const setInterval=w.setInterval.bind(w), clearInterval=w.clearInterval.bind(w);
  w.Date.now = () => now;
  w.setInterval = (fn, delay) => {
    if (delay!==1000) return setInterval(fn,delay);
    intervals.set(--timerId,fn); return timerId;
  };
  w.clearInterval = id => { if (!intervals.delete(id)) clearInterval(id); };
  w.acquireVsCodeApi=()=>({getState:()=>undefined,setState:()=>{},postMessage:message=>sent.push(message)});
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.OVH_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
  for (const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','chat.js']) w.eval(fs.readFileSync(path.join(root,'media',name),'utf8'));
  return {dom, document:w.document, intervals, sent,
    emit:data=>w.dispatchEvent(new w.MessageEvent('message',{data})),
    advance:ms=>{now+=ms;for (const tick of intervals.values()) tick();}};
}

test('progress explanations stay visible through tools, streaming and completion', () => {
  const {dom,document,emit} = browser();
  try {
    const explanation='Je vérifie le port actif.\n<img src=x onerror=alert(1)>';
    const plan={type:'plan_updated',steps:['Inspecter','Vérifier'],current_step:0,explanation};
    emit({type:'status',text:'Working'});
    emit(plan);
    emit(plan);
    assert.equal(document.querySelectorAll('.progress-update').length,1);
    assert.equal(document.querySelector('.progress-update').textContent,explanation);
    assert.equal(document.querySelector('.progress-update img'),null);
    assert.equal(document.querySelector('.activity').open,false);
    emit({type:'tool_start',id:'read',tool:'read',title:'Lecture',detail:'client.conf'});
    emit({type:'delta',html:'<p>Résultat en cours.</p>'});
    emit({...plan,current_step:1,explanation:'Le port diffère de celui du service. Je vérifie la correction.'});
    emit({type:'response',html:'<p>Connexion vérifiée.</p>'});
    assert.equal(document.querySelectorAll('.progress-update').length,2);
    assert.match(document.querySelector('.content').textContent,/Connexion vérifiée/);
  } finally {dom.window.close();}
});

test('progress explanations are restored from archived plan events', () => {
  const {dom,document,emit} = browser();
  try {
    emit({type:'archive_loaded',id:'progress',messages:[
      {type:'user',text:'Vérifie la connexion.'},
      {type:'plan_updated',steps:['Vérifier'],current_step:0,explanation:'Je compare les ports du client et du service.'},
      {type:'response',html:'<p>Vérifié.</p>'},
    ]});
    assert.equal(document.querySelectorAll('.progress-update').length,1);
    assert.match(document.querySelector('.progress-update').textContent,/compare les ports/);
  } finally {dom.window.close();}
});

test('long command progress stays visible without accumulating notices or surviving completion', () => {
  const {dom,document,emit} = browser();
  try {
    emit({type:'status',text:'Working'});
    emit({type:'tool_start',id:'check',tool:'command',title:'Vérification',detail:'tests'});
    emit({type:'tool_progress',id:'check',elapsed_seconds:40,commentary:false});
    assert.equal(document.querySelector('.progress-waiting'),null);
    emit({type:'tool_progress',id:'check',elapsed_seconds:40,commentary:true});
    emit({type:'tool_progress',id:'check',elapsed_seconds:80,commentary:true});
    assert.equal(document.querySelectorAll('.progress-waiting').length,1);
    assert.match(document.querySelector('.progress-waiting').textContent,/80 s/);
    emit({type:'tool_end',id:'check',success:true,detail:'Vérifié.'});
    assert.equal(document.querySelector('.progress-waiting'),null);
    emit({type:'tool_progress',id:'check',elapsed_seconds:90,commentary:true});
    assert.equal(document.querySelector('.progress-waiting'),null);
  } finally {dom.window.close();}
});

test('context, routing and usage stay in the journal without replacing the current tool', () => {
  const {dom,document,emit,advance} = browser();
  try {
    const summary=()=>document.querySelector('.activity-summary-text').textContent;
    const context={type:'context_compacted',estimated_tokens:27648,budget_tokens:65536};
    emit({type:'status',text:'Working'});
    emit(context);
    assert.equal(summary(),'Réflexion en cours…');
    emit({type:'plan_updated',steps:['Examiner la comptabilité','Tester les flux'],current_step:0});
    emit(context);
    assert.equal(summary(),'Examiner la comptabilité');
    emit({type:'tool_start',id:'read',tool:'read',title:'Lecture du fichier',detail:'ovh_budget.py · lignes 1–80'});
    const action=summary();
    emit({type:'plan_updated',steps:['Lire le code','Tester les flux'],current_step:1});
    emit({type:'model_active',model:'Qwen3.5-9B'});
    emit({type:'model_routed',model:'Qwen3.5-9B',reason:'Coût'});
    emit({type:'request_prepared',sources:[],preparation_ms:2});
    emit({...context,compacted:true});
    emit({type:'usage',model:'Qwen3.5-9B',input_tokens:40,output_tokens:10,cost_eur:0.0001});
    advance(3000);
    assert.equal(summary(),action);
    emit({type:'tool_end',id:'read',success:true,detail:'80 lignes lues'});
    emit(context);
    emit({type:'delta',html:'<p>Je vérifie maintenant les budgets.</p>'});
    assert.equal(summary(),action);
    assert.match(document.querySelector('.activity-list').textContent,/Budget de contexte.*27648.*65536/s);
    emit({type:'tool_start',id:'test',tool:'command',title:'Exécution des tests',detail:'python scripts/test-budget.py'});
    assert.match(summary(),/^Exécution des tests · python/);
    emit({type:'tool_end',id:'read',success:true,detail:'Résultat tardif'});
    assert.match(summary(),/^Exécution des tests/);
  } finally {dom.window.close();}
});

test('elapsed time ticks visibly, freezes on completion and survives archive snapshots', () => {
  const {dom,document,emit,advance,intervals} = browser();
  try {
    emit({type:'status',text:'Working'});
    const elapsed=document.querySelector('.activity-elapsed');
    assert(elapsed,'the activity summary has a visible elapsed counter');
    assert.equal(document.querySelector('.message.assistant > .content').nextElementSibling,document.querySelector('.activity'));
    assert.equal(elapsed.textContent,'0 s');
    advance(7000);
    assert.equal(elapsed.textContent,'7 s');
    advance(58000);
    assert.equal(elapsed.textContent,'1 min 5 s');
    document.querySelector('.activity').open=true;
    emit({type:'response',html:'<p>Tests réussis.</p>'});
    assert.equal(document.querySelector('.message.assistant > .content').nextElementSibling,document.querySelector('.activity'));
    assert.match(document.querySelector('.activity-summary-text').textContent,/^Terminé · /);
    assert.equal(document.querySelector('.activity').open,true);
    assert.equal(intervals.size,0);
    advance(60000);
    assert.equal(elapsed.textContent,'1 min 5 s');
    // Also upgrade an archive saved with the old journal-before-text layout.
    const content=document.querySelector('.message.assistant > .content');
    content.before(document.querySelector('.activity'));
    const html=document.querySelector('#messages').innerHTML;
    emit({type:'snapshot_loaded',id:'archive',html});
    assert.equal(document.querySelector('.activity-elapsed').textContent,'1 min 5 s');
    assert.equal(intervals.size,0);
    assert.equal(document.querySelector('.activity').open,false);
    assert.equal(document.querySelector('.message.assistant > .content').nextElementSibling,document.querySelector('.activity'));
    assert.equal(document.querySelector('.message.assistant > .content').textContent,'Tests réussis.');
  } finally {dom.window.close();}
});

test('each concurrent action ticks independently and freezes at its recorded duration, including failures',()=>{
 const {dom,document,emit,advance,intervals}=browser();
 try {
  const row=id=>[...document.querySelectorAll('.activity-row')].find(item=>item.dataset.activityId===id);
  const duration=id=>row(id).querySelector('.activity-duration').textContent;
  emit({type:'tool_start',id:'a',tool:'read',title:'Lecture A'});advance(500);
  emit({type:'tool_start',id:'b',tool:'command',title:'Commande B'});advance(1500);
  assert.equal(duration('a'),'2 s');assert.equal(duration('b'),'1,5 s');assert.equal(intervals.size,1);
  emit({type:'tool_end',id:'a',success:true,detail:'100 lignes',duration_ms:9});
  assert.equal(duration('a'),'9 ms');advance(1000);assert.equal(duration('a'),'9 ms');assert.equal(duration('b'),'2,5 s');
  emit({type:'tool_end',id:'b',success:false,detail:'Échec',duration_ms:2030});
  assert.equal(duration('b'),'2,03 s');assert(row('b').classList.contains('failure'));
  emit({type:'tool_start',id:'c',tool:'read',title:'Lecture interrompue'});advance(2000);
  emit({type:'idle'});assert.equal(duration('c'),'≥ 2 s');assert(row('c').classList.contains('stopped'));
  assert.equal(intervals.size,0);advance(60000);assert.equal(duration('c'),'≥ 2 s');
  // A duplicated legacy completion must not count all the time since the first completion.
  emit({type:'tool_end',id:'a',success:true,detail:'100 lignes'});assert.equal(duration('a'),'9 ms');
 } finally {dom.window.close();}
});

test('telemetry and model execution show their own measured durations and survive sanitized snapshots',()=>{
 const {dom,document,emit,advance}=browser();
 try {
  emit({type:'request_prepared',sources:[],preparation_ms:9});
  emit({type:'model_active',model:'Test'});advance(1500);
  emit({type:'model_routed',model:'Test',duration_ms:12});
  emit({type:'context_compacted',estimated_tokens:100,budget_tokens:1000,duration_ms:25});
  emit({type:'verification_check',id:'check',check:'Unit tests',passed:true,duration_ms:1234});
  emit({type:'usage',purpose:'compaction',model:'Test',duration_ms:42});
  const durations=()=>[...document.querySelectorAll('.activity-duration')].map(el=>el.textContent);
  assert.deepEqual(durations(),['9 ms','1,5 s','12 ms','25 ms','1,23 s','42 ms']);
  advance(1000);assert.equal(durations()[1],'2,5 s');
  emit({type:'usage',model:'Test',duration_ms:2300});emit({type:'response',html:'<p>Terminé.</p>'});
  const before=durations();assert.equal(before[1],'2,3 s');assert.equal(before.at(-1),'2,3 s');
  const html=document.getElementById('messages').innerHTML;
  emit({type:'snapshot_loaded',id:'saved',html});assert.deepEqual(durations(),before);
  advance(100000);assert.deepEqual(durations(),before);
 } finally {dom.window.close();}
});

test('legacy archives recover paired tool durations without inventing times for unmeasured events',()=>{
 const {dom,document,emit}=browser();
 try {
  const row=title=>`<div class="activity-row success"><span class="activity-icon"></span><div><strong>${title}</strong><code>Détail</code></div></div>`;
  const html='<article class="message assistant done"><div class="content">Réponse</div><details class="activity"><summary>Terminé</summary><div class="activity-list">'+['Contexte préparé','Lecture du fichier','Lecture du fichier','Modèle exécuté'].map(row).join('')+'</div></details></article>';
  const timeline=[{type:'request_prepared',timestamp:'2026-10-17T19:24:00Z',preparation_ms:9},
   {type:'tool_start',id:'first',title:'Lecture du fichier',timestamp:'2026-10-17T19:24:00Z'},
   {type:'tool_start',id:'second',title:'Lecture du fichier',timestamp:'2026-10-17T19:24:01Z'},
   {type:'tool_end',id:'second',timestamp:'2026-10-17T19:24:01Z'},
   {type:'tool_end',id:'first',timestamp:'2026-10-17T19:24:03Z',duration_ms:3123}];
  emit({type:'snapshot_loaded',id:'old',html,timeline});
  assert.deepEqual([...document.querySelectorAll('.activity-duration')].map(el=>el.textContent),['9 ms','3,12 s','< 1 s','Durée non enregistrée']);
  assert.equal(document.querySelectorAll('.activity-row .message-time')[2].dateTime,'2026-10-17T19:24:01.000Z');
 } finally {dom.window.close();}
});

test('an in-flight snapshot recovers the final model duration from the timeline across timestamp formats',()=>{
 const {dom,document,emit,advance}=browser();
 try {
  const start={type:'model_active',model:'Test',timestamp:'2026-10-17T19:24:00.123+00:00'};
  emit(start);advance(1000);
  const html=document.getElementById('messages').innerHTML;
  emit({type:'snapshot_loaded',id:'saved',html,timeline:[start,
   {type:'user',steering:true,timestamp:'2026-10-17T19:24:01.000+00:00'},
   {type:'usage',model:'Test',duration_ms:2750,timestamp:'2026-10-17T19:24:03.000+00:00'}]});
  assert.equal(document.querySelector('.activity-duration').textContent,'2,75 s');
  advance(100000);assert.equal(document.querySelector('.activity-duration').textContent,'2,75 s');
 } finally {dom.window.close();}
});

test('stopping, failure and a new conversation stop the timer without reporting success', () => {
  for (const [event,label] of [[{type:'idle'},'Interrompu'],[{type:'error',text:'Test error'},'Échec']]) {
    const {dom,document,emit,advance,intervals}=browser();
    try {
      emit({type:'status',text:'Working'});
      advance(4000);
      emit(event);
      assert.match(document.querySelector('.activity-summary-text').textContent,new RegExp('^'+label+' · '));
      assert.equal(document.querySelector('.activity-elapsed').textContent,'4 s');
      assert.equal(intervals.size,0);
      emit({type:'status',text:'Next task'});
      assert.equal(document.querySelectorAll('.activity-elapsed')[1].textContent,'0 s');
      assert.equal(intervals.size,1);
      document.querySelector('#clear').click();
      assert.equal(intervals.size,0);
      assert.equal(document.querySelector('.activity-elapsed'),null);
    } finally {dom.window.close();}
  }
});

test('verification shows actual failed checks and restores the evidence from an archive', () => {
  const {dom,document,emit} = browser();
  try {
    emit({type:'status',text:'Working'});
    emit({type:'tool_start',id:'edit',tool:'edit',title:'Modification du fichier',detail:'app.py'});
    const action = document.querySelector('.activity-summary-text').textContent;
    emit({type:'verification_status',status:'pending'});
    assert.match(document.querySelector('.verification-status').textContent,/attente/);
    const check = {type:'verification_check',id:'receipt-1',check:'Behavior',phase:'after',argv:['python','check.py'],exit_code:1,passed:false,output:'AssertionError: VALUE=3',duration_ms:15};
    emit(check);
    emit({type:'verification_status',status:'failed',problems:['Behavior failed']});
    assert.equal(document.querySelector('.activity-summary-text').textContent,action);
    assert.match(document.querySelector('.activity-row.failure').textContent,/exit=1.*AssertionError/s);
    assert.match(document.querySelector('.activity-row.failure').textContent,/Tests décomptés : indisponible/);
    assert.equal(document.querySelector('.verification-status').textContent,'Validation refusée');
    assert.equal(document.querySelector('.verification-status').title,'Behavior failed');
    emit({type:'verification_status',status:'rolled_back',restored:['app.py']});
    emit({type:'error',text:'Unverified changes rolled back'});
    assert.match(document.querySelector('.verification-status').textContent,/restaurées/);
    const html = document.querySelector('#messages').innerHTML;
    emit({type:'snapshot_loaded',id:'archive',html});
    assert.match(document.querySelector('.verification-status').textContent,/restaurées/);
    assert(document.querySelector('.verification-rolled_back'));
    emit({type:'archive_loaded',id:'raw',messages:[{...check,reported_tests:19},{type:'verification_status',status:'passed'}]});
    assert.match(document.querySelector('#messages').textContent,/exit=1.*AssertionError/s);
    assert.match(document.querySelector('#messages').textContent,/Tests décomptés : 19/);
    assert.equal(document.querySelector('.verification-status').textContent,'Contrôles configurés réussis');
    assert.match(document.querySelector('.verification-status').title,/périmètre/);
  } finally {dom.window.close();}
});

test('syntax and content fallback discloses missing functional coverage in live and archived verification',()=>{
  const {dom,document,emit}=browser();
  try {
    const verification={type:'verification_status',status:'passed',scope:'syntax_and_content',functional_coverage_missing:['app.js']};
    emit(verification);
    let badge=document.querySelector('.verification-status');
    assert.match(badge.textContent,/Contrôles limités.*couverture fonctionnelle incomplète/);
    assert.doesNotMatch(badge.textContent,/Contrôles configurés réussis/);
    assert.match(badge.title,/syntaxe disponible.*ne valide pas leur comportement/);
    assert.match(badge.title,/Sans contrôle fonctionnel : app\.js/);
    emit({type:'archive_loaded',id:'mixed',messages:[{...verification,scope:'configured_local_checks'}]});
    badge=document.querySelector('.verification-status');
    assert.match(badge.textContent,/couverture fonctionnelle incomplète/);
    assert.match(badge.title,/app\.js/);
    emit({type:'verification_status',status:'passed',scope:'syntax_and_content'});
    assert.match([...document.querySelectorAll('.verification-status')].at(-1).textContent,/couverture fonctionnelle incomplète/);
  } finally {dom.window.close();}
});

test('audit without a configured suite displays neutral local observations in live and archived views',()=>{
  const {dom,document,emit}=browser();
  try {
    const verification={type:'verification_status',status:'not_configured',scope:'local_observations_only',checks:[]};
    const check=()=>{
      const badge=document.querySelector('.verification-status');
      assert.equal(badge.textContent,'Observations locales · aucun test fonctionnel exécuté');
      assert.match(badge.title,/ne valident pas le comportement fonctionnel/);
      assert(badge.classList.contains('verification-not_configured'));
      assert(!badge.classList.contains('verification-failed'));
      assert(!badge.classList.contains('verification-passed'));
    };
    emit(verification);check();
    emit({type:'archive_loaded',id:'limited-audit',messages:[verification]});check();
  } finally {dom.window.close();}
});

test('activity labels use icons without emojis while intermediate prose retains them', () => {
  const {dom,document,emit} = browser();
  try {
    emit({type:'ready',emoji_mode:1});
    for (const [tool,icon] of [['web','search'],['read','read'],['write','edit'],['command','terminal']]) {
      emit({type:'tool_start',id:tool,tool,title:'Action — test 🔎'});
      assert(document.querySelector('.run-indicator .icon-'+icon),tool);
      assert.equal(document.querySelector('.activity-summary-text').textContent,'Action - test');
      emit({type:'tool_end',id:tool,success:true});
      assert(document.querySelector('.activity-row:last-child .icon-'+icon));
    }
    for (const mode of [0,1,2,3]) {
      emit({type:'ready',emoji_mode:mode});
      emit({type:'tool_start',id:'none',tool:'read',title:'✅ Lecture ➡️ 🔎'});
      assert.equal(document.querySelector('.activity-summary-text').textContent,'Lecture');
      assert.equal(document.querySelector('.activity-row:last-child strong').textContent,'Lecture');
    }
    emit({type:'ready',emoji_mode:1});
    emit({type:'delta',html:'<p>➡️ Je vérifie les contrôles existants.</p>'});
    assert.match(document.querySelector('.markdown').textContent,/➡️ Je vérifie/);
    const saved=document.querySelector('.message.assistant').outerHTML.replace('Lecture</span>', '➡️ Lecture</span>');
    dom.window.upgradeSnapshotPresentation(document.querySelector('#messages'));
    const restored=document.createElement('div');
    restored.innerHTML=saved;
    dom.window.upgradeSnapshotPresentation(restored);
    assert.equal(restored.querySelector('.activity-summary-text').textContent,'Lecture');
    assert.match(restored.querySelector('.markdown').textContent,/➡️ Je vérifie/);
    emit({type:'response',html:'<p>Fini.</p>'});
    assert(document.querySelector('.run-indicator .icon-check'));
    assert(!/[✅➡]/u.test(document.querySelector('.activity-summary-text').textContent));
    const finished=document.querySelector('.message.assistant');
    // Old snapshots used the final usage row's dollar icon as the completed summary.
    const legacy=dom.window.AgentIcons.svg('cost');
    finished.querySelector('.run-indicator').innerHTML=legacy;
    finished.querySelector('.activity-row:last-child .activity-icon').innerHTML=legacy;
    dom.window.upgradeSnapshotPresentation(finished.parentElement);
    assert(finished.querySelector('.run-indicator .icon-check'));
    assert.equal(finished.querySelector('.icon-cost'),null);
    emit({type:'status',text:'Working'});
    emit({type:'usage',model:'Qwen3.5-9B',input_tokens:10,output_tokens:1,cost_eur:0.01});
    assert.equal(document.querySelector('.icon-cost'),null);
  } finally {dom.window.close();}
});

test('legacy journal titles lose their emojis while action icons and literal evidence survive', () => {
  const {dom,document,emit}=browser();
  try {
    const titles=['Contexte préparé','Modèle exécuté','Budget de contexte','Lecture du fichier'];
    const rows=titles.map(title=>`<div class="activity-row"><span class="activity-icon">${dom.window.AgentIcons.svg('read')}</span><div><strong>➡️ ${title}</strong><code>printf '✅ ➡️'</code></div></div>`).join('');
    emit({type:'snapshot_loaded',id:'legacy',html:`<article class="message assistant"><div class="content markdown"><p>➡️ Recommandation conservée.</p></div><details class="activity"><summary><span class="activity-summary-text">✅ Terminé</span></summary>${rows}</details></article>`});
    assert.deepEqual([...document.querySelectorAll('.activity-row strong')].map(node=>node.textContent),titles);
    assert.equal(document.querySelectorAll('.activity-icon .icon-read').length,4);
    assert.equal(document.querySelector('.activity-row code').textContent,"printf '✅ ➡️'");
    assert.match(document.querySelector('.markdown').textContent,/➡️ Recommandation/);
  } finally {dom.window.close();}
});

test('history nests conversations in project folders and routes cross-project opens explicitly',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const a={id:'a'.repeat(20),name:'Current',root:'/project/a',active:true,items:[{id:'same-id',title:'Current conversation',current:true}]};
  const b={id:'b'.repeat(20),name:'Other <img src=x>',root:'/project/b',active:false,items:[{id:'same-id',title:'Other conversation',current:true}]};
  emit({type:'archives',items:[],projects:[a,b]});
  let groups=document.querySelectorAll('.archive-project');assert.equal(groups.length,2);assert.equal(groups[0].open,false);
  assert.equal(groups[0].querySelectorAll('.archive-item').length,1);assert.equal(groups[1].querySelectorAll('.archive-item.current').length,0);
  assert.equal(groups[1].querySelectorAll('img,.archive-resume').length,0);
  assert.equal(groups[1].querySelectorAll('.archive-delete').length,1);
  groups[0].querySelector('.project-caret').click();assert.equal(groups[0].open,true);
  assert.equal(groups[0].querySelector('.project-caret').getAttribute('aria-expanded'),'true');
  emit({type:'archives',items:[],projects:[a,b]});groups=document.querySelectorAll('.archive-project');assert.equal(groups[0].open,true);
  groups[1].querySelector('.archive-open').click();assert.equal(sent.at(-1).type,'openProject');assert.equal(sent.at(-1).project,b.id);assert.equal(sent.at(-1).conversation,'same-id');
  assert.equal(document.querySelector('.project-folder-open'),null);
  const sentBefore=sent.length;groups[1].querySelector('.project-caret').click();assert.equal(groups[1].open,true);assert.equal(sent.length,sentBefore+1);
  assert.equal(sent.at(-1).type,'openProject');assert.equal(sent.at(-1).project,b.id);assert.equal(sent.at(-1).conversation,undefined);
  emit({type:'project_selected',project:b.id});assert(groups[1].classList.contains('selected'));assert(!groups[0].classList.contains('selected'));
  emit({type:'archive_loaded',id:'same-id',messages:[]});
  groups[0].querySelector('.archive-open').click();assert.equal(sent.at(-1).type,'loadArchive');assert.equal(sent.at(-1).id,'same-id');
  emit({type:'archives',items:[],projects:[{...a,items:[]}]});assert.equal(document.querySelector('.archive-project .archive-empty').textContent,'Aucune conversation associée à ce projet.');
 } finally {dom.window.close();}
});

test('folder plus follows the pencil and creates a conversation directly in that folder',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const project={id:'a'.repeat(20),name:'Target',root:'/project/a',active:true,items:[]};
  emit({type:'archives',items:[],projects:[project]});
  const group=document.querySelector('.archive-project');
  const controls=[...group.querySelectorAll(':scope > summary > button')];
  assert.equal(controls.indexOf(group.querySelector('.project-add')),controls.indexOf(group.querySelector('.project-edit'))+1);
  group.querySelector('.project-add').click();
  assert.equal(sent.at(-1).type,'newProjectConversation');assert.equal(sent.at(-1).project,project.id);
 } finally {dom.window.close();}
});

test('history keeps ungrouped conversations, routes assignments by source and edits project appearance',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const source='a'.repeat(20),target='b'.repeat(20);
  const project={id:target,name:'Ma sélection',root:'/project/b',color:'#cc3366',active:false,
   documents:[{name:'README.md',path:'/project/b/README.md',scope:'root'}],
   items:[{id:'selected',title:'Choisie',archive_project:source,archive_active:true,resumable:true}]};
  const loose={id:'loose',title:'Hors projet',archive_project:source,archive_active:true};
  emit({type:'archives',items:[loose],projects:[project]});
  const group=document.querySelector('.archive-project');
  const outside=document.querySelector('#archive-list > .archive-ungrouped');
  assert.equal(outside.querySelectorAll('.archive-item').length,1);
  assert.equal(outside.querySelector('.archive-section-title').textContent,'Conversations hors projet');
  assert.equal(group.nextElementSibling,outside);
  assert.equal(outside.querySelector('.archive-item').dataset.archiveId,'loose');
  assert.equal(group.querySelectorAll('.archive-item').length,1);
  assert.equal(group.style.getPropertyValue('--project-color'),'#cc3366');
  assert(group.querySelector('.project-folder-icon .icon-folder'));
  assert(group.querySelector('.archive-resume'));assert(group.querySelector('.archive-delete'));
  group.querySelector('.archive-classify').click();
  assert.equal(sent.at(-1).type,'assignProject');assert.equal(sent.at(-1).source,source);assert.equal(sent.at(-1).conversation,'selected');
  group.querySelector('.project-document').click();assert.equal(sent.at(-1).type,'openProjectDocument');assert.equal(sent.at(-1).name,'README.md');
  group.querySelector('.project-edit').click();
  assert.equal(group.open,true);const editor=group.querySelector('.project-editor');assert.equal(editor.hidden,false);
  editor.querySelector('input[type=text]').value='Nouveau titre';editor.querySelector('input[type=color]').value='#123456';
  editor.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  assert.equal(sent.at(-1).type,'updateProject');assert.equal(sent.at(-1).project,target);assert.equal(sent.at(-1).name,'Nouveau titre');assert.equal(sent.at(-1).color,'#123456');
  emit({type:'archive_delete_pending',id:'selected'});assert(group.querySelector('.archive-item').classList.contains('deleting'));
  emit({type:'archive_delete_failed',id:'selected'});assert(!group.querySelector('.archive-item').classList.contains('deleting'));
 } finally {dom.window.close();}
});

test('Markdown lists start collapsed, keep explicit expansion across refreshes and have no active-project badge',()=>{
 const {dom,document,emit}=browser();
 try {
  const project={id:'a'.repeat(20),name:'Folder',root:'/project',active:true,items:[],documents:[{name:'README.md',path:'/project/README.md',scope:'root'}]};
  emit({type:'archives',items:[],projects:[project]});
  const group=document.querySelector('.archive-project');group.querySelector('.project-caret').click();
  const documents=group.querySelector('.project-documents');
  assert.equal(group.open,true);assert.equal(documents.tagName,'DETAILS');assert.equal(documents.open,false);
  assert.equal(document.querySelector('.project-folder-active'),null);
  documents.querySelector('summary').click();assert.equal(documents.open,true);
  emit({type:'archives',items:[],projects:[{...project,color:'#123456'}]});
  assert.equal(document.querySelector('.project-documents').open,true);
  document.querySelector('.project-documents > summary').click();
  emit({type:'archives',items:[],projects:[project]});assert.equal(document.querySelector('.project-documents').open,false);
 } finally {dom.window.close();}
});

test('new conversation completes while startup events are filtered, without a project label above the header',()=>{
 const {dom,document,emit}=browser();
 try {
  emit({type:'ready',workspace:'/home/coder/old-project',conversation:'old'});
  assert.equal(document.querySelector('.project-context'),null);
  assert.equal(document.body.firstElementChild.tagName,'HEADER');
  document.getElementById('clear').click();
  emit({type:'ready',workspace:'/home/coder',conversation:'previous-root-conversation'});
  assert.equal(document.getElementById('clear').disabled,true);
  emit({type:'cleared',workspace:'/home/coder',conversation:'fresh'});
  assert.equal(document.querySelector('.project-context'),null);
  assert.equal(document.getElementById('clear').disabled,false);
 } finally {dom.window.close();}
});

test('project color picker matches the primary or custom icon color, with explicit conversation counts',()=>{
 for(const [configured,expected] of [['#000E9C','#000e9c'],['#168c57','#168c57'],['invalid','#3e3aab']]) {
  const {dom,document,emit}=browser({OVH_AGENT_PRIMARY_COLOR:configured});
  try {
   const project={id:'a'.repeat(20),name:'Brand default',root:'/project',items:[]};
   emit({type:'archives',items:[],projects:[project]});
   const group=document.querySelector('.archive-project');
   assert.equal(group.style.getPropertyValue('--project-color').toLowerCase(),expected);
   assert.equal(group.querySelector('input[type=color]').value,expected);
   assert.equal(group.querySelector('.project-folder-count').textContent,'0');
   assert(group.querySelector('.project-folder-count .icon-chat'));
   assert.equal(group.querySelector('.project-folder-count').getAttribute('aria-label'),'0 conversations');
   assert.equal(group.querySelector('.archive-empty').textContent,'Aucune conversation associée à ce projet.');
   emit({type:'archives',items:[],projects:[{...project,color:'#dd4400',items:[{id:'selected',title:'Selected'}]}]});
   assert.equal(document.querySelector('.project-folder-count').textContent,'1');
   assert(document.querySelector('.project-folder-count .icon-chat'));
   assert.equal(document.querySelector('.project-folder-count').getAttribute('aria-label'),'1 conversation');
   assert.equal(document.querySelector('input[type=color]').value,'#dd4400');
   assert.equal(document.querySelector('.archive-project').style.getPropertyValue('--project-color'),'#dd4400');
   emit({type:'archives',items:[],projects:[]});
   assert.equal(document.querySelector('.archive-empty').textContent,'Aucune conversation archivée.');
  } finally {dom.window.close();}
 }
});

test('folder color drafts cancel cleanly and persist with the title only after Save',()=>{
 const {ProjectStore}=require('../project-store'),os=require('os');
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'folder-color-ui-')),store=new ProjectStore(path.join(root,'data'),root);
 const saved=store.create(root,'Folder'),project={...saved,color:'#123456',items:[]};
 store.update(saved.id,{color:project.color});
 const {dom,document,emit,sent}=browser();
 try {
  emit({type:'archives',items:[],projects:[project]});
  let group=document.querySelector('.archive-project');group.querySelector('.project-edit').click();
  const editor=group.querySelector('.project-editor'),title=editor.querySelector('input[type=text]'),color=editor.querySelector('input[type=color]');
  title.value='Titre en cours';color.value='#aa22cc';color.focus();
  color.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  color.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  assert.equal(group.style.getPropertyValue('--project-color'),project.color);
  assert.equal(sent.filter(m=>m.type==='updateProject').length,0);
  assert.equal(store.list().find(p=>p.id===project.id).color,project.color);
  emit({type:'archives',items:[],projects:[{...project}]});
  assert.equal(document.querySelector('input[type=color]'),color);assert.equal(document.activeElement,color);
  assert.equal(title.value,'Titre en cours');assert.equal(color.value,'#aa22cc');
  editor.querySelector('button[type=button]').click();
  group=document.querySelector('.archive-project');
  assert.equal(group.style.getPropertyValue('--project-color'),project.color);
  assert.equal(group.querySelector('input[type=color]').value,project.color);
  assert.equal(group.querySelector('.project-folder-name').textContent,'Folder');
  assert.equal(sent.filter(m=>m.type==='updateProject').length,0);
  group.querySelector('.project-edit').click();
  const form=group.querySelector('.project-editor'),picker=form.querySelector('input[type=color]');
  form.querySelector('input[type=text]').value='Titre validé';picker.value='#aa22cc';picker.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  const message=sent.at(-1);assert.equal(message.type,'updateProject');assert.equal(message.project,project.id);
  assert.equal(message.color,'#aa22cc');assert.equal(message.name,'Titre validé');
  const persisted=store.update(message.project,message);
  emit({type:'project_updated',project:persisted,requestId:message.requestId});
  emit({type:'archives',items:[],projects:[{...persisted,items:[]}]});
  assert.equal(document.querySelector('.project-editor').hidden,true);
  assert.equal(document.querySelector('.archive-project').style.getPropertyValue('--project-color'),'#aa22cc');
  assert.equal(document.querySelector('.project-folder-name').textContent,'Titre validé');
  const reloaded=browser();
  try {
   reloaded.emit({type:'archives',items:[],projects:new ProjectStore(store.dataRoot,root).list()});
   assert.equal(reloaded.document.querySelector('input[type=color]').value,'#aa22cc');
   assert.equal(reloaded.document.querySelector('.archive-project').style.getPropertyValue('--project-color'),'#aa22cc');
  } finally {reloaded.dom.window.close();}
 } finally {dom.window.close();fs.rmSync(root,{recursive:true,force:true});}
});

test('later color drafts survive a save confirmation and a failed explicit save remains retryable',()=>{
 const {dom,document,emit,sent}=browser(),project={id:'a'.repeat(20),name:'Folder',root:'/project',color:'#123456',items:[]};
 try {
  emit({type:'archives',items:[],projects:[project]});
  const group=document.querySelector('.archive-project');group.querySelector('.project-edit').click();
  const editor=group.querySelector('.project-editor'),color=editor.querySelector('input[type=color]');
  const choose=value=>{const count=sent.length;color.value=value;color.dispatchEvent(new dom.window.Event('change',{bubbles:true}));assert.equal(sent.length,count);};
  const save=()=>{editor.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));return sent.at(-1);};
  choose('#aa0000');const first=save();choose('#00bb00');
  emit({type:'project_updated',project:{...project,color:first.color},requestId:first.requestId});
  assert.equal(editor.hidden,false);assert.equal(color.value,'#00bb00');
  assert.equal(group.style.getPropertyValue('--project-color'),first.color);
  const second=save();assert.equal(document.getElementById('archive-notice').textContent,'Enregistrement…');
  emit({type:'project_update_failed',project:project.id,requestId:second.requestId,text:'Disque plein'});
  emit({type:'archives',items:[],projects:[{...project,color:first.color}]});
  assert.equal(editor.hidden,false);assert.equal(color.value,second.color);
  assert.match(document.getElementById('archive-notice').textContent,/Enregistrement impossible.*Disque plein/);
  const retry=save();assert.equal(retry.color,second.color);assert.notEqual(retry.requestId,second.requestId);
  emit({type:'project_updated',project:{...project,color:retry.color},requestId:retry.requestId});
  emit({type:'archives',items:[],projects:[{...project,color:retry.color}]});
  assert.equal(document.querySelector('.project-editor').hidden,true);
  assert.equal(document.querySelector('.archive-project').style.getPropertyValue('--project-color'),second.color);
 } finally {dom.window.close();}
});

test('closing after Save applies the confirmed color without stealing focus',()=>{
 const {dom,document,emit,sent}=browser(),project={id:'a'.repeat(20),name:'Folder',root:'/project',color:'#123456',items:[]};
 try {
  emit({type:'archives',items:[],projects:[project]});
  document.querySelector('.project-edit').click();
  const color=document.querySelector('input[type=color]');color.value='#abcdef';color.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  document.querySelector('.project-editor').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  const message=sent.at(-1);
  emit({type:'archives',items:[],projects:[project]});
  document.querySelector('.project-editor button[type=button]').click();
  assert.equal(document.querySelector('.archive-project').style.getPropertyValue('--project-color'),project.color);
  const other=document.getElementById('model-picker');other.focus();
  emit({type:'project_updated',project:{...project,color:message.color},requestId:message.requestId});
  assert.equal(document.activeElement,other);
  emit({type:'archives',items:[],projects:[{...project,color:message.color}]});
  assert.equal(document.querySelector('.archive-project').style.getPropertyValue('--project-color'),'#abcdef');
 } finally {dom.window.close();}
});

test('opening another source keeps expanded folders and displays only the selected conversation',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const project={id:'b'.repeat(20),name:'Other',root:'/other',active:false,items:[{id:'chosen',title:'Chosen'}]};
  emit({type:'archives',projects:[project],items:[]});
  document.querySelector('.archive-project').open=true;
  document.querySelector('.project-documents')?.setAttribute('open','');
  document.querySelector('.archive-open').click();
  assert.equal(sent.at(-1).type,'openProject');
  emit({type:'ready',conversation:'previous',model:'model'});
  emit({type:'archive_loaded',id:'previous',messages:[{type:'user',text:'Wrong conversation'}]});
  assert(!document.getElementById('messages').textContent.includes('Wrong conversation'));
  emit({type:'archives',projects:[{...project,active:true}],items:[]});
  assert.equal(document.querySelector('.archive-project').open,true);
  emit({type:'archive_loaded',id:'chosen',messages:[{type:'user',text:'Selected conversation'}],has_context:true});
  assert.equal(document.getElementById('archive-panel').hidden,true);
  assert(document.getElementById('messages').textContent.includes('Selected conversation'));
  document.getElementById('history').click();
  assert.equal(document.querySelector('.archive-project').open,true);
  assert.equal(document.getElementById('archive-panel').hidden,false);
 } finally {dom.window.close();}
});

test('snapshot restoration closes history and a rejected open allows retry',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const project={id:'b'.repeat(20),name:'Other',root:'/other',active:false,items:[{id:'chosen',title:'Chosen'}]};
  emit({type:'archives',projects:[project],items:[]});
  document.querySelector('.archive-open').click();
  emit({type:'archive_open_failed',text:'Retry'});
  assert.equal(document.getElementById('archive-panel').hidden,false);
  const count=sent.length;document.querySelector('.archive-open').click();assert(sent.length>count);
  emit({type:'snapshot_loaded',id:'chosen',html:'<p>Saved conversation</p>'});
  assert.equal(document.getElementById('archive-panel').hidden,true);
  assert(document.getElementById('messages').textContent.includes('Saved conversation'));
 } finally {dom.window.close();}
});

test('project colors expose HEX first and synchronize validated edits with the visual picker',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const id='a'.repeat(20);
  emit({type:'archives',projects:[{id,name:'Project',root:'/project',color:'#123abc',items:[]}],items:[]});
  document.querySelector('.project-edit').click();
  const hex=document.querySelector('.project-color-hex'),picker=document.querySelector('input[type=color]');
  assert.equal(hex.value,'#123ABC');assert.equal(hex.parentElement.firstElementChild,hex);
  hex.value='FF8800';hex.dispatchEvent(new dom.window.Event('input'));
  assert.equal(picker.value,'#ff8800');
  hex.dispatchEvent(new dom.window.Event('change'));assert.equal(sent.filter(m=>m.type==='updateProject').length,0);assert.equal(hex.value,'#FF8800');
  const count=sent.length;hex.value='#12';hex.dispatchEvent(new dom.window.Event('change'));assert.equal(sent.length,count);assert.equal(hex.checkValidity(),false);
  picker.value='#aabbcc';picker.dispatchEvent(new dom.window.Event('change'));assert.equal(hex.value,'#AABBCC');assert.equal(hex.checkValidity(),true);
  assert.equal(sent.length,count);document.querySelector('.project-editor').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));assert.equal(sent.at(-1).color,'#aabbcc');
 } finally {dom.window.close();}
});

test('notifications carry explicit semantic status for saving, success, warning and failure',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const project={id:'a'.repeat(20),name:'Project',root:'/project',color:'#123abc',items:[]};
  emit({type:'archives',projects:[project],items:[]});
  document.querySelector('.project-edit').click();
  const color=document.querySelector('input[type=color]');color.value='#abcdef';color.dispatchEvent(new dom.window.Event('change'));
  document.querySelector('.project-editor').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  const notice=document.getElementById('archive-notice'),status=document.getElementById('status');
  assert.equal(notice.dataset.status,'info');
  const request=sent.at(-1);emit({type:'project_updated',requestId:request.requestId,project:{...project,color:'#abcdef'}});
  assert.equal(notice.dataset.status,'success');assert.equal(notice.style.opacity,'');
  emit({type:'archive_delete_pending',id:'missing',source:project.id});
  assert.equal(notice.dataset.status,'warning');assert.equal(status.dataset.status,'warning');
  emit({type:'archive_delete_failed',id:'missing',source:project.id,text:'Disque indisponible'});
  assert.equal(notice.dataset.status,'error');assert.equal(status.dataset.status,'error');
  emit({type:'archive_deleted',id:'missing',source:project.id,active:false});
  assert.equal(notice.dataset.status,'success');assert.equal(status.dataset.status,'success');assert.equal(notice.style.opacity,'');
  emit({type:'idle'});assert.equal(status.hasAttribute('data-status'),false);
 } finally {dom.window.close();}
});

test('notifications disappear after seven seconds independently and replacement restarts the delay',()=>{
 const {dom,document,emit}=browser();
 try {
  const w=dom.window,timers=new Map(),nativeSet=w.setTimeout.bind(w),nativeClear=w.clearTimeout.bind(w);
  let now=0,id=0;
  w.setTimeout=(fn,delay,...args)=>{if(delay!==7000)return nativeSet(fn,delay,...args);timers.set(--id,{fn,at:now+delay});return id;};
  w.clearTimeout=id=>{if(!timers.delete(id))nativeClear(id);};
  const advance=ms=>{now+=ms;for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.fn();}};
  const notice=document.getElementById('archive-notice'),status=document.getElementById('status');
  emit({type:'archive_delete_failed',id:'none',text:'Failed'});
  advance(6999);assert.equal(notice.textContent,'Failed');assert.equal(status.textContent,'Failed');
  advance(1);assert.equal(notice.textContent,'');assert.equal(status.textContent,'');
  assert.equal(notice.hasAttribute('data-status'),false);assert.equal(status.classList.contains('error-message'),false);
  emit({type:'archive_deleted',id:'none',active:false});
  advance(5000);assert(notice.textContent);assert(status.textContent);
  emit({type:'archive_organization_failed',text:'New notification'});
  advance(2000);assert.equal(status.textContent,'');assert.equal(notice.textContent,'New notification');
  advance(4999);assert.equal(notice.textContent,'New notification');
  advance(1);assert.equal(notice.textContent,'');assert.equal(timers.size,0);
 } finally {dom.window.close();}
});

test('project editor saves a new path and keeps the renamed identity expanded after refresh',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const old={id:'a'.repeat(20),name:'Project',root:'/old',color:'#123abc',items:[]};
  emit({type:'archives',projects:[old],items:[]});document.querySelector('.project-edit').click();
  const editor=document.querySelector('.project-editor'),input=editor.querySelector('.project-path-input');
  assert.equal(input.value,'/old');input.value='/new folder';
  editor.dispatchEvent(new dom.window.Event('submit',{cancelable:true}));
  const request=sent.at(-1);assert.equal(request.type,'updateProject');assert.equal(request.root,'/new folder');
  assert.equal(input.disabled,true);
  const updated={...old,id:'b'.repeat(20),root:'/new folder'};
  emit({type:'project_updated',previousId:old.id,project:updated,requestId:request.requestId});
  assert.equal(document.querySelector('.archive-project').dataset.projectId,updated.id);
  assert.equal(document.querySelector('.project-folder-path').textContent,'/new folder');
  assert.equal(input.disabled,false);
  emit({type:'archives',projects:[updated],items:[]});
  assert.equal(document.querySelector('.archive-project').open,true);assert.equal(document.querySelector('.project-path-input').value,'/new folder');
  document.querySelector('.project-edit').click();document.querySelector('.project-path-input').value='/invalid';
  document.querySelector('.project-editor button[type=button]').click();
  assert.equal(document.querySelector('.project-path-input').value,'/new folder');
 } finally {dom.window.close();}
});

test('execution evidence stays internal and does not block opening another conversation',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  emit({type:'archives',items:[{id:'next',title:'Conversation suivante'}]});
  emit({type:'status',text:'Working'});
  emit({type:'execution_evidence',command_status:'passed',test_status:'passed',criteria_status:'passed',contract_satisfied:true,incomplete:false});
  emit({type:'response',html:'<p>Travail terminé.</p>'});
  assert.equal(document.querySelectorAll('.execution-evidence').length,0);
  document.getElementById('history').click();
  document.querySelector('.archive-open').click();
  assert.equal(sent.at(-1).type,'loadArchive');
  assert.equal(sent.at(-1).id,'next');
  emit({type:'snapshot_loaded',id:'next',html:'<article class="message assistant"><div class="execution-evidence">Ancien badge</div><div class="content">Réponse archivée</div></article>',timeline:[{type:'execution_evidence',criteria_status:'failed'}]});
  assert.equal(document.querySelectorAll('.execution-evidence').length,0);
 } finally {dom.window.close();}
});

test('action blocks collapse inside, outside and with the bottom control, preserving controls and selection', () => {
  const {dom,document,emit} = browser();
  try {
    emit({type:'status',text:'Working'});
    const activity=document.querySelector('details.activity');
    const list=activity.querySelector('.activity-list');
    activity.open=true;
    list.click(); assert.equal(activity.open,false);
    activity.open=true;
    document.body.click(); assert.equal(activity.open,false);
    activity.open=true;
    activity.querySelector('.activity-bottom-chevron').click(); assert.equal(activity.open,false);
    activity.open=true;
    const button=document.createElement('button');list.append(button);
    button.click();assert.equal(activity.open,true);
    const text=document.createTextNode('selection');list.append(text);
    const range=document.createRange();range.selectNodeContents(text);
    dom.window.getSelection().removeAllRanges();
    dom.window.getSelection().addRange(range);
    assert.equal(dom.window.getSelection().toString(), "selection");
    list.click();assert.equal(activity.open,true);
    dom.window.getSelection().removeAllRanges();
    activity.querySelector('summary').click();assert.equal(activity.open,false);
    activity.querySelector('summary').click();assert.equal(activity.open,true);
  } finally {dom.window.close();}
});

test('folder deletion requires confirmation, supports cancel/Escape and restores the conversation warning',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const project={id:'a'.repeat(20),name:'Dossier <test>',root:'/demo',items:[{id:'saved',title:'Conversation',archive_project:'b'.repeat(20)}]};
  emit({type:'archives',projects:[project],items:[]});document.querySelector('.project-edit').click();
  const dialog=document.getElementById('delete-dialog');dialog.showModal=()=>dialog.open=true;dialog.close=()=>dialog.open=false;
  const remove=document.querySelector('.project-editor .project-delete'),confirm=document.getElementById('delete-confirm');
  assert.equal(remove.textContent,'Supprimer');sent.length=0;remove.click();
  assert(dialog.open);assert.match(document.getElementById('delete-description').textContent,/toutes les conversations/);
  assert.match(document.getElementById('delete-description').textContent,/fichiers du projet seront conservés/);
  assert.equal(document.activeElement.id,'delete-cancel');assert.equal(dialog.getAttribute('aria-describedby'),'delete-description');
  assert(document.getElementById('delete-title').textContent.includes('<test>'));assert(!document.getElementById('delete-title').querySelector('test'));
  document.getElementById('delete-cancel').click();confirm.click();assert.equal(sent.length,0);
  remove.click();dialog.dispatchEvent(new dom.window.Event('cancel'));confirm.click();assert.equal(sent.length,0);
  document.querySelector('.archive-delete').click();
  assert.match(document.getElementById('delete-description').textContent,/cette conversation/);
  assert(!document.getElementById('delete-description').textContent.includes('toutes les conversations'));
  confirm.click();assert.equal(sent.at(-1).type,'deleteArchiveConfirmed');
 } finally {dom.window.close();}
});
test('folder deletion handles pending, failure, duplicate clicks, renamed IDs and stale history refreshes',()=>{
 const {dom,document,emit,sent}=browser();
 try {
  const project={id:'a'.repeat(20),name:'Test',root:'/old',items:[]};
  emit({type:'archives',projects:[project],items:[]});document.querySelector('.project-edit').click();
  const dialog=document.getElementById('delete-dialog');dialog.showModal=()=>{};dialog.close=()=>{};
  const editor=document.querySelector('.project-editor');editor.querySelector('.project-path-input').value='/new';
  editor.dispatchEvent(new dom.window.Event('submit',{cancelable:true}));const save=sent.at(-1);
  const updated={...project,id:'b'.repeat(20),root:'/new'};
  emit({type:'project_updated',previousId:project.id,project:updated,requestId:save.requestId});
  document.querySelector('.project-edit').click();document.querySelector('.project-delete').click();
  const confirm=document.getElementById('delete-confirm');confirm.click();confirm.click();
  assert.equal(sent.filter(m=>m.type==='deleteProjectConfirmed').length,1);assert.equal(sent.at(-1).project,updated.id);
  assert(document.querySelector('.project-delete').disabled);assert.equal(document.querySelector('.archive-project').getAttribute('aria-busy'),'true');
  emit({type:'project_delete_failed',project:updated.id,text:'Disque indisponible'});
  assert(!document.querySelector('.project-delete').disabled);assert.match(document.querySelector('#archive-notice').textContent,/Disque indisponible/);
  document.querySelector('.project-delete').click();confirm.click();
  emit({type:'archives',projects:[updated],items:[]});
  emit({type:'project_deleted',project:updated.id});
  assert.equal(document.querySelector('.archive-project'),null);
  dom.window.flushDeferredArchives();assert.equal(document.querySelector('.archive-project'),null);
  assert.match(document.querySelector('#archive-notice').textContent,/supprimés définitivement/);
 } finally {dom.window.close();}
});


test('DeepSeek live and restored costs use euros and update when rates arrive', () => {
 const {dom,document,emit}=browser();
 const exchange_rates={date:'2026-10-28',rates:{EUR:1,USD:2,CNY:8},stale:false};
 const usage={type:'usage',model:'deepseek-flash',input_tokens:100,output_tokens:10,cost_usd:2,currency:'USD',timestamp:'2026-10-17T15:40:01Z'};
 try {
  emit({type:'status',text:'Working'});
  emit(usage);
  assert.match(document.querySelector('[data-event-type="usage"] code').textContent,/indisponible/);
  emit({type:'models',items:[],exchange_rates});
  assert.match(document.querySelector('[data-event-type="usage"] code').textContent,/1,00\s*€/);
  emit({type:'model_routed',model:'deepseek-flash',projected_cost_usd:4,previous_cost_usd:2,previous_requests:1,exchange_rates});
  const route=document.querySelector('[data-event-type="model_routed"] code').textContent;
  assert.match(route,/estimation 2,00\s*€/);assert.match(route,/historique.*1,00\s*€/);assert.doesNotMatch(route,/\$/);
  const row=document.querySelector('[data-event-type="usage"]');
  delete row.dataset.deepseekCost;row.querySelector('code').textContent='deepseek-flash · 2,00 $US';
  const html=document.getElementById('messages').innerHTML;
  emit({type:'snapshot_loaded',id:'fx-snapshot',html,timeline:[usage],model:'deepseek-flash'});
  assert.match(document.querySelector('[data-event-type="usage"] code').textContent,/1,00\s*€/);
  assert.doesNotMatch(document.querySelector('[data-event-type="usage"] code').textContent,/\$/);
  emit({type:'archive_loaded',id:'fx-events',messages:[{type:'user',text:'check'},usage,{type:'response',html:'<p>Done</p>'}]});
  assert.match(document.querySelector('[data-event-type="usage"] code').textContent,/1,00\s*€/);
 } finally {dom.window.close();}
});
