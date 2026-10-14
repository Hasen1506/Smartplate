// v3 Today (static/v3/v3.js) on top of app.js, without a browser: rendering rules and the flag.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const v3 = fs.readFileSync('smartplate/static/v3/v3.js', 'utf8');
const app = fs.readFileSync('smartplate/static/app.js', 'utf8').replace('boot().catch', 'globalThis.bootPromise = boot().catch');

const TODAY = {
  date: '2026-11-02', weekday: 'Monday', meals: ['breakfast', 'lunch'], label: 'B+L',
  budget: { amount: 600, source: 'week', default: 600, basis: 'Your weekly ₹4,200 ÷ 7', presets: [300, 600, 1000], picked: 0, left: 600, unpriced: [] },
  weather: { known: false },
  rules: { diet: 'veg', allergens: ['peanut'], medical: [], min_rating: 4, max_eta: 30, nutrition: false },
  pools: {
    breakfast: [{ ref: 't:1', source: 'typed', kind: 'order', name: 'Cappuccino', restaurant: 'Corner Cafe', price: 140, veg: true, rating: null, eta_min: null, qty: 1, hard: false, reasons: [] }],
    lunch: [{ ref: 't:2', source: 'typed', kind: 'order', name: 'Chicken <b>Burrito</b>', restaurant: null, price: 220, veg: null, rating: null, eta_min: null, qty: 1, hard: true, reasons: ['Not marked veg'] }],
    dinner: [],
  },
  picks: {}, modes: { favourites: 'From my favourites only', favourites_cook: 'Favourites + cook at home', favourites_new: 'Favourites + new places at or above my rating', surprise: 'Surprise me (within my rules)' },
  price_compare: null, swiggy: { connected: false, address: null }, account_saved: false, share_text: 'Today',
};

function world({ ui = null, today = TODAY } = {}) {
  const calls = [];
  const element = { innerHTML: '', classList: { toggle() {}, add() {} }, appendChild() {}, remove() {}, addEventListener() {}, setAttribute() {} };
  const store = { 'smartplate.user': '2' };
  if (ui) store['ziggy.ui'] = ui;
  const view = {
    plan: { id: 42, mode: 'balanced', mode_label: 'Balanced', week_start: '2026-11-02' },
    user: { id: 2, name: 'Priya', diet: 'veg', weekly_budget: 4200, allergens: [], medical: [], health_targets: {} },
    budget: { spend: 0, budget: 4200 }, counts: {}, grid: [], nutrition: { daily_target: { kcal: 1800 }, daily_avg: { kcal: 900 } },
  };
  const replies = {
    '/api/meta': { modes: {}, mode_outcomes: {}, version: '1.1.0', swiggy_provider: 'live', fixture_data: false },
    '/api/users': [{ id: 2, name: 'Priya' }],
    '/api/user/2/plan': view, '/api/plan/42/orders': { results: [] }, '/api/user/2/swiggy': { connected: false },
    '/api/user/2/today': today,
  };
  const context = vm.createContext({
    document: { getElementById: () => element, querySelectorAll: () => [], querySelector: () => null,
      createElement: () => element, addEventListener() {}, body: element },
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    location: { search: '' }, URLSearchParams, history: { replaceState() {} },
    fetch: async (url, options) => { calls.push({ url, options });
      return { ok: !!replies[url], status: replies[url] ? 200 : 404, json: async () => replies[url] || { error: 'Unexpected route' } }; },
    setTimeout: () => {}, clearTimeout: () => {}, console,
  });
  vm.runInContext(v3, context);
  vm.runInContext(app, context);
  return { context, calls, element, store };
}
const run = (w, code) => vm.runInContext(code, w.context);

test('v3 is on by default: four tabs with Cook, and Today is the v3 screen', async () => {
  const w = world(); await w.context.bootPromise;
  assert.equal(run(w, 'ZiggyV3.on()'), true);
  assert.equal(run(w, 'TABS.map(t => t[0]).join()'), 'today,week,cook,saved');
  await run(w, 'ZiggyV3._test.load()');
  const html = run(w, 'tabBody()');
  assert.match(html, /Today's budget/);
  assert.match(html, /₹600/);
  assert.match(html, /\(B\+L\)/);
  assert.match(html, /Breakfast pool/);
  assert.match(html, /Decide for me/);
  assert.doesNotMatch(html, /v3-compare/, 'no price row without data');
  assert.doesNotMatch(html, /Hungry now/, 'old entry points are not on v3 Today');
});

test('greyed-out pool dishes carry the reason, are escaped and cannot be picked', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  const html = run(w, 'tabBody()');
  assert.match(html, /v3-pool-item off/);
  assert.match(html, /Not marked veg/);
  assert.ok(!html.includes('<b>Burrito</b>'), 'dish names are escaped');
  assert.match(html, /<option value="t:2"[^>]*disabled/);
});

test('no account form before the first pick; offered after it', async () => {
  const w = world(); await w.context.bootPromise;
  run(w, 'keys.put(2, "k", "Priya")');
  await run(w, 'ZiggyV3._test.load()');
  assert.doesNotMatch(run(w, 'tabBody()'), /Keep this on every device/);
  run(w, 'S.v3.data.picks = { breakfast: { ...S.v3.data.pools.breakfast[0], qty: 1, how: "you", reasons: [] } }');
  assert.match(run(w, 'tabBody()'), /Keep this on every device/);
});

test('Decide sheet: the Decide button waits for a mode', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, 'S.v3.sheet = { kind: "decide" }');
  assert.match(run(w, 'ZiggyV3.overlays()'), /data-v3-act="decide" disabled/);
  run(w, 'S.v3.sheet.choice = "favourites"');
  assert.doesNotMatch(run(w, 'ZiggyV3.overlays()'), /data-v3-act="decide" disabled/);
});

test('first run: no diet preselected, Continue disabled, two steps', async () => {
  const w = world(); await w.context.bootPromise;
  run(w, 'ZiggyV3.startOnboard()');
  const html = run(w, 'ZiggyV3.screen()');
  assert.doesNotMatch(html, /aria-checked="true"/);
  assert.match(html, /data-v3-ob="nav:next" disabled/);
  assert.match(html, /Step 1 of 2/);
});

test('Week nutrition is off by default and the aside hides it', async () => {
  const w = world(); await w.context.bootPromise;
  assert.equal(run(w, 'ZiggyV3.nutritionOn()'), false);
  assert.match(run(w, 'ZiggyV3.weekTop()'), /Show nutrition/);
  assert.doesNotMatch(run(w, 'ZiggyV3.weekTop()'), /class="card v3-nut"/);
});

test('?ui=classic (ziggy.ui=classic) keeps the previous Today and three tabs', async () => {
  const w = world({ ui: 'classic' }); await w.context.bootPromise;
  assert.equal(run(w, 'ZiggyV3.on()'), false);
  assert.equal(run(w, 'TABS.map(t => t[0]).join()'), 'today,week,saved');
  assert.equal(run(w, 'ZiggyV3.body("today")'), null);
  assert.equal(run(w, 'ZiggyV3.nutritionOn()'), true);
});
