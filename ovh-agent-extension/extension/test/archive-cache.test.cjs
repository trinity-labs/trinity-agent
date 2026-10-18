const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {ArchiveStore} = require('../archive-store');

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
