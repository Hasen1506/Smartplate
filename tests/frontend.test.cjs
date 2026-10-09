// Test browser-independent UI state without a browser dependency or CDN.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('smartplate/static/app.js', 'utf8').replace(
  'boot().catch', 'globalThis.bootPromise = boot().catch');

function fixture(overrides = {}, env = {}) {
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
    '/api/meta': { modes: { balanced: 'Balanced' }, mode_outcomes: {}, version: '1.1.0', swiggy_provider: 'simulated', fixture_data: true },
    '/api/users': [{ id: 1, name: 'Sample profile' }, { id: 2, name: 'Meera' }],
    '/api/user/2/plan': view,
    '/api/plan/42/orders': { attempted: 1, placed: 1, substituted: 0, failed: 0,
      results: [{ day: 0, meal: 'lunch', item: 'Meal', state: 'placed', placed: true, substituted: true, substitution: null }] },
  };
  Object.assign(replies, overrides);
  const context = vm.createContext({
    document: { getElementById: () => element, querySelectorAll: () => [], querySelector: () => null,
      createElement: () => element, addEventListener() {}, body: element },
    localStorage: env.storage || { getItem: () => '2', setItem() {} },
    ...(env.location ? { location: env.location, URLSearchParams, history: { replaceState() {} } } : {}),
    fetch: async (url, options) => { calls.push({ url, options });
      return { ok: !!replies[url] && !replies[url].__status, status: replies[url] ? (replies[url].__status || 200) : 404,
        json: async () => replies[url] || { error: 'Unexpected route' } }; },
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

test('Swiggy setup shows the exact callback and reconnect state safely', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = {
    connected: false, needs_reconnect: true,
    callback_url: 'https://smartplate.onrender.com/swiggy/callback?x=<unsafe>'
  };`, context);
  const html = vm.runInContext('connectionPanel()', context);
  assert.match(html, /Reconnect required/);
  assert.match(html, /exact-match allowlisted HTTPS redirect/);
  assert.match(html, /smartplate\.onrender\.com\/swiggy\/callback/);
  assert.ok(!html.includes('<unsafe>'));
  // the quick Connect sheet says the same, and never offers a sign-in that cannot work
  const sheet = vm.runInContext('connectSheet()', context);
  assert.match(sheet, /hasn't approved connecting/);
  assert.ok(!sheet.includes('data-act="swiggy-connect"') && !sheet.includes('<unsafe>'));
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

test('pending actions disable review buttons across render and preserve unavailable items', async () => {
  const { context } = fixture(); await context.bootPromise;
  const review = { disabled: false, dataset: {} };
  const unavailable = { disabled: true, dataset: {} };
  let buttons = [review, unavailable];
  context.document.querySelectorAll = selector => selector === 'button' ? buttons : [];
  const pending = vm.runInContext(`guard(async () => {
    await new Promise(resolve => globalThis.finishAction = resolve);
  })`, context);
  assert.equal(review.disabled, true);
  const renderedReview = { disabled: false, dataset: {} };
  buttons = [renderedReview, unavailable];
  vm.runInContext('render()', context);
  assert.equal(renderedReview.disabled, true);
  context.finishAction(); await pending;
  assert.equal(renderedReview.disabled, false);
  assert.equal(unavailable.disabled, true);
  assert.equal(renderedReview.dataset.busyDisabled, undefined);
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
  assert.match(element.innerHTML, /data-act="start-onboard">Get started/);
  assert.match(element.innerHTML, /<div class="wordmark"[^>]*>ziggy<\/div>/);
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

test('Today never shows a sample or demo banner; without live menus it says how to get real dishes', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  vm.runInContext("S.meta.swiggy_redirect_approved = true; S.view.source = { kind: 'none', connected: false, needs: 'connect', note: 'n' }", context);
  const html = vm.runInContext('todayScreen()', context);
  assert.doesNotMatch(html, /demonstration|Demo plan|sample/i);
  assert.match(html, /Plan from real places near you/);
  assert.match(html, /data-act="connect-open"/);
  vm.runInContext("S.view.source = { kind: 'none', connected: true, needs: 'address' }", context);
  assert.match(vm.runInContext('todayScreen()', context), /data-act="addr-open"[^>]*>Choose delivery address/);
});

test('connected with an address: Today shows a skeleton and reads real dishes by itself, once', async () => {
  const live = { plan: { id: 42, mode: 'balanced', week_start: '2026-09-14' }, user: { id: 2, name: 'Meera', allergens: [], medical: [] },
    budget: { spend: 0, budget: 1500 }, grid: [], week_context: [], source: { kind: 'live', label: 'Live Swiggy menus', note: '' } };
  const { context, calls } = fixture({ '/api/plan/42/live-menus': live }); await context.bootPromise;
  vm.runInContext("S.swiggy = { connected: true, address: { id: 'addr-home', label: 'Home' } }; S.view.source = { kind: 'none', connected: true, needs: 'sync' }", context);
  assert.match(vm.runInContext('realDishesState()', context), /Finding real dishes near you/);
  await vm.runInContext('autoSyncLive()', context);
  assert.equal(vm.runInContext('S.view.source.kind', context), 'live');
  vm.runInContext("S.view.source = { kind: 'none', connected: true, needs: 'sync' }", context);
  await vm.runInContext('autoSyncLive()', context);                 // never loops for the same address
  assert.equal(calls.filter(c => c.url === '/api/plan/42/live-menus').length, 1);
});

test('a failed live sync says why and offers Try again and search, never a fake dish', async () => {
  const { context } = fixture({ '/api/plan/42/live-menus': { __status: 502, error: "Swiggy's menus had no dishes Ziggy can plan with yet." } });
  await context.bootPromise;
  vm.runInContext("S.swiggy = { connected: true, address: { id: 'addr-w', label: 'Work' } }; S.view.source = { kind: 'none', connected: true, needs: 'sync' }", context);
  await vm.runInContext('autoSyncLive()', context);
  const html = vm.runInContext('realDishesState()', context);
  assert.match(html, /no dishes Ziggy can plan with yet/);
  assert.match(html, /data-act="live-menus"/);
  assert.match(html, /save a restaurant you like in Saved/);
});

test('Retry re-runs a click handler after the event finished (no null currentTarget crash)', async () => {
  const { context } = fixture({ '/api/session/9/swiggy-cart/preview': { __status: 502, error: "Couldn't reach Swiggy", code: 'swiggy_error' } });
  await context.bootPromise;
  richView(context);
  // a real DOM event: currentTarget is reset to null once dispatch ends
  const el = { dataset: { cart: '9' }, listeners: {}, addEventListener(ev, fn) { this.listeners[ev] = fn; } };
  vm.runInContext('document', context).querySelectorAll = (sel) => sel === '[data-cart]' ? [el] : [];
  vm.runInContext('wire()', context);
  const event = { _ct: el, get currentTarget() { return this._ct; }, preventDefault() {}, stopPropagation() {} };
  el.listeners.click(event);
  await new Promise(r => setImmediate(r));
  event._ct = null;                                                   // dispatch over
  assert.match(vm.runInContext('S.error', context), /Couldn't reach Swiggy/);
  await vm.runInContext('guard(retryLast)', context);                // the Retry button
  assert.match(vm.runInContext('S.error', context), /Couldn't reach Swiggy/);
  assert.doesNotMatch(vm.runInContext('S.error', context), /dataset/);
});

test('connected profiles with ingredient rules keep the direct Swiggy hand-off', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  vm.runInContext("S.swiggy = { connected: true, address: { id: 'home', label: 'Home' } }", context);
  const html = vm.runInContext('todayScreen()', context);
  assert.match(html, /aria-label="Next meal"/);
  assert.match(html, /Veg &lt;b&gt;Meals&lt;\/b&gt;/);
  assert.match(html, /Order on Swiggy/);
  assert.match(html, /can't verify your ingredient or medical rules/);
  assert.ok(!html.includes('data-cart="9"'));
});

test('eligible profiles add the planned dish to the Swiggy cart in one tap, and the elephant rolls meanwhile', async () => {
  const { context, calls } = fixture({
    '/api/session/9/swiggy-cart/preview': { session_id: 9, item: 'Veg Meals', restaurant: 'Saravana', address: 'Home', fingerprint: 'f9' },
    '/api/session/9/swiggy-cart': { session_id: 9, item: 'Veg Meals', restaurant: 'Saravana', planned_cost: 150, to_pay: 185,
      over_plan: 35, checkout_url: 'https://www.swiggy.com/checkout', bill: { to_pay: 185, lines: [{ label: 'Item Total', amount: 150 }, { label: 'Delivery Fee', amount: 35 }] } } });
  await context.bootPromise;
  richView(context);
  vm.runInContext(`S.view.user.diet = 'nonveg'; S.view.user.allergens = []; S.swiggy = { connected: true, address: { id: 'home' } };`, context);
  assert.match(vm.runInContext('nextUpCard(S.view.next_up)', context), /data-cart="9"[^>]*>Add to Swiggy cart/);
  vm.runInContext('S.adding = 9', context);
  const adding = vm.runInContext('nextUpCard(S.view.next_up)', context);
  assert.match(adding, /aria-busy="true"><span class="roller"/);
  assert.match(adding, /Adding to your cart…/);
  vm.runInContext('S.adding = null', context);
  await vm.runInContext('orderMeal(9)', context);
  const cart = calls.find(c => c.url === '/api/session/9/swiggy-cart');
  assert.deepEqual(JSON.parse(cart.options.body), { expected_fingerprint: 'f9' });     // exactly the item Swiggy just priced
  assert.equal(vm.runInContext('S.carts[9].to_pay', context), 185);
  assert.equal(vm.runInContext('S.adding', context), null);
  assert.match(vm.runInContext('nextUpCard(S.view.next_up)', context), /Added to your Swiggy cart/);
});

test('connected Saved uses real Swiggy IDs and no sample restaurant cards', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'addr-home', label: 'Home' } };
    S.places = [{ id: 1, name: 'Sample Diner', favourite: true }];
    S.liveFavourites = [{ id: 'rest-42', name: '<Real Place>' }];
    S.liveBrowseMenu = { restaurant: { id: 'rest-42', name: '<Real Place>' }, address: 'Home', fetched: 'today',
      items: [{ id: 'item-7', name: '<Dish>', price: null, veg: true, in_stock: true, has_options: false }] };`, context);
  const html = vm.runInContext('savedScreen()', context) + vm.runInContext('menuSheet()', context);
  assert.match(html, /data-live-place="rest-42"/);
  assert.match(html, /data-live-item="item-7"/);
  assert.match(html, /Price in cart/);
  assert.match(html, /Search restaurants on Swiggy/);
  assert.ok(!html.includes('Sample Diner') && !html.includes('<Real Place>') && !html.includes('<Dish>'));
});

test('real order confirmation shows live total, address and payment before placement', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'home', label: 'Home' }, order_enabled: true };
    S.liveCart = { item: 'Tiffin', restaurant: 'Real Place', to_pay: 160, checkout_url: 'https://www.swiggy.com/' };
    S.checkoutReview = { item: '<Tiffin>', quantity: 1, address: '<Home>', to_pay: 160,
      payment_label: 'Cash on Delivery', fingerprint: 'reviewed' };`, context);
  assert.match(vm.runInContext('liveCartBlock()', context), /Review and place order/);
  const dialog = vm.runInContext('checkoutReviewDialog()', context);
  assert.match(dialog, /Cash on Delivery/);
  assert.match(dialog, /Confirm and place order · ₹160/);
  assert.ok(!dialog.includes('<Tiffin>') && !dialog.includes('<Home>'));
});

test('recent provider orders and uncertain attempts are visible without raw markup', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'home', label: 'Home' }, order_enabled: true };
    S.saved = { liked: [], recent: [] };
    S.liveOrderHistory = { address: '<Home>', attempts: [{ state: 'unknown' }],
      provider_orders: [{ order_id: 'o-1', restaurant: '<Restaurant>', item: 'Tiffin',
        status: 'PREPARING', total: '160', ordered_time: 'now' }] };`, context);
  const html = vm.runInContext('savedRecent()', context);
  assert.match(html, /data-act="live-order-history">Recent Swiggy orders/);
  assert.match(html, /uncertain result/);
  assert.match(html, /o-1/);
  assert.ok(!html.includes('<Home>') && !html.includes('<Restaurant>'));
});

test('live configuration never offers the order simulator', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.meta.swiggy_provider = 'live';`, context);
  assert.ok(!vm.runInContext('ordersPanel()', context).includes('data-act="exec"'));
  assert.ok(!vm.runInContext('weekScreen()', context).includes('data-act="exec"'));
  vm.runInContext(`S.view.source = { kind: 'sample', connected: false, label: 'Sample dishes (test data)', note: 'x' };`, context);
  const week = vm.runInContext('weekScreen()', context);
  assert.match(week, /Sample dishes \(test data\)/);
  assert.match(week, /planned of ₹1,500/);
  assert.ok(!week.includes('Prices include delivery and expected surge'));
});

test('week: a day strip, the chosen day\'s meals, holidays and order-by times', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  const html = vm.runInContext('weekScreen()', context);
  assert.match(html, /data-day="0" aria-pressed="true"/);
  assert.match(html, /data-meal="9"/);
  assert.match(html, /Gandhi Jayanti/);
  assert.match(html, /order by 12:05/);
  assert.doesNotMatch(html, /data-kind="\d+:|data-pin=|draggable/);          // no per-row toggles: tap opens the Change sheet
  vm.runInContext('S.moving = 10', context);
  assert.match(vm.runInContext('weekScreen()', context), /Tap to swap here/);
});

test('change sheet: better fits, other places, cook and hidden counts', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.sheet = { sid: 9, tab: 'fits', data: { session: { id: 9, day: 'Fri', date: '2026-09-25', meal: 'lunch' },
    limits: { left_week: 400, left_day: null }, has_favourites: true, usual_more: 0, timing_tip: null, current: null,
    usual: [{ restaurant: 'A"B', rating: 4.5, eta_min: 25, favourite: true, more: 2,
      dishes: [{ item_id: 1, name: 'Dosa', price: 120, fits: true, why: 'fits your budget', instructions: [] }] }],
    new: [{ item_id: 2, name: 'Bowl', price: 450, fits: false, why: '₹50 over what\\'s left', restaurant: 'New', instructions: [] }],
    cook: [{ recipe_key: 'dal_rice', name: 'Dal + rice', price: 45 }], hidden: { not_safe: 4, below_rating: 1, not_again: 0 } } }`, context);
  let html = vm.runInContext('sheetDialog()', context);
  assert.match(html, /Change lunch/);
  assert.match(html, /data-pick="1"[^]*fits your budget/);
  assert.match(html, /class="pick over/);
  assert.match(html, /Over by ₹50/);
  assert.match(html, /A&quot;B/);
  assert.match(html, /4 not safe for your allergies or diet/);
  assert.match(html, /data-cook="dal_rice">Cook instead · ₹45/);
  vm.runInContext("S.sheet.tab = 'places'; S.sheet.place = 'New'", context);
  html = vm.runInContext('sheetDialog()', context);
  assert.match(html, /data-sheet-place="New" aria-pressed="true"/);
  assert.match(html, /data-pick="2"/); assert.doesNotMatch(html, /data-pick="1"/);
});

test('onboarding uses four steps without sample restaurant choices or sample budget quotes', async () => {
  const { context, calls } = fixture(); await context.bootPromise;
  vm.runInContext('startOnboard()', context);
  assert.match(vm.runInContext('onboardingScreen()', context), /What do you eat/);
  vm.runInContext("onboardChip('allergens', 'peanut', true); onboardChip('diet', 'veg', false)", context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(S.onboard.d.allergens)', context)), ['peanut']);
  assert.equal(vm.runInContext('S.onboard.d.diet', context), 'veg');
  vm.runInContext("S.onboard.step = 1", context);
  const rhythm = vm.runInContext('onboardingScreen()', context);
  assert.match(rhythm, /How do your days usually go/);
  for (const m of ['breakfast', 'lunch', 'dinner']) for (const k of ['order', 'cook', 'skip'])
    assert.match(rhythm, new RegExp(`data-ob="rh_${m}" data-val="${k}"`));
  vm.runInContext("onboardChip('rh_breakfast', 'cook', false)", context);
  assert.equal(vm.runInContext('S.onboard.d.rhythm.breakfast', context), 'cook');
  vm.runInContext("onboardChip('rh_breakfast', 'skip', false); onboardChip('rh_lunch', 'skip', false); onboardChip('rh_dinner', 'skip', false)", context);
  await assert.rejects(vm.runInContext("onboardNav('next')", context), /at least one meal/);
  vm.runInContext('S.onboard.step = 2', context);
  const budget = vm.runInContext('onboardingScreen()', context);
  assert.match(budget, /Delivery, fees and groceries all count/);
  assert.match(budget, /data-ob-budget="250"/);
  assert.ok(!budget.includes('Usual · ₹2,000'));
  assert.equal(vm.runInContext('OB_STEPS.length', context), 4);
  assert.ok(!calls.some(c => c.url.includes('/api/suggest-budget')));
});

test('private profile keys are sent as a header and merged into the profile list', async () => {
  const { context, calls } = fixture(); await context.bootPromise;
  vm.runInContext(`const bag = {}; localStorage.getItem = (k) => bag[k] ?? null; localStorage.setItem = (k, v) => { bag[k] = v; };
    keys.put(2, 'secret-key-123456789', 'Meera')`, context);
  await vm.runInContext("api('/api/user/2/plan')", context);
  assert.equal(calls.at(-1).options.headers['X-SmartPlate-Key'], 'secret-key-123456789');
  const users = JSON.parse(vm.runInContext("JSON.stringify(mergeUsers([{ id: 1, name: 'Sample' }, { id: 2, name: 'Meera' }]))", context));
  assert.deepEqual(users.map(u => [u.id, !!u.private]), [[2, true], [1, false]]);
  assert.match(vm.runInContext('receiptsPanel()', context), /data-act="download-csv"/);
  assert.match(vm.runInContext('meScreen()', context), /data-act="download-ics"/);
  assert.ok(!source.includes('withKey('));
  await assert.rejects(vm.runInContext("useRecoveryCode('not a code')", context), /recovery code/);
});

test('private exports fetch with a header and never put recovery keys in URLs', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`const bag2 = {}; localStorage.getItem = (k) => bag2[k] ?? null;
    localStorage.setItem = (k, v) => { bag2[k] = v; };
    keys.put(2, 'export-secret', 'Meera');`, context);
  let requested, downloaded;
  context.Blob = Blob;
  context.URL = { createObjectURL: () => 'blob:export', revokeObjectURL() {} };
  context.fetch = async (url, options) => {
    requested = { url, options };
    return { ok: true, blob: async () => new Blob(['date,item\n'], { type: 'text/csv' }) };
  };
  context.document.createElement = () => ({ click() { downloaded = this.download; }, remove() {} });
  await vm.runInContext("downloadPrivate('/api/receipts/2/export.csv', 'expenses.csv', 'text/csv')", context);
  assert.equal(requested.url, '/api/receipts/2/export.csv');
  assert.equal(requested.options.headers['X-SmartPlate-Key'], 'export-secret');
  assert.equal(downloaded, 'expenses.csv');
});

test('shared profiles cannot start a personal Swiggy connection', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext('S.swiggy = { connected: false, requires_private_profile: true }', context);
  const html = vm.runInContext('connectionPanel()', context);
  assert.match(html, /Create my profile/);
  assert.match(html, /data-act="signin-open"/);
  assert.ok(!html.includes('data-act="swiggy-connect"'));
});

test('boot restores the actual provider cart after a page reload', async () => {
  const { context, calls } = fixture({
    '/api/user/2/swiggy': { connected: true, address: { id: 'home', label: 'Home' } },
    '/api/user/2/swiggy/favourites': [],
    '/api/user/2/swiggy/live-cart': { cart: { item: 'Real Dish', restaurant: 'Real Place', to_pay: 160, orderable: true } },
  });
  await context.bootPromise;
  assert.equal(vm.runInContext('S.liveCart.item', context), 'Real Dish');
  assert.equal(vm.runInContext('S.liveCart.to_pay', context), 160);
  assert.ok(calls.some(c => c.url.endsWith('/swiggy/live-cart') && c.options.method === 'GET'));
});

test('an external cart is visible but cannot be placed without a new review', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'home' }, order_enabled: true };
    S.liveCart = { item: '<External item>', restaurant: 'Real Place', to_pay: 160, orderable: false };`, context);
  const html = vm.runInContext('liveCartBlock()', context);
  assert.ok(!html.includes('data-act="review-live-checkout"') && !html.includes('<External item>'));
  assert.match(html, /clear/);
});

test('checkout names and escapes the reviewed restaurant', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.checkoutReview = { restaurant: '<Kitchen>', item: 'Dish', quantity: 1,
    address: 'Full address', to_pay: 160, payment_label: 'Cash' };`, context);
  const html = vm.runInContext('checkoutReviewDialog()', context);
  assert.match(html, /&lt;Kitchen&gt;/);
  assert.ok(!html.includes('<Kitchen>'));
});

test('saved orders remain trackable after reload with readable provider progress', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'home' } };
    S.placedOrder = null;
    S.liveOrderHistory = { attempts: [{ state: 'confirmed', order_id: 'o-1' }],
      provider_orders: [{ order_id: 'o-1', restaurant: 'Kitchen', item: 'Dish', status: 'PREPARING' }] };
    S.liveOrderStatus = { order_id: 'o-1', tracking: { title: '<Preparing>', subtitle: 'At the restaurant', eta: '25 minutes' } };`, context);
  const html = vm.runInContext('orderHistoryCard()', context) + vm.runInContext('liveCartBlock()', context);
  assert.match(html, /data-live-track="o-1"/);
  assert.match(html, /&lt;Preparing&gt;/);
  assert.match(html, /25 minutes/);
  assert.ok(!html.includes('<Preparing>') && !html.includes('"tracking":'));
});

test('Profiles exposes authenticated export and an explicit deletion form', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`const savedKeys = {}; localStorage.getItem = k => savedKeys[k] || null;
    localStorage.setItem = (k,v) => savedKeys[k] = v; keys.put(2, 'my-private-key', 'Meera');`, context);
  const html = vm.runInContext('profilesPanel()', context);
  assert.match(html, /data-act="download-profile"/);
  assert.match(html, /id="delete-profile"/);
  assert.match(html, /Type DELETE/);
  assert.match(html, /existing orders stay/);
  vm.runInContext(`S.liveCart = { item: 'Private dish' }; S.liveOrderHistory = { private: true }; clearCurrentProfile()`, context);
  assert.equal(vm.runInContext('S.liveCart', context), null);
  assert.equal(vm.runInContext('S.liveOrderHistory', context), null);
  assert.equal(vm.runInContext('S.userId', context), null);
});

test('restaurant dish search loads actual pages without replacing earlier results', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'home' } };
    S.liveBrowseMenu = { restaurant: { id: 'real-r', name: 'Real Kitchen' }, items: [], address: 'Home' };`, context);
  const requests = [];
  context.fetch = async (url) => {
    requests.push(url);
    const more = url.endsWith('offset=20');
    return { ok: true, json: async () => ({ items: more ? [{ id: 'real-item-2', name: 'Second dish' }] :
      [{ id: 'real-item-1', name: '<First dish>' }], query: 'tiffin', has_more: !more, next_offset: more ? null : 20,
      hidden_nonveg: 0, fetched: 'now' }) };
  };
  await vm.runInContext("searchLiveDishes('tiffin')", context);
  assert.match(vm.runInContext('menuSheet()', context), /Load more matching dishes/);
  await vm.runInContext("searchLiveDishes('tiffin', true)", context);
  assert.equal(vm.runInContext('S.liveBrowseMenu.items.length', context), 2);
  assert.ok(requests[0].includes('restaurant_id=real-r') && requests[1].endsWith('offset=20'));
  const html = vm.runInContext('menuSheet()', context);
  assert.match(html, /real-item-1/); assert.match(html, /real-item-2/);
  assert.ok(!html.includes('<First dish>'));
});

test('recovery rotation saves the new key and replaces the checkout approval', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`const rotateBag = {}; localStorage.getItem = k => rotateBag[k] ?? null;
    localStorage.setItem = (k,v) => rotateBag[k] = v; localStorage.removeItem = k => delete rotateBag[k];
    keys.put(2, 'old-key', 'Meera'); S.checkoutReview = { fingerprint: 'old-approval' };`, context);
  const requests = [];
  context.fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => url.endsWith('rotate-key') ?
      { user_id: 2, key: 'fresh-private-key', name: 'Meera' } : { devices: [], private: true } };
  };
  await vm.runInContext('rotateRecoveryCode()', context);
  assert.equal(requests[0].options.headers['X-SmartPlate-Key'], 'old-key');
  assert.equal(requests[1].options.headers['X-SmartPlate-Key'], 'fresh-private-key');
  assert.equal(vm.runInContext('keys.get(2)', context), 'fresh-private-key');
  assert.equal(vm.runInContext('S.checkoutReview', context), null);
});

test('blocked storage cannot silently discard access during recovery rotation', async () => {
  const { context, calls } = fixture(); await context.bootPromise;
  const before = calls.length;
  await assert.rejects(vm.runInContext('rotateRecoveryCode()', context), /Enable browser storage/);
  assert.equal(calls.length, before);
});

/* ---- Roadmap E: household, weekly recap, grocery list from cook meals ---- */
const HOUSEHOLD = { name: 'Home', members: ['Meera', 'Dev'], merged_allergens: ['dairy', 'peanut'], split_method: 'by_consumption',
  people: [{ id: 2, name: 'Meera', diet: 'vegan', allergens: ['dairy'], medical: [], you: true, managed: false, meals: ['lunch', 'dinner'] },
    { id: 7, name: 'Dev', diet: 'veg', allergens: ['peanut'], medical: [], you: false, managed: true, meals: ['dinner'] }],
  rules: { allergens: [{ rule: 'dairy', who: ['Meera'] }, { rule: 'peanut', who: ['Dev'] }], medical: [], diet: { rule: 'vegan', who: ['Meera'] } },
  split: [{ member_id: 2, member: 'Meera', share: 600 }, { member_id: 7, member: 'Dev', share: 400 }] };

test('household panel: create form for a private profile, sample profiles are sent to set-up', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext('keys.get = () => null', context);
  assert.match(vm.runInContext('householdPanel()', context), /Sample profiles are shared by every visitor/);
  vm.runInContext("keys.get = () => 'k'", context);
  const html = vm.runInContext('householdPanel()', context);
  assert.match(html, /<form id="hh-create"/);
  assert.match(html, /Every shared meal meets all their allergies and diets/);
});

test('household panel: everyone\'s rules with who they are for, people, and the split', async () => {
  const { context } = fixture(); await context.bootPromise;
  context.HH = HOUSEHOLD;
  vm.runInContext('S.view.household = HH; S.view.user.meals = ["lunch", "dinner"]', context);
  const html = vm.runInContext('householdPanel()', context);
  assert.match(html, /No dairy · Meera/);
  assert.match(html, /No peanut · Dev/);
  assert.match(html, /Vegan · Meera/);
  assert.match(html, /<b>Meera<\/b> <small class='muted'>\(you\)<\/small>/);
  assert.match(html, /data-hh-edit="7"/);
  assert.ok(!html.includes('data-hh-edit="2"'));                                // your own row goes to settings
  assert.match(html, /eats dinner/);
  assert.match(html, /<option value="by_consumption" selected>By who eats each meal<\/option>/);
  assert.match(html, /<td>Dev<\/td><td>₹400<\/td>/);
  vm.runInContext('S.hhEdit = 7', context);
  const edit = vm.runInContext('householdPanel()', context);
  assert.match(edit, /<form id="hh-edit"/);
  assert.match(edit, /value="Dev"/);
  assert.match(edit, /name="allergens" value="peanut" checked/);
  assert.match(edit, /name="meals" value="dinner" checked/);
  assert.ok(!/name="meals" value="lunch" checked/.test(edit));
});

test('meal sheet: who is eating, toggled per person and sent to the server', async () => {
  const { context, calls } = fixture({ '/api/session/9/eaters': { plan: { id: 42 }, user: { id: 2 }, grid: [], budget: {} } });
  await context.bootPromise;
  context.HH = HOUSEHOLD;
  vm.runInContext(`S.view.household = HH; S.view.grid = [{ day: 'Fri', meals: { lunch: { session_id: 9, eaters: [2] } } }];
    S.sheet = { sid: 9, data: { session: { id: 9, day: 'Fri', date: '2026-09-25', meal: 'lunch' }, limits: { left_week: 400, left_day: null },
      has_favourites: false, usual_more: 0, timing_tip: null, current: null, usual: [], new: [], cook: [], hidden: {} } }`, context);
  const html = vm.runInContext('sheetDialog()', context);
  assert.match(html, /Who's eating/);
  assert.match(html, /class="chip" aria-pressed="true" data-eater="9:2">Meera/);
  assert.match(html, /class="chip" aria-pressed="false" data-eater="9:7">Dev/);
  await vm.runInContext('toggleEater(9, 7)', context);
  assert.deepEqual(JSON.parse(calls.find(c => c.url === '/api/session/9/eaters').options.body), { eaters: [2, 7] });
  const before = calls.length;
  vm.runInContext('S.view.grid = [{ day: "Fri", meals: { lunch: { session_id: 9, eaters: [2] } } }]', context);
  await vm.runInContext('toggleEater(9, 2)', context);                          // nobody left: refused locally
  assert.equal(calls.length, before);
});

test('weekly recap: spent vs planned, unmarked meals, nutrition and who owes what', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.recap = { week_start: '2026-11-02', spend: { budget: 3000, spent: 450, planned: 1800, left: 750, delivery: 300, cooked: 150, last_week: 2100 },
    meals: { had: 3, ordered: 2, cooked: 1, ahead: 9, skipped: 1, unmarked: 2 }, top_place: { name: 'Sangeetha Veg', times: 2 },
    nutrition: { days_logged: 2, avg: { kcal: 1500, protein_g: 48 }, target: { kcal: 2000, protein_g: 60 }, protein_days: 0 },
    household: { name: 'Home', split_method: 'by_consumption', split: [{ member: 'Meera', share: 300 }, { member: 'Dev', share: 150 }] } }`, context);
  const html = vm.runInContext('recapPanel()', context);
  assert.match(html, /Spent<\/span><b>₹450<\/b>/);
  assert.match(html, /3 had \(2 ordered, 1 cooked\) · 9 still ahead · 1 skipped/);
  assert.match(html, /2 past meals aren't marked/);
  assert.match(html, /Most ordered from: Sangeetha Veg \(2×\)/);
  assert.match(html, /1500 \/ 2000/);
  assert.match(html, /on the 2 days you logged meals/);
  assert.match(html, /Last week you spent ₹2,100/);
  assert.match(html, /By who ate each meal you marked as had/);
  vm.runInContext('S.recap.nutrition = { days_logged: 0, avg: null, target: { kcal: 2000, protein_g: 60 }, protein_days: 0 }', context);
  assert.match(vm.runInContext('recapPanel()', context), /No meals logged yet this week/);
});

test('grocery list: packs, need, servings and have-it ticks', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.view.coach = { headline: '3 cook meals still to cook this week — grocery run ≈ ₹250', recipes: [],
    basket: { total: 250, items: [
      { name: 'Toor dal 500g', qty: 2, price: 180, need: 480, unit: 'g', servings: 8, recipe: 'Dal + rice', have: false },
      { name: 'Rice 1kg', qty: 1, price: 70, need: 1440, unit: 'g', servings: 8, recipe: 'Dal + rice', have: true },
      { name: 'Eggs (6)', qty: 1, price: 60, need: 4, unit: 'eggs', servings: 2, recipe: 'Egg curry', have: false }] } }`, context);
  const html = vm.runInContext('cookingPanel()', context);
  assert.match(html, /2 × Toor dal 500g/);
  assert.match(html, /needs 480 g for 8 servings/);
  assert.match(html, /<s>Rice 1kg<\/s>/);
  assert.match(html, /needs 1\.4 kg for 8 servings/);
  assert.match(html, /needs 4 eggs for 2 servings/);
  assert.match(html, /data-have="Rice 1kg" checked/);
  assert.match(html, /rounded up to whole packs/);
});

/* ---- Roadmap G: Epicure swaps, "more like this", cuisine lean, credits ---- */
const COACH = { headline: '1 cook sessions this week — grocery run ≈ ₹200', swaps_available: true,
  recipes: [{ session: 'Fri lunch', key: 'dal_rice', name: 'Dal + rice', cost: 45, steps: ['Cook'],
    ingredients: [{ token: 'toor_dal', name: 'Toor dal', swappable: true, swap: { token: 'masoor_dal', name: 'Masoor dal', reason: 'out_of_stock' } },
      { token: 'rice', name: 'Rice', swappable: true, swap: null }, { token: 'onion', name: 'Onion', swappable: false, swap: null }] }],
  basket: { total: 200, items: [
    { name: 'Toor dal 500g', recipe: 'Dal + rice', price: 90, token: 'toor_dal', swappable: true, swap: { token: 'masoor_dal', name: 'Masoor dal', reason: 'out_of_stock' } },
    { name: 'Rice 1kg', recipe: 'Dal + rice', price: 70, token: 'rice', swappable: true, swap: null },
    { name: 'Onion/tomato', recipe: 'Dal + rice', price: 40, token: null, swappable: false, swap: null }] },
  safe_with_swap: [{ key: 'egg_curry', name: 'Egg curry + roti', swaps: [{ from: 'egg', from_name: 'Eggs', to: 'paneer', to_name: 'Paneer' }] }] };

test('cooking panel: ingredient swaps, out-of-stock grocery lines and safe-with-a-swap recipes', async () => {
  const { context } = fixture(); await context.bootPromise;
  context.COACH = COACH;
  vm.runInContext('S.view.coach = COACH', context);
  const html = vm.runInContext('cookingPanel()', context);
  assert.match(html, /Masoor dal <small>for Toor dal<\/small>/);              // recipe shows the swap
  assert.match(html, /data-swap-ing="rice"/);                                   // swappable ingredient
  assert.ok(!html.includes('data-swap-ing="onion"'));                           // not in the pantry: no button
  assert.match(html, /<s>Toor dal 500g<\/s> → <b>Masoor dal<\/b> <small>\(out of stock\)<\/small>/);
  assert.match(html, /data-oos="rice"/);
  assert.ok(!html.includes('data-oos="null"'));
  assert.match(html, /Prices are for the original items/);
  assert.match(html, /Egg curry \+ roti<\/b>: use Paneer instead of Eggs/);
});

test('swap picker asks for the replacement, then saves it with the reason', async () => {
  const { context, calls } = fixture({
    '/api/plan/42/swaps?token=rice': { available: true, ingredient: 'rice', name: 'Rice', hidden_unsafe: 2,
      options: [{ token: 'brown_rice', name: 'Brown rice', similarity: 0.6 }] },
    '/api/plan/42/grocery-swap': { plan: { id: 42 }, user: { id: 2, prefs: {} }, grid: [], coach: COACH, budget: {} } });
  await context.bootPromise;
  context.COACH = COACH;
  vm.runInContext('S.view.coach = COACH', context);
  await vm.runInContext("openSwap('rice', 'out_of_stock')", context);
  const html = vm.runInContext('cookingPanel()', context);
  assert.match(html, /Rice is out of stock\. Use instead:/);
  assert.match(html, /data-swap-to="brown_rice"/);
  assert.match(html, /\(2 left out\)/);
  await vm.runInContext("setSwap('rice', 'brown_rice', 'out_of_stock')", context);
  const call = calls.find(c => c.url === '/api/plan/42/grocery-swap');
  assert.deepEqual(JSON.parse(call.options.body), { token: 'rice', swap_token: 'brown_rice', reason: 'out_of_stock' });
  assert.equal(vm.runInContext('S.swapPick', context), null);
});

test('more like this appears only for a planned restaurant dish when Epicure is installed', async () => {
  const { context } = fixture(); await context.bootPromise;
  const sheet = `S.sheet = { sid: 9, data: { session: { id: 9, day: 'Fri', date: '2026-09-25', meal: 'lunch' },
    limits: { left_week: 400, left_day: null }, has_favourites: false, usual_more: 0, timing_tip: null,
    current: { item: 'Chicken Biryani', kind: 'delivery', cost: 290 }, usual: [], new: [], cook: [], hidden: {} } }`;
  vm.runInContext(sheet, context);
  assert.ok(!vm.runInContext('sheetDialog()', context).includes('data-more-like'));   // no Epicure: no button
  vm.runInContext("S.meta.epicure = { available: true, cuisines: { Mediterranean: 'Mediterranean' } }", context);
  assert.match(vm.runInContext('sheetDialog()', context), /data-more-like="9">More like Chicken Biryani/);
  vm.runInContext("S.sheet.similar = { dish: 'Chicken Biryani', items: [{ item_id: 5, name: 'Mutton Biryani', restaurant: 'JK', price: 320 }] }", context);
  const html = vm.runInContext('sheetDialog()', context);
  assert.match(html, /Like Chicken Biryani/);
  assert.match(html, /data-pick="5"/);
  vm.runInContext("S.sheet.data.current.kind = 'cook'; S.sheet.similar = null", context);
  assert.ok(!vm.runInContext('sheetDialog()', context).includes('data-more-like'));
});

test('settings: cuisine lean and the "more like" list, sent as cuisine_tilt', async () => {
  const { context } = fixture(); await context.bootPromise;
  assert.ok(!vm.runInContext('settingsPanel()', context).includes('tilt_cuisine'));     // hidden without Epicure
  vm.runInContext(`S.meta.epicure = { available: true, cuisines: { South_Asian: 'Indian', Mediterranean: 'Mediterranean' } };
    S.view.user.prefs = { cuisine_tilt: { cuisine: 'Mediterranean', strength: 'strong' }, more_like: [{ name: 'Masala Dosa' }] }`, context);
  const html = vm.runInContext('settingsPanel()', context);
  assert.match(html, /<option value="Mediterranean" selected>Mediterranean<\/option>/);
  assert.match(html, /<option value="strong" selected>A lot<\/option>/);
  assert.match(html, /data-forget-like="Masala Dosa"/);
  const form = (entries) => { const m = new Map(Object.entries(entries));
    return { get: k => m.has(k) ? m.get(k) : null, getAll: () => [], has: k => m.has(k) }; };
  context.FormData = function (f) { return f; };
  const base = { name: 'Meera', diet: 'veg', weekly_budget: '1500', rating_floor: '4', cook: 'never', variety: 'light', goal: 'none', daily_cap: '' };
  context.F1 = form({ ...base, tilt_cuisine: 'South_Asian', tilt_strength: 'light' });
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(readSettings(F1).cuisine_tilt)', context)), { cuisine: 'South_Asian', strength: 'light' });
  context.F2 = form({ ...base, tilt_cuisine: '', tilt_strength: 'light' });
  assert.equal(vm.runInContext('readSettings(F2).cuisine_tilt', context), null);
  context.F3 = form(base);
  assert.equal(vm.runInContext('"cuisine_tilt" in readSettings(F3)', context), false);
});

test('Me lists Credits & licences, linking to the credits page', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext('S.more = null', context);
  assert.match(vm.runInContext('meScreen()', context), /<a class="set" href="\/credits"><span>Credits &amp; licences<\/span>/);
});

test('settings: rhythm, calorie split, targets and variety cap are editable in one form', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.view.user.prefs = { rhythm: { breakfast: 'cook', lunch: 'order', dinner: 'skip' } };
    S.view.user.nutrition_targets = { meal_share: { breakfast: 0.2, lunch: 0.5, dinner: 0.3 }, kcal: 2100 };
    S.view.user.health_targets = { max_item_repeat: 5 }`, context);
  const html = vm.runInContext('settingsPanel()', context);
  assert.equal((html.match(/<form /g) || []).length, 1);                // one form, one save
  assert.doesNotMatch(html, /name="meals"/);                          // "What to plan" replaces the meal checkboxes
  assert.match(html, /name="rh_breakfast"[^]*value="cook" selected/);
  assert.match(html, /name="sh_lunch" type="number" min="10" max="70" value="50"/);
  assert.match(html, /name="kcal" type="number" min="1000" max="4500" value="2100"/);
  assert.match(html, /name="max_repeat" type="number" min="1" max="7" value="5"/);
  assert.match(html, /<select name="mode">/);                          // how tight money is lives here, not as a toggle
  // only changed targets are sent, so an untouched calorie target keeps following the goal
  const el = (v, d = v) => ({ value: v, defaultValue: d });
  const values = { rh_breakfast: 'cook', rh_lunch: 'order', rh_dinner: 'skip', sh_breakfast: '20', sh_lunch: '50', sh_dinner: '30',
    kcal: '2100', protein_g: '60', max_repeat: '5' };
  const mk = (changed) => { const m = new Map(Object.entries({ ...values, ...changed }));
    const elements = Object.fromEntries([...m].map(([k, v]) => [k, el(v, values[k])]));
    return { get: k => m.has(k) ? m.get(k) : null, getAll: () => [], has: k => m.has(k), elements }; };
  context.FormData = function (f) { return f; };
  context.T1 = mk({});
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(readTuning(T1))', context)),
    { rhythm: { breakfast: 'cook', lunch: 'order', dinner: 'skip' } });
  context.T2 = mk({ max_repeat: '6' });
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(readTuning(T2).tuning)', context)), { max_repeat: 6 });
});

test('timestamps read like a person wrote them', async () => {
  const { context } = fixture(); await context.bootPromise;
  assert.doesNotMatch(vm.runInContext("fmtWhen('2026-09-14T08:05')", context), /T08/);
  assert.match(vm.runInContext("fmtWhen('2026-09-14T08:05')", context), /14 Sept?, 08:05/);
  assert.equal(vm.runInContext('fmtWhen(null)', context), 'recently');
});

test('rating reasons are one tap each, and what was learned shows with an undo', async () => {
  const { context } = fixture(); await context.bootPromise;
  const before = vm.runInContext(`rateRow({ session_id: 5, rating_given: null, reasons_given: [] })`, context);
  assert.match(before, /data-rate="5:1"[^>]*>Good/); assert.match(before, /data-rate="5:-1"[^>]*>Not again/);
  assert.doesNotMatch(before, /data-reason/);                          // reasons come after the one-tap rating
  const row = vm.runInContext(`rateRow({ session_id: 5, rating_given: 1, reasons_given: ['late'] })`, context);
  for (const k of ['late', 'small', 'spicy', 'pricey', 'great']) assert.match(row, new RegExp(`data-reason="5:${k}"`));
  assert.match(row, /data-reason="5:late" aria-pressed="true"/);
  vm.runInContext(`S.view.learned = [{ key: 'late:3', reason: 'late', text: 'Chennai Mess often arrives late — planned less', taps: 2 }]`, context);
  const line = vm.runInContext('learnedChips()', context);
  assert.match(line, /often arrives late/); assert.match(line, /data-unlearn="late:3"/);
});

test('order this week: any slot and day, the real bill, and tap-to-place without an order API', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.orderQueue = { plan_id: 42, order_enabled: false, queued: 2, planned_total: 400, confirmed_total: 262.5,
    scheduling: { supported: false, why: "Swiggy's order tool places an order immediately" },
    meals: [
      { session_id: 7, day_index: 2, meal: 'breakfast', item: 'Mini Tiffin', restaurant: 'HSB', planned_cost: 150, queued: true, state: 'queued', orderable: true },
      { session_id: 9, day_index: 2, meal: 'dinner', item: 'Veg Meals', restaurant: 'HSB', planned_cost: 250, queued: true, state: 'cart_ready', to_pay: 262.5, orderable: true,
        bill: { to_pay: 262.5, itemised: true, lines: [{ label: 'Items', amount: 210 }, { label: 'Delivery', amount: 35 }, { label: 'Platform fee', amount: 10 }, { label: 'GST & taxes', amount: 7.5 }] } },
      { session_id: 11, day_index: 3, meal: 'lunch', item: 'Curd Rice', planned_cost: 100, queued: false, orderable: true }] };`, context);
  const html = vm.runInContext('ordersPanel()', context);
  assert.match(html, /value="breakfast"/); assert.match(html, /value="dinner"/); assert.match(html, /value="lunch"/);
  assert.match(html, /data-oq-cart="7"/);
  assert.match(html, /Platform fee/); assert.match(html, /GST &amp; taxes/);
  assert.match(html, /Cart ready, tap to place in Swiggy/);
  assert.doesNotMatch(html, /Approve/);
  vm.runInContext(`S.orderQueue.order_enabled = true`, context);
  assert.match(vm.runInContext('ordersPanel()', context), /Approve ₹262.50 and place/);
});

test('the week says what it is planned from and offers the next step', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.view.source = { kind: 'sample', connected: true, label: 'Sample dishes (not real restaurants)', note: 'x' };`, context);
  assert.match(vm.runInContext('weekScreen()', context), /data-act="live-menus"/);
  vm.runInContext(`S.meta.swiggy_redirect_approved = true; S.view.source = { kind: 'sample', connected: false, label: 'Sample dishes (not real restaurants)', note: 'x' };`, context);
  assert.match(vm.runInContext('weekScreen()', context), /data-act="connect-open"/);
  vm.runInContext(`S.view.source = { kind: 'live', label: 'Live Swiggy menus · 1 restaurant, 3 dishes', note: 'Nutrition is estimated', fetched: '2026-11-02T08:00' };`, context);
  const html = vm.runInContext('weekScreen()', context);
  assert.match(html, /Live Swiggy menus/); assert.match(html, /Nutrition is estimated/);
  assert.doesNotMatch(html, /2026-11-02T08:00/);
  assert.match(html, /<span class="est" title="Estimate">≈₹1,000<i>est\.<\/i><\/span>/);   // the week's spend is an estimate
});

test('an unconnected Swiggy offers Connect, not Retry (409 swiggy_not_connected)', async () => {
  const { context } = fixture({ '/api/user/2/swiggy/addresses': { __status: 409, error: 'swiggy_not_connected',
    code: 'swiggy_not_connected', message: 'Connect your Swiggy account first (More → Swiggy connection).',
    action: { label: 'Connect Swiggy', act: 'swiggy-connect' } } });
  await context.bootPromise;
  await vm.runInContext("guard(() => api('/api/user/2/swiggy/addresses'))", context);
  const bar = vm.runInContext('errbar()', context);
  assert.match(bar, /data-act="connect-open"/);
  assert.match(bar, /Connect Swiggy to use real menus and prices\./);      // one message on every live surface
  assert.doesNotMatch(bar, /data-act="reload"/);
  // a real upstream failure keeps Retry
  vm.runInContext("S.error = 'Swiggy returned an error'; S.errorCode = 'swiggy_error'", context);
  assert.match(vm.runInContext('errbar()', context), /data-act="reload"/);
});

test('merge E+G: one grocery line shows its pack count, who it serves, a have-it tick and its swap', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.view.coach = { headline: '3 cook meals still to cook this week', swaps_available: true, recipes: [], safe_with_swap: [],
    basket: { total: 180, items: [
      { name: 'Toor dal 500g', qty: 2, price: 180, need: 360, unit: 'g', servings: 6, recipe: 'Dal + rice', have: false, token: 'toor_dal',
        swappable: true, swap: { token: 'masoor_dal', name: 'Masoor dal', reason: 'out_of_stock' } },
      { name: 'Rice 1kg', qty: 1, price: 70, need: 540, unit: 'g', servings: 6, recipe: 'Dal + rice', have: true, token: 'rice', swappable: true, swap: null }] } }`, context);
  const html = vm.runInContext('cookingPanel()', context);
  assert.match(html, /2 × <s>Toor dal 500g<\/s> → <b>Masoor dal<\/b> <small>\(out of stock\)<\/small>/);
  assert.match(html, /needs 360 g for 6 servings/);
  assert.match(html, /data-have="Toor dal 500g" >/);
  assert.match(html, /data-unswap="toor_dal"/);
  assert.match(html, /data-have="Rice 1kg" checked/);
  assert.ok(!html.includes('data-oos="rice"'));            // already have it: nothing to report out of stock
  assert.match(html, /Prices are for the original items/);
});

// ---- One "Connect Swiggy" path on every live surface (proposal 2) ----
const NOT_CONNECTED = { __status: 409, error: 'swiggy_not_connected', code: 'swiggy_not_connected',
  message: 'Connect your Swiggy account first.' };
function memoryStorage(initial = {}) {
  const m = new Map(Object.entries(initial));
  return { map: m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}
function assertConnectPrompt(html, where) {
  assert.match(html, /Connect Swiggy to use real menus and prices\./, where);
  assert.match(html, /data-act="connect-open"/, where);
  assert.doesNotMatch(html, /data-act="reload"|>Retry</, where);
}

test('every live surface without a Swiggy connection shows the same Connect prompt, never Retry', async () => {
  const { context } = fixture({
    '/api/plan/42/live-menus': NOT_CONNECTED,
    '/api/session/7/swiggy-cart/preview': NOT_CONNECTED,
    '/api/user/2/swiggy/menu?restaurant=A2B': NOT_CONNECTED,
    '/api/user/2/swiggy/live-menu?restaurant_id=r-1&restaurant_name=A2B': NOT_CONNECTED,
    '/api/user/2/swiggy/live-cart': NOT_CONNECTED,
  });
  await context.bootPromise;
  const actions = [['live-menus'], ['order-meal', 7], ['order-week', 7], ['live-place', 'r-1', 'A2B']];
  for (const [kind, ...args] of actions) {
    vm.runInContext(`S.error = null; S.errorCode = null;`, context);
    await vm.runInContext(`guard(() => liveAction(${JSON.stringify(kind)}, ...${JSON.stringify(args)}))`, context);
    assertConnectPrompt(vm.runInContext('errbar()', context), kind);
    assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(S.pendingResume)', context)), { kind, args });
  }
  await vm.runInContext('refreshLiveCart(false)', context);                         // the live cart panel
  assertConnectPrompt(vm.runInContext('liveError(S.liveCartError, S.liveCartErrorCode)', context), 'live cart');
  vm.runInContext('S.meta.swiggy_redirect_approved = true', context);
  assertConnectPrompt(vm.runInContext("sourceLine({ kind: 'sample', connected: false, label: 'Sample dishes', note: '' })", context), 'plan source');
  // an ordinary failure still offers Retry, and is not mistaken for a connection problem
  vm.runInContext(`S.error = "Swiggy is busy"; S.errorCode = "swiggy_rate_limited";`, context);
  assert.match(vm.runInContext('errbar()', context), /data-act="reload"/);
});

test('after connecting, the action the user started resumes exactly once', async () => {
  // 1. Not connected: "Add to Swiggy cart" is remembered across the Swiggy sign-in redirect.
  const storage = memoryStorage({ 'smartplate.user': '2' });
  const first = fixture({ '/api/session/7/swiggy-cart/preview': NOT_CONNECTED }, { storage });
  await first.context.bootPromise;
  await vm.runInContext('guard(() => liveAction("order-meal", 7))', first.context);
  vm.runInContext('rememberResume()', first.context);
  assert.equal(JSON.parse(storage.map.get('smartplate.resume')).kind, 'order-meal');

  // 2. Back from Swiggy, connected but no address yet: "Deliver to" opens, and the action waits for it.
  const review = { session_id: 7, item: 'Mini Tiffin', restaurant: 'A2B', address: 'Home', menu_price: 125,
    planned_cost: 160, fingerprint: 'f1' };
  const cart = { session_id: 7, item: 'Mini Tiffin', restaurant: 'A2B', planned_cost: 160, to_pay: 171, over_plan: 11, bill: null,
    checkout_url: 'https://www.swiggy.com/checkout' };
  const connected = { connected: true, address: null, tools: [], callback_url: '' };
  const back = fixture({ '/api/user/2/swiggy': connected, '/api/session/7/swiggy-cart/preview': review,
    '/api/session/7/swiggy-cart': cart, '/api/user/2/swiggy/addresses': [{ id: 'addr-home', label: 'Home', text: 'Adyar' }] },
    { storage, location: { search: '?swiggy=connected', href: '/' } });
  await back.context.bootPromise;
  const previews = () => back.calls.filter(c => c.url === '/api/session/7/swiggy-cart/preview').length;
  assert.equal(previews(), 0);
  assert.equal(vm.runInContext('S.addrSheet', back.context), true);                // quick connection: straight to "Deliver to"
  assert.equal(storage.map.has('smartplate.resume'), false);                       // taken once, never again
  assert.equal(vm.runInContext('S.pendingResume.kind', back.context), 'order-meal');

  // 3. The address is chosen: the cart is filled, once.
  vm.runInContext("S.swiggy = { ...S.swiggy, address: { id: 'addr-home', label: 'Home' } }", back.context);
  assert.equal(await vm.runInContext('resumePending()', back.context), true);
  assert.equal(previews(), 1);
  assert.equal(vm.runInContext('S.carts[7].to_pay', back.context), 171);
  assert.equal(await vm.runInContext('resumePending()', back.context), false);
  assert.equal(previews(), 1);

  // 4. A later reload does not run it again.
  const again = fixture({ '/api/user/2/swiggy': { ...connected, address: { id: 'addr-home', label: 'Home' } },
    '/api/session/7/swiggy-cart/preview': review }, { storage, location: { search: '?swiggy=connected', href: '/' } });
  await again.context.bootPromise;
  assert.equal(again.calls.filter(c => c.url === '/api/session/7/swiggy-cart/preview').length, 0);
});

test('a resume saved for another profile, or long ago, is dropped', async () => {
  const old = { user: 2, at: 0, kind: 'order-meal', args: [7] };
  const storage = memoryStorage({ 'smartplate.user': '2', 'smartplate.resume': JSON.stringify(old) });
  const { context, calls } = fixture({ '/api/user/2/swiggy': { connected: true, address: { id: 'a', label: 'Home' }, tools: [] } },
    { storage, location: { search: '?swiggy=connected', href: '/' } });
  await context.bootPromise;
  assert.equal(vm.runInContext('S.pendingResume', context), null);
  assert.equal(calls.filter(c => c.url.includes('swiggy-cart/preview')).length, 0);
});

// ---- The real bill corrects the plan (proposal 1) ----
test('an over-budget cart shows the overage with Approve anyway and Re-plan, never a silent approve', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.orderQueue = { order_enabled: false, queued: 1, planned_total: 165, confirmed_total: 208.5,
    scheduling: { why: 'x' }, meals: [{ session_id: 9, day_index: 1, meal: 'lunch', item: 'Peanut Chutney Dosa',
    restaurant: 'A2B', planned_cost: 165, queued: true, state: 'cart_ready', to_pay: 208.5, bill: null }] };
    S.cartBudget = { 9: { over: true, over_by: 23.5, message: 'This cart is ₹208.50. It takes the week ₹23.50 over your ₹1130 budget (₹1153.50 planned in all).' } };`, context);
  const html = vm.runInContext('orderQueuePanel()', context);
  assert.match(html, /takes the week ₹23\.50 over/);
  assert.match(html, /data-over-ok="9"/);
  assert.match(html, /data-replan="9"/);
  assert.doesNotMatch(html, /data-oq-place="9"/);
  // the single-meal hand-off hides the Swiggy link until the user says yes
  vm.runInContext(`S.carts = { 9: { item: 'Dosa', restaurant: 'A2B', to_pay: 208.5, over_plan: 43.5, checkout_url: 'https://www.swiggy.com/',
    budget: { over: true, message: 'over' } } }`, context);
  assert.doesNotMatch(vm.runInContext('cartNote({ session_id: 9 })', context), /data-handoff/);
  vm.runInContext('S.carts[9].budgetOk = true', context);
  assert.match(vm.runInContext('cartNote({ session_id: 9 })', context), /data-handoff="9"/);
});

test('a live dish says its delivery fee is estimated until a real bill', async () => {
  const { context } = fixture(); await context.bootPromise;
  const row = (fee, extra = '') => vm.runInContext(`mealRow({ kind: 'delivery', status: 'active', item: 'Dosa', restaurant: 'A2B',
    cost: 171, session_id: 3, delivery_fee: ${JSON.stringify(fee)}${extra} }, 'lunch', 0)`, context);
  assert.match(row({ amount: 35, estimated: true }), /delivery ₹35 est\./);
  assert.match(row({ amount: 41, estimated: false }), /delivery ₹41 from your bill/);
  assert.match(row(null, ', real_bill: true'), /real Swiggy bill/);
});

// ---- Honest copy (8 Oct 2026 audit, S1/S2): the UI says what this server really does ----
const SIMULATED_FREE = { modes: { balanced: 'Balanced' }, mode_outcomes: {}, version: '1.1.0', swiggy_provider: 'simulated',
  swiggy_redirect_approved: false, storage: { engine: 'sqlite', persistent: false, reason: "on Render's temporary disk" } };

test('welcome on the simulated, unapproved server never promises real Swiggy restaurants', async () => {
  const { context, element } = fixture({ '/api/meta': SIMULATED_FREE });
  vm.runInContext("localStorage.getItem = () => null", context);
  await vm.runInContext('boot()', context);
  const html = element.innerHTML;
  assert.doesNotMatch(html, /sample|demo/i);
  assert.match(html, /once Swiggy approves connecting/);
  assert.doesNotMatch(html, /connect Swiggy to find real restaurants/i);
  assert.doesNotMatch(html, /browse their current Swiggy menus/);
});

test('welcome promises real restaurants only once Swiggy approved sign-in', async () => {
  const meta = { ...SIMULATED_FREE, swiggy_provider: 'live', swiggy_redirect_approved: true, storage: { persistent: true } };
  const { context, element } = fixture({ '/api/meta': meta });
  vm.runInContext("localStorage.getItem = () => null", context);
  await vm.runInContext('boot()', context);
  const lede = element.innerHTML.match(/<h1 class="hero-h">[^<]*<\/h1>\s*<p class="sub"[^>]*>([^<]*)<\/p>/)[1];
  assert.match(lede, /from Swiggy places near you/);
  assert.doesNotMatch(lede, /sample|approves/i);
});

test('a temporary-disk server shows the data-erasure banner on every screen', async () => {
  const { context, element } = fixture({ '/api/meta': SIMULATED_FREE });
  await context.bootPromise;                       // signed-in app screen
  assert.match(element.innerHTML, /Trial server:[^<]*<\/strong> profiles, plans and Swiggy links here are stored on a temporary disk and are erased/);
  vm.runInContext("S.view = null; S.welcome = true; render()", context);
  assert.match(element.innerHTML, /class="storage-warning"/);
  vm.runInContext("startOnboard()", context);
  assert.match(element.innerHTML, /class="storage-warning"/);
});

test('no erasure banner when storage is persistent or unknown', async () => {
  for (const persistent of [true, null]) {
    const { context, element } = fixture({ '/api/meta': { ...SIMULATED_FREE, storage: { persistent } } });
    await context.bootPromise;
    assert.doesNotMatch(element.innerHTML, /storage-warning/);
  }
});

test('a free server with Postgres (DATABASE_URL) shows no erasure banner', async () => {
  const storage = { engine: 'postgres', persistent: true,
                    reason: 'external Postgres (DATABASE_URL): survives restarts and redeploys' };
  const { context, element } = fixture({ '/api/meta': { ...SIMULATED_FREE, storage } });
  await context.bootPromise;
  assert.doesNotMatch(element.innerHTML, /storage-warning|temporary disk/);
  vm.runInContext("S.view = null; S.welcome = true; render()", context);
  assert.doesNotMatch(element.innerHTML, /storage-warning/);
});

test('the no-dishes state offers Connect Swiggy only when sign-in is approved', async () => {
  const { context } = fixture({ '/api/meta': SIMULATED_FREE }); await context.bootPromise;
  richView(context);
  vm.runInContext("S.view.source = { kind: 'none', connected: false, needs: 'connect', note: 'Connecting Swiggy from SmartPlate is waiting for Swiggy\\'s approval.' }", context);
  let html = vm.runInContext('todayScreen()', context);
  assert.doesNotMatch(html, /data-act="connect-open"/);
  assert.match(html, /waiting for Swiggy&#39;s approval/);
  vm.runInContext('S.meta.swiggy_redirect_approved = true', context);
  html = vm.runInContext('todayScreen()', context);
  assert.match(html, /data-act="connect-open"/);
});

test('address list says how to add a missing address and marks the one Ziggy uses', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, tools: [], address: { id: 'addr-home', label: 'Home · Adyar' } };
    S.swAddrs = [{ id: 'addr-home', label: 'Home', text: 'Adyar' }, { id: 'addr-work', label: 'Work', text: '<OMR>' }];`, context);
  const html = vm.runInContext('addressSheet()', context);
  assert.match(html, /Address not listed\? Add it in the Swiggy app/);
  assert.match(html, /data-act="swiggy-refresh-addresses"/);
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
  assert.ok(!html.includes('<OMR>'));
  vm.runInContext('S.swAddrs = null', context);
  assert.match(vm.runInContext('addressSheet()', context), /data-act="swiggy-refresh-addresses"/);
  assert.match(vm.runInContext('connectionPanel()', context), /data-act="addr-open">Change/);
});

test('Refresh addresses reads Swiggy fresh and drops a default that is gone', async () => {
  const { context, calls } = fixture({
    '/api/user/2/swiggy/addresses/refresh': { dropped: 'Home · Adyar', status: { connected: true, address: null },
      addresses: [{ id: 'addr-minjur', label: 'Other', text: 'Minjur', chosen: false }] },
  });
  await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, tools: [], address: { id: 'addr-home', label: 'Home · Adyar' } };
    S.liveCart = { item: 'Old cart' }; S.liveCartError = 'Swiggy returned the cart for a different delivery address.';`, context);
  await vm.runInContext('refreshSwiggyAddresses()', context);
  const call = calls.find(c => c.url.endsWith('/swiggy/addresses/refresh'));
  assert.equal(call.options.method, 'POST');
  assert.equal(vm.runInContext('S.swiggy.address', context), null);
  assert.equal(vm.runInContext('S.liveCart', context), null);
  assert.equal(vm.runInContext('S.liveCartError', context), null);
  assert.equal(vm.runInContext('S.swAddrs[0].id', context), 'addr-minjur');
});

test('a cart for another address says which and offers to deliver there', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'addr-home', label: 'Home · Adyar' }, order_enabled: true };
    S.liveCart = { item: 'Veg Meals', restaurant: 'Real Place', to_pay: 215, orderable: false,
      other_address: { id: 'addr-work', label: 'Work · OMR' } };`, context);
  const html = vm.runInContext('liveCartBlock()', context);
  assert.match(html, /This cart is for Work · OMR, not Home · Adyar/);
  assert.match(html, /data-swaddr="addr-work"/);
  assert.ok(!html.includes('data-act="review-live-checkout"'));
});

// Live finding (8 Oct 2026): Review showed only "⚠ not found", and Retry re-ran an
// unrelated plan reload, so the banner never cleared.
const LIVE_MENU = `S.swiggy = { connected: true, address: { id: 'addr-home', label: 'Minjur' } };
  S.liveBrowseMenu = { restaurant: { id: 'r-1', name: 'Minjur Bhavan' }, address: 'Minjur', fetched: 'today',
    items: [{ id: 'm0', name: 'Veg Biryani', price: 160, veg: true, in_stock: true, has_options: false }] };`;

test('a failed Review names the action and its reason, and Retry re-runs that Review', async () => {
  const preview = '/api/user/2/swiggy/live-cart/preview';
  const { context, calls } = fixture({ [preview]: { __status: 502, error: 'Swiggy said: Item lookup failed',
    code: 'swiggy_error' } });
  await context.bootPromise;
  vm.runInContext(LIVE_MENU, context);
  await vm.runInContext("guard(() => reviewLiveItem('m0', 'Veg Biryani'), 'Review “Veg Biryani”')", context);
  const bar = vm.runInContext('errbar()', context);
  assert.match(bar, /Review “Veg Biryani” failed: Swiggy said: Item lookup failed/);
  assert.doesNotMatch(bar, /⚠ not found/);
  assert.match(bar, /data-act="reload"/);
  const before = calls.filter(c => c.url === preview).length;
  const plans = calls.filter(c => c.url === '/api/plan/42').length;
  await vm.runInContext('guard(retryLast)', context);                       // the Retry button
  assert.equal(calls.filter(c => c.url === preview).length, before + 1);   // the same Review again
  assert.equal(calls.filter(c => c.url === '/api/plan/42').length, plans); // not an unrelated reload
  assert.match(vm.runInContext('S.error', context), /^Review “Veg Biryani” failed:/);
});

test('a profile the server no longer has returns to welcome with the reason, never a dead Retry', async () => {
  const msg = "This profile is no longer on this server. The server's temporary storage was erased by a restart or redeploy. Create your profile again, or add it with its recovery code.";
  const { context } = fixture({ '/api/user/2/swiggy/dishes?restaurant_id=r-1&restaurant_name=Minjur%20Bhavan&query=Veg%20Biryani&offset=0':
    { __status: 404, error: msg, code: 'profile_missing' } });
  await context.bootPromise;
  vm.runInContext(LIVE_MENU, context);
  await vm.runInContext("guard(() => searchLiveDishes('Veg Biryani'), 'Find dishes for “Veg Biryani”')", context);
  assert.equal(vm.runInContext('S.userId', context), null);
  assert.equal(vm.runInContext('S.welcome', context), true);
  assert.match(vm.runInContext('S.error', context), /^Find dishes for “Veg Biryani” failed: This profile is no longer on this server/);
  const bar = vm.runInContext('errbar(false)', context);
  assert.match(bar, /restart or redeploy/);
  assert.doesNotMatch(bar, /data-act="reload"/);
});

test('an error with no message names the HTTP status instead of a bare word', async () => {
  const { context } = fixture({ '/api/user/2/swiggy/restaurants?query=Sangeetha': { __status: 500 } });
  await context.bootPromise;
  vm.runInContext(LIVE_MENU, context);
  await vm.runInContext("guard(() => searchLivePlaces('Sangeetha'), 'Search Swiggy for “Sangeetha”')", context);
  assert.match(vm.runInContext('S.error', context), /^Search Swiggy for “Sangeetha” failed: The server answered HTTP 500/);
});

// ---- Dark redesign (8 Oct 2026): three destinations, honest money, real reasons, one-tap toggles ----
test('three tabs, the same on phone and web; Me holds everything else', async () => {
  const { context, element } = fixture(); await context.bootPromise;
  vm.runInContext('render()', context);
  const nav = element.innerHTML.match(/<nav class="nav" aria-label="Main">([^]*?)<\/nav>/)[1];
  assert.deepEqual([...nav.matchAll(/data-tab="(\w+)"/g)].map(m => m[1]), ['today', 'week', 'saved']);
  assert.match(nav, /<span>Today<\/span>[^]*<span>Week<\/span>[^]*<span>Saved<\/span>/);
  assert.match(element.innerHTML, /class="avatar" data-tab="more"/);
  vm.runInContext('S.more = null', context);
  const me = vm.runInContext('meScreen()', context);
  for (const k of ['recap', 'insights', 'settings', 'connection', 'household', 'calendar', 'profiles'])
    assert.match(me, new RegExp(`data-go="more:${k}"`));
  assert.match(me, /data-look="theme:dark"/); assert.match(me, /data-look="palette:mesh"/);
  vm.runInContext("S.tab = 'week'; S.weekView = 'groceries'; S.view.coach = { headline: 'x', recipes: [], basket: { total: 0, items: [] } }", context);
  assert.match(vm.runInContext('weekScreen()', context), /Cooking &amp; groceries/);
});

test('appearance: light, dark or the phone\'s setting, and the Zomato × Swiggy colours, remembered here', async () => {
  const storage = memoryStorage({ 'smartplate.user': '2' });
  const { context } = fixture({}, { storage }); await context.bootPromise;
  const attrs = {};
  context.document.documentElement = { setAttribute: (k, v) => { attrs[k] = v; }, removeAttribute: (k) => { delete attrs[k]; } };
  vm.runInContext("setLook('theme', 'dark'); setLook('palette', 'mesh')", context);
  assert.deepEqual(attrs, { 'data-theme': 'dark', 'data-palette': 'mesh' });
  assert.equal(storage.map.get('ziggy.theme'), 'dark');
  vm.runInContext("setLook('theme', 'system'); setLook('palette', 'ember')", context);
  assert.deepEqual(attrs, {});
  assert.match(vm.runInContext('meScreen()', context), /data-look="theme:system" aria-pressed="true"/);
});

test('the pick explains itself in plain words, never with a score, and its price is an estimate', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  vm.runInContext(`S.view.user.diet = 'nonveg'; S.view.user.allergens = ['peanut'];
    S.view.next_up.cell.nutrition = { protein_g: 38 }; S.view.budget = { spend: 1000, budget: 1500 }`, context);
  const html = vm.runInContext('nextUpCard(S.view.next_up)', context);
  assert.match(html, /aria-label="Why this pick"/);
  assert.match(html, /Week stays in budget/);
  assert.match(html, /≈38 g protein \(estimate\)/);
  assert.match(html, /Peanut filtered out/);                         // sample catalogue
  assert.match(html, /Your pick/);
  assert.doesNotMatch(html, /\bFit\b|score/i);
  assert.match(html, /<span class="est" title="Estimate">≈₹150<i>est\.<\/i><\/span>/);
  vm.runInContext("S.view.source = { kind: 'live' }", context);    // live menus: only what the name says
  assert.match(vm.runInContext('nextUpCard(S.view.next_up)', context), /No peanut by dish name/);
  // two taps at most: Change opens one sheet; Skip is one tap with Undo
  assert.match(html, /data-sheet="9"/);
  assert.match(html, /data-sess="9:skipped"/);
  assert.doesNotMatch(html, /data-kind="\d+:|data-pin=/);
});

test('a checked cart shows Swiggy\'s real bill, the estimate it replaced, and paying becomes the next step', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  vm.runInContext(`S.view.user.diet = 'nonveg'; S.view.user.allergens = [];
    S.swiggy = { connected: true, address: { id: 'home', label: 'Home' } };
    S.carts = { 9: { item: 'Veg Meals', restaurant: 'Saravana', to_pay: 215, over_plan: 65, planned_cost: 150,
      checkout_url: 'https://www.swiggy.com/checkout', budget: { over: false },
      bill: { to_pay: 215, lines: [{ label: 'Item total', amount: 180 }, { label: 'Delivery fee', amount: 35 }] } } }`, context);
  const html = vm.runInContext('nextUpCard(S.view.next_up)', context);
  assert.match(html, /Added to your Swiggy cart/);
  assert.match(html, /Swiggy's bill/);
  assert.match(html, /<td class="num"><span class="real"[^>]*>₹180<\/span><\/td>/);
  assert.match(html, /Plan estimate <span class="est"[^>]*>≈₹150<i>est\.<\/i><\/span> · ₹65 more/);
  assert.doesNotMatch(html, /data-cart="9"/);                       // already reviewed: no second "Review"
  assert.match(html, /href="https:\/\/www\.swiggy\.com\/checkout"[^>]*data-handoff="9">Pay in Swiggy/);
  assert.match(html, /Swiggy(&#39;|')s cancellation policy applies\./);  // Swiggy sent no cancellation note
  assert.match(html, /Ziggy never pays for you/);
  vm.runInContext("S.carts[9].cancellation_note = '100% cancellation fee if cancelled after 60 seconds'", context);
  const withNote = vm.runInContext('nextUpCard(S.view.next_up)', context);
  assert.match(withNote, /100% cancellation fee if cancelled after 60 seconds/);
  assert.doesNotMatch(withNote, /cancellation policy applies/);
});

test('week rows: tap opens the Change sheet; skipped meals read as skipped; weather only when it is real', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  const week = vm.runInContext('weekScreen()', context);
  assert.match(week, /class="mrow[^"]*" data-meal="9" aria-label="Lunch: Veg &lt;b&gt;Meals&lt;\/b&gt;"/);
  assert.match(week, /data-meal="10"/);
  assert.doesNotMatch(week, /28°/);                                   // sample weather is not drawn
  vm.runInContext("S.view.weather_source = 'live'; S.view.week_context[0].weather_source = 'live'", context);
  assert.match(vm.runInContext('weekScreen()', context), /☂ 28°/);
  const off = vm.runInContext(`mealRow({ kind: 'skipped', status: 'skipped', item: 'Skipped', session_id: 4, reasons: [] }, 'dinner', 0)`, context);
  assert.match(off, /class="mrow off/);
  assert.match(off, /aria-label="Dinner: Skipped"/);
});

test('sample weather never appears as a heads-up; real weather does', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.view.heads_up = [{ kind: 'weather', level: 'info', icon: '🌧', title: 'Rain likely', body: 'x' },
    { kind: 'holiday', level: 'info', icon: '🎉', title: 'Diwali', body: 'y' }]; S.view.weather_source = 'sample'`, context);
  let html = vm.runInContext('headsUp()', context);
  assert.doesNotMatch(html, /Rain likely/); assert.match(html, /Diwali/);
  vm.runInContext("S.view.weather_source = 'live'", context);
  assert.match(vm.runInContext('headsUp()', context), /Rain likely/);
});

test('dishes get distinct icons by kind, and a real Swiggy photo when one is given', async () => {
  const { context } = fixture(); await context.bootPromise;
  const kind = (n) => vm.runInContext(`dishKind(${JSON.stringify(n)})[0]`, context);
  assert.deepEqual(['Chicken Dum Biryani', 'Ghee Roast Dosa', 'Idli Vada', 'Paneer Butter Masala', 'Filter Coffee',
    'Gulab Jamun', 'Veg Meals', 'Chicken Shawarma Roll', 'Hakka Noodles', 'Kothu Parotta', 'Fish Fry', 'Egg Omelette',
    'Chicken 65', 'Margherita Pizza', 'Samosa', 'Quinoa Bowl', 'Something Unknown'].map(kind),
    ['biryani', 'dosa', 'tiffin', 'curry', 'drink', 'dessert', 'thali', 'wrap', 'noodles', 'bread', 'fish', 'egg',
     'grill', 'pizza', 'snack', 'bowl', 'plate']);
  assert.equal(vm.runInContext('dishKind("Dal + rice", true)[0]', context), 'cook');
  const photo = vm.runInContext(`dishIcon('<Dosa>', { image: 'https://media-assets.swiggy.com/a.jpg' })`, context);
  assert.match(photo, /<img class="dphoto" src="https:\/\/media-assets\.swiggy\.com\/a\.jpg" alt="" loading="lazy"/);
  assert.match(photo, /data-dish="&lt;Dosa&gt;"/);
});

test('full live menu: Swiggy categories, bestsellers, veg marks, name-based allergen flags and one-tap filters', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.view.user.diet = 'nonveg'; S.view.user.allergens = ['peanut']; S.view.budget = { spend: 1300, budget: 1500 };
    S.swiggy = { connected: true, address: { id: 'home', label: 'Home' } };
    S.liveBrowseMenu = { restaurant: { id: 'r-1', name: 'Real Kitchen' }, address: 'Home', fetched: 'now', truncated: true,
      categories: ['Recommended', 'Dosa', 'Biryani'], items: [
      { id: 'a', name: 'Peanut Chutney Dosa', price: 90, veg: true, in_stock: true, categories: ['Recommended', 'Dosa'], bestseller: true, name_allergens: ['peanut'] },
      { id: 'b', name: 'Chicken Biryani', price: 260, veg: false, in_stock: true, categories: ['Biryani'], name_allergens: [] },
      { id: 'c', name: 'Masala Dosa', price: 120, veg: true, in_stock: false, categories: ['Dosa'], name_allergens: [] }] }`, context);
  let html = vm.runInContext('menuSheet()', context);
  assert.match(html, /data-cat="Dosa"[^>]*>Dosa<\/button>/);
  assert.match(html, /data-cat="Recommended"[^>]*>Bestsellers/);
  assert.equal((html.match(/data-live-item="a"/g) || []).length, 1);   // once, under its own category
  assert.match(html, /<p class="lmcat">Dosa<\/p>[^]*<p class="lmcat">Biryani<\/p>/);
  assert.match(html, /Name suggests peanut/);
  assert.match(html, /no flag doesn't mean safe/);
  assert.match(html, /at most 150 dishes/);
  assert.match(html, /aria-label="Non-veg"/);
  assert.match(html, /Within ₹200 left/);
  vm.runInContext("S.filters = { veg: true, stock: true, budget: true, flagged: true, fast: false }", context);
  html = vm.runInContext('menuSheet()', context);
  assert.doesNotMatch(html, /data-live-item="[abc]"/);                 // non-veg, unavailable, flagged and over-budget all hidden
  assert.match(html, /3 hidden by your filters/);
  vm.runInContext("S.filters = { veg: false, stock: false, budget: false, flagged: false, fast: false }; S.menuCat = 'Biryani'", context);
  html = vm.runInContext('menuSheet()', context);
  assert.match(html, /data-live-item="b"/); assert.doesNotMatch(html, /data-live-item="a"/);
});

test('the address pill opens the real Swiggy address list in one tap', async () => {
  const { context, calls } = fixture({ '/api/user/2/swiggy/addresses': [{ id: 'addr-1', label: 'Home', text: '<Minjur>' }] });
  await context.bootPromise;
  vm.runInContext("S.swiggy = { connected: true, address: { id: 'addr-1', label: 'Home · Minjur' } }", context);
  assert.match(vm.runInContext('topbar()', context), /data-act="addr-open"[^>]*aria-label="Delivering to Home · Minjur\. Change address"/);
  await vm.runInContext('openAddresses()', context);
  assert.ok(calls.some(c => c.url === '/api/user/2/swiggy/addresses'));
  const sheet = vm.runInContext('addressSheet()', context);
  assert.match(sheet, /data-swaddr="addr-1" aria-pressed="true"/);
  assert.match(sheet, /&lt;Minjur&gt;/);
  assert.match(sheet, /data-act="swiggy-refresh-addresses"/);
  vm.runInContext('S.swiggy = { connected: false }', context);
  assert.doesNotMatch(vm.runInContext('topbar()', context), /class="addr/);   // no fake address when not connected
});

test('one-tap cook picks the first safe recipe the server offers; order picks the first dish that fits', async () => {
  const opts = { session: { id: 9 }, limits: {}, usual: [{ restaurant: 'A2B', dishes: [{ item_id: 3, name: 'Too much', fits: false }, { item_id: 4, name: 'Dosa', fits: true }] }],
    new: [], cook: [{ recipe_key: 'dal_rice', name: 'Dal + rice' }], hidden: {} };
  const view = { plan: { id: 42 }, user: { id: 2 }, grid: [], budget: {} };
  const { context, calls } = fixture({ '/api/session/9/options': opts, '/api/session/9/choose': view });
  await context.bootPromise;
  richView(context);
  vm.runInContext('globalThis.SAVED = JSON.stringify(S.view)', context);   // choose() adopts the reply's view
  await vm.runInContext("toKind(9, 'cook')", context);
  assert.deepEqual(JSON.parse(calls.filter(c => c.url === '/api/session/9/choose').at(-1).options.body), { recipe_key: 'dal_rice' });
  vm.runInContext("S.view = JSON.parse(SAVED); S.view.grid[0].meals.lunch.kind = 'cook'", context);
  await vm.runInContext("toKind(9, 'delivery')", context);
  assert.deepEqual(JSON.parse(calls.filter(c => c.url === '/api/session/9/choose').at(-1).options.body), { item_id: 4 });
  vm.runInContext("S.view = JSON.parse(SAVED)", context);
  await vm.runInContext('togglePin(9)', context);                        // pinned: unpin = let SmartPlate choose
  assert.deepEqual(JSON.parse(calls.filter(c => c.url === '/api/session/9/choose').at(-1).options.body), { action: 'auto' });
});

test('expenses say which amounts are real Swiggy bills', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.receipts = { total: 395, business_total: 0, rows: [
    { iso_date: '2026-11-02', note: 'Veg Meals', category: 'personal', amount: 245, real: true },
    { iso_date: '2026-11-03', note: 'Dosa', category: 'personal', amount: 150, real: false }] }`, context);
  const html = vm.runInContext('receiptsPanel()', context);
  assert.match(html, /<span class="real"[^>]*>₹245<\/span> <span class="tag good">real bill<\/span>/);
  assert.match(html, /<span class="est" title="Estimate">≈₹150<i>est\.<\/i><\/span>/);
  assert.match(html, /Real bills<\/span><b>₹245</);
});

test('offline says so and how old the plan is; the over-budget choice gives both options equal weight', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext("S.offline = true; S.loadedAt = new Date(2026, 10, 2, 13, 5)", context);
  assert.match(vm.runInContext('offlineBar()', context), /You're offline\. Showing your plan as loaded at 1:05/);
  const warn = vm.runInContext("budgetWarn(9, { message: 'This cart takes the week ₹80 over.' })", context);
  assert.match(warn, /<button data-over-ok="9">Approve anyway<\/button>/);
  assert.match(warn, /<button data-replan="9">Re-plan the rest<\/button>/);
  assert.doesNotMatch(warn, /primary/);
});

test('boot shows a clear error with Try again when the database is unavailable (no endless splash)', async () => {
  const { context, element } = fixture({
    '/api/meta': { __status: 503, error: "SmartPlate can't reach its database right now. Your data is safe; try again in a minute.",
      code: 'database_unavailable' } });
  await context.bootPromise;
  assert.match(element.innerHTML, /Ziggy can(&#39;|')t start right now/);
  assert.match(element.innerHTML, /reach its database/);
  assert.match(element.innerHTML, /retry-boot/);
  assert.doesNotMatch(element.innerHTML, /Loading Ziggy/);
});

test('boot treats a generic 500 as a server problem, not a silent hang', async () => {
  const { context, element } = fixture({ '/api/users': { __status: 500 } });
  await context.bootPromise;
  assert.match(element.innerHTML, /start right now/);
  assert.match(element.innerHTML, /retry-boot/);
});

test('money looks the same everywhere: menu and plan prices are ≈ est., only cart numbers are real and exact', async () => {
  const { context } = fixture(); await context.bootPromise;
  assert.equal(vm.runInContext('rupee(21.58)', context), '₹21.58');
  assert.equal(vm.runInContext('rupee(21.5)', context), '₹21.50');
  assert.equal(vm.runInContext('rupee(160)', context), '₹160');
  assert.match(vm.runInContext('livePrice(180)', context), /class="est"[^>]*>≈₹180<i>est\.<\/i>/);
  assert.match(vm.runInContext('livePrice(180, false)', context), /class="est"/);   // a menu price is never "real"
  const bill = vm.runInContext(`billLines({ to_pay: 188, rounding: 0.42, itemised: true, lines: [
    { label: 'Item Total', amount: 160 }, { label: 'Delivery Fee', amount: 6 }, { label: 'GST & Other Charges', amount: 21.58 }] })`, context);
  assert.match(bill, /GST &amp; Other Charges<\/td><td class="num"><span class="real"[^>]*>₹21\.58<\/span>/);
  assert.doesNotMatch(bill, /₹22|as Swiggy shows them/);
  assert.match(bill, /Swiggy rounds the total to the rupee/);
  const unitemised = vm.runInContext(`billLines({ to_pay: 262.5, unitemised: 17.5, itemised: false, lines: [{ label: 'Item Total', amount: 210 }] })`, context);
  assert.match(unitemised, /includes <span class="real"[^>]*>₹17\.50<\/span> it didn't itemise/);
});

test('a dish picked from a live menu shows a review with its estimate and "Adding…" while the cart updates', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext("S.liveOrderReview = { item: '<Veg Meals>', restaurant: 'Saravana', address: 'Home', menu_price: 180, adding: true }", context);
  const adding = vm.runInContext('liveOrderReviewDialog()', context);
  assert.match(adding, /disabled aria-busy="true"><span class="roller"[^]*Adding…/);
  assert.match(adding, /≈₹180<i>est\.<\/i>/);
  assert.ok(!adding.includes('<Veg Meals>'));
  vm.runInContext("S.liveOrderReview = null; S.liveLoading = 'Saravana'", context);
  assert.match(vm.runInContext('menuSheet()', context), /aria-busy="true"[^]*Opening Saravana…[^]*class="skeleton"/);
});

test('the setup wizard saves a sign-in so the profile opens on another device', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext('startOnboard(); S.onboard.step = 3', context);
  const html = vm.runInContext('onboardingScreen()', context);
  assert.match(html, /id="ob-login"/); assert.match(html, /id="ob-pw" type="password"/);
  assert.match(html, /So you can open Ziggy on any device/);
  await assert.rejects(vm.runInContext("onboardNav('next')", context), /sign-in name/);
});
