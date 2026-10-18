const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createMarkdownRenderer,renderMarkdown}=require('../markdown-renderer');
const {BillingChannel}=require('../billing-channel');

test('render cache is exact, per-provider, bounded, and cleared explicitly',()=>{
  let calls=0;const render=text=>{calls++;return renderMarkdown(text);};
  const a=createMarkdownRenderer(render),b=createMarkdownRenderer(render);
  const html=a('**safe**');assert.equal(a('**safe**'),html);assert.equal(calls,1);
  b('**safe**');assert.equal(calls,2);
  a('changed');a('**safe**');assert.equal(calls,4);
  a.clear();a('**safe**');assert.equal(calls,5);
  const large='x'.repeat(128*1024+1);a(large);a(large);assert.equal(calls,7);
  assert(!a('<script>alert(1)</script>').includes('<script>'));
});

test('billing timeout, cancellation and late replies cannot settle another request',async()=>{
  const channel=new BillingChannel(10),sent=[];
  const first=channel.request(value=>sent.push(value));
  assert.equal(channel.request(()=>assert.fail('duplicate send')),first);
  await assert.rejects(first,/facturation/);
  const second=channel.request(value=>sent.push(value),true);
  assert.equal(channel.receive({request_id:sent[0].request_id}),false);
  assert.equal(channel.receive({request_id:sent[1].request_id,value:42}),true);
  assert.equal((await second).value,42);
  const third=channel.request(()=>{});channel.cancel();await assert.rejects(third,/interrompu/);
  await assert.rejects(channel.request(()=>{throw Error('pipe closed');}),/pipe closed/);
});

const {NdjsonDecoder}=require('../backend-protocol');
test('NDJSON fragments join once per line and preserve framing across every split',()=>{
  const source='{"type":"delta","text":"français ✅"}\n\n{"type":"idle"}\r\n';
  for(let split=0;split<=source.length;split++) {
    const decoder=new NdjsonDecoder();const fail=()=>assert.fail('valid frame rejected');
    const lines=[...decoder.feed(source.slice(0,split),fail),...decoder.feed(source.slice(split),fail)];
    assert.deepEqual(lines.filter(Boolean).map(line=>JSON.parse(line).type),['delta','idle']);
    assert.equal(decoder.bufferedChars,0);
  }
  const decoder=new NdjsonDecoder(8),errors=[];
  assert.deepEqual(decoder.feed('1234',e=>errors.push(e)),[]);
  assert.deepEqual(decoder.feed('56789\n{}\n',e=>errors.push(e)),['{}']);
  assert.equal(errors.length,1);assert.equal(decoder.bufferedChars,0);
  decoder.feed('unfinished',()=>{});decoder.reset();
  assert.deepEqual(decoder.feed('{}\n',()=>{}),['{}']);
});

test('one-byte writes keep fragment metadata bounded and preserve exact contents',()=>{
  const decoder=new NdjsonDecoder(20000);
  for(let i=0;i<10000;i++)decoder.feed('x',()=>assert.fail('rejected'));
  assert.equal(decoder.bufferedChars,10000);
  assert(decoder.fragments.length<1024);assert(decoder.blocks.length<10);
  assert.deepEqual(decoder.feed('\n',()=>{}),['x'.repeat(10000)]);
});
