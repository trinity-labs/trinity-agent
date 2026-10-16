const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const {JSDOM} = require('jsdom');
const {validMessage} = require('../security');

test('follow-up bridge restricts conversation, actions, mode, lengths and access', () => {
  const message = {type:'followup', action:'add', conversation:'test', id:'one', text:'Adjust task', mode:'steer', fullAccess:false};
  assert(validMessage(message));
  for (const changes of [{conversation:'../x'}, {action:'execute'}, {mode:'shell'}, {text:' '}, {text:'x'.repeat(16001)}, {fullAccess:'true'}, {id:'../x'}]) assert(!validMessage({...message,...changes}));
});

test('composer queues/steers without duplicate runs, edits safely and renders text without HTML', () => {
  const root = path.resolve(__dirname, '..');
  const nativeRequire = createRequire(path.join(root, 'extension.js'));
  const vscode = {workspace:{workspaceFolders:[{uri:{fsPath:root}}]}, Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)},env:{language:'fr'}};
  const sandbox = {require:n=>n==='vscode'?vscode:nativeRequire(n), module:{exports:{}},process:{env:{...process.env,OVH_AGENT_LANG:'fr'}}, console,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider;',sandbox);
  const provider = new sandbox.module.exports.Provider(root, {});
  const dom = new JSDOM(provider.getHtml({asWebviewUri:x=>x,cspSource:'local'}), {runScripts:'outside-only',url:'https://test.invalid'});
  try {
    const w=dom.window, doc=w.document, sent=[];
    w.acquireVsCodeApi=()=>({getState:()=>undefined,setState:()=>{},postMessage:m=>sent.push(m)});
    w.HTMLElement.prototype.scrollIntoView=function(){};
    w.OVH_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
    for(const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','chat.js']) w.eval(fs.readFileSync(path.join(root,'media',name),'utf8'));
    const emit=data=>w.dispatchEvent(new w.MessageEvent('message',{data}));
    const prompt=doc.getElementById('prompt'), send=doc.getElementById('send'), mode=doc.getElementById('followup-mode');
    emit({type:'status',conversation:'test',text:'Working'});
    assert(!send.disabled);assert(!mode.hidden);
    prompt.value='Next task';send.click();
    let command=sent.at(-1);
    assert.equal(command.type,'followup');assert.equal(command.action,'add');assert.equal(command.mode,'queue');
    assert.equal(prompt.value,'Next task');assert(send.disabled);
    send.click();assert.equal(sent.at(-1),command);
    emit({type:'followup_ack',id:command.id,action:'add'});
    assert.equal(prompt.value,'');assert(!send.disabled);
    mode.value='steer';prompt.value='New direction';send.click();command=sent.at(-1);
    assert.equal(command.mode,'steer');
    prompt.value='Do not lose this newer draft';
    emit({type:'followup_ack',id:command.id,action:'add'});
    assert.equal(prompt.value,'Do not lose this newer draft');
    emit({type:'followups',conversation:'test',items:[{id:'queued',text:'<img onerror=alert(1)>',mode:'queue'}]});
    const box=doc.getElementById('followups');
    assert.equal(box.querySelector('img'),null);assert(box.textContent.includes('<img'));
    prompt.value='';box.querySelector('[title="Modifier"]').click();
    assert.equal(prompt.value,'<img onerror=alert(1)>');
    prompt.value='Edited';send.click();command=sent.at(-1);
    assert.equal(command.action,'edit');assert.equal(command.id,'queued');
    emit({type:'followup_error',id:command.id,text:'Message already started or removed'});
    assert.equal(prompt.value,'Edited');assert(!send.disabled);
    box.querySelector('[title="Retirer"]').click();assert.equal(sent.at(-1).action,'remove');
    doc.getElementById('stop').click();assert.equal(sent.at(-1).type,'cancel');
    emit({type:'idle'});
    assert.equal(mode.hidden,true);
    assert(box.textContent.includes('Exécuter la file d’attente'));
  } finally {dom.window.close();}
});
