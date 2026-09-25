// Test browser-independent UI state without a browser dependency or CDN.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('smartplate/static/app.js', 'utf8').replace(
  'boot().catch', 'globalThis.bootPromise = boot().catch');

function fixture() {
  const calls = [];
  const element = { innerHTML: '', classList: { toggle() {} }, appendChild() {}, remove() {}, addEventListener() {}, setAttribute() {} };
  const view = {
    plan: { id: 42, mode: 'balanced', mode_label: 'Balanced', week_start: '2026-09-14' },
    user: { id: 2, name: 'Meera', city: 'Chennai', diet: 'vegan', rating_floor: 4.2,
      weekly_budget: 1500, allergens: ['dairy'], medical: [], health_targets: {} },
    budget: { spend: 1000, budget: 1500, pct_used: 66 }, counts: { delivery: 1, cook: 0, skip: 20 },
    carbon: { total_kg: 1, band: 'low' }, summary_reasons: [], week_context: [], grid: [],
    nutrition: { daily_target: { kcal: 1800, protein_g: 55 } },
  };
  const replies = {
    '/api/meta': { modes: { balanced: 'Balanced' }, mode_outcomes: {}, version: '1.1.0', swiggy_provider: 'simulated' },
    '/api/users': [{ id: 1, name: 'Sample profile' }, { id: 2, name: 'Meera' }],
    '/api/user/2/plan': view,
    '/api/plan/42/orders': { attempted: 1, placed: 1, substituted: 0, failed: 0,
      results: [{ day: 0, meal: 'lunch', item: 'Meal', state: 'placed', placed: true, substituted: true, substitution: null }] },
  };
  const context = vm.createContext({
    document: { getElementById: () => element, querySelectorAll: () => [], querySelector: () => null,
      createElement: () => element, addEventListener() {}, body: element },
    localStorage: { getItem: () => '2', setItem() {} },
    fetch: async (url, options) => { calls.push({ url, options });
      return { ok: !!replies[url], json: async () => replies[url] || { error: 'Unexpected route' } }; },
    setTimeout: () => {}, console,
  });
  vm.runInContext(source, context);
  return { context, calls, element };
}

test('boot resumes selected profile and latest plan, including saved orders', async () => {
  const { context, calls } = fixture();
  await context.bootPromise;
  assert.equal(vm.runInContext('S.userId', context), 2);
  assert.equal(vm.runInContext('S.planId', context), 42);
  assert.equal(vm.runInContext('S.exec.placed', context), 1);
  assert.ok(calls.every(c => c.options.method === 'GET'));
});

test('empty community rendering does not recursively fetch', async () => {
  const { context, calls } = fixture(); await context.bootPromise;
  const before = calls.length;
  const html = vm.runInContext('communityPanel()', context);
  assert.match(html, /No templates yet/);
  assert.equal(calls.length, before);
});

test('saved substituted orders render without transient substitution details', async () => {
  const { context } = fixture(); await context.bootPromise;
  assert.match(vm.runInContext('ordersPanel()', context), /substituted/);
});

test('settings render persisted inputs and escape profile names', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext('S.view.user.name = \'<img src=x onerror="alert(1)">\'', context);
  const html = vm.runInContext('settingsPanel()', context);
  assert.ok(!html.includes('<img'));
  assert.match(html, /name="weekly_budget"/);
  assert.match(html, /value="1500"/);
  assert.match(html, /value="vegan" selected/);
});

test('busy action guard ignores a second click during the same action', async () => {
  const { context } = fixture(); await context.bootPromise;
  await vm.runInContext(`(async () => {
    let release; globalThis.actionCount = 0;
    const first = guard(async () => { actionCount++; await new Promise(r => release = r); });
    await guard(async () => { actionCount++; }); release(); await first;
  })()`, context);
  assert.equal(context.actionCount, 1);
});

/* ---- everyday screens ---- */
function richView(context) {
  vm.runInContext(`S.view.plan.week_start = '2026-09-21';
    const cell = { kind: 'delivery', item: 'Veg <b>Meals</b>', restaurant: 'Saravana', cost: 150, rating: 4.5,
      session_id: 9, status: 'active', pinned: true, usual: true, reasons: ['Veg Meals from Saravana (₹150)', 'Recent reviews: loved (3 read).'],
      order: { order_at: '12:05', why: 'arrives about 12:40' }, handoff_url: 'https://www.swiggy.com/search?query=x' };
    S.view.grid = [{ day: 'Fri', date: '2026-09-25', meals: { lunch: cell,
      dinner: { kind: 'cook', item: 'Cook: Dal + rice', recipe_key: 'dal_rice', cost: 45, session_id: 10, status: 'active', reasons: [] } } }];
    S.view.week_context = [{ day: 'Fri', date: '2026-09-25', weather: 'rain', temp_c: 28, rain_prob: 80, festival: 'Gandhi Jayanti', festival_effect: 'holiday' }];
    S.view.next_up = { day_index: 0, day: 'Fri', date: '2026-09-25', meal: 'lunch', when: 'Today', cell };
    S.view.heads_up = [{ level: 'warn', icon: '₹', kind: 'budget', title: '2 meals didn\\'t fit', body: 'Raise it' }];
    S.view.learning = { favourites: 3, ratings: 1, orders: 2 };`, context);
}

test('no saved profile opens the welcome screen instead of a sample', async () => {
  const { context, calls, element } = fixture();
  vm.runInContext("localStorage.getItem = () => null", context);
  await vm.runInContext('boot()', context);
  assert.equal(vm.runInContext('S.welcome', context), true);
  assert.match(element.innerHTML, /Set up my week/);
  assert.ok(!calls.some(c => c.url.includes('/plan')));
});

test('today shows the next meal with order-by time, hand-off and escaped names', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  const html = vm.runInContext('todayScreen()', context);
  assert.match(html, /Order by 12:05/);
  assert.match(html, /Order on Swiggy/);
  assert.match(html, /data-confirm="9"/);
  assert.match(html, /2 meals didn&#39;t fit/);
  assert.ok(!html.includes('<b>Meals</b>'));
});

test('week rows are draggable and carry weather and holidays', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  const html = vm.runInContext('weekScreen()', context);
  assert.match(html, /data-meal="9" draggable="true"/);
  assert.match(html, /Gandhi Jayanti/);
  assert.match(html, /order by 12:05/);
  vm.runInContext('S.moving = 10', context);
  assert.match(vm.runInContext('weekScreen()', context), /Tap to swap here/);
});

test('shortlist sheet lists usual places, new picks and hidden counts', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.sheet = { sid: 9, data: { session: { id: 9, day: 'Fri', date: '2026-09-25', meal: 'lunch' },
    limits: { left_week: 400, left_day: null }, has_favourites: true, usual_more: 0, timing_tip: null, current: null,
    usual: [{ restaurant: 'A"B', rating: 4.5, eta_min: 25, favourite: true, more: 2,
      dishes: [{ item_id: 1, name: 'Dosa', price: 120, fits: true, why: 'fits your budget', instructions: [] }] }],
    new: [{ item_id: 2, name: 'Bowl', price: 200, fits: false, why: '₹10 over what\\'s left', restaurant: 'New', instructions: [] }],
    cook: [{ recipe_key: 'dal_rice', name: 'Dal + rice', price: 45 }], hidden: { not_safe: 4, below_rating: 1, not_again: 0 } } }`, context);
  const html = vm.runInContext('sheetDialog()', context);
  assert.match(html, /Your usual places/);
  assert.match(html, /A&quot;B/);
  assert.match(html, /class="dish over/);
  assert.match(html, /4 not safe for your allergies or diet/);
  assert.match(html, /data-cook="dal_rice"/);
});

test('onboarding walks five steps and validates before moving on', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext('startOnboard()', context);
  assert.match(vm.runInContext('onboardingScreen()', context), /What do you eat/);
  vm.runInContext("onboardChip('allergens', 'peanut', true); onboardChip('diet', 'veg', false)", context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(S.onboard.d.allergens)', context)), ['peanut']);
  assert.equal(vm.runInContext('S.onboard.d.diet', context), 'veg');
  vm.runInContext("S.onboard.step = 1; onboardChip('meals', 'lunch', true); onboardChip('meals', 'dinner', true)", context);
  await assert.rejects(vm.runInContext("onboardNav('next')", context), /at least one meal/);
  vm.runInContext("S.onboard.step = 3; S.onboard.suggest = { feasible: true, tight: 1500, suggested: 2000, roomy: 2500 }", context);
  assert.match(vm.runInContext('onboardingScreen()', context), /Usual · ₹2,000/);
});

test('private profile keys are sent as a header and merged into the profile list', async () => {
  const { context, calls } = fixture(); await context.bootPromise;
  vm.runInContext(`const bag = {}; localStorage.getItem = (k) => bag[k] ?? null; localStorage.setItem = (k, v) => { bag[k] = v; };
    keys.put(2, 'secret-key-123456789', 'Meera')`, context);
  await vm.runInContext("api('/api/user/2/plan')", context);
  assert.equal(calls.at(-1).options.headers['X-SmartPlate-Key'], 'secret-key-123456789');
  const users = JSON.parse(vm.runInContext("JSON.stringify(mergeUsers([{ id: 1, name: 'Sample' }, { id: 2, name: 'Meera' }]))", context));
  assert.deepEqual(users.map(u => [u.id, !!u.private]), [[2, true], [1, false]]);
  assert.match(vm.runInContext("withKey('/api/receipts/2/export.csv')", context), /\?key=secret-key-123456789$/);
  await assert.rejects(vm.runInContext("useRecoveryCode('not a code')", context), /recovery code/);
});
