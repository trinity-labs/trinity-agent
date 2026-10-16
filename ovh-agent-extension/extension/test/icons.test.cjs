const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const {JSDOM} = require('jsdom');
const {svg} = require('../media/icons.min.js');
const media = path.resolve(__dirname,'../media');
test('Font Awesome subset works offline in Node and browser within size budget', () => {
  const js=fs.readFileSync(path.join(media,'icons.min.js'),'utf8');
  assert.ok(Buffer.byteLength(js)<12000);
  assert.ok(zlib.gzipSync(js).length<5000);
  const dom=new JSDOM('',{runScripts:'outside-only'});
  dom.window.eval(js);
  for(const name of ['nodes','archive','back','chevron','database','key','plus','close','reload','image','shield','stop','send','play','trash','terminal','list','read','search','edit','context','cost','model','copy','check','error','robot','wallet']) {
    const html=svg(name,'custom-class');
    assert.equal(dom.window.AgentIcons.svg(name,'custom-class'),html);
    dom.window.document.body.innerHTML=html;
    const icon=dom.window.document.querySelector('svg.fa-icon');
    assert.equal(icon.getAttribute('aria-hidden'),'true');
    assert.equal(icon.getAttribute('focusable'),'false');
    assert.ok(icon.querySelector('path').getAttribute('d'));
    assert.equal(icon.querySelectorAll('script, image, use, foreignObject').length,0);
  }
  assert.throws(()=>svg('constructor'));
  assert.throws(()=>svg('key','" onload="alert(1)'));
  dom.window.close();
});
test('Font Awesome vectors survive the archive HTML sanitizer', () => {
  const dom=new JSDOM('',{runScripts:'outside-only'});
  for(const name of ['purify.min.js','safe-html.js'])dom.window.eval(fs.readFileSync(path.join(media,name),'utf8'));
  const html=dom.window.safeHtml(svg('terminal','run-icon'));
  dom.window.document.body.innerHTML=html;
  const icon=dom.window.document.querySelector('svg.fa-icon');
  assert.ok(icon);
  assert.equal(icon.getAttribute('viewBox'),'0 0 512 512');
  assert.equal(icon.getAttribute('aria-hidden'),'true');
  assert.ok(icon.querySelector('path').getAttribute('d'));
  dom.window.close();
});
