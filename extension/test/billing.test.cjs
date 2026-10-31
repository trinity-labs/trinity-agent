const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const i18n = require('../media/i18n');

const source = fs.readFileSync(path.join(__dirname, '../media/chat.js'), 'utf8');
test('DeepSeek shows the converted euro balance without OVH billing instructions', () => {
  const dom = billingView(), w = dom.window;
  try {
    w.applyBillingStatus({provider:'deepseek', configured:true, available:true,
      currency:'EUR', credit_available:12.34, last_update:'2026-10-24', refresh_seconds:60});
    assert.match(w.liveBalance.textContent,/DeepSeek.*12,34/);
    assert.doesNotMatch(w.liveBalance.title,/OVH/);
    assert.match(w.liveBalance.textContent,/€/);
    assert.equal(w.liveBalance.querySelector('.balance-amount').textContent,
      w.formatMoney(12.34,'EUR'));
    assert(!w.liveBalance.querySelector('.balance-amount').classList.contains('negative'));
    assert(w.billing.classList.contains('connected'));
    w.applyBillingStatus({provider:'deepseek', configured:true, available:false, error:'DeepSeek unavailable'});
    assert.equal(w.liveBalance.textContent,'DeepSeek · —');
    assert(w.billing.classList.contains('failed'));
    assert.equal(w.liveBalance.querySelector('.balance-amount'),null);
  } finally { dom.window.close(); }
});
test('OpenAI shows configured allowance, local token usage and official-billing notice', () => {
  const dom=billingView(),w=dom.window;
  try {
    w.applyBillingStatus({provider:'openai',configured:true,available:true,currency:'USD',
      credit_available:100,current_usage:1.25,credit_remaining:98.75,input_tokens:1200,
      output_tokens:300,requests:2,refresh_seconds:60,display_currency:'EUR',
      exchange_rates:{date:'2026-10-30',rates:{EUR:1,USD:2}}});
    assert.match(w.liveBalance.textContent,/OpenAI Codex/);
    assert.match(w.liveBalance.textContent,/49,38/);
    assert.match(w.liveBalance.textContent,/€/);
    assert.match(w.liveBalance.title,/0,63/);
    assert.match(w.liveBalance.title,/1.?200 tokens entrée/);
    assert.match(w.liveBalance.title,/source officielle/);
    assert.doesNotMatch(w.liveBalance.title,/OVH/);
  } finally {dom.window.close();}
});
function billingView() {
  const dom = new JSDOM('<button id="billing"></button><button id="balance"></button>', {runScripts: 'outside-only'});
  const w = dom.window;
  i18n.setLanguage('fr');
  w.t = i18n.t;
  w.uiLanguage = 'fr';
  w.billing = w.document.getElementById('billing');
  w.liveBalance = w.document.getElementById('balance');
  w.setStatus = () => {};
  w.scheduleBillingRefresh = seconds => { w.refreshSeconds = seconds; };
  // Evaluate the production render functions without starting the chat backend.
  w.eval(source.slice(source.indexOf('function formatModelCost('), source.indexOf('function addApproval(')));
  w.eval(source.slice(source.indexOf('function priceLabel('), source.indexOf('function modelAvailabilityLabel(')));
  return dom;
}

test('estimated credit displays cents, calculation precision and provider reporting delay', () => {
  const dom = billingView(), w = dom.window;
  w.applyBillingStatus({configured: true, available: true, balance_estimated: true,
    currency: 'EUR', credit_currency: 'EUR', credit_available: 100, current_usage: 10.04,
    credit_remaining: 89.96, credit_remaining_display: 89.96,
    forecast_usage: 20, last_update: '2026-10-16T10:00:00Z', refresh_seconds: 15});
  assert.match(w.liveBalance.textContent, /Solde estimé : 89,96/);
  assert.match(w.liveBalance.title, /89,96/);
  assert.match(w.liveBalance.title, /consommation courante du projet 10,04/);
  assert.match(w.liveBalance.title, /2026-10-16T10:00:00Z/);
  assert.match(w.liveBalance.title, /retard/);
  assert.match(w.liveBalance.title, /au centime/);
  assert.match(w.liveBalance.title, /sans arrondi intermédiaire/);
  assert.doesNotMatch(w.liveBalance.title, /Affichage arrondi/);
  assert.match(w.liveBalance.title, /dépenses de l’agent sont comprises dans le relevé OVH/);
  assert.doesNotMatch(w.liveBalance.title, /USD/);
  assert.equal(w.refreshSeconds, 15);
  assert(w.billing.classList.contains('connected'));
  // Older cached payloads may still contain the former tenth-euro display value.
  w.applyBillingStatus({configured: true, available: true, balance_estimated: true,
    currency: 'EUR', credit_available: 100, current_usage: 10.04,
    credit_remaining: 89.96, credit_remaining_display: 90});
  assert.match(w.liveBalance.textContent, /Solde estimé : 89,96/);
  dom.window.close();
});

test('negative estimates remain visible and incompatible currencies keep raw credit label', () => {
  const dom = billingView(), w = dom.window;
  w.applyBillingStatus({configured: true, available: true, balance_estimated: true,
    currency: 'EUR', credit_available: 1, current_usage: 1.05,
    credit_remaining: -.05, credit_remaining_display: -.05, stale: true});
  assert.match(w.liveBalance.textContent, /Solde estimé : -0,05/);
  assert(w.liveBalance.querySelector('.balance-amount').classList.contains('negative'));
  assert(w.billing.classList.contains('failed'));
  w.applyBillingStatus({configured: true, available: true, balance_estimated: false,
    currency: 'EUR', credit_currency: 'USD', credit_available: 100, current_usage: 10,
    balance_error: 'Devises du crédit et de la consommation incompatibles ; déduction indisponible.'});
  assert.match(w.liveBalance.textContent, /^Crédit OVH : /);
  assert.match(w.liveBalance.textContent, /\$/);
  assert.match(w.liveBalance.title, /incompatibles/);
  assert.doesNotMatch(w.liveBalance.title, /Solde estimé/);
  dom.window.close();
});

test('budget updates do not add an indicator or change OVH credit', () => {
  const dom = billingView(), w = dom.window;
  try {
    const billing = {configured: true, available: true, currency: 'EUR', credit_available: 100};
    w.applyBillingStatus(billing);
    const balance = w.liveBalance.textContent, balanceTitle = w.liveBalance.title;
    for (const budget_status of [
      {currency: 'EUR', blocked: false, periods: {daily: {limit: 2, spent: .25, reserved: .5, remaining: 1.25, reset_at: '2026-10-17T00:00:00Z'}}},
      {currency: 'EUR', blocked: true, error: 'Registre illisible <img src=x>'},
    ]) {
      w.applyBillingStatus({...billing, budget_status});
      assert.equal(w.document.getElementById('agent-budget'), null);
      assert.equal(w.liveBalance.textContent, balance);
      assert.equal(w.liveBalance.title, balanceTitle);
      assert.equal(w.document.querySelector('img'), null);
    }
  } finally {dom.window.close();}
});

test('catalogue and model costs use the selected display currency', () => {
  const dom = billingView(), w = dom.window;
  try {
    const price = w.priceLabel({id: 'native-eur', currency: 'EUR', input_per_million: .08, output_per_million: .32});
    assert.match(price, /0,08\s*€ entrée.*0,32\s*€ sortie/);
    assert.doesNotMatch(price, /\$/);
    assert.match(w.priceLabel({id: 'default-eur', hourly: .126}), /0,13\s*€\/heure/);
    assert.match(w.priceLabel({id: 'legacy-usd', currency: 'USD', input_per_million: .08,
      display_currency:'USD'}), /0,08\s*\$US entrée/);
    assert.match(w.formatModelCost(.009), /0,01\s*€/);
    assert.match(w.formatModelCost(0), /0,00\s*€/);
    assert.match(w.formatModelCost(1.23, 'USD', {display_currency:'USD'}), /1,23\s*\$US/);
    assert.match(w.formatModelCost(2, 'USD', {display_currency:'EUR',
      exchange_rates:{date:'2026-10-30',rates:{EUR:1,USD:2}}}), /1,00\s*€/);
    assert.match(w.formatModelCost(2, 'USD', {display_currency:'GBP',
      exchange_rates:{date:'2026-10-30',rates:{EUR:1,USD:2,GBP:.8}}}), /0,80\s*£GB/);
    assert.match(w.priceLabel({id:'gpt-6-astra',currency:'USD',input_per_million:10,output_per_million:50,
      long_context_threshold:272000,long_input_multiplier:2,long_output_multiplier:1.5,
      display_currency:'EUR',exchange_rates:{date:'2026-10-30',rates:{EUR:1,USD:1.1172}}}),
      /8,95\s*€ entrée.*44,75\s*€ sortie.*>272k.*17,90\s*€.*67,13\s*€/);
  } finally {dom.window.close();}
});

const os = require("node:os"), vm = require("node:vm");
const {createRequire} = require('node:module');
const root = path.join(__dirname, '..');
const native = createRequire(path.join(root,'extension.js'));
const tick = () => new Promise(resolve => setImmediate(resolve));
function setupBillingProvider(t, providerName, cache) {
 const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'trinity-billing-review-'));
 const archives=native('./archive-store');
 const settings={provider:providerName},calls=[],posts=[];
 const fakeSecrets={TRINITY_DEEPSEEK_API_KEY:'test-deepseek-key',TRINITY_OVHCLOUD_API_KEY:'test-ovh-key',TRINITY_OVHCLOUD_APPLICATION_KEY:'test-app-key',TRINITY_OVHCLOUD_APPLICATION_SECRET:'test-app-secret',TRINITY_OVHCLOUD_CONSUMER_KEY:'test-consumer-key',TRINITY_OVHCLOUD_PROJECT:'project-a',TRINITY_OVHCLOUD_API_BASE_URL:'https://example.invalid'};
 const vscode={workspace:{workspaceFolders:[{uri:{fsPath:temporary}}],getConfiguration:()=>({get:(key,fallback)=>settings[key] ?? fallback})},env:{language:'fr'}};
 const scope={require:name=>name==='vscode'?vscode:(name==='node:child_process'||name==='child_process')?{...native(name),execFile:(file,argv,options,done)=>calls.push({file,argv,options,done}),spawn:()=>{throw Error('Unexpected inference process startup');}}:name==='./archive-store'?{...archives,ArchiveStore:class extends archives.ArchiveStore{constructor(workspace){super(workspace,path.join(temporary,'data'));}}}:native(name),module:{exports:{}},process:{env:{TRINITY_AGENT_PROVIDER:'ovhcloud'}},console,setTimeout,clearTimeout,readFakeSecret:name=>fakeSecrets[name]||''};
 vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nreadServerSecret=readFakeSecret;module.exports.Provider=TrinityAgentViewProvider;',scope);
 const provider=new scope.module.exports.Provider(root,{}, {get:()=>undefined});
 provider.view={webview:{postMessage:message=>posts.push(message)}};
 fs.mkdirSync(provider.store.root,{recursive:true});
 if(cache)fs.writeFileSync(path.join(provider.store.root,`billing-${providerName}.json`),JSON.stringify(cache));
 t.after(()=>{provider.billingChannel.cancel();fs.rmSync(temporary,{recursive:true,force:true});});
 return {provider,settings,calls,posts,done:(index,result)=>calls[index].done(null,JSON.stringify(result))};
}
const deepCache={provider:'deepseek',schema_version:2,credit_currency:'EUR',configured:true,available:true,currency:'EUR',credit_available:12.34,cached_at:Date.now()/1000,last_update:'2026-10-26T15:00:00Z'};
const ovhCache={schema_version:2,configured:true,available:true,project:'project-a',currency:'EUR',credit_currency:'EUR',credit_available:100,current_usage:12.34,cached_at:Date.now()/1000,last_update:'2026-10-26T15:00:00Z'};
test('cached DeepSeek balance is sent before the upstream response and refresh uses selected provider',async t=>{
 const h=setupBillingProvider(t,'deepseek',deepCache);let ovhCalls=0;h.provider.billingCredentials=async()=>{ovhCalls++;return undefined;};
 const pending=h.provider.refreshBilling(false);await tick();
 assert(h.posts.some(p=>p.provider==='deepseek'&&p.available&&p.credit_available===12.34),'No immediate cached DeepSeek balance');
 assert.equal(ovhCalls,0);assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.env.TRINITY_AGENT_PROVIDER,'deepseek');
 h.done(0,{...deepCache,credit_available:13});await pending;assert.equal(h.posts.at(-1).credit_available,13);
});
test('OVH cached estimate is derived without waiting for API and never uses another project',async t=>{
 const h=setupBillingProvider(t,'ovhcloud',ovhCache);const pending=h.provider.refreshBilling(false);await tick();
 const cached=h.posts.find(p=>p.available&&p.balance_estimated);assert(cached,'No immediate OVH cached estimate');assert.equal(cached.credit_remaining,87.66);
 assert.equal(h.calls[0].options.env.TRINITY_AGENT_PROVIDER,'ovhcloud');h.done(0,{...ovhCache,balance_estimated:true,credit_remaining:87.66});await pending;
 const wrong=setupBillingProvider(t,'ovhcloud',{...ovhCache,project:'another-project',credit_available:999});const next=wrong.provider.refreshBilling(false);await tick();
 assert(!wrong.posts.some(p=>p.available),'Balance from another OVH project was displayed');wrong.done(0,{...ovhCache,balance_estimated:true,credit_remaining:87.66});await next;
});
test('late standalone OVH response cannot overwrite selected DeepSeek balance or clear its pending request',async t=>{
 const h=setupBillingProvider(t,'ovhcloud');const old=h.provider.refreshBilling(false);await tick();assert.equal(h.calls.length,1);
 h.settings.provider='deepseek';const boundary=h.posts.length;
 const fresh=h.provider.refreshBilling(false);await tick();assert.equal(h.calls.length,2,'New provider reused old provider request');
 h.done(0,{...ovhCache,balance_estimated:true,credit_remaining:87.66});await old;await tick();
 assert(!h.posts.slice(boundary).some(p=>p.provider==='ovhcloud'&&p.available),'Old provider response leaked after switch');
 const duplicate=h.provider.refreshBilling(false);await tick();assert.equal(h.calls.length,2,'Old request cleared newer in-flight request');
 h.done(1,{...deepCache,credit_available:15});await Promise.all([fresh,duplicate]);assert.equal(h.posts.at(-1).provider,'deepseek');assert.equal(h.posts.at(-1).credit_available,15);
});

test('DeepSeek without cache or OVH credentials fetches its configured provider balance', async t => {
  const h = setupBillingProvider(t, 'deepseek');
  h.provider.billingCredentials = async () => assert.fail('DeepSeek must not consult OVH credentials');
  const pending = h.provider.refreshBilling(false); await tick();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].options.env.TRINITY_AGENT_PROVIDER, 'deepseek');
  assert(h.posts.every(p => p.provider === 'deepseek'));
  h.done(0, {...deepCache, credit_available: 20}); await pending;
  assert.equal(h.posts.at(-1).credit_available, 20);
});

test('pending refresh preserves the displayed balance and a provider switch rejects a late balance', () => {
  for (const provider of ['ovhcloud', 'deepseek']) {
    const dom = billingView(), w = dom.window;
    try {
      w.document.body.dataset.provider = provider;
      w.applyBillingStatus({provider, configured:true, available:true, currency:'EUR', credit_available:12.34});
      const before = w.liveBalance.textContent;
      w.applyBillingStatus({provider, pending:true, configured:true, available:false}, false);
      assert.equal(w.liveBalance.textContent, before);
      w.document.body.dataset.provider = provider === 'deepseek' ? 'ovhcloud' : 'deepseek';
      w.applyBillingStatus({provider:w.document.body.dataset.provider, pending:true}, false);
      const switching = w.liveBalance.textContent;
      w.applyBillingStatus({provider, configured:true, available:true, currency:'EUR', credit_available:999}, false);
      assert.equal(w.liveBalance.textContent, switching);
      assert.doesNotMatch(switching, /12,34|999/);
      if (w.document.body.dataset.provider === 'deepseek') assert.doesNotMatch(w.billing.title, /OVH/);
    } finally { dom.window.close(); }
  }
});


test('a running OVH task cannot supply the selected DeepSeek balance', async t => {
  const h = setupBillingProvider(t, 'deepseek');
  h.provider.agent = {provider:'ovhcloud', stdin:{writable:true, write:() => assert.fail('Wrong provider backend used')}};
  const pending = h.provider.refreshBilling(false); await tick();
  assert.equal(h.calls.length, 1);
  h.provider.consumeOutput(JSON.stringify({type:'billing_status', configured:true, available:true, credit_available:999})+'\n');
  assert(!h.posts.some(p => p.credit_available === 999));
  h.done(0, {...deepCache, credit_available:20}); await pending;
  assert.equal(h.posts.at(-1).credit_available, 20);
});

test('expired cached balance remains visible and is marked stale if refresh fails', async t => {
  const h = setupBillingProvider(t, 'deepseek', {...deepCache, cached_at:1});
  const pending = h.provider.refreshBilling(false); await tick();
  assert(h.posts[0].available && h.posts[0].stale);
  h.calls[0].done(new Error('upstream unavailable')); await pending;
  assert.equal(h.posts.at(-1).credit_available, 12.34);
  assert(h.posts.at(-1).stale);
  const dom = billingView(), w = dom.window;
  try {
    w.applyBillingStatus(h.posts.at(-1), false);
    assert.match(w.liveBalance.textContent, /12,34.*non actualisé/);
    assert(w.billing.classList.contains('failed'));
  } finally { dom.window.close(); }
});

const eurRates = {source:'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml',date:'2026-10-28',rates:{EUR:1,USD:2,CNY:8},stale:false};
test('DeepSeek prices and costs convert USD and CNY, keeping OVH prices native', () => {
 const dom=billingView(),w=dom.window;
 try {
  const context={provider:'deepseek',exchange_rates:eurRates};
  assert.match(w.priceLabel({id:'deepseek-flash',currency:'USD',input_per_million:.3,output_per_million:1.2,...context}),/0,15\s*€ entrée.*0,60\s*€ sortie/);
  assert.match(w.formatModelCost(16,'CNY',context),/2,00\s*€/);
  assert.match(w.formatModelCost(0,'USD',context),/0,00\s*€/);
  assert.match(w.formatModelCost(2,'USD',{display_currency:'USD'}),/2,00\s*\$US/);
  assert.match(w.formatModelCost(2,'USD',{...context,exchange_rates:{date:'2026-10-28',rates:{}}}),/indisponible/);
  assert.match(w.formatModelCost(2,'USD',{...context,exchange_rates:{...eurRates,stale:true}}),/1,00.*non actualisé/);
 } finally {dom.window.close();}
});
test('DeepSeek balance tooltip exposes the yuan contribution in euros and the ECB date', () => {
 const dom=billingView(),w=dom.window;
 try {
  w.applyBillingStatus({...deepCache,credit_available:3,exchange_rates:eurRates,
   balance_infos:[{source_currency:'CNY',currency:'EUR',total_balance:2},{source_currency:'USD',currency:'EUR',total_balance:1}]});
  assert.match(w.liveBalance.textContent,/3,00\s*€/);
  assert.match(w.liveBalance.title,/CNY → 2,00\s*€.*USD → 1,00\s*€/);
  assert.match(w.liveBalance.title,/BCE du 2026-10-28/);
  w.applyBillingStatus({...deepCache,currency:'USD',credit_available:999});
  assert.equal(w.liveBalance.textContent,'DeepSeek · —');
 } finally {dom.window.close();}
});
test('old dollar-only DeepSeek cache is rejected before the euro refresh', async t => {
 const h=setupBillingProvider(t,'deepseek',{...deepCache,currency:'USD',schema_version:undefined});
 const pending=h.provider.refreshBilling();await tick();
 assert(!h.posts.some(p=>p.available),'Legacy USD cache must not be relabelled as EUR');
 h.done(0,deepCache);await pending;
 assert.equal(h.posts.at(-1).currency,'EUR');
 assert.equal(h.posts.at(-1).credit_available,12.34);
});
