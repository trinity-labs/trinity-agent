const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const {JSDOM}=require('jsdom');
const {validMessage}=require('../security');
const root=path.resolve(__dirname,'..');
function provider() {
  const native=createRequire(path.join(root,'extension.js'));
  const vscode={workspace:{workspaceFolders:[{uri:{fsPath:root}}]},Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)},env:{language:'fr'}};
  const sandbox={require:n=>n==='vscode'?vscode:native(n),module:{exports:{}},process:{env:{...process.env,OVH_AGENT_LANG:'fr'}},console,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider;',sandbox);
  return new sandbox.module.exports.Provider(root,{});
}
function setup(t) {
  const dom=new JSDOM(provider().getHtml({asWebviewUri:x=>x,cspSource:'local'}),{runScripts:'outside-only',pretendToBeVisual:true,url:'https://test.invalid'});
  t.after(()=>dom.window.close());
  const w=dom.window,doc=w.document,sent=[];
  w.acquireVsCodeApi=()=>({getState:()=>undefined,setState:()=>{},postMessage:m=>sent.push(JSON.parse(JSON.stringify(m)))});
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.OVH_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
  w.Image=class {set src(value){this.naturalWidth=640;this.naturalHeight=320;setTimeout(()=>this.onload(),5);}};
  for(const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','chat.js'])w.eval(fs.readFileSync(path.join(root,'media',name),'utf8'));
  const input=doc.getElementById('prompt');
  const file=()=>new w.File(['png-data'],'capture.png',{type:'image/png'});
  function paste(data){const e=new w.Event('paste',{bubbles:true,cancelable:true});Object.defineProperty(e,'clipboardData',{value:data});input.dispatchEvent(e);return e;}
  const settle=()=>new Promise(resolve=>setTimeout(resolve,150));
  return {w,doc,sent,input,file,paste,settle};
}
test('screenshot DataTransferItems preserve accompanying text',async t=>{
  const {doc,input,file,paste,settle}=setup(t);
  input.value='avant après';input.setSelectionRange(6,6);
  const e=paste({files:[],items:[{kind:'file',type:'image/png',getAsFile:file}],getData:()=> 'capture '});
  assert(e.defaultPrevented);await settle();assert.equal(input.value,'avant capture après');
  assert.equal(doc.querySelectorAll('.attachment-preview').length,1);
});
test('plain text and null clipboard files retain native behavior',t=>{
  const {paste}=setup(t);
  assert(!paste({files:[],items:[],getData:()=> 'texte'}).defaultPrevented);
  assert(!paste({files:[],items:[{kind:'file',type:'image/png',getAsFile:()=>null}]}).defaultPrevented);
});
test('files/items dual representation does not duplicate screenshots',async t=>{
  const {doc,file,paste,settle}=setup(t);const image=file();
  paste({files:[image],items:[{kind:'file',type:'image/png',getAsFile:()=>image}]});await settle();
  assert.equal(doc.querySelectorAll('.attachment-preview').length,1);
});
test('concurrent pastes respect limit and cannot send during decoding',async t=>{
  const {doc,input,sent,file,paste,settle}=setup(t);input.value='Analyse';
  for(let i=0;i<7;i++)paste({files:[file()]});
  doc.getElementById('send').click();assert(!sent.some(m=>m.type==='prompt'));assert.equal(input.value,'Analyse');
  await settle();await settle();assert.equal(doc.querySelectorAll('.attachment-preview').length,5);
});
