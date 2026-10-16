// Fix round 3 "menus-offers" (UX audit 16 Oct 2026 #3 #9 #16-#19 #26 #34 #35) in v3, without a browser.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const v3 = fs.readFileSync('smartplate/static/v3/v3.js', 'utf8');
const app = fs.readFileSync('smartplate/static/app.js', 'utf8').replace('boot().catch', 'globalThis.bootPromise = boot().catch');

const TODAY = {
  date: '2026-11-02', weekday: 'Monday', meals: ['breakfast', 'lunch'], label: 'B+L',
  budget: { amount: 600, source: 'week', default: 600, presets: [300, 600, 1000], picked: 0, left: 600, unpriced: [] },
  weather: { known: false }, rules: { diet: 'nonveg', allergens: [], medical: [], min_rating: 4, max_eta: 30, nutrition: false },
  pools: { breakfast: [], lunch: [{ ref: 'sw:r1:i1', source: 'swiggy', kind: 'order', name: 'Dal Makhani', restaurant: 'Dhaba', restaurant_id: 'r1', item_id: 'i1', price: 190, veg: true, rating: 4.1, eta_min: 40, qty: 1, hard: false, reasons: [] }], dinner: [] },
  picks: { lunch: { ref: 'sw:r1:i1', source: 'swiggy', kind: 'order', name: 'Dal Makhani', restaurant: 'Dhaba', restaurant_id: 'r1', item_id: 'i1', price: 190, qty: 1, reasons: [] } },
  modes: {}, price_compare: { swiggy: 190, others: 'coming', basis: 'x' }, swiggy: { connected: true, address: 'Home' }, account_saved: true, share_text: 'Today',
};
const MENU = {
  restaurant: { id: 'r1', name: 'Punjab Da Dhaba', offer: { label: '₹100 off above ₹399', text: '₹100 OFF ABOVE ₹399' } },
  items: [
    { id: 'i1', name: 'Dal Makhani', price: 190, veg: true, in_stock: true, categories: ['Curries'], bestseller: true, image: 'https://media-assets.swiggy.com/x.jpg' },
    { id: 'i2', name: 'Veg Thali', price: 199, list_price: 230, veg: true, in_stock: true, categories: ['Thali'], best_value: true },
    { id: 'i3', name: 'Family Pack', price: 890, veg: false, in_stock: true, has_options: true, categories: ['Combos'] },
    { id: 'i4', name: 'Lassi', price: 80, veg: true, in_stock: false, categories: ['Beverages'] },
  ],
  total_items: 140, value: { item_id: 'i2', per_meal: 300, why: 'Most food for the money among dishes within your ₹300 a meal.' },
  hidden_items: [], source: 'cache', age_min: 12, refreshing: false, partial: false,
};

function world(extra = {}) {
  const calls = [];
  const element = { innerHTML: '', classList: { toggle() {}, add() {} }, appendChild() {}, remove() {}, addEventListener() {}, setAttribute() {} };
  const store = { 'smartplate.user': '2' };
  const view = { plan: { id: 42, mode: 'balanced', mode_label: 'Balanced', week_start: '2026-11-02' },
    user: { id: 2, name: 'Priya', diet: 'nonveg', weekly_budget: 4200, allergens: [], medical: [], health_targets: {} },
    budget: { spend: 0, budget: 4200 }, counts: {}, grid: [], nutrition: { daily_target: { kcal: 1800 }, daily_avg: { kcal: 900 } } };
  const replies = {
    '/api/meta': { modes: {}, mode_outcomes: {}, version: '1.1.0', swiggy_provider: 'live', fixture_data: false },
    '/api/users': [{ id: 2, name: 'Priya' }], '/api/user/2/plan': view, '/api/plan/42/orders': { results: [] },
    '/api/user/2/swiggy': { connected: true, address: { id: 'a1', label: 'Home' } }, '/api/user/2/swiggy/favourites': [],
    '/api/user/2/today': TODAY,
    '/api/user/2/swiggy/live-cart': { cart: { item: 'Dal Makhani', restaurant: 'Dhaba', to_pay: 241, orderable: true } },
    '/api/user/2/swiggy/live-cart/offers': { coupons: [{ code: 'TRYNEW', title: 'Flat ₹50 off above ₹199' }] },
    '/api/user/2/swiggy/live-menu?restaurant_id=r1&restaurant_name=Punjab%20Da%20Dhaba': MENU,
    '/api/user/2/swiggy/nearby?meal=lunch': { meal: 'lunch', note: 'Within your rules.', dishes: [{ restaurant_id: 'r2', restaurant: 'Green Bowl', item_id: 'g1', name: 'Rajma Rice Bowl', price: 190, rating: 4.5 }] },
    ...extra,
  };
  const context = vm.createContext({
    document: { getElementById: () => element, querySelectorAll: () => [], querySelector: () => null, createElement: () => element, addEventListener() {}, body: element },
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    location: { search: '' }, URLSearchParams, history: { replaceState() {} },
    fetch: async (url, options) => { calls.push({ url, options });
      return { ok: !!replies[url], status: replies[url] ? 200 : 404, json: async () => replies[url] || { error: 'Unexpected route' } }; },
    setTimeout: () => {}, clearTimeout: () => {}, console,
  });
  vm.runInContext(v3, context);
  vm.runInContext(app, context);
  return { context, calls };
}
const run = (w, code) => vm.runInContext(code, w.context);
const tick = () => new Promise(r => setImmediate(r));

test('#16 boot in v3: no dish-photo search, no quick strip, no cart read until a pick can be carted', async () => {
  const w = world(); await w.context.bootPromise;
  const urls = w.calls.map(c => c.url);
  assert.ok(!urls.some(u => u.includes('/swiggy/photos')), 'no photo lookups');
  assert.ok(!urls.some(u => u.endsWith('/swiggy/live-cart')), 'no cart read on boot');
  assert.ok(!urls.some(u => u.includes('/options')), 'no quick strip');
  await run(w, 'ZiggyV3._test.load()'); await tick(); await tick();
  assert.equal(w.calls.filter(c => c.url.endsWith('/swiggy/live-cart')).length, 1, 'read once: lunch is a Swiggy pick');
  await run(w, 'ZiggyV3._test.load()'); await tick();
  assert.equal(w.calls.filter(c => c.url.endsWith('/swiggy/live-cart')).length, 1, 'once a session');
});

test('#9 coupons are read at the cart only and shown, never applied', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()'); await tick(); await tick();
  run(w, 'ZiggyV3._test.cartOffers()'); await tick(); await tick();
  const html = run(w, 'ZiggyV3._test.cartOffers()');
  assert.match(html, /TRYNEW/);
  assert.match(html, /Apply one in Swiggy/);
  assert.equal(w.calls.filter(c => c.url.endsWith('/live-cart/offers')).length, 1);
});

test('#35 the compare row stays hidden with one source', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  assert.equal(run(w, 'ZiggyV3._test.compareRow(S.v3.data)'), '');
  assert.doesNotMatch(run(w, 'tabBody()'), /Other apps · coming/);
});

test('#17 #9 #19 #34 the menu: sections, search, thumbnails, badges, struck price, options, offer, ETA in min', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.v3.sheet = { kind: 'add', meal: 'lunch', tab: 'swiggy', place: { id: 'r1', name: 'Punjab Da Dhaba', rating: 4.1, eta: '40' } }`);
  await run(w, `ZiggyV3._test.openPlace('r1', 'Punjab Da Dhaba', '4.1', '40', '')`);
  const html = run(w, 'ZiggyV3._test.overlays()');
  assert.match(html, /<details class="v3-sec"[^>]*><summary class="k">Curries/);
  assert.match(html, /id="v3-mq"[^>]*placeholder="Search this menu"/);
  assert.match(html, /data-v3-vegonly/);
  assert.match(html, /<img class="v3-thumb" src="https:\/\/media-assets\.swiggy\.com\/x\.jpg"/);
  assert.match(html, /Best value/);
  assert.match(html, /Bestseller/);
  assert.match(html, /<s class="muted">₹230<\/s> ₹199/);
  assert.match(html, /4\.1★ · 40 min/);
  assert.match(html, /₹100 off above ₹399/);
  assert.match(html, /Showing 4 of 140/);
  assert.match(html, /Most food for the money/);
  assert.match(html, /data-v3-additem="i3"(?![^>]*disabled)/, 'a dish with options can be added');
  assert.match(html, /sizes or add-ons: chosen in Swiggy/);
  assert.match(html, /data-v3-additem="i4"[^>]*disabled/, 'out of stock stays disabled');
  assert.match(html, /Menu checked 12 min ago/);
});

test('#18 an open place takes the sheet: suggestions, tabs and meal chips step aside', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.v3.sheet = { kind: 'add', meal: 'lunch', tab: 'swiggy', sugg: { imported: false, consent: 'Read my orders?' } }`);
  assert.match(run(w, 'ZiggyV3._test.overlays()'), /Suggest from my Swiggy orders/);
  await run(w, `ZiggyV3._test.openPlace('r1', 'Punjab Da Dhaba', '4.1', '40', '')`);
  const html = run(w, 'ZiggyV3._test.overlays()');
  assert.doesNotMatch(html, /Suggest from my Swiggy orders/);
  assert.doesNotMatch(html, /data-v3-addtab/);
  assert.doesNotMatch(html, /data-v3-addmeal/);
  assert.match(html, /All places/);
});

test('#17 veg only hides dishes not marked veg', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.v3.sheet = { kind: 'add', meal: 'lunch', tab: 'swiggy', vegOnly: true }`);
  await run(w, `ZiggyV3._test.openPlace('r1', 'Punjab Da Dhaba', '', '', '')`);
  assert.doesNotMatch(run(w, 'ZiggyV3._test.overlays()'), /Family Pack/);
});

test('#34 ETA text: numbers get "min", Swiggy text stays', async () => {
  const w = world(); await w.context.bootPromise;
  assert.equal(run(w, 'ZiggyV3._test.etaText(30)'), '30 min');
  assert.equal(run(w, "ZiggyV3._test.etaText('25-30 mins')"), '25-30 mins');
  assert.equal(run(w, 'ZiggyV3._test.etaText(null)'), null);
});

test('#26 Swap shows Nearby now from the catalogue', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.v3.sheet = { kind: 'swap', meal: 'lunch', nearby: { note: 'Within your rules.', dishes: [{ restaurant_id: 'r2', restaurant: 'Green Bowl', item_id: 'g1', name: 'Rajma Rice Bowl', price: 190, rating: 4.5 }] } }`);
  const html = run(w, 'ZiggyV3._test.swapSheet()');
  assert.match(html, /Nearby now/);
  assert.match(html, /data-v3-nearby="0"[^]*Rajma Rice Bowl[^]*Green Bowl · ₹190 · 4\.5★/);
});
