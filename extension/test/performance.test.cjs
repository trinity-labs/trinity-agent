const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const {EventEmitter}=require('node:events');
const {JSDOM}=require('jsdom');
const {validMessage}=require('../security');
const root=path.resolve(__dirname,'..');
function setup(settings={},env={}) {
  const native=createRequire(path.join(root,'extension.js')),updates=[],spawned=[],sent=[],executed=[];
  const config={get:key=>settings[key],inspect:key=>({globalValue:settings[key]}),update:async(key,value)=>{updates.push({key,value});settings[key]=value;}};
  const vscode={commands:{executeCommand:name=>executed.push(name)},workspace:{workspaceFolders:[{uri:{fsPath:root}}],getConfiguration:()=>config},Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)},env:{language:'fr'},ConfigurationTarget:{Global:1}};
  const spawn=(command,args,options)=>{
    spawned.push({command,args,mode:options.env.TRINITY_AGENT_PERFORMANCE_MODE,env:options.env});
    const process=new EventEmitter();
    for(const name of ['stdout','stderr']){process[name]=new EventEmitter();process[name].setEncoding=()=>{};}
    process.stdin={writable:true,write:line=>sent.push(JSON.parse(line))};
    return process;
  };
  const sandbox={require:name=>name==='vscode'?vscode:name==='child_process'?{...native(name),spawn}:native(name),module:{exports:{}},process:{env:{TRINITY_AGENT_LANG:'fr',...env}},console,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports.Provider=TrinityAgentViewProvider; readServerSecret=()=>"test-only-key";',sandbox);
  const provider=new sandbox.module.exports.Provider(root,{});
  const posted=[];provider.post=value=>posted.push(JSON.parse(JSON.stringify(value)));
  return {provider,posted,config,settings,updates,spawned,sent,executed};
}

test('performance bridge accepts only Auto/Max',()=>{
  for(const mode of ['auto','max'])assert(validMessage({type:'setPerformance',mode}));
  for(const mode of ['unlimited','../max','MAX','',null,{},['max']])assert(!validMessage({type:'setPerformance',mode}));
});

test('performance choice persists without starting an idle backend and updates a running backend',async()=>{
  const {provider,posted,settings,updates,spawned,sent,config}=setup();
  assert.equal(provider.performanceMode(),'auto');
  await provider.setPerformance('max');
  assert.equal(settings.performanceMode,'max');assert.equal(spawned.length,0);
  assert.deepEqual(posted.at(-1),{type:'performance_status',mode:'max'});
  provider.agent={provider:"ovhcloud",stdin:{writable:true,write:line=>sent.push(JSON.parse(line))}};provider.busy=true;
  await provider.setPerformance('auto');
  assert.deepEqual(sent,[{type:'set_performance',mode:'auto'}]);assert.equal(provider.busy,true);
  config.update=async()=>{throw Error('Cannot save preferences');};
  await provider.setPerformance('max');
  assert.equal(provider.performanceMode(),'auto');assert.equal(sent.length,1);
  assert.deepEqual(posted.at(-1),{type:'performance_error',mode:'auto',text:'Cannot save preferences'});
  assert.equal(updates.length,2);
});

test('launch inherits performance setting over environment and invalid settings fall back to Auto',async()=>{
  for(const [mode,environment,expected] of [[undefined,'max','max'],['auto','max','auto'],['max','auto','max'],['invalid','max','auto']]){
    const {provider,spawned}=setup({backendPath:path.join(root,'extension.js'),performanceMode:mode},{TRINITY_AGENT_PERFORMANCE_MODE:environment});
    provider.windowLock={};provider.billingCredentials=async()=>undefined;
    assert.equal(await provider.startAgent(),true);
    assert.equal(spawned[0].mode,expected);
    assert.equal(spawned.length,1);
  }
});

test('DeepSeek launch selects its key and skips OVH billing credentials',async()=>{
  const {provider,spawned}=setup({backendPath:path.join(root,'extension.js'),provider:'deepseek'});
  provider.windowLock={};provider.billingCredentials=async()=>{throw Error('OVH billing must not be requested');};
  assert.equal(await provider.startAgent(),true);
  assert.equal(spawned[0].env.TRINITY_AGENT_PROVIDER,'deepseek');
  assert.equal(spawned[0].env.TRINITY_DEEPSEEK_API_KEY,'test-only-key');
  assert.equal(spawned[0].env.TRINITY_OVHCLOUD_API_KEY,undefined);
  assert.equal(spawned[0].env.TRINITY_OVHCLOUD_API_KEY,undefined);
});

test('chat selector displays actual resource profile, waits for acknowledgement and recovers from errors',t=>{
  const {provider}=setup({performanceMode:'max'});
  const dom=new JSDOM(provider.getHtml({asWebviewUri:x=>x,cspSource:'local'}),{runScripts:'outside-only',url:'https://test.invalid'});
  t.after(()=>dom.window.close());
  const w=dom.window,doc=w.document,sent=[];
  w.acquireVsCodeApi=()=>({getState:()=>undefined,setState:()=>{},postMessage:m=>sent.push(JSON.parse(JSON.stringify(m)))});
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.TRINITY_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
  for(const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','chat.js'])w.eval(fs.readFileSync(path.join(root,'media',name),'utf8'));
  const emit=data=>w.dispatchEvent(new w.MessageEvent('message',{data}));
  const picker=doc.getElementById('performance-mode');assert.equal(picker.value,'max');
  emit({type:'performance_status',mode:'max',workers:24,cache_mb:10000});
  assert.match(picker.title,/24 lecteurs.*10000 Mio/);
  picker.value='auto';picker.dispatchEvent(new w.Event('change'));
  assert(picker.disabled);assert.deepEqual(sent.at(-1),{type:'setPerformance',mode:'auto'});
  emit({type:'performance_error',mode:'max',text:'<img onerror=alert(1)>'});
  assert(!picker.disabled);assert.equal(picker.value,'max');assert.equal(doc.querySelector('#status img'),null);
  emit({type:'performance_status',mode:'auto',workers:4,cache_mb:256});assert.equal(picker.value,'auto');
  emit({type:'ready',model:'test',conversation:'test',resources:{mode:'max',workers:2,cache_mb:128}});
  assert.equal(picker.value,'max');assert.match(picker.title,/2 lecteurs.*128 Mio/);
  picker.value='auto';picker.dispatchEvent(new w.Event('change'));emit({type:'error',text:'Backend stopped'});
  assert(!picker.disabled);
});

test('backend process failures release host busy state as well as the webview',async()=>{
  for(const event of ['error','exit']) {
    const {provider,posted}=setup({backendPath:path.join(root,'extension.js')});
    provider.windowLock={};provider.billingCredentials=async()=>undefined;
    await provider.startAgent();
    const child=provider.agent;
    provider.busy=true;provider.clearingConversation=true;
    child.emit(event,event==='error'?new Error('Simulated backend failure'):1);
    assert.equal(provider.busy,false,event);
    assert.equal(provider.clearingConversation,false,event);
    assert.equal(provider.agent,undefined,event);
    assert(posted.some(message=>message.type==='error'),event);
  }
});

test('stream bursts coalesce and flush before terminal events without losing text',async()=>{
  const {provider,posted}=setup();
  provider.consumeOutput(Array.from({length:100},()=>JSON.stringify({type:'delta',text:'word '})).join('\n')+'\n');
  assert.equal(posted.length,0);
  await new Promise(resolve=>setTimeout(resolve,70));
  assert.equal(posted.length,1);
  assert.equal(posted[0].html.match(/word/g).length,100);
  provider.consumeOutput('{"type":"delta","text":"end"}\n{"type":"idle"}\n');
  assert.equal(posted.at(-2).type,'delta');assert.match(posted.at(-2).html,/end/);
  assert.equal(posted.at(-1).type,'idle');
  await new Promise(resolve=>setTimeout(resolve,70));assert.equal(posted.length,3);
});

test('webview asset URLs remain cacheable while CSP nonces change',()=>{
  const {provider}=setup();const webview={asWebviewUri:x=>x,cspSource:'local'};
  const a=provider.getHtml(webview),b=provider.getHtml(webview);
  assert.equal(a.match(/chat(?:\.min)?\.js\?build=([^" ]+)/)[1],b.match(/chat(?:\.min)?\.js\?build=([^" ]+)/)[1]);
  assert.notEqual(a.match(/nonce="([^"]+)"/)[1],b.match(/nonce="([^"]+)"/)[1]);
});

test('malformed backend frames do not crash or flood diagnostic output',()=>{
  const {provider,posted}=setup();
  provider.consumeOutput('null\n[]\n42\n'+ 'x'.repeat(10000)+'\n{"type":"idle"}\n');
  assert.equal(posted[0].text.length,4096);assert.equal(posted.at(-1).type,'idle');
});

test('oversized unterminated backend frame is dropped and framing recovers',()=>{
  const {provider,posted}=setup();
  provider.consumeOutput('x'.repeat(64*1024*1024+1));
  assert.equal(provider.decoder.bufferedChars,0);assert.equal(provider.decoder.discarding,true);
  provider.consumeOutput('tail');assert.equal(provider.decoder.bufferedChars,0);
  provider.consumeOutput('\n{"type":"idle"}\n');
  assert.equal(provider.decoder.discarding,false);assert.equal(posted.at(-1).type,'idle');
});

test('final response replaces pending delta instead of rendering twice',()=>{
  const {provider,posted}=setup();let renders=0;
  provider.renderMarkdown=text=>{renders++;return text;};
  provider.consumeOutput('{"type":"delta","text":"complete"}\n{"type":"response","text":"complete"}\n');
  assert.equal(renders,1);assert.equal(posted.length,1);assert.equal(posted[0].html,'complete');
});

test('billing uses the existing backend, coalesces polls and does not flush streaming',async()=>{
  const {provider,posted,spawned}=setup();const sent=[];
  provider.agent={provider:"ovhcloud",stdin:{writable:true,write:line=>sent.push(JSON.parse(line))}};
  provider.billingCredentials=()=>assert.fail('running backend already owns credentials');
  provider.consumeOutput('{"type":"delta","text":"stream"}\n');
  const a=provider.refreshBilling(),b=provider.refreshBilling();
  await Promise.resolve();
  assert.equal(a,b);assert.equal(sent.length,1);assert.equal(spawned.length,0);assert.equal(sent[0].refresh,false);
  provider.consumeOutput(JSON.stringify({type:'billing_status',request_id:sent[0].request_id,configured:true,available:true})+'\n');
  await a;
  assert.equal(posted.filter(p=>p.type==='delta').length,0);
  assert.equal(posted.filter(p=>p.type==='billing_status'&&p.available).length,1);
  provider.flushDelta();
  const manual=provider.refreshBilling(true);await Promise.resolve();assert.equal(sent[1].refresh,true);
  provider.consumeOutput(JSON.stringify({type:'billing_status',request_id:sent[1].request_id,configured:true,available:true})+'\n');
  await manual;
});

test('archive notifications coalesce without retaining stale directory results',async()=>{
  const {provider}=setup();let scans=0;provider.showArchives=()=>{scans++;};
  for(let i=0;i<100;i++)provider.consumeOutput('{"type":"archives"}\n');
  assert.equal(scans,0);await new Promise(resolve=>setTimeout(resolve,100));assert.equal(scans,1);
  provider.requestArchivesRefresh();await new Promise(resolve=>setTimeout(resolve,100));assert.equal(scans,2);
});

test('Explorer refresh is reserved for possible mutations including failed commands',()=>{
  const {provider,executed}=setup();
  const emit=event=>provider.consumeOutput(JSON.stringify(event)+'\n');
  emit({type:'response',text:'read-only'});assert.equal(executed.length,0);
  emit({type:'file_change'});emit({type:'error',text:'verification failed'});
  assert.deepEqual(executed,['workbench.files.action.refreshFilesExplorer']);
  emit({type:'response',text:'next'});assert.equal(executed.length,1);
  emit({type:'tool_start',tool:'read_file'});emit({type:'response',text:'read'});assert.equal(executed.length,1);
  emit({type:'tool_start',tool:'run_command'});emit({type:'idle'});assert.equal(executed.length,2);
});

test('archive reads keep the host event loop available and recover after a storage error',async t=>{
  const os=require('node:os');
  const {ArchiveReader,ArchiveStore}=require('../archive-store');
  const folder=fs.mkdtempSync(path.join(os.tmpdir(),'trinity-archive-worker-'));
  const dataRoot=path.join(folder,'data'),store=new ArchiveStore(folder,dataRoot);
  const conversation=path.join(store.root,'saved');fs.mkdirSync(conversation,{recursive:true});
  fs.writeFileSync(path.join(conversation,'meta.json'),JSON.stringify({workspace:folder,title:'Saved'}));
  fs.writeFileSync(path.join(conversation,'events.jsonl'),JSON.stringify({type:'user',text:'Keep this task'})+'\n');
  const reader=new ArchiveReader({allowedRoot:folder});
  t.after(async()=>{await reader.dispose();fs.rmSync(folder,{recursive:true,force:true});});
  let responsive=false;
  const pending=reader.read({workspace:folder,dataRoot}).then(result=>{assert(responsive,'host heartbeat must run before the scan completes');return result;});
  await new Promise(resolve=>setImmediate(()=>{responsive=true;resolve();}));
  const result=await pending;
  assert(responsive);assert.equal(result.items[0].id,'saved');assert(result.items[0].resumable);
  assert.equal(result.archive_root,store.root);
  assert(Number.isSafeInteger(result.archive_used_bytes) && result.archive_used_bytes>0,'The worker must deliver the measured filesystem usage');
  fs.writeFileSync(path.join(dataRoot,'projects.json'),'invalid');
  await assert.rejects(reader.read({workspace:folder,dataRoot}));
  fs.writeFileSync(path.join(dataRoot,'projects.json'),'[]');
  assert.equal((await reader.read({workspace:folder,dataRoot})).items.length,1);
  const abandoned=assert.rejects(reader.read({workspace:folder,dataRoot}),/closed/);
  await reader.dispose();await abandoned;
  await assert.rejects(reader.read({workspace:folder,dataRoot}),/closed/);
});

test('archive refresh coalesces requests and discards results for a previous workspace',async()=>{
  const {provider,posted}=setup();const reads=[];
  provider.archiveReader={read:request=>new Promise(resolve=>reads.push({request,resolve}))};
  const pending=provider.showArchives();
  assert.equal(reads.length,1);
  provider.workspace+='-new';
  assert.equal(provider.showArchives(),pending);
  assert.equal(provider.showArchives(),pending);
  reads[0].resolve({type:'archives',items:[{id:'stale'}]});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(reads.length,2);assert.equal(posted.length,0);
  reads[1].resolve({type:'archives',items:[{id:'current'}]});
  await pending;
  assert.equal(posted.length,1);assert.equal(posted[0].items[0].id,'current');
});
