const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const {spawn} = require('node:child_process');
const {once, EventEmitter} = require('node:events');
const {acquireWindowLock, releaseWindowLock} = require('../window-lock');

function providerType(root) {
  const nativeRequire = createRequire(path.join(root, 'extension.js'));
  const vscode = {workspace:{workspaceFolders:[{uri:{fsPath:root}}]},Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)},env:{language:'fr'}};
  // The lifecycle fixture owns its workspace root. Do not depend on the
  // deployment-specific /home/coder directory existing on a CI runner.
  const stores = nativeRequire('./project-store');
  const projectStore = {...stores, ProjectStore:class extends stores.ProjectStore {
    constructor(dataRoot) { super(dataRoot, root); }
  }};
  const sandbox = {require:n=>n==='vscode'?vscode:n==='./project-store'?projectStore:nativeRequire(n),module:{exports:{}},process:{env:{...process.env,OVH_AGENT_LANG:'fr'}},console,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider;',sandbox);
  return sandbox.module.exports.Provider;
}

test('window ownership spans processes and is released after a crash', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'window-owner-'));
  let child;
  try {
    const code = `const {acquireWindowLock}=require(process.argv[1]); acquireWindowLock(process.argv[2]).then(lock=>{process.stdout.write(lock?'owned':'blocked');setInterval(()=>{},1000);});`;
    child = spawn(process.execPath, ['-e',code,require.resolve('../window-lock'),root], {stdio:['ignore','pipe','pipe']});
    const [message] = await once(child.stdout, 'data');
    assert.equal(message.toString(),'owned');
    assert.equal(await acquireWindowLock(root),null);
    const exited=once(child,'exit');
    child.kill('SIGKILL');
    await exited;
    const recovered=await acquireWindowLock(root);
    assert(recovered);
    await releaseWindowLock(recovered);
  } finally {
    child?.kill();
    fs.rmSync(root,{recursive:true,force:true});
  }
});

test('second window cannot dispatch actions and can retry after owner closes', async () => {
  const root=path.resolve(__dirname,'..'), temp=fs.mkdtempSync(path.join(os.tmpdir(),'agent-windows-'));
  const Provider=providerType(root), first=new Provider(root,{}), second=new Provider(root,{});
  first.store.dataRoot=second.store.dataRoot=temp;
  function view() {
    const messages=[];
    const item={webview:{asWebviewUri:x=>x,cspSource:'local',postMessage:m=>messages.push(m),onDidReceiveMessage:fn=>{item.receive=fn;}}};
    item.onDidDispose=fn=>{item.close=fn;};
    return item;
  }
  const a=view(),b=view();
  let dispatched=0;
  second.sendPrompt=async()=>{dispatched++;};
  try {
    await first.resolveWebviewView(a);
    await second.resolveWebviewView(b);
    assert(first.windowLock);
    assert.equal(second.windowLock,null);
    assert.match(b.webview.html,/Agent déjà ouvert/);
    await b.receive({type:'prompt',text:'must not run'});
    assert.equal(dispatched,0);
    assert.equal(await second.ensureAgent(),false);
    a.close();
    await first.windowClosing;
    await b.receive({type:'retryWindow'});
    assert(second.windowLock);
    assert.match(b.webview.html,/id="prompt"/);
    await b.receive({type:'prompt',text:'now allowed'});
    assert.equal(dispatched,1);
  } finally {
    await first.closeWindow(); await second.closeWindow();
    fs.rmSync(temp,{recursive:true,force:true});
  }
});

test('new conversation waits for old backend to close before restarting', async t => {
  const root=path.resolve(__dirname,'..'), Provider=providerType(root), provider=new Provider(root,{});
  const data=fs.mkdtempSync(path.join(os.tmpdir(),'new-conversation-'));
  t.after(()=>fs.rmSync(data,{recursive:true,force:true}));provider.store.dataRoot=data;
  const processStub=new EventEmitter(), calls=[];
  Object.assign(processStub,{exitCode:null,signalCode:null,kill:signal=>{calls.push(signal);}});
  provider.agent=processStub;
  provider.ensureAgent=async()=>{
    calls.push('start');
    provider.agent={stdin:{write:line=>calls.push(JSON.parse(line).type)}};
    return true;
  };
  const clearing=provider.clearConversation();
  await Promise.resolve();
  assert.deepEqual(calls,['SIGTERM']);
  processStub.emit('close');
  await clearing;
  assert.deepEqual(calls,['SIGTERM','start','clear']);
  provider.agent=undefined;
});
