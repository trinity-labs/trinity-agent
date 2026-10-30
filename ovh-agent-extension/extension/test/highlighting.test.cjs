const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const {JSDOM} = require('jsdom');

function browser(minified, saved) {
  const root = path.resolve(__dirname, '..');
  const nativeRequire = createRequire(path.join(root, 'extension.js'));
  const vscode = {workspace:{workspaceFolders:[{uri:{fsPath:root}}]}, Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)}, env:{language:'fr'}};
  const scope = {require:name=>name==='vscode'?vscode:nativeRequire(name), module:{exports:{}}, process, console, setTimeout, clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports={Provider:OvhAgentViewProvider,renderMarkdown};',scope);
  const provider = new scope.module.exports.Provider(root, {});
  const dom = new JSDOM(provider.getHtml({asWebviewUri:x=>x,cspSource:'local'}), {runScripts:'outside-only',url:'https://test.invalid'});
  const w = dom.window;
  const states=[],posted=[];
  w.acquireVsCodeApi=()=>({getState:()=>saved,setState:value=>states.push(value),postMessage:value=>posted.push(value)});
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.OVH_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
  for(const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','highlight.min.js','chat.js']) w.eval(fs.readFileSync(minified && ['i18n.js','safe-html.js','chat.js'].includes(name) ? path.join(minified,name.replace('.js','.min.js')) : path.join(root,'media',name),'utf8'));
  return {dom, w, states, posted, render:scope.module.exports.renderMarkdown,
    emit:data=>w.dispatchEvent(new w.MessageEvent('message',{data}))};
}

test('legacy auto model state is normalized before sending a prompt',()=>{
  const {dom,w,posted}=browser(undefined,{model:'auto'});
  try {
    const prompt=w.document.getElementById('prompt');
    prompt.value='Test du routage';
    w.document.getElementById('send').click();
    const request=posted.find(message=>message.type==='prompt');
    assert(request);assert.equal(request.model,'auto-balanced');
  } finally {dom.window.close();}
});

test('Monokai code highlighting preserves literal code, safe restoration and copy', async () => {
  const {dom,w,render,emit} = browser();
  try {
    const source = 'const count = 42;\nconst text = "<img src=x onerror=alert(1)>";\n';
    emit({type:'response',html:render('```javascript\n'+source+'```\n\nInline `count`.')});
    const content=w.document.querySelector('.message.assistant .markdown');
    let code=content.querySelector('pre code');
    assert.equal(code.textContent,source);
    assert(code.querySelector('.hljs-keyword'));
    assert(code.querySelector('.hljs-string'));
    assert.equal(content.querySelector('img'),null);
    assert.equal(content.querySelector('p code').classList.contains('hljs'),false);
    let copied;
    Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
    content.querySelector('.copy-code').click();
    await Promise.resolve();
    assert.equal(copied,source);
    content.innerHTML=w.safeHtml(content.innerHTML);
    w.enhanceMarkdown(content);
    w.enhanceMarkdown(content);
    code=content.querySelector('pre code');
    assert.equal(code.textContent,source);
    assert(code.querySelector('.hljs-keyword'));
    assert.equal(code.querySelector('span span span'),null);
    assert.equal(content.querySelectorAll('.copy-code').length,1);
    assert(w.document.querySelector('link[href*="monokai.min.css"]'));
    assert(w.document.querySelector('script[src*="highlight.min.js"][nonce]'));
    for (const [language,sample] of [['python','def hello():\n    return 42\n'],['bash','echo "$USER"\n'],['dockerfile','FROM alpine:3.21\nRUN echo hello\n'],['yaml','service: trinity\n']]) {
      content.innerHTML=w.safeHtml(render('```'+language+'\n'+sample+'```'));
      w.enhanceMarkdown(content);
      assert.equal(content.querySelector('code').textContent,sample);
      assert(content.querySelector('code span'),language);
    }
  } finally {dom.window.close();}
});

test('streaming defers automatic detection and unknown or large code stays intact', () => {
  const {dom,w,render,emit} = browser();
  try {
    const original=w.hljs.highlightAuto;
    let detections=0;
    w.hljs.highlightAuto=(...args)=>{detections++;return original(...args);};
    const html=render('```\nconst count = 42;\n```');
    emit({type:'delta',html});
    assert.equal(detections,0);
    emit({type:'response',html});
    assert.equal(detections,1);
    assert(w.document.querySelector('pre code span'));
    const content=w.document.querySelector('.message.assistant .markdown');
    for (const [language,source] of [['unknown-language','<script>literal</script>\n'],['javascript','x'.repeat(100001)+'\n'],['plaintext','const plain = 42;\n']]) {
      content.innerHTML=w.safeHtml(render('```'+language+'\n'+source+'```'));
      w.enhanceMarkdown(content);
      assert.equal(content.querySelector('code').textContent,source);
      assert.equal(content.querySelector('code span, script'),null);
      assert.equal(detections,1);
    }
  } finally {dom.window.close();}
});

test('assistant prose only displays the two allowed emojis in streams and restored archives', async () => {
  const {dom,w,render,emit} = browser();
  try {
    const source='🔎 Audit 🛠️ ⚠️ 👩🏽‍💻 🇫🇷 1️⃣\n\n➡️ Conseil. ✅ Résultat. © 2026 · API v1.2 · 0,75 €.\n\n'
      +'Littéral `const icon = "🛠️"`.\n\n```javascript\nconst icon = "👩🏽‍💻";\n```\n\n'
      +'> Citation exacte : ⚠️\n\n[Fichier 🔎](https://example.invalid/%F0%9F%94%8E)';
    const assertProse=()=>{
      const content=w.document.querySelector('.message.assistant .markdown');
      assert.equal(content.querySelector('p').textContent.trim(),'Audit');
      assert.match(content.querySelectorAll('p')[1].textContent,/➡️ Conseil\. ✅ Résultat\. © 2026 · API v1\.2 · 0,75 €/);
      assert.equal(content.querySelector('p code').textContent,'const icon = "🛠️"');
      assert.equal(content.querySelector('pre code').textContent,'const icon = "👩🏽‍💻";\n');
      assert.match(content.querySelector('blockquote').textContent,/Citation exacte : ⚠️/);
      assert.equal(content.querySelector('a').textContent,'Fichier 🔎');
      return content;
    };
    emit({type:'delta',html:render(source)});
    assertProse();
    emit({type:'response',html:render(source)});
    let content=assertProse();
    let copied;
    Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
    content.querySelector('.copy-code').click();
    await Promise.resolve();
    assert.equal(copied,'const icon = "👩🏽‍💻";\n');
    // An old snapshot may still contain decorative emojis: enforce the same rule on restoration.
    content.innerHTML=w.safeHtml(render(source));
    emit({type:'snapshot_loaded',id:'emoji-archive',html:w.document.querySelector('#messages').innerHTML});
    assertProse();
    emit({type:'archive_loaded',id:'emoji-archive',messages:[
      {type:'user',text:'Mon message 🔎 reste intact'},
      {type:'response',html:render(source)},
    ]});
    assertProse();
    assert.equal(w.document.querySelector('.message.user .content').textContent,'Mon message 🔎 reste intact');
  } finally {dom.window.close();}
});

test('text tables align their columns without losing highlighting in mixed blocks', async () => {
  const {dom,w,render,emit} = browser();
  try {
    const diagram = [
      '┌───────────────────────────────────────────┐',
      '│                Services                   │',
      '├───────────────────────────────────────────┤',
      '│ trinity (port 8888) │ API                │',
      '│ ├─ Secrets: 15 fichiers   │ Auth         │',
      '│ trinity-pdf │ Sécurité renforcée         │',
      '└───────────────────────────────────────────┘',
    ].join('\n');
    const source = 'const port = 8888;\n\n' + diagram + '\n\nconsole.log(port);\n';
    emit({type:'response',html:render('```\n'+source+'```')});
    const content=w.document.querySelector('.message.assistant .markdown');
    const code=content.querySelector('pre code');
    const aligned=code.textContent;
    const rows=aligned.split('\n').filter(line=>/^[┌│├└]/.test(line));
    assert.equal(new Set(rows.map(row=>row.length)).size,1,'all outer borders align');
    assert.equal(new Set(rows.filter(row=>(row.match(/│/g)||[]).length===3).map(row=>row.indexOf('│',1))).size,1,'inner columns align');
    assert(aligned.startsWith('const port = 8888;\n\n'));
    assert(aligned.endsWith('\n\nconsole.log(port);\n'));
    assert(code.querySelector('[class^="hljs-"]'),'mixed content still has syntax colours');
    assert.equal(w.alignTextTables(aligned),aligned,'alignment is stable after restoration');
    let copied;
    Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
    content.querySelector('.copy-code').click();
    await Promise.resolve();
    assert.equal(copied,aligned);
    content.innerHTML=w.safeHtml(content.innerHTML);
    w.enhanceMarkdown(content);
    assert.equal(content.querySelector('code').textContent,aligned);
    assert(content.querySelector('code [class^="hljs-"]'));
    // A diagram inside an explicitly labelled program can be a string literal.
    const program='const diagram = `\n'+diagram+'\n`;\n';
    content.innerHTML=w.safeHtml(render('```javascript\n'+program+'```'));
    w.enhanceMarkdown(content);
    assert.equal(content.querySelector('code').textContent,program);
    const incomplete=diagram.split('\n').slice(0,-1).join('\n');
    assert.equal(w.alignTextTables(incomplete),incomplete);
    const unicode=diagram.replace('API','日本語');
    assert.equal(w.alignTextTables(unicode),unicode,'do not guess wide-character padding');
  } finally {dom.window.close();}
});

test('emoji modes apply to generated prose and punctuation without corrupting code', () => {
  const {dom,w,render,emit} = browser();
  try {
    for (const mode of [0,1,2,3]) {
      emit({type:'ready',emoji_mode:mode});
      emit({type:'response',html:render('✅ Bon ⭕ Erreur ➡️ Suite 🔎 Recherche — suite\n\n`const value = "—🔎";`')});
      const prose=w.document.querySelector('.message.assistant:last-child .markdown p');
      assert(prose.textContent.includes('- suite'));
      assert.equal(prose.textContent.includes('✅'),mode>0);
      assert.equal(prose.textContent.includes('⭕'),mode>0);
      assert.equal(prose.textContent.includes('➡️'),mode>0);
      assert.equal(prose.textContent.includes('🔎'),mode>=2);
      assert.equal(w.document.querySelector('.message.assistant:last-child code').textContent,'const value = "—🔎";');
    }
  } finally {dom.window.close();}
});

test('diff line background remains visible behind literal source', () => {
  const {dom,w} = browser();
  try {
    const style=w.document.createElement('style');
    style.textContent=fs.readFileSync(path.join(__dirname,'../media/chat.css'),'utf8');
    w.document.head.append(style);
    const content=w.document.createElement('div');
    // Real file-change diffs sit outside .markdown. Simulate the host code theme.
    const hostStyle=w.document.createElement('style');
    hostStyle.textContent='code { background: rgb(60, 60, 60); border-radius: 4px; }';
    w.document.head.prepend(hostStyle);
    content.className='diff-scroller';
    content.innerHTML='<div class="diff-line added"><code>const x = 1;</code></div><div class="diff-line removed"><code>const x = 0;</code></div>';
    w.document.body.append(content);
    for (const line of content.children) {
      assert.notEqual(w.getComputedStyle(line).backgroundColor,'rgba(0, 0, 0, 0)');
      assert.equal(w.getComputedStyle(line.querySelector('code')).backgroundColor,'rgba(0, 0, 0, 0)');
    }
  } finally {dom.window.close();}
});

test('explicit grammars run once at completion, never per streamed chunk',()=>{
  const {dom,w,render,emit}=browser();
  try {
    let count=0;const original=w.hljs.highlight;
    w.hljs.highlight=(...args)=>{count++;return original(...args);};
    const html=render('```javascript\nconst value=42;\n```');
    for(let i=0;i<100;i++)emit({type:'delta',html});
    assert.equal(count,0);
    emit({type:'response',html});assert.equal(count,1);
    assert.equal(w.document.querySelector('pre code').textContent,'const value=42;\n');
  } finally {dom.window.close();}
});

test('stream persistence is coalesced and final identical HTML keeps its DOM',()=>{
  const {dom,w,states,render,emit}=browser();
  try {
    emit({type:'delta',html:render('First')});const initial=states.length;
    for(let i=0;i<100;i++)emit({type:'delta',html:render('Text '+i)});
    assert.equal(states.length,initial,'no whole-history serialization per streamed fragment');
    const paragraph=w.document.querySelector('.markdown p');
    emit({type:'response',html:render('Text 99')});
    assert.equal(w.document.querySelector('.markdown p'),paragraph,'final response reuses the last DOM');
    assert(states.at(-1).html.includes('Text 99'));
  } finally {dom.window.close();}
});

test('generated minified frontend preserves streams, final state, safety and source maps',async()=>{
  const os=require('node:os');const {buildFrontend}=require('../scripts/build-frontend.cjs');
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'frontend-min-'));
  let dom;
  try {
    const sizes=await buildFrontend(directory);assert(sizes.every(row=>row.minified<row.source));
    const browserState=browser(directory);dom=browserState.dom;
    const {w,emit,render,states}=browserState;
    emit({type:'delta',html:render('```javascript\nconst x=1;\n```')});
    emit({type:'response',html:render('```javascript\nconst x=1;\n```')});
    assert.equal(w.document.querySelector('pre code').textContent,'const x=1;\n');
    assert(w.document.querySelector('.hljs-keyword'));assert(states.at(-1).html.includes('const'));
    assert(!w.safeHtml('<img src="https://evil.invalid" onerror="alert(1)"><script>bad()</script>').includes('onerror'));
    const map=JSON.parse(fs.readFileSync(path.join(directory,'chat.min.js.map')));
    assert.equal(map.sourcesContent[0],fs.readFileSync(path.join(__dirname,'../media/chat.js'),'utf8'));
  } finally {if(dom)dom.window.close();fs.rmSync(directory,{recursive:true,force:true});}
});


test('archive header restores its own routed model and clears stale routes', () => {
  const {dom,w,emit}=browser();
  try {
    const label=()=>w.document.getElementById('selected-model').textContent;
    emit({type:'model_selected',model:'auto-balanced'});
    emit({type:'model_routed',model:'qwen-old'});
    assert.match(label(),/qwen-old/);
    emit({type:'archive_loaded',id:'gpt',messages:[
      {type:'user',text:'hello',selection:'auto-balanced',model:'gpt-oss-120b'},
      {type:'model_active',model:'gpt-oss-120b',selection:'auto-balanced',automatic:true}
    ]});
    assert.match(label(),/gpt-oss-120b/);assert.doesNotMatch(label(),/qwen/);
    emit({type:'snapshot_loaded',id:'qwen',html:'<p>snapshot</p>',model:'auto-balanced',timeline:[
      {type:'model_active',model:'qwen-new',selection:'auto-balanced'}
    ]});
    assert.match(label(),/qwen-new/);
    emit({type:'archive_loaded',id:'unknown',messages:[]});
    assert.doesNotMatch(label(),/qwen|gpt/);
    emit({type:'archive_loaded',id:'manual',model:'manual-model',messages:[]});
    assert.equal(label(),'manual-model');
    emit({type:'model_active',model:'gpt-oss-120b',selection:'auto-balanced',automatic:true});
    assert.equal(label(),'manual-model','a delayed execution does not replace the restored choice');
    emit({type:'cleared',conversation:'fresh'});
    assert.doesNotMatch(label(),/gpt|qwen/);
  } finally {dom.window.close();}
});

for (const restoration of ['archive_loaded','snapshot_loaded'])
test(`${restoration} restores the opened conversation model and sends the next prompt with it`,()=>{
  const {dom,w,emit,posted}=browser(undefined,{model:'previous-model'});
  try {
    emit({type:'archives',items:[{id:'target',title:'Target'}]});
    w.document.querySelector('[data-archive-id="target"] .archive-open').click();
    emit({type:restoration,id:'target',model:'Qwen3.8-27B',html:'<p>Target context</p>',messages:[],timeline:[]});
    assert.equal(w.document.getElementById('selected-model').textContent,'Qwen3.8-27B');
    emit({type:'models',selected:'previous-model',items:[]});
    emit({type:'model_selected',model:'previous-model'});
    w.document.getElementById('prompt').value='Continue';
    w.document.getElementById('send').click();
    assert.equal(posted.find(e=>e.type==='prompt').model,'Qwen3.8-27B');
  } finally {dom.window.close();}
});

test('legacy archive uses the last chronological choice after an earlier automatic execution',()=>{
  const {dom,w,emit}=browser(undefined,{model:'previous-model'});
  try {
    emit({type:'archive_loaded',id:'legacy',messages:[
      {type:'model_active',model:'gpt-oss-120b',selection:'auto-balanced'},
      {type:'model_changed',to_model:'Qwen3.8-27B'},
      {type:'response',text:'done'}]});
    assert.equal(w.document.getElementById('selected-model').textContent,'Qwen3.8-27B');
  } finally {dom.window.close();}
});

for (const failure of ['error','session_busy','idle'])
test(`${failure} from the previous conversation cannot cancel a newer archive open`,()=>{
  const {dom,w,emit,posted}=browser();
  try {
    emit({type:'archives',items:[{id:'a',title:'A'},{id:'b',title:'B'}]});
    const click=id=>w.document.querySelector(`[data-archive-id="${id}"] .archive-open`).click();
    click('a');
    emit({type:'status',conversation:'a',text:'Loading'});
    emit({type:failure,text:'Backend unavailable'});
    assert.equal(w.document.getElementById('messages').hasAttribute('aria-busy'),true);
    emit({type:'archive_open_failed',id:'a',text:'Archive unavailable'});
    assert.equal(w.document.getElementById('messages').hasAttribute('aria-busy'),false);
    click('b');
    assert.equal(posted.filter(e=>e.type==='loadArchive').at(-1).id,'b');
    emit({type:'archive_loaded',id:'b',model:'Qwen3.8-27B',messages:[]});
    assert.equal(w.document.getElementById('selected-model').textContent,'Qwen3.8-27B');
  } finally {dom.window.close();}
});

for (const failure of ['error','session_busy'])
test(`${failure} opens the conversation requested while the task was busy`,()=>{
  const {dom,w,emit,posted}=browser();
  try {
    emit({type:'archives',items:[{id:'target',title:'Target'}]});
    emit({type:'status',conversation:'old',text:'Working'});
    w.document.querySelector('[data-archive-id="target"] .archive-open').click();
    assert(!posted.some(e=>e.type==='loadArchive'));
    emit({type:failure,text:'Request failed'});
    assert.equal(posted.filter(e=>e.type==='loadArchive').at(-1).id,'target');
  } finally {dom.window.close();}
});

test('manual model changes render and restore an inline conversation divider',()=>{
  const {dom,w,emit}=browser();
  try {
    emit({type:'model_changed',from_model:'gpt-oss-120b',to_model:'Qwen3.8-27B'});
    assert.equal(w.document.getElementById('selected-model').textContent,'Qwen3.8-27B');
    let divider=w.document.querySelector('.model-change');
    assert(divider);assert.match(divider.textContent,/gpt-oss-120b/);assert.match(divider.textContent,/Qwen3\.8-27B/);
    assert.equal(divider.querySelectorAll('.model-change-line').length,2);
    assert.match(divider.querySelector('.model-change-info').title,/context|contexte/i);
    emit({type:'archive_loaded',id:'model-history',messages:[
      {type:'user',text:'Avant'},
      {type:'model_changed',from_model:'auto-balanced',to_model:'Qwen3.6-27B'},
      {type:'user',text:'Après'}
    ]});
    divider=w.document.querySelector('.model-change');
    assert(divider);assert.match(divider.textContent,/Qwen3\.6-27B/);
    assert.equal(w.document.getElementById('selected-model').textContent,'Qwen3.6-27B','opening the archive restores its latest model choice');
    const children=[...w.document.getElementById('messages').children];
    assert(children.indexOf(divider)>children.findIndex(node=>node.textContent.includes('Avant')));
    assert(children.indexOf(divider)<children.findIndex(node=>node.textContent.includes('Après')));
  } finally {dom.window.close();}
});


test('empty rollback does not claim files were restored',()=>{
  const {dom,w,emit}=browser();
  try {
    emit({type:'verification_status',status:'rolled_back',restored:[],conflicts:[]});
    assert.equal(w.document.querySelector('.verification-status').textContent,'No changes to restore');
    emit({type:'verification_status',status:'rolled_back',restored:['app.py'],conflicts:[]});
    assert.equal(w.document.querySelector('.verification-status').textContent,'Unverified changes rolled back');
  } finally {dom.window.close();}
});


test('archive click immediately displays themed loading shell, bounded preview and error recovery',()=>{
  const {dom,w,emit}=browser();
  try {
    const items=[{id:'a',title:'Selected A',updated_at:'v1'},{id:'b',title:'Selected B',updated_at:'v1'}];
    emit({type:'archives',items});
    const click=id=>w.document.querySelector(`[data-archive-id="${id}"] .archive-open`).click();
    click('a');
    const messages=w.document.getElementById('messages');
    assert.equal(messages.getAttribute('aria-busy'),'true');
    assert.match(messages.textContent,/Selected A/);
    assert(messages.querySelector('[role="status"]'));
    emit({type:'snapshot_loaded',id:'wrong',html:'<p>WRONG</p>'});
    assert.doesNotMatch(messages.textContent,/WRONG/);
    emit({type:'snapshot_loaded',id:'a',html:'<p>Cached A</p>',model:'auto-balanced',timeline:[]});
    assert.equal(messages.hasAttribute('aria-busy'),false);
    click('b');assert.doesNotMatch(messages.textContent,/Cached A/);
    emit({type:'archive_open_failed',text:'failed'});
    assert.equal(messages.hasAttribute('aria-busy'),false);
    click('a');assert.match(messages.textContent,/Cached A/);
    emit({type:'archive_loaded',id:'a',messages:[]});
    assert.equal(messages.querySelector('.archive-loading-overlay'),null);
  } finally {dom.window.close();}
});


test('restored execution survives delayed global model replies and snapshots echo its revision', async()=>{
  const {dom,w,emit,posted}=browser();
  try {
    emit({type:'ready',model:'auto-balanced',conversation:'x'});
    emit({type:'snapshot_loaded',id:'x',html:'<p>Latest answer</p>',model:'auto-balanced',archive_revision:'42:1234',timeline:[
      {type:'user',model:'gpt-oss-120b',selection:'auto-balanced'},
      {type:'model_active',model:'gpt-oss-120b',selection:'auto-balanced',automatic:true}
    ]});
    emit({type:'models',selected:'Qwen3.5-9B',items:[]});
    const label=w.document.getElementById('selected-model').textContent;
    assert.match(label,/gpt-oss-120b/);assert.doesNotMatch(label,/Qwen/);
    await new Promise(resolve=>setTimeout(resolve,400));
    const snapshot=posted.filter(message=>message.type==='snapshot').at(-1);
    assert.equal(snapshot.archive_revision,'42:1234');
    assert.match(snapshot.html,/Latest answer/);
    emit({type:'response',html:'<p>Newer answer</p>',archive_revision:'84:5678'});
    await new Promise(resolve=>setTimeout(resolve,400));
    assert.equal(posted.filter(message=>message.type==='snapshot').at(-1).archive_revision,'84:5678');
  } finally {dom.window.close();}
});

test('model selection closes the catalogue and survives delayed catalogue and archive replies',()=>{
  const {dom,w,emit,posted}=browser();
  try {
    emit({type:'archives',items:[{id:'a',title:'A'}]});
    w.document.querySelector('[data-archive-id="a"] .archive-open').click();
    w.document.getElementById('model-picker').click();
    emit({type:'models',selected:'auto-balanced',items:[
      {id:'auto-balanced',kind:'auto',selectable:true},
      {id:'gpt-oss-120b',kind:'chat',selectable:true,provider:'ovhcloud'}
    ]});
    const panel=w.document.getElementById('model-panel');
    assert.equal(panel.hidden,false);
    [...w.document.querySelectorAll('.model-item')].find(item=>item.textContent.includes('gpt-oss-120b')).click();
    assert.equal(panel.hidden,true);
    assert.equal(w.document.activeElement,w.document.getElementById('model-picker'));
    emit({type:'models',selected:'auto-balanced',items:[
      {id:'auto-balanced',kind:'auto',selectable:true},
      {id:'gpt-oss-120b',kind:'chat',selectable:true,provider:'ovhcloud'}
    ]});
    emit({type:'archive_loaded',id:'a',model:'auto-balanced',messages:[]});
    assert.equal(panel.hidden,true);
    assert.equal(w.document.getElementById('selected-model').textContent,'gpt-oss-120b');
    assert.match(w.document.querySelector('.model-item.selected').textContent,/gpt-oss-120b/);
    assert(posted.some(message=>message.type==='selectModel' && message.model==='gpt-oss-120b' && message.provider==='ovhcloud'));
    w.document.getElementById('model-picker').click();
    assert.equal(panel.hidden,false);
    assert.match(w.document.querySelector('.model-item.selected').textContent,/gpt-oss-120b/);
  } finally {dom.window.close();}
});

test('automatic model context remains descriptive until a provider limit is known',()=>{
  const {dom,w,emit}=browser();
  try {
    w.OvhI18n.setLanguage('fr');
    for(const context_length of [null,undefined,0,NaN]) {
      emit({type:'models',selected:'auto-balanced',items:[
        {id:'auto-balanced',kind:'auto',selectable:true,context_length}
      ]});
      const label=w.document.querySelector('.model-item .model-meta').textContent;
      assert.match(label,/Contexte selon le modèle choisi/);
      assert.doesNotMatch(label,/0k|NaN/);
    }
    emit({type:'models',selected:'auto-balanced',items:[
      {id:'auto-balanced',kind:'auto',selectable:true,context_length:262144},
      {id:'provider-model',kind:'chat',selectable:true,context_length:131072}
    ]});
    const labels=[...w.document.querySelectorAll('.model-item .model-meta')].map(node=>node.textContent);
    assert(labels.some(label=>label.includes('Jusqu’à 256k selon le modèle')));
    assert(labels.some(label=>label.includes('128k contexte')));
  } finally {dom.window.close();}
});


test('reopening interrupted conversation restores actions, partial text and resume control',()=>{
  const {dom,w,emit}=browser();
  try {
    emit({type:'archive_loaded',id:'interrupted',resumable:true,messages:[
      {type:'user',text:'Modifie le code'},
      {type:'model_active',model:'gpt-oss-120b',timestamp:'2026-10-18T20:00:00Z'},
      {type:'tool_start',id:'r1',title:'Lecture',detail:'app.py',timestamp:'2026-10-18T20:00:01Z'},
      {type:'tool_end',id:'r1',success:true,detail:'Code lu',duration_ms:18},
      {type:'partial_response',html:'<p>Analyse provisoire conservée</p>'}
    ]});
    assert.ok(w.document.querySelector('#messages').textContent.includes('Analyse provisoire conservée'));
    assert.ok(w.document.querySelector('#messages').textContent.includes('Code lu'));
    assert.equal(w.document.querySelectorAll('.message.working,.markdown.streaming').length,0);
    assert.ok(w.document.querySelector('.message.stopped'));
    assert.match(w.document.querySelector('#send').title,/Reprendre|Resume/i);
  } finally {dom.window.close();}
});

// A passing generic suite did not detect the later theme overrides in production.
test('header background stays transparent and its themed logo fully opaque', () => {
  const css = fs.readFileSync(path.resolve(__dirname, '../media/chat.css'), 'utf8');
  for (const theme of ['vscode-light', 'vscode-dark', 'vscode-high-contrast']) {
    const dom = new JSDOM(`<style>${css}</style><body class="${theme}"><header><span class="history-brand"><img class="header-app-logo header-app-logo-light" src="light.svg"><img class="header-app-logo header-app-logo-dark" src="dark.svg"></span></header></body>`);
    assert.equal(dom.window.getComputedStyle(dom.window.document.querySelector('header')).backgroundColor, 'rgba(0, 0, 0, 0)', theme);
    const light = dom.window.document.querySelector('.header-app-logo-light');
    const dark = dom.window.document.querySelector('.header-app-logo-dark');
    assert.equal(dom.window.getComputedStyle(light).display, theme === 'vscode-light' ? 'block' : 'none', `${theme}: light logo`);
    assert.equal(dom.window.getComputedStyle(dark).display, theme === 'vscode-light' ? 'none' : 'block', `${theme}: dark logo`);
    const logo = theme === 'vscode-light' ? light : dark;
    const computed = dom.window.getComputedStyle(logo);
    assert.equal(computed.width, '28px', theme);
    assert.equal(computed.height, '28px', theme);
    let opacity = 1;
    for (let node = logo; node; node = node.parentElement) {
      const value = dom.window.getComputedStyle(node).opacity;
      opacity *= value === '' ? 1 : Number(value);
    }
    assert.equal(opacity, 1, `${theme}: combined logo and ancestor opacity`);
    dom.window.close();
  }
});

// Folder color is sampled at click time, including a pending color edit.
test('archive loading uses folder color and clears it for ungrouped conversations',()=>{
  const {dom,w,emit}=browser();
  try {
    emit({type:'archives',items:[{id:'plain',title:'Plain'}]});
    const row=w.document.querySelector('[data-archive-id="plain"]');
    const group=w.document.createElement('section');group.className='archive-project';
    group.style.setProperty('--project-color','#e29136');
    row.parentNode.append(group);group.append(row);
    row.querySelector('.archive-open').click();
    const messages=w.document.getElementById('messages');
    assert.equal(messages.style.getPropertyValue('--archive-loading-color'),'#e29136');
    emit({type:'archive_open_failed',text:'failed'});
    assert.equal(messages.style.getPropertyValue('--archive-loading-color'),'');
    group.parentNode.append(row);
    row.querySelector('.archive-open').click();
    assert.equal(messages.style.getPropertyValue('--archive-loading-color'),'');
  } finally {dom.window.close();}
});

test('restoring a long action history saves and scrolls once without losing actions',()=>{
  const {dom,w,states}=browser();
  try {
    states.length=0;let layouts=0;
    const messages=w.document.getElementById('messages');
    Object.defineProperty(messages,'scrollHeight',{get(){layouts++;return 1000;}});
    const items=[{type:'user',text:'Keep all actions'}];
    for(let i=0;i<100;i++)items.push(
      {type:'tool_start',id:'restored-'+i,tool:'read_file',title:'Read '+i},
      {type:'tool_end',id:'restored-'+i,success:true,detail:'Result '+i});
    items.push({type:'response',text:'Completed',html:'<p>Completed</p>'});
    w.renderArchiveMessages(items);
    assert.equal(states.length,1);assert.equal(layouts,1);
    assert.equal(messages.querySelectorAll('.activity-row').length,100);
    assert.match(states[0].html,/Result 0/);assert.match(states[0].html,/Result 99/);
    assert.match(states[0].html,/Completed/);
  } finally {dom.window.close();}
});

for(const restoration of ['archive_loaded','snapshot_loaded'])
for(const restoreFirst of [false,true])
test(`selecting a model from history preserves navigation with ${restoration} ${restoreFirst?'before':'after'} selection`,()=>{
  const {dom,w,emit,posted}=browser();
  try {
    const doc=w.document,history=doc.getElementById('archive-panel'),catalogue=doc.getElementById('model-panel');
    assert.equal(history.hidden,false);
    doc.getElementById('model-picker').click();
    assert.equal(catalogue.hidden,false);
    emit({type:'models',selected:'auto-balanced',items:[
      {id:'auto-balanced',kind:'auto',selectable:true},
      {id:'chosen-model',kind:'chat',selectable:true,provider:'ovhcloud'}
    ]});
    const restore=()=>{
      emit({type:'ready',conversation:'previous',model:'old-model'});
      emit({type:restoration,automatic:true,id:'previous',model:'old-model',
        messages:[{type:'response',html:'<p>Previous conversation</p>'}],
        html:'<article class="message assistant"><p>Previous conversation</p></article>'});
    };
    if(restoreFirst)restore();
    [...doc.querySelectorAll('.model-item')].find(item=>item.textContent.includes('chosen-model')).click();
    assert.equal(catalogue.hidden,true);assert.equal(history.hidden,false);
    if(!restoreFirst)restore();
    assert.equal(doc.getElementById('selected-model').textContent,'chosen-model');
    emit({type:'model_selected',model:'chosen-model'});
    assert.equal(catalogue.hidden,true);assert.equal(history.hidden,false);
    assert.equal(doc.getElementById('selected-model').textContent,'chosen-model');
    assert.equal(posted.filter(message=>message.type==='selectModel').length,1);
    assert(!posted.some(message=>['loadArchive','openProject','resumeArchive','prompt'].includes(message.type)));
    doc.getElementById('model-picker').click();doc.getElementById('close-models').click();
    assert.equal(catalogue.hidden,true);assert.equal(history.hidden,false);
  } finally {dom.window.close();}
});

for (const restoration of ['archive_loaded','snapshot_loaded'])
for (const saved of [undefined,{model:'chosen-model'}])
test(`catalogue startup preserves the ${saved?'saved webview':'backend'} user model through ${restoration}`,()=>{
  const {dom,w,emit,posted,states}=browser(undefined,saved);
  try {
    const doc=w.document;
    doc.getElementById('model-picker').click();
    emit({type:'ready',model:saved?'old-model':'chosen-model',conversation:'old'});
    emit({type:restoration,automatic:true,id:'old',model:'old-model',
      html:'<p>Old conversation</p>',messages:[],timeline:[
        {type:'model_active',model:'old-model',selection:'old-model'}
      ]});
    emit({type:'models',selected:'old-model',items:[
      {id:'chosen-model',kind:'chat',selectable:true},
      {id:'old-model',kind:'chat',selectable:true}
    ]});
    assert.equal(doc.getElementById('selected-model').textContent,'chosen-model');
    assert.match(doc.querySelector('.model-item.selected').textContent,/chosen-model/);
    doc.getElementById('refresh-models').click();
    emit({type:'models',selected:'auto-balanced',items:[]});
    assert.equal(doc.getElementById('selected-model').textContent,'chosen-model');
    assert(!posted.some(message=>message.type==='selectModel'));
    assert.equal(states.at(-1).model,'chosen-model');
    doc.getElementById('close-models').click();
    const prompt=doc.getElementById('prompt');prompt.value='Continue with my model';
    doc.getElementById('send').click();
    assert.equal(posted.find(message=>message.type==='prompt').model,'chosen-model');
  } finally {dom.window.close();}
});

test('the latest explicit choice survives provider restart, stale acknowledgements and webview reload',()=>{
  const first=browser();let reloaded;
  try {
    const {w,emit,posted,states}=first,doc=w.document;
    emit({type:'models',items:[
      {id:'first-model',kind:'chat',selectable:true,provider:'ovhcloud'},
      {id:'last-model',kind:'chat',selectable:true,provider:'deepseek'}
    ]});
    for(const model of ['first-model','last-model']) {
      doc.getElementById('model-picker').click();
      [...doc.querySelectorAll('.model-item')].find(item=>item.textContent.includes(model)).click();
    }
    emit({type:'provider_switched',provider:'deepseek'});
    emit({type:'ready',model:'provider-default',conversation:'previous'});
    emit({type:'model_selected',model:'first-model'});
    emit({type:'model_changed',from_model:'auto-balanced',to_model:'first-model'});
    emit({type:'model_active',model:'first-model',selection:'first-model'});
    emit({type:'models',selected:'provider-default',items:[]});
    assert.equal(doc.getElementById('selected-model').textContent,'last-model');
    emit({type:'model_selected',model:'last-model'});
    assert.equal(states.at(-1).model,'last-model');
    assert.deepEqual(posted.filter(message=>message.type==='selectModel').map(message=>message.model),['first-model','last-model']);
    reloaded=browser(undefined,states.at(-1));
    reloaded.emit({type:'ready',model:'first-model',conversation:'previous'});
    reloaded.emit({type:'models',selected:'first-model',items:[]});
    assert.equal(reloaded.w.document.getElementById('selected-model').textContent,'last-model');
  } finally {first.dom.window.close();reloaded?.dom.window.close();}
});

test('a model selected while an archive loads is persisted before the archive reply',()=>{
  const saved={html:'<p>Saved conversation</p>',model:'old-model'};
  const {dom,w,emit,states,posted}=browser(undefined,saved);
  try {
    const doc=w.document;
    emit({type:'archives',items:[{id:'slow',title:'Loading conversation'}]});
    doc.querySelector('[data-archive-id="slow"] .archive-open').click();
    doc.getElementById('model-picker').click();
    emit({type:'models',items:[{id:'chosen-model',kind:'chat',selectable:true}]});
    doc.querySelector('.model-item').click();
    assert.equal(states.at(-1).model,'chosen-model');
    assert.equal(states.at(-1).hasModelPreference,true);
    assert.equal(states.at(-1).html,saved.html,'the loading placeholder must not replace saved conversation content');
    assert(!posted.some(message=>message.type==='snapshot'));
    emit({type:'snapshot_loaded',id:'slow',html:'<p>Archive loaded</p>',model:'old-model'});
    assert.equal(doc.getElementById('selected-model').textContent,'chosen-model');
    assert.equal(states.at(-1).model,'chosen-model');
  } finally {dom.window.close();}
});

test('snapshot restoration saves state once and preserves all sanitized content',()=>{
 const {dom,w,states}=browser();
 try {
  states.length=0;
  w.applySnapshot({html:'<article class="message assistant"><div class="content markdown"><p>Saved answer</p><script>bad()</script></div></article>'});
  assert.equal(states.length,1);
  assert.match(states[0].html,/Saved answer/);assert.doesNotMatch(states[0].html,/<script>/);
 } finally {dom.window.close();}
});
