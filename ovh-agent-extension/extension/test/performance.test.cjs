const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const {EventEmitter}=require('node:events');
const {JSDOM}=require('jsdom');
const {validMessage}=require('../security');
const root=path.resolve(__dirname,'..');
function setup(settings={},env={}) {
  const native=createRequire(path.join(root,'extension.js')),updates=[],spawned=[],sent=[];
  const config={get:key=>settings[key],inspect:key=>({globalValue:settings[key]}),update:async(key,value)=>{updates.push({key,value});settings[key]=value;}};
  const vscode={workspace:{workspaceFolders:[{uri:{fsPath:root}}],getConfiguration:()=>config},Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)},env:{language:'fr'},ConfigurationTarget:{Global:1}};
  const spawn=(command,args,options)=>{
    spawned.push({command,args,mode:options.env.OVH_AGENT_PERFORMANCE_MODE});
    const process=new EventEmitter();
    for(const name of ['stdout','stderr']){process[name]=new EventEmitter();process[name].setEncoding=()=>{};}
    process.stdin={writable:true,write:line=>sent.push(JSON.parse(line))};
    return process;
  };
  const sandbox={require:name=>name==='vscode'?vscode:name==='child_process'?{...native(name),spawn}:native(name),module:{exports:{}},process:{env:{OVH_AGENT_LANG:'fr',...env}},console,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider; readPersistentKey=()=>"test-only-key";',sandbox);
  const provider=new sandbox.module.exports.Provider(root,{});
  const posted=[];provider.post=value=>posted.push(JSON.parse(JSON.stringify(value)));
  return {provider,posted,config,settings,updates,spawned,sent};
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
  provider.agent={stdin:{writable:true,write:line=>sent.push(JSON.parse(line))}};provider.busy=true;
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
    const {provider,spawned}=setup({backendPath:path.join(root,'extension.js'),performanceMode:mode},{OVH_AGENT_PERFORMANCE_MODE:environment});
    provider.windowLock={};provider.billingCredentials=async()=>undefined;
    assert.equal(await provider.startAgent(),true);
    assert.equal(spawned[0].mode,expected);
    assert.equal(spawned.length,1);
  }
});

test('chat selector displays actual resource profile, waits for acknowledgement and recovers from errors',t=>{
  const {provider}=setup({performanceMode:'max'});
  const dom=new JSDOM(provider.getHtml({asWebviewUri:x=>x,cspSource:'local'}),{runScripts:'outside-only',url:'https://test.invalid'});
  t.after(()=>dom.window.close());
  const w=dom.window,doc=w.document,sent=[];
  w.acquireVsCodeApi=()=>({getState:()=>undefined,setState:()=>{},postMessage:m=>sent.push(JSON.parse(JSON.stringify(m)))});
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.OVH_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
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
