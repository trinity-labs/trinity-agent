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
    const original = fs.readFileSync;
    let reads = 0;
    fs.readFileSync = (...args) => {reads++; return original(...args);};
    try {assert.equal(store.list()[0].title, 'Before');} finally {fs.readFileSync = original;}
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
