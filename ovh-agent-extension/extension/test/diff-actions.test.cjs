const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const {JSDOM} = require('jsdom');

function browser(saved) {
  const root = path.resolve(__dirname, '..');
  const nativeRequire = createRequire(path.join(root, 'extension.js'));
  const vscode = {workspace:{workspaceFolders:[{uri:{fsPath:root}}]}, Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)}, env:{language:'fr'}};
  const scope = {require:name=>name==='vscode'?vscode:nativeRequire(name), module:{exports:{}}, process, console, setTimeout, clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports=OvhAgentViewProvider;',scope);
  const provider = new scope.module.exports(root, {});
  const dom = new JSDOM(provider.getHtml({asWebviewUri:x=>x,cspSource:'local'}), {runScripts:'outside-only',url:'https://test.invalid'});
  const w = dom.window;
  w.acquireVsCodeApi=()=>({getState:()=>saved,setState:()=>{},postMessage:()=>{}});
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.OVH_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
  for(const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','highlight.min.js','chat.js']) w.eval(fs.readFileSync(path.join(root,'media',name),'utf8'));
  return {dom,w,emit:data=>w.dispatchEvent(new w.MessageEvent('message',{data}))};
}

test('command and result survive success, failure, event replay and legacy snapshots without becoming HTML',()=>{
  const {dom,w,emit}=browser();
  try {
    for(const success of [true,false]) {
      const id=success?'success':'failure';
      const detail='cwd=/tmp\nprintf \'<img src=x onerror=alert(1)>\\n\'';
      const start={type:'tool_start',tool:'command',id,title:'Commande dans le conteneur IDE',detail};
      const end={type:'tool_end',id,success,detail:`code de sortie ${success?0:1}\nRésultat <script>littéral</script>`,duration_ms:21};
      const check=()=>{
        const row=w.document.querySelector(`[data-activity-id="${id}"]`);
        assert(row.textContent.includes(detail),'retain exact cwd and command');
        assert(row.textContent.includes(end.detail),'retain exact execution result');
        assert.equal(row.querySelector('img,script'),null);
        assert(row.classList.contains(success?'success':'failure'));
      };
      emit(start);emit(end);check();
      const html=w.document.querySelector('#messages').innerHTML;
      emit({type:'snapshot_loaded',id:'snapshot',html,timeline:[start,end]});check();
      emit({type:'archive_loaded',id:'events',messages:[{type:'user',text:'Vérifie'},start,end,{type:'response',html:'<p>Fin</p>'}]});check();
      // Old snapshots only stored the result: recover the recorded command from the timeline.
      const legacy=`<article class="message assistant"><details class="activity"><summary>Actions</summary><div class="activity-list"><div class="activity-row ${success?'success':'failure'}" data-activity-id="${id}"><span class="activity-icon"></span><div><strong>Commande dans le conteneur IDE</strong><code></code></div></div></div></details></article>`;
      const holder=w.document.createElement('div');holder.innerHTML=legacy;holder.querySelector('code').textContent=end.detail;
      emit({type:'snapshot_loaded',id:'old',html:holder.innerHTML,timeline:[start,end]});check();
    }
    emit({type:'tool_start',tool:'read',id:'read',title:'Lecture',detail:'example.js'});
    emit({type:'tool_end',id:'read',success:true,detail:'Contenu lu'});
    const read=w.document.querySelector('[data-activity-id="read"]');
    assert.equal(read.querySelectorAll('code').length,1);
    assert.equal(read.querySelector('code').textContent,'Contenu lu');
  } finally {dom.window.close();}
});

test('diff highlighting keeps multiline tokens, source, line numbers and copied patch through restoration',async()=>{
  const {dom,w,emit}=browser();
  try {
    const diff='--- a/example.js\n+++ b/example.js\n@@ -1,4 +1,4 @@\n /* comment\n-old value\n+new value\n */\n-const html = "<img src=x onerror=alert(1)>";\n+const html = "<script>literal</script>";\n@@ -10,2 +10,2 @@\n---counter;\n+++counter;\n const value = 42;\n';
    const data={type:'file_change',path:'example.js',diff,added:3,removed:3,action:'modified'};
    const expected=['/* comment','old value','new value','*/','const html = "<img src=x onerror=alert(1)>";','const html = "<script>literal</script>";','--counter;','++counter;','const value = 42;'];
    const check=()=>{
      const change=w.document.querySelector('.file-change');
      const codes=Array.from(change.querySelectorAll('.diff-line:not(.hunk) > code'));
      assert.deepEqual(codes.map(c=>c.textContent),expected);
      assert(change.querySelector('.hljs-keyword'),'JavaScript is highlighted');
      assert.equal(codes[1].querySelector('.hljs-comment')?.textContent,'old value');
      assert.equal(codes[2].querySelector('.hljs-comment')?.textContent,'new value');
      assert.equal(change.querySelector('img,script'),null);
      const counter=codes.find(c=>c.textContent==='++counter;').parentElement;
      assert.equal(counter.querySelectorAll('.diff-number')[1].textContent,'10');
      return change;
    };
    emit(data);let change=check();
    let copied;Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
    change.querySelector('.copy-diff').click();await Promise.resolve();assert.equal(copied,diff);
    // Simulate a snapshot from before syntax colours were implemented.
    for(const code of change.querySelectorAll('.diff-line > code')) {code.textContent=code.textContent;code.removeAttribute('data-highlighted');}
    const html=w.document.querySelector('#messages').innerHTML;
    emit({type:'snapshot_loaded',id:'diff-snapshot',html,timeline:[data]});change=check();
    change.querySelector('.copy-diff').click();await Promise.resolve();assert.equal(copied,diff);
    // Legacy raw attributes containing literal HTML can be removed by DOMPurify.
    // The authoritative event timeline still contains the exact patch.
    change.dataset.diff=diff;
    const legacyHtml=w.document.querySelector('#messages').innerHTML;
    const localLegacy=browser({html:legacyHtml});
    try {
      assert.equal(localLegacy.w.document.querySelectorAll('.diff-line:not(.hunk)').length,expected.length,'keep visible legacy rows until the backend restores the patch');
      assert.equal(localLegacy.w.document.querySelector('.copy-diff').disabled,true,'a missing patch cannot be copied as an empty success');
    } finally {localLegacy.dom.window.close();}
    emit({type:'snapshot_loaded',id:'legacy-diff',html:legacyHtml,timeline:[data]});change=check();
    change.querySelector('.copy-diff').click();await Promise.resolve();assert.equal(copied,diff);
    emit({type:'archive_loaded',id:'diff-events',messages:[{type:'user',text:'Modifie'},data]});check();
    const restored=browser({html});
    try {assert(restored.w.document.querySelector('.file-change .hljs-keyword'),'webview state is upgraded too');}
    finally {restored.dom.window.close();}
  } finally {dom.window.close();}
});

test('approval diffs use the same syntax renderer and upgrade old snapshots',()=>{
  const {dom,w,emit}=browser();
  try {
    const diff='--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-return 1\n+return 42\n';
    emit({type:'approval',id:'approval-diff',title:'Modifier le fichier',detail:'example.py',diff});
    const check=()=>{
      const card=w.document.querySelector('.approval-card');
      assert.equal(card.querySelector('.diff-line.added code').textContent,'return 42');
      assert(card.querySelector('.hljs-keyword'));
      return card;
    };
    check();
    emit({type:'snapshot_loaded',id:'approval-snapshot',html:w.document.querySelector('#messages').innerHTML});
    const card=check(),legacy=w.document.createElement('pre');legacy.className='diff';legacy.textContent=diff;
    card.querySelector('.approval-diff').replaceWith(legacy);
    emit({type:'snapshot_loaded',id:'legacy-approval',html:w.document.querySelector('#messages').innerHTML});check();
  } finally {dom.window.close();}
});

test('large diffs and missing or failing highlighters keep literal code readable',()=>{
  const {dom,w,emit}=browser();
  try {
    const source='x'.repeat(100001)+'<script>literal</script>';
    const data={type:'file_change',path:'large.js',diff:'--- a/large.js\n+++ b/large.js\n@@ -0,0 +1 @@\n+'+source,added:1,removed:0,action:'created'};
    const original=w.hljs.highlight;
    let calls=0;w.hljs.highlight=(...args)=>{calls++;return original(...args);};
    emit(data);
    assert.equal(calls,0,'do not tokenize an oversized diff');
    assert.equal(w.document.querySelector('.diff-line.added code').textContent,source);
    for(const broken of [null,{getLanguage:()=>true,highlight:()=>{throw new Error('grammar failed');}}]) {
      w.hljs=broken;
      emit({...data,path:'small.js',diff:'@@ -0,0 +1 @@\n+const text = "<script>literal</script>";'});
      const code=Array.from(w.document.querySelectorAll('.diff-line.added code')).at(-1);
      assert.equal(code.textContent,'const text = "<script>literal</script>";');
      assert.equal(code.querySelector('script'),null);
    }
  } finally {dom.window.close();}
});
