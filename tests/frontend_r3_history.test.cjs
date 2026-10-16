// Fix round 3 "history-favs" (ux-audit 16 Oct 2026 #2 #5 #6 #7 #8 #29 #30 #33): v3 rendering, no browser.
// The world() harness is copied from frontend_v3.test.cjs so this file runs on its own.
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

function world({ ui = null, today = TODAY, width = null } = {}) {
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
    ...(width ? { matchMedia: (q) => ({ matches: width >= Number(/min-width:\s*(\d+)/.exec(q)?.[1] || 0), addEventListener() {} }) } : {}),
  });
  vm.runInContext(v3, context);
  vm.runInContext(app, context);
  return { context, calls, element, store };
}
const run = (w, code) => vm.runInContext(code, w.context);

const SUGG = { imported: true, note: 'Tick the ones you like.', attribution: 'Powered by Swiggy', imported_at: '2026-11-01T09:30',
  consent: 'long consent', consent_short: 'short consent',
  restaurants: [{ restaurant_id: 'r-102', restaurant_name: 'Biryani Junction', rating: 4.2, saved: false },
    { restaurant_id: 'r-101', restaurant_name: 'Annapoorna', rating: null, saved: true }],
  combos: [{ restaurant_id: 'r-102', restaurant_name: 'Biryani Junction', label: 'Veg Biryani + 2 Raita', pool_meal: 'lunch', pool_meals: ['lunch', 'dinner'], times: 2,
    items: [{ name: 'Veg Biryani', qty: 1, side: false }, { name: 'Raita', qty: 2, side: true }] }],
  items: [
    { restaurant_id: 'r-102', restaurant_name: 'Biryani Junction', item_name: 'Veg Biryani', pool_meal: 'lunch', pool_meals: ['lunch', 'dinner'], times: 2, qty: 1, side: false, with_main_only: false, rating: 4.2 },
    { restaurant_id: 'r-102', restaurant_name: 'Biryani Junction', item_name: 'Raita', pool_meal: 'lunch', pool_meals: ['lunch', 'dinner'], times: 2, qty: 2, side: true, with_main_only: true, rating: 4.2 },
    { restaurant_id: 'r-101', restaurant_name: 'Annapoorna', item_name: 'Kesari', pool_meal: 'snack', pool_meals: ['snack'], times: 1, qty: 1, side: false, with_main_only: false, rating: null }] };

test('r3 #5/#7/#8/#30: checklist rows, whole orders, no lone sides, save places, refresh with date', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.v3.sheet = { kind: "add", meal: "dinner", sugg: ${JSON.stringify(SUGG)} }`);
  const html = run(w, `ZiggyV3._test.suggBlock(S.v3.sheet, "dinner")`);
  assert.doesNotMatch(html, /class="tick/, '#5: not the old 28px badge class');
  assert.match(html, /class="v3-check v3-sugg-row"/);
  assert.match(html, /Veg Biryani \+ 2 Raita/);
  assert.match(html, /whole order/);
  assert.equal((html.match(/data-v3-sugg="/g) || []).length, 2, 'combo + Veg Biryani; Raita only inside its order');
  assert.doesNotMatch(html, /Kesari/, 'a snack is not offered for dinner');
  assert.match(html, /Save this place/, 'one unsaved place');
  assert.match(html, /data-v3-act="sugg-places"/);
  assert.match(html, /Last read 01\/11\/2026/);
  assert.match(html, /data-v3-act="sugg-refresh"/);
  const snack = run(w, `ZiggyV3._test.suggBlock(S.v3.sheet, "snack")`);
  assert.match(snack, /Kesari/);
});

test('r3 #7: ticking a whole order posts it as a combo', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.v3.sheet = { kind: "add", meal: "dinner", sugg: ${JSON.stringify(SUGG)} }`);
  run(w, `ZiggyV3._test.suggBlock(S.v3.sheet, "dinner")`);
  run(w, `document.querySelectorAll = (q) => q === "[data-v3-sugg]" ? [{ checked: true, dataset: { v3Sugg: "0" } }, { checked: true, dataset: { v3Sugg: "1" } }] : []`);
  w.calls.length = 0;
  await run(w, 'ZiggyV3._test.addSugg()').catch(() => {});
  const call = w.calls.find(c => c.url.endsWith('/today/pool/suggested'));
  assert.ok(call, 'posted');
  assert.deepEqual(JSON.parse(call.options.body).items, [
    { restaurant_id: 'r-102', combo: 'Veg Biryani + 2 Raita', meal: 'dinner' },
    { restaurant_id: 'r-102', item_name: 'Veg Biryani', meal: 'dinner' }]);
});

test('r3 #29: the consent is one line + Details, and "Not now" is remembered', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.v3.sheet = { kind: "add", meal: "lunch", sugg: { imported: false, consent: 'LONG consent text', consent_short: 'Short line', items: [] } }`);
  let html = run(w, `ZiggyV3._test.suggBlock(S.v3.sheet, "lunch")`);
  assert.match(html, /<p class="fine">Short line<\/p><details class="v3-consent-more"><summary class="fine">Details<\/summary><p class="fine">LONG consent text/);
  assert.match(html, /data-v3-act="sugg-later"/);
  w.store['ziggy.v3.sugg-later.2'] = '1';
  html = run(w, `ZiggyV3._test.suggBlock(S.v3.sheet, "lunch")`);
  assert.doesNotMatch(html, /LONG consent text/);
  assert.match(html, /data-v3-act="sugg-open"/);
  run(w, 'S.v3.sheet.suggOpen = true');
  assert.match(run(w, `ZiggyV3._test.suggBlock(S.v3.sheet, "lunch")`), /data-v3-act="sugg-import"/);
});

test('r3 #8: history chips open the menu directly (id), names without an id still search', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.v3.sheet = { kind: "add", meal: "lunch", history: [{ id: 'r-9', name: 'Wok <i>Street</i>' }, { id: null, name: 'Old Place' }] }`);
  let html = run(w, 'ZiggyV3._test.histChips(S.v3.sheet)');
  assert.match(html, /data-v3-place="r-9" data-v3-pname="Wok &lt;i&gt;Street&lt;\/i&gt;"/);
  assert.match(html, /data-v3-hist="Old Place"/);
  run(w, `S.v3.sheet.sugg = ${JSON.stringify(SUGG)}`);
  html = run(w, 'ZiggyV3._test.histChips(S.v3.sheet)');
  assert.match(html, /data-v3-place="r-102"[^>]*data-v3-prating="4.2"/, 'imported places need no extra Swiggy call');
});

test('r3 #6: a usual snack shows under Today as optional; a picked snack is in Place order', async () => {
  const snack = { ref: 'sw:r-101:k', source: 'swiggy', kind: 'order', name: 'Kesari', restaurant: 'Annapoorna', restaurant_id: 'r-101', item_id: 'k',
    price: 60, veg: true, rating: null, eta_min: null, qty: 1, hard: false, reasons: [] };
  const w = world({ today: { ...TODAY, pools: { ...TODAY.pools, snack: [snack] } } }); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  let html = run(w, 'tabBody()');
  assert.match(html, /Snack \(optional\)/);
  assert.match(html, /data-v3-pick="snack"/);
  const none = world(); await none.context.bootPromise; await run(none, 'ZiggyV3._test.load()');
  assert.doesNotMatch(run(none, 'tabBody()'), /Snack \(optional\)/, 'no snack line without a usual snack');
  run(w, `S.v3.data.picks = { snack: { ...S.v3.data.pools.snack[0], how: 'you' } }; S.v3.sheet = { kind: "order" }`);
  html = run(w, 'ZiggyV3.overlays()');
  assert.match(html, /Snack: 1× Kesari/);
  run(w, `S.v3.sheet = { kind: "add", meal: "snack" }`);
  assert.match(run(w, 'ZiggyV3.overlays()'), /data-v3-addmeal="snack" aria-pressed="true"/);
});

test('r3 #2: the address toast says what changed', async () => {
  const w = world(); await w.context.bootPromise;
  assert.equal(run(w, `addressToast({ address: { label: 'Work · OMR' } })`), 'Delivering to Work · OMR');
  const t = run(w, `addressToast({ address: { label: 'Work · OMR' }, address_changed: { kept: 3, not_checked_here: 1 } })`);
  assert.match(t, /Delivering to Work · OMR\. Your 3 usual dishes stay; 1 saved for your other address is greyed until you add it here\./);
  assert.match(t, /Menus and cart are read again/);
});
