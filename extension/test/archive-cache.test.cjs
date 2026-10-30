const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {ArchiveStore,archiveCacheBudget} = require('../archive-store');

test('mount usage measures filesystem blocks, handles a new archive and preserves unavailable state',t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'archive-usage-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const store=new ArchiveStore(root,path.join(root,'missing-data'));
  const seen=[];
  const stat=t.mock.method(fs,'statfsSync',folder=>{
    seen.push(folder);
    if(folder!==root)throw Object.assign(new Error('Missing'),{code:'ENOENT'});
    return {blocks:100,bfree:20,bavail:10,bsize:4096};
  });
  assert.equal(store.usedFilesystemBytes(),80*4096,'Reserved free blocks are not occupied space');
  assert.equal(seen[0],store.root);assert.equal(seen.at(-1),root);
  assert.equal(fs.existsSync(store.dataRoot),false,'Measuring must not create storage');
  assert.equal(store.storedBytes(),0,'A missing data root has no stored bytes');
  fs.mkdirSync(store.dataRoot,{recursive:true});
  fs.writeFileSync(path.join(store.dataRoot,'data.bin'),'12345');
  fs.symlinkSync(root,path.join(store.dataRoot,'external'));
  assert.equal(store.storedBytes(),5,'Stored size counts files without following links');
  stat.mock.mockImplementation(()=>{throw Object.assign(new Error('Denied'),{code:'EACCES'});});
  assert.equal(store.usedFilesystemBytes(),null);
  stat.mock.mockImplementation(()=>({blocks:100,bfree:100,bsize:4096}));
  assert.equal(store.usedFilesystemBytes(),0);
  stat.mock.mockImplementation(()=>({blocks:10,bfree:20,bsize:4096}));
  assert.equal(store.usedFilesystemBytes(),null);
});

test('conversation provider follows saved model, legacy changes and automatic provider',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'archive-model-'));
  try {
    const store=new ArchiveStore(root,root),folder=path.join(store.root,'one');
    fs.mkdirSync(folder,{recursive:true});
    const meta=path.join(folder,'meta.json');
    fs.writeFileSync(meta,JSON.stringify({model:'deepseek-flash'}));
    assert.deepEqual(store.modelSelection('one'),{model:'deepseek-flash',provider:'deepseek'});
    fs.writeFileSync(meta,JSON.stringify({model:'auto-balanced',provider:'deepseek'}));
    assert.deepEqual(store.modelSelection('one'),{model:'auto-balanced',provider:'deepseek'});
    fs.writeFileSync(meta,JSON.stringify({model:'auto-balanced',active_model:'deepseek-flash'}));
    assert.deepEqual(store.modelSelection('one'),{model:'auto-balanced',provider:'deepseek'});
    fs.writeFileSync(meta,'{}');
    fs.writeFileSync(path.join(folder,'events.jsonl'),[
      {type:'model_active',model:'deepseek-flash',selection:'deepseek-flash'},
      {type:'model_changed',to_model:'Qwen3.8-27B'},
      {type:'response',text:'Done'}].map(e=>JSON.stringify(e)).join('\n'));
    assert.deepEqual(store.modelSelection('one'),{model:'Qwen3.8-27B',provider:'ovhcloud'});
    assert.deepEqual(store.modelSelection('../one'),{});
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('archive cache auto follows cgroup allowance and current pressure instead of host RAM',()=>{
  const mib=1024*1024;
  const system={totalmem:()=>64*1024*mib,freemem:()=>48*1024*mib};
  const environment={TRINITY_AGENT_ARCHIVE_CACHE_MB:'auto'};
  const runtime={constrainedMemory:()=>512*mib,availableMemory:()=>256*mib};
  const small=archiveCacheBudget({environment,system,runtime});
  const large=archiveCacheBudget({environment,system,runtime:{constrainedMemory:()=>16*1024*mib,availableMemory:()=>12*1024*mib}});
  assert(small>0&&small<=4*mib);assert(large>small);
  assert.equal(archiveCacheBudget({environment,system,runtime:{...runtime,availableMemory:()=>0}}),0);
  assert.equal(archiveCacheBudget({environment:{TRINITY_AGENT_ARCHIVE_CACHE_MB:'1'},system,runtime}),mib);
  assert.equal(archiveCacheBudget({environment:{TRINITY_AGENT_ARCHIVE_CACHE_MB:'0'},system,runtime}),0);
  const fallback=archiveCacheBudget({environment,system,runtime:{}});
  assert(fallback<=2*mib,'Unavailable cgroup-aware APIs must not allocate from host-sized RAM');
});

test('archive cache reapplies an explicit zero on a warm read without losing source data',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'archive-pressure-'));
  const before=process.env.TRINITY_AGENT_ARCHIVE_CACHE_MB;
  try {
    process.env.TRINITY_AGENT_ARCHIVE_CACHE_MB='auto';
    const store=new ArchiveStore(root,root), folder=path.join(store.root,'one');
    fs.mkdirSync(folder,{recursive:true});
    const file=path.join(folder,'meta.json');fs.writeFileSync(file,'{"title":"preserved"}');
    assert.equal(store.readCached(file).title,'preserved');assert(store.cache.size>0);
    process.env.TRINITY_AGENT_ARCHIVE_CACHE_MB='0';store.nextCacheRefresh=0;
    assert.equal(store.readCached(file).title,'preserved');
    assert.equal(store.cacheLimit,0);assert.equal(store.cacheBytes,0);assert.equal(store.cache.size,0);
    assert(fs.existsSync(file));
  } finally {
    if(before===undefined)delete process.env.TRINITY_AGENT_ARCHIVE_CACHE_MB;else process.env.TRINITY_AGENT_ARCHIVE_CACHE_MB=before;
    fs.rmSync(root,{recursive:true,force:true});
  }
});

test('archive metadata cache reuses reads and invalidates edited, replaced and deleted state', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'archive-cache-'));
  try {
    const store = new ArchiveStore(root, root);
    const folder = path.join(store.root, 'one');
    fs.mkdirSync(folder, {recursive:true});
    const meta = path.join(folder, 'meta.json'), task = path.join(folder, 'task-state.json');
    fs.writeFileSync(meta, JSON.stringify({title:'Before'}));
    fs.writeFileSync(task, JSON.stringify({status:'running'}));
    fs.writeFileSync(path.join(store.root, 'current'), 'one');
    assert(store.list()[0].resumable);
    const original = fs.openSync;
    let reads = 0;
    fs.openSync = (...args) => {reads++; return original(...args);};
    try {assert.equal(store.list()[0].title, 'Before');} finally {fs.openSync = original;}
    assert.equal(reads, 1, 'only current pointer is reread');
    fs.writeFileSync(meta, JSON.stringify({title:'Edited'}));
    fs.writeFileSync(task+'.new', JSON.stringify({status:'completed'}));
    fs.renameSync(task+'.new', task);
    assert.equal(store.list()[0].title, 'Edited');
    assert.equal(store.list()[0].resumable, false);
    fs.unlinkSync(task);
    fs.writeFileSync(path.join(folder, 'events.jsonl'), '{"type":"user"}\n');
    assert(store.list()[0].resumable);
    fs.appendFileSync(path.join(folder, 'events.jsonl'), '{"type":"response"}\n');
    assert.equal(store.list()[0].resumable, false);
    store.cacheLimit = 1; store.cache.clear(); store.cacheBytes = 0;
    assert.equal(store.list()[0].title, 'Edited');
    assert.equal(store.cache.size, 0);
    assert.equal(store.remove('one'), true);
    assert.deepEqual(store.list(), []);
  } finally {fs.rmSync(root, {recursive:true,force:true});}
});

test('history recovers a missing automatic title while preserving custom titles', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'archive-title-'));
  try {
    const store=new ArchiveStore(root,root), folder=path.join(store.root,'one');
    fs.mkdirSync(folder,{recursive:true});
    const meta=path.join(folder,'meta.json');
    fs.writeFileSync(path.join(folder,'task-state.json'),JSON.stringify({status:'completed'}));
    fs.writeFileSync(path.join(folder,'events.jsonl'),'invalid\nnull\n'+JSON.stringify({type:'user',text:'Analyse\n  le projet docker_trinity'})+'\n'+JSON.stringify({type:'user',text:'Later request'}));
    for(const title of [undefined,null,'','   ','Nouvelle conversation']) {
      fs.writeFileSync(meta,JSON.stringify({title}));
      assert.equal(store.list()[0].title,'Analyse le projet docker_trinity');
    }
    fs.writeFileSync(meta,JSON.stringify({title:'Custom title'}));
    assert.equal(store.list()[0].title,'Custom title');
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('archive operations reject symlinked storage parents and metadata', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'archive-boundary-'));
  try {
    const store=new ArchiveStore(root,path.join(root,'data'));
    const outside=path.join(root,'outside');
    fs.mkdirSync(path.join(outside,'victim'),{recursive:true});
    fs.mkdirSync(path.dirname(store.root),{recursive:true});
    fs.symlinkSync(outside,store.root);
    assert.throws(()=>store.remove('victim'),/symbolique/);
    assert.throws(()=>store.list(),/symbolique/);
    assert(fs.existsSync(path.join(outside,'victim')));
    fs.unlinkSync(store.root);fs.mkdirSync(path.join(store.root,'one'),{recursive:true});
    const secret=path.join(outside,'private.json');fs.writeFileSync(secret,'{"title":"private"}');
    fs.symlinkSync(secret,path.join(store.root,'one','meta.json'));
    assert.deepEqual(store.list(),[]);
    assert.throws(()=>store.readCached(secret),/./); // outside storage must also be refused
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('storage reader rejects large and special files and refuses parent links',()=>{
  const {readStorageFile}=require('../project-store');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'storage-read-'));
  try {
    const file=path.join(root,'file');fs.writeFileSync(file,'12345');
    assert.throws(()=>readStorageFile(file,'utf8',4),/volumineux/);
    assert.equal(readStorageFile(file,'utf8',5),'12345');
    assert.throws(()=>readStorageFile(root),/régulier/);
    fs.symlinkSync(file,path.join(root,'link'));
    assert.throws(()=>readStorageFile(path.join(root,'link')),/symbolique/);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('single archive lookup validates metadata without scanning or following symlinks',()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'archive-has-'));
 try {
  const store=new ArchiveStore(root,root),folder=path.join(store.root,'saved');
  fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,'meta.json'),'{}');
  store.list=()=>assert.fail('No archive enumeration');
  assert(store.has('saved'));assert(!store.has('../saved'));assert(!store.has('missing'));
  fs.symlinkSync(folder,path.join(store.root,'linked'));assert(!store.has('linked'));
  fs.renameSync(path.join(folder,'meta.json'),path.join(folder,'original.json'));
  fs.symlinkSync(path.join(folder,'original.json'),path.join(folder,'meta.json'));assert(!store.has('saved'));
 } finally {fs.rmSync(root,{recursive:true,force:true});}
});
