// Fix round 3, "today-truth" (usab audit 16 Oct 2026): v3 Today without a browser. Cart-open
// reminder button (#21), per-meal reminder sheet (#22), rule defaults tag (#10), "+ Add another"
// (#25), "Pick anyway?" (#27), Decide note with actions (#28), asking the meal time once (#14),
// other meals behind "Show other meals" (#32), Swap without Swiggy, Eggetarian/Jain (#11),
// and the Week's own "Decide for me" when meals are open (#24).
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
const DINNER = { ref: 'sw:r-104:r-104-i1', source: 'swiggy', kind: 'order', name: 'Dal Makhani', restaurant: 'Punjab Da Dhaba',
  restaurant_id: 'r-104', item_id: 'r-104-i1', price: 220, veg: true, rating: 4.1, eta_min: 40, qty: 1, hard: false, how: 'you',
  reasons: ['40 min away, over your 30 min'] };
const WHEN = { order_at: '19:45', label: '19:45', cart_state: 'early', cart_opens_label: '18:15', session_id: 31, reminder: false };
const withDinner = (extra = {}) => ({ ...TODAY, meals: ['lunch', 'dinner'], pools: { ...TODAY.pools, dinner: [DINNER] }, ...extra });

test('#21: the button says when the cart opens, from today\'s own pick (not the Week cell)', async () => {
  const pick = { ...DINNER, reasons: [], when: { ...WHEN } };
  const w = world({ today: withDinner({ picks: { dinner: pick } }) }); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  let html = run(w, 'ZiggyV3._test.actions(S.v3.data)');
  assert.match(html, /data-v3-remind="dinner"[^>]*>.*Cart opens 18:15 · remind me/);
  assert.doesNotMatch(html, /Place order/);
  run(w, 'S.v3.data.picks.dinner.when.reminder = true');
  assert.match(run(w, 'ZiggyV3._test.actions(S.v3.data)'), /Cart opens 18:15 · reminder set/);
  run(w, 'S.v3.data.picks.dinner.when.cart_state = "open"');
  assert.match(run(w, 'ZiggyV3._test.actions(S.v3.data)'), /Place order/);
});

test('#22: Remind me is for this one meal, with its own calendar file', async () => {
  const w = world({ today: withDinner({ picks: { dinner: { ...DINNER, reasons: [], when: { ...WHEN } } } }) }); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, 'S.v3.sheet = { kind: "remind", meal: "dinner" }');
  const html = run(w, 'ZiggyV3.overlays()');
  assert.match(html, /Remind me: dinner/);
  assert.match(html, /nudges you at 18:15, when the Swiggy cart opens\. Order by 19:45/);
  assert.match(html, /data-v3-ics="\/api\/user\/2\/reminders\.ics\?meal=dinner&amp;date=2026-11-02"/);
  assert.match(html, /Only this meal/);
});

test('#10: rules row says when rating and time are Ziggy\'s defaults or learned', async () => {
  const w = world({ today: { ...TODAY, rules: { ...TODAY.rules, min_rating: 0, max_eta: 45, source: { min_rating: 'ziggy', max_eta: 'ziggy' } } } });
  await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  let html = run(w, 'ZiggyV3._test.rulesRow(S.v3.data)');
  assert.match(html, /Any rating/); assert.match(html, /≤ 45 min/); assert.match(html, /Ziggy defaults/);
  run(w, `S.v3.data.rules.source = { min_rating: 'learned', max_eta: 'you' }; S.v3.data.rules.defaults_basis = 'Learned from your own places: rated about 4.1★.'`);
  html = run(w, 'ZiggyV3._test.rulesRow(S.v3.data)');
  assert.match(html, /Learned from your places/); assert.match(html, /title="Learned from your own places: rated about 4\.1★\."/);
  run(w, `S.v3.data.rules.source = { min_rating: 'you', max_eta: 'you' }`);
  assert.doesNotMatch(run(w, 'ZiggyV3._test.rulesRow(S.v3.data)'), /defaults|Learned/);
});

test('#25 #27: + Add another in the meal select; a greyed dish asks "Pick anyway?" with a relax chip', async () => {
  const w = world({ today: { ...withDinner(), rules: { ...TODAY.rules, max_eta: 30 } } }); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  let html = run(w, 'ZiggyV3._test.mealLine(S.v3.data, "dinner")');
  assert.match(html, /<option value="__add__">\+ Add another dinner…<\/option>/);
  run(w, 'S.v3.confirm = { meal: "dinner", ref: "sw:r-104:r-104-i1" }');
  html = run(w, 'ZiggyV3._test.mealLine(S.v3.data, "dinner")');
  assert.match(html, /Dal Makhani<\/b>: 40 min away, over your 30 min\. Pick anyway\?/);
  assert.match(html, /data-v3-confirm="pick">Pick anyway/);
  assert.match(html, /data-v3-relax="max_eta:45">Relax to ≤45 min/);
  assert.equal(run(w, 'JSON.stringify(ZiggyV3._test.relaxFor({ rating: 3.9, eta_min: 20 }, { min_rating: 4, max_eta: 30 }))'),
    JSON.stringify({ key: 'min_rating', value: 3.5, label: 'Relax to 3.5★+' }));
  assert.equal(run(w, 'ZiggyV3._test.relaxFor({ eta_min: 70 }, { max_eta: 30 }).label'), 'Any delivery time');
});

test('#28: a Decide that could not fill a meal says why, with buttons', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, `S.v3.decideNote = { note: "Couldn't fill lunch: No safe lunch in your favourites (Peanut chikki: Contains peanut). Add one or try Surprise me.",
    actions: [{ act: 'add', meal: 'lunch', label: 'Add a lunch' }, { act: 'surprise', label: 'Surprise me' }] }`);
  const html = run(w, 'tabBody()');
  assert.match(html, /No safe lunch in your favourites \(Peanut chikki/);
  assert.match(html, /data-v3-add="lunch">Add a lunch/); assert.match(html, /data-v3-surprise="1">Surprise me/);
});

test('#14: asks once when dinner is, offering the late time the Swiggy history suggests first', async () => {
  const ask = { meal: 'dinner', options: ['20:00', '21:00', '22:00', '23:00'], hint: { meal: 'dinner', at: '22:30', why: 'Most of your recent Swiggy dinners were ordered after 10 pm.' } };
  const w = world({ today: withDinner({ ask_meal_time: ask, meal_times: { dinner: { at: '20:30', own: false } } }) }); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  const html = run(w, 'tabBody()');
  assert.match(html, /When do you usually eat dinner\?<\/b> Most of your recent Swiggy dinners/);
  assert.match(html, /data-v3-mealtime="dinner:22:30">22:30<\/button><button class="chip small" data-v3-mealtime="dinner:20:00">/);
  assert.match(html, /data-v3-mealtime="dismiss">Not now/);
  run(w, 'S.v3.data.ask_meal_time = null; S.v3.data.meal_times.dinner = { at: "23:00", own: true }');
  assert.match(run(w, 'ZiggyV3._test.mealLine(S.v3.data, "dinner")'), /Dinner <span class="fine">· 23:00<\/span>/);
  run(w, 'S.v3.sheet = { kind: "rules" }');
  const sheet = run(w, 'ZiggyV3.overlays()');
  assert.match(sheet, /Meals I order/); assert.match(sheet, /data-v3-mymeal="breakfast" aria-pressed="false"/);
  assert.match(sheet, /<input type="time" name="dinner" value="23:00"/);
});

test('#32: meals that are not today sit behind "Show other meals"', async () => {
  const w = world({ width: 390 }); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  const html = run(w, 'tabBody()');
  assert.match(html, /<details class="v3-other-usual"><summary class="fine" data-v3-other="1">Show other meals<\/summary>.*Dinner \(not today\)/s);
});

test('Swap without Swiggy says why it is empty and what to do', async () => {
  const w = world(); await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  run(w, 'S.v3.sheet = { kind: "swap", meal: "dinner" }');
  const html = run(w, 'ZiggyV3.overlays()');
  assert.match(html, /Swiggy isn't connected yet, so Swap can only offer dishes you add yourself/);
  assert.doesNotMatch(html, /passes your rules yet/);
});

test('#11: onboarding offers Eggetarian and Jain / no onion-garlic; the rules chip names them', async () => {
  const w = world(); await w.context.bootPromise;
  run(w, 'ZiggyV3.startOnboard()');
  const html = run(w, 'ZiggyV3.screen()');
  assert.match(html, /data-v3-ob="diet:egg".*Eggetarian.*Vegetarian, plus eggs/s);
  assert.match(html, /data-v3-ob="diet:jain".*Jain \/ no onion-garlic.*no onion, garlic or root vegetables/s);
  await run(w, 'ZiggyV3._test.load()');
  run(w, 'S.v3.data.rules.diet = "jain"');
  assert.match(run(w, 'ZiggyV3._test.rulesRow(S.v3.data)'), /Jain \/ no onion-garlic/);
});

test('#24: the Week offers its own Decide for me only while meals are open', async () => {
  const w = world(); await w.context.bootPromise;
  run(w, `S.view.grid = [{ date: todayIso(), meals: { lunch: { session_id: 5, kind: 'skip', open: true, status: 'active', reasons: ['Not chosen yet: add a usual lunch or tap Decide for me.'] } } }]`);
  assert.match(run(w, 'ZiggyV3.weekTop()'), /data-v3-weekdecide="1">Decide for me/);
  assert.match(run(w, 'mealRow(S.view.grid[0].meals.lunch, "lunch")'), /<b>Open /);
  run(w, 'S.view.grid[0].meals.lunch.open = false');
  assert.doesNotMatch(run(w, 'ZiggyV3.weekTop()'), /weekdecide/);
});
