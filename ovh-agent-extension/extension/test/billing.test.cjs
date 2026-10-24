const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const i18n = require('../media/i18n');

const source = fs.readFileSync(path.join(__dirname, '../media/chat.js'), 'utf8');
test('DeepSeek shows the provider balance in USD without OVH billing instructions', () => {
  const dom = billingView(), w = dom.window;
  try {
    w.applyBillingStatus({provider:'deepseek', configured:true, available:true,
      currency:'USD', credit_available:12.34, last_update:'2026-10-24', refresh_seconds:60});
    assert.match(w.liveBalance.textContent,/DeepSeek.*12,34/);
    assert.doesNotMatch(w.liveBalance.title,/OVH|EUR/);
    assert(w.billing.classList.contains('connected'));
    w.applyBillingStatus({provider:'deepseek', configured:true, available:false, error:'DeepSeek unavailable'});
    assert.equal(w.liveBalance.textContent,'DeepSeek · —');
    assert(w.billing.classList.contains('failed'));
  } finally { dom.window.close(); }
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

test('catalogue and model costs use native currency with cent precision and no conversion', () => {
  const dom = billingView(), w = dom.window;
  try {
    const price = w.priceLabel({id: 'native-eur', currency: 'EUR', input_per_million: .08, output_per_million: .32});
    assert.match(price, /0,08\s*€ entrée.*0,32\s*€ sortie/);
    assert.doesNotMatch(price, /\$/);
    assert.match(w.priceLabel({id: 'default-eur', hourly: .126}), /0,13\s*€\/heure/);
    assert.match(w.priceLabel({id: 'legacy-usd', currency: 'USD', input_per_million: .08}), /0,08\s*\$US entrée/);
    assert.match(w.formatModelCost(.009), /0,01\s*€/);
    assert.match(w.formatModelCost(0), /0,00\s*€/);
    assert.match(w.formatModelCost(1.23, 'USD'), /1,23\s*\$US/);
  } finally {dom.window.close();}
});
