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

test('v3 is on by default: four tabs with Cook, and Today is the v3 screen', async () => {
  const w = world(); await w.context.bootPromise;
  assert.equal(run(w, 'ZiggyV3.on()'), true);
  assert.equal(run(w, 'TABS.map(t => t[0]).join()'), 'today,week,cook,saved');
  await run(w, 'ZiggyV3._test.load()');
  const html = run(w, 'tabBody()');
  assert.match(html, /Today's budget/);
  assert.match(html, /₹600/);
  assert.match(html, /\(B\+L\)/);
  assert.match(html, /Usual breakfasts/);
  assert.match(html, /Your usual meals/);
  assert.doesNotMatch(html.replace(/<[^>]+>/g, ' '), /\bpools?\b/i, 'the founder\'s word is "usual meals"');
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

test('integration: v3 Today shows the W4 weekly target vs real spend and the low-budget warning', async () => {
  const w = world(); await w.context.bootPromise;
  await run(w, 'ZiggyV3._test.load()');
  run(w, `S.view.budget = { spend: 0, budget: 1800, weekly_budget: 4200, prorated: true, kind: 'target', actual: { amount: 340, orders: 2, complete: false } };
    S.view.heads_up = [{ kind: 'low_budget', title: '₹300 covers about 3 delivered meals a week', body: 'Plan 3 delivered meals and cook the rest.' }];`);
  const html = run(w, 'tabBody()');
  // ux #23: Mon–Sun spend against the whole week's ₹4,200, never the pro-rated ₹1,800
  assert.match(html, /This week \(Mon–Sun\): ₹340 spent on Swiggy of your ₹4,200 a week \(so far/);
  assert.doesNotMatch(html, /of a ₹1,800/);
  assert.match(html, /₹300 covers about 3 delivered meals a week/);
});

test('usab #17: a week taken over its target by the person\'s own picks is said on v3 Today, with a way out', async () => {
  const w = world(); await w.context.bootPromise;
  await run(w, 'ZiggyV3._test.load()');
  const over = { kind: 'budget', level: 'warn', title: '₹265 over this week\'s target',
    body: 'Planned meals come to ₹565 of your ₹300 target, with 2 meals you picked yourself. Ziggy didn\'t drop your picks: change one, or raise the target.',
    actions: [{ act: 'raise-budget', label: 'Raise the target', amount: 300 }] };
  const skipped = { kind: 'budget', level: 'warn', title: '7 meals didn\'t fit the weekly target', body: 'Raise the weekly target.' };
  run(w, `S.view.budget = { spend: 565, budget: 300, over: true, kind: 'target' }; S.view.heads_up = ${JSON.stringify([skipped, over])};`);
  let html = run(w, 'tabBody()');
  assert.match(html, /v3-week-over/);
  assert.match(html, /₹265 over this week&#39;s target|₹265 over this week's target/);
  assert.match(html, /2 meals you picked yourself/);
  assert.match(html, /data-act="budget">Change the weekly target/);
  assert.match(html, /data-tab="week">See the week/);
  assert.doesNotMatch(html, /7 meals didn/, 'only the over-target heads-up is on Today');
  run(w, `S.view.budget = { spend: 200, budget: 300, over: false, kind: 'target' };`);
  html = run(w, 'tabBody()');
  assert.doesNotMatch(html, /v3-week-over/, 'nothing said while the week fits');
});

test('integration: a live menu lists the W1 hidden dishes with their reasons', async () => {
  const w = world(); await w.context.bootPromise;
  const html = run(w, `ZiggyV3._test.hiddenList({ hidden_items: [{ name: 'Kozhi <i>Varuval</i>', reason: 'The name says “kozhi” (chicken, not veg)' }] })`);
  assert.match(html, /1 dish hidden for you/);
  assert.match(html, /Kozhi &lt;i&gt;Varuval&lt;\/i&gt;<\/b> — The name says “kozhi”/);
  assert.equal(run(w, 'ZiggyV3._test.hiddenList({ hidden_items: [] })'), '');
});

// ---- integration phase 2: W7 suggestions, W2 cart window, W4 charges + Sunday recap ----
const PONGAL = { ref: 'sw:r-101:r-101-i5', source: 'swiggy', kind: 'order', name: 'Ven Pongal', restaurant: 'Annapoorna', restaurant_id: 'r-101',
  item_id: 'r-101-i5', price: 90, veg: true, rating: 4.4, eta_min: 25, qty: 2, hard: false, reasons: [], how: 'you',
  bill: { items: 180, delivery: 17.11, fees: 28.08, gst: 9, total: 234.19, estimated: true, range: [220, 250], label: '~₹234 incl. ₹54 delivery, fees & GST (estimate, ₹220–₹250)' } };

test('integration: suggested favourites ask consent first, then list this meal\'s ticks (escaped, not already pooled)', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, 'S.v3.sheet = { kind: "add", meal: "breakfast" }');
  let html = run(w, `ZiggyV3._test.suggBlock(S.v3.sheet, "breakfast")`);
  assert.equal(html, '', 'nothing before the suggestions are read');
  run(w, `S.v3.sheet.sugg = { imported: false, consent: 'Ziggy will read your last 15 Swiggy orders once', items: [] }`);
  html = run(w, `ZiggyV3._test.suggBlock(S.v3.sheet, "breakfast")`);
  assert.match(html, /last 15 Swiggy orders once/);
  assert.match(html, /data-v3-act="sugg-import"/);
  run(w, `S.v3.sheet.sugg = { imported: true, note: 'Tick the ones you like.', attribution: 'Powered by Swiggy', items: [
    { restaurant_id: 'r-1', restaurant_name: 'Corner Cafe', item_name: 'Cappuccino', pool_meal: 'breakfast', times: 3, rating: null },
    { restaurant_id: 'r-2', restaurant_name: 'Dosa <b>Hut</b>', item_name: 'Masala Dosa', pool_meal: 'breakfast', times: 2, rating: 4.2 },
    { restaurant_id: 'r-3', restaurant_name: 'Biryani Junction', item_name: 'Veg Biryani', pool_meal: 'dinner', times: 4, rating: null }] }`);
  html = run(w, `ZiggyV3._test.suggBlock(S.v3.sheet, "breakfast")`);
  assert.doesNotMatch(html, /Cappuccino/, 'already in the breakfast pool');
  assert.doesNotMatch(html, /Veg Biryani/, 'a dinner suggestion is not offered for breakfast');
  assert.match(html, /Masala Dosa/);
  assert.ok(!html.includes('<b>Hut</b>'), 'escaped');
  assert.match(html, /Add ticked to your usual breakfasts/);
  assert.match(html, /Powered by Swiggy/);
  assert.match(html, /data-v3-act="sugg-delete"/);
});

test('integration: too early for the cart → "Cart opens … · remind me" instead of Place order (W2 cart_state, usab #21)', async () => {
  const w = world({ today: { ...TODAY, picks: { breakfast: PONGAL } } }); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.view.grid = [{ date: todayIso(), meals: { breakfast: { session_id: 7, status: 'active', order: { cart_state: 'early', label: '08:15', cart_opens_label: '07:45' } } } }]`);
  let html = run(w, 'ZiggyV3._test.actions(S.v3.data)');
  assert.match(html, /data-v3-remind="breakfast"[^>]*>.*Cart opens 07:45 · remind me/);
  assert.doesNotMatch(html, /Place order/);
  run(w, 'S.v3.sheet = { kind: "order" }');
  html = run(w, 'ZiggyV3.overlays()');
  assert.match(html, /Cart opens 07:45 · remind me/);
  assert.match(html, /Order by 08:15/);
  assert.doesNotMatch(html, /data-v3-cart="breakfast"/);
  run(w, `S.view.grid[0].meals.breakfast.order = { cart_state: 'open', label: '08:15' }`);
  html = run(w, 'ZiggyV3._test.actions(S.v3.data)');
  assert.match(html, /Place order/);
  assert.doesNotMatch(html, /remind me/);
});

test('integration: a Swiggy pick shows its whole bill (W4 charges); typed picks show only their price', async () => {
  const typed = { ...TODAY.pools.breakfast[0], qty: 1, how: 'you', reasons: [] };
  const w = world({ today: { ...TODAY, meals: ['breakfast', 'lunch'], picks: { breakfast: PONGAL, lunch: { ...typed, ref: 't:9', name: 'Home Thali', price: 150 } },
    budget: { ...TODAY.budget, picked: 330, left: 270, picked_bill: 384.19, bill_estimated: true } } });
  await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  const html = run(w, 'tabBody()');
  assert.match(html, /₹180 <span class="v3-bill"[^>]*>· ~₹234 with delivery, fees &amp; GST \(estimate\)/);
  assert.equal((html.match(/class="v3-bill"/g) || []).length, 1, 'no fees invented for the typed pick');
  assert.match(html, /About ₹384 with Swiggy delivery, fees &amp; GST \(estimate until Swiggy shows the bill\)/);
  assert.match(html, /<b class="v3-left">₹216 left after fees<\/b> <span class="tag  v3-left-tag">estimate<\/span>/, '600 − 384.19, marked an estimate');
});

test('R2: "left" is net of fees — the server figure wins, a real bill says so, no Swiggy pick = plain left', async () => {
  const typed = { ...TODAY.pools.breakfast[0], qty: 1, how: 'you', reasons: [] };
  let w = world({ today: { ...TODAY, picks: { breakfast: PONGAL }, budget: { ...TODAY.budget, picked: 180, left: 420, picked_bill: 234.19, bill_estimated: true, left_after_fees: 351.5, left_estimated: false } } });
  await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  let html = run(w, 'tabBody()');
  assert.match(html, /₹351\.5 left after fees|₹352 left after fees/);
  assert.match(html, /class="tag good v3-left-tag">real bill</);
  w = world({ today: { ...TODAY, picks: { breakfast: PONGAL }, budget: { ...TODAY.budget, amount: 200, picked: 180, left: 20, picked_bill: 234.19, bill_estimated: true } } });
  await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  assert.match(run(w, 'tabBody()'), /₹34\.19 over after fees|₹34 over after fees/);
  w = world({ today: { ...TODAY, picks: { breakfast: typed }, budget: { ...TODAY.budget, picked: 140, left: 460, picked_bill: null } } });
  await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  html = run(w, 'tabBody()');
  assert.match(html, /<b class="v3-left">₹460 left<\/b>/);
  assert.doesNotMatch(html, /after fees/);
});

test('integration: the budget card reads the server payload — dishes picked, left after fees (server, fees included)', async () => {
  // what today.view sends now: picked/left include fees, picked_dishes is the dish sum,
  // left_after_fees = left, left_estimated until the Swiggy cart shows the real bill
  const w = world({ today: { ...TODAY, picks: { breakfast: PONGAL }, budget: { ...TODAY.budget, picked: 234.19, picked_dishes: 180,
    left: 365.81, left_after_fees: 365.81, left_estimated: true, picked_bill: 234.19, bill_estimated: true, fees_included: true } } });
  await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  const html = run(w, 'tabBody()');
  assert.match(html, /₹180 picked/, 'the dish sum, the fees are on the next line');
  assert.match(html, /₹365\.81 left after fees|₹366 left after fees/);
  assert.match(html, /class="tag  v3-left-tag">estimate</);
});

test('R2 usual meals: on a phone each meal line has its own drawer and there is no separate block', async () => {
  const w = world({ width: 375 }); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  let html = run(w, 'tabBody()');
  assert.doesNotMatch(html, /class="v3-pools"/, 'no separate usual-meals block on a phone');
  assert.match(html, /data-v3-usual="breakfast" aria-expanded="false"[^>]*>Usual \(1\)/);
  assert.match(html, /data-v3-usual="lunch" aria-expanded="false"/);
  assert.match(html, /Dinner \(not today\)<\/span><button[^>]*data-v3-usual="dinner"/, 'a meal that is not on today stays reachable');
  assert.doesNotMatch(html, /id="v3-usual-breakfast"/, 'closed until tapped');
  run(w, 'S.v3.usualOpen = { breakfast: true }');
  html = run(w, 'tabBody()');
  assert.match(html, /data-v3-usual="breakfast" aria-expanded="true"/);
  const drawer = html.slice(html.indexOf('id="v3-usual-breakfast"'));
  assert.match(drawer, /aria-label="Your usual breakfasts"/);
  assert.match(drawer, /Cappuccino/);
  assert.match(drawer, /data-v3-remove="breakfast\|t:1"[^>]*aria-label="Remove Cappuccino from your usual breakfasts"/);
  assert.match(drawer, /data-v3-add="breakfast"[^>]*>.*New item \/ restaurant/);
  assert.ok(html.indexOf('id="v3-usual-breakfast"') < html.indexOf('data-v3-meal="lunch"'), 'the drawer opens under its own meal line');
});

test('R2 usual meals: from 1024 px the three panels sit side by side and the lines have no drawer', async () => {
  const w = world({ width: 1280 }); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  const html = run(w, 'tabBody()');
  assert.match(html, /class="v3-pools"/);
  for (const h of ['Usual breakfasts', 'Usual lunches', 'Usual dinners']) assert.match(html, new RegExp(`<h2 class="h3" id="v3-pool-[a-z]+">${h}</h2>`));
  assert.doesNotMatch(html, /data-v3-usual=/);
});

test('integration: Sunday recap card shows only real numbers', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  assert.equal(run(w, 'ZiggyV3._test.recapCard(S.v3.data)'), '', 'no recap on other days');
  run(w, `S.v3.data.recap = { target: 4200, actual: 0, orders: 0, kept: null, finished: false, best_day: null, worst_day: null, share_text: null,
    note: 'Read your Swiggy order history to see what you kept', basis: 'Real Swiggy bills only.' }`);
  let html = run(w, 'ZiggyV3._test.recapCard(S.v3.data)');
  assert.match(html, /₹0<\/b> <span class="muted">spent on Swiggy of your ₹4,200 target · 0 orders/);
  assert.doesNotMatch(html, /kept<\/b>|data-v3-recap-share/);
  assert.match(html, /Read your Swiggy order history/);
  run(w, `S.v3.data.recap = { target: 4200, actual: 3860, orders: 9, kept: 340, finished: true, best_day: { date: '2026-11-03', day: 'Tue', spent: 300 },
    worst_day: { date: '2026-11-07', day: 'Sat', spent: 900 }, share_text: '₹340 kept this week on Ziggy — that\\'s a movie.', note: null, basis: 'Real Swiggy bills only.' }`);
  html = run(w, 'ZiggyV3._test.recapCard(S.v3.data)');
  assert.match(html, /₹340 kept/);
  assert.match(html, /Lightest ordering day Tue \(₹300\) · heaviest Sat \(₹900\)/);
  assert.match(html, /data-v3-recap-share=/);
});
