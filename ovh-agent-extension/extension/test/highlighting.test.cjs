const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const {JSDOM} = require('jsdom');

function browser() {
  const root = path.resolve(__dirname, '..');
  const nativeRequire = createRequire(path.join(root, 'extension.js'));
  const vscode = {workspace:{workspaceFolders:[{uri:{fsPath:root}}]}, Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)}, env:{language:'fr'}};
  const scope = {require:name=>name==='vscode'?vscode:nativeRequire(name), module:{exports:{}}, process, console, setTimeout, clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports={Provider:OvhAgentViewProvider,renderMarkdown};',scope);
  const provider = new scope.module.exports.Provider(root, {});
  const dom = new JSDOM(provider.getHtml({asWebviewUri:x=>x,cspSource:'local'}), {runScripts:'outside-only',url:'https://test.invalid'});
  const w = dom.window;
  w.acquireVsCodeApi=()=>({getState:()=>undefined,setState:()=>{},postMessage:()=>{}});
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.OVH_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
  for(const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','highlight.min.js','chat.js']) w.eval(fs.readFileSync(path.join(root,'media',name),'utf8'));
  return {dom, w, render:scope.module.exports.renderMarkdown,
    emit:data=>w.dispatchEvent(new w.MessageEvent('message',{data}))};
}

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
      emit({type:'response',html:render('✅ Bon ➡️ Suite 🔎 Recherche — suite\n\n`const value = "—🔎";`')});
      const prose=w.document.querySelector('.message.assistant:last-child .markdown p');
      assert(prose.textContent.includes('- suite'));
      assert.equal(prose.textContent.includes('✅'),mode>0);
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
    content.className='markdown';
    content.innerHTML='<div class="diff-line added"><code>const x = 1;</code></div><div class="diff-line removed"><code>const x = 0;</code></div>';
    w.document.body.append(content);
    for (const line of content.children) {
      assert.notEqual(w.getComputedStyle(line).backgroundColor,'rgba(0, 0, 0, 0)');
      assert.equal(w.getComputedStyle(line.querySelector('code')).backgroundColor,'rgba(0, 0, 0, 0)');
    }
  } finally {dom.window.close();}
});
