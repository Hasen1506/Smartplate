// Test browser-independent UI state without a browser dependency or CDN.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('smartplate/static/app.js', 'utf8').replace(
  'boot().catch', 'globalThis.bootPromise = boot().catch');

function fixture(overrides = {}) {
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
  Object.assign(replies, overrides);
  const context = vm.createContext({
    document: { getElementById: () => element, querySelectorAll: () => [], querySelector: () => null,
      createElement: () => element, addEventListener() {}, body: element },
    localStorage: { getItem: () => '2', setItem() {} },
    fetch: async (url, options) => { calls.push({ url, options });
      return { ok: !!replies[url] && !replies[url].__status, json: async () => replies[url] || { error: 'Unexpected route' } }; },
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
  assert.match(element.innerHTML, /Create my private profile/);
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

test('sample banner describes demo data without inventing an allergy', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  vm.runInContext("S.view.user.prefs = { sample: true }; S.view.user.allergens = []", context);
  const html = vm.runInContext('todayScreen()', context);
  assert.match(html, /demonstration preferences and sample menu data/);
  assert.ok(!html.includes('fictional allergies'));
});

test('connected profiles with ingredient rules keep the direct Swiggy hand-off', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  vm.runInContext("S.swiggy = { connected: true, address: { id: 'home', label: 'Home' } }", context);
  const html = vm.runInContext('todayScreen()', context);
  assert.match(html, /Order from your area/);
  assert.match(html, /sample weekly planner/);
  assert.ok(!html.includes('data-cart="9"'));
});

test('eligible profiles review and escape the exact live item before a cart update', async () => {
  const { context } = fixture(); await context.bootPromise;
  richView(context);
  vm.runInContext(`S.view.user.diet = 'nonveg'; S.view.user.allergens = [];
    S.swiggy = { connected: true, address: { id: 'home' } };
    S.cartReview = { session_id: 9, item: '<img src=x>', restaurant: '<b>Kitchen</b>',
      address: 'Home', planned_cost: 150, menu_price: null, fingerprint: 'fingerprint' };`, context);
  assert.match(vm.runInContext('nextUpCard(S.view.next_up)', context), /data-cart="9"/);
  const dialog = vm.runInContext('cartReviewDialog()', context);
  assert.match(dialog, /Price to verify/);
  assert.match(dialog, /Add to Swiggy cart/);
  assert.ok(!dialog.includes('<img src=x>') && !dialog.includes('<b>Kitchen</b>'));
});

test('connected Places uses real Swiggy IDs and no sample restaurant cards', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'addr-home', label: 'Home' } };
    S.places = [{ id: 1, name: 'Sample Diner', favourite: true }];
    S.liveFavourites = [{ id: 'rest-42', name: '<Real Place>' }];
    S.liveBrowseMenu = { restaurant: { id: 'rest-42', name: '<Real Place>' }, address: 'Home', fetched: 'today',
      items: [{ id: 'item-7', name: '<Dish>', price: null, veg: true, in_stock: true, has_options: false }] };`, context);
  const html = vm.runInContext('placesScreen()', context);
  assert.match(html, /Order from your area/);
  assert.match(html, /rest-42/);
  assert.match(html, /item-7/);
  assert.match(html, /Price in cart/);
  assert.ok(!html.includes('Sample Diner') && !html.includes('<Real Place>') && !html.includes('<Dish>'));
});

test('real order confirmation shows live total, address and payment before placement', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'home', label: 'Home' }, order_enabled: true };
    S.liveCart = { item: 'Tiffin', restaurant: 'Real Place', to_pay: 160, checkout_url: 'https://www.swiggy.com/' };
    S.checkoutReview = { item: '<Tiffin>', quantity: 1, address: '<Home>', to_pay: 160,
      payment_label: 'Cash on Delivery', fingerprint: 'reviewed' };`, context);
  assert.match(vm.runInContext('livePlacesScreen()', context), /Review and place order/);
  const dialog = vm.runInContext('checkoutReviewDialog()', context);
  assert.match(dialog, /Cash on Delivery/);
  assert.match(dialog, /Confirm and place order · ₹160/);
  assert.ok(!dialog.includes('<Tiffin>') && !dialog.includes('<Home>'));
});

test('recent provider orders and uncertain attempts are visible without raw markup', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.swiggy = { connected: true, address: { id: 'home', label: 'Home' }, order_enabled: true };
    S.liveOrderHistory = { address: '<Home>', attempts: [{ state: 'unknown' }],
      provider_orders: [{ order_id: 'o-1', restaurant: '<Restaurant>', item: 'Tiffin',
        status: 'PREPARING', total: '160', ordered_time: 'now' }] };`, context);
  const html = vm.runInContext('livePlacesScreen()', context);
  assert.match(html, /Check recent Swiggy orders/);
  assert.match(html, /uncertain result/);
  assert.match(html, /o-1/);
  assert.ok(!html.includes('<Home>') && !html.includes('<Restaurant>'));
});

test('live configuration routes orders to real Places instead of a dead simulator', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.meta.swiggy_provider = 'live';`, context);
  assert.match(vm.runInContext('ordersPanel()', context), /Open live Places/);
  assert.ok(!vm.runInContext('ordersPanel()', context).includes('data-act="exec"'));
  assert.ok(!vm.runInContext('weekScreen()', context).includes('data-act="exec"'));
  vm.runInContext(`S.view.source = { kind: 'sample', connected: false, label: 'Sample dishes (not real restaurants)', note: 'Connect Swiggy to plan from real restaurants near you.' };`, context);
  assert.match(vm.runInContext('weekScreen()', context), /Sample dishes \(not real restaurants\)/);
  const budget = vm.runInContext('budgetCard()', context);
  assert.match(budget, /Sample plan estimate/);
  assert.match(budget, /actual purchases are reviewed separately/);
  assert.ok(!budget.includes('Prices include delivery and expected surge'));
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

test('onboarding uses four steps without sample restaurant choices or sample budget quotes', async () => {
  const { context, calls } = fixture(); await context.bootPromise;
  vm.runInContext('startOnboard()', context);
  assert.match(vm.runInContext('onboardingScreen()', context), /What do you eat/);
  vm.runInContext("onboardChip('allergens', 'peanut', true); onboardChip('diet', 'veg', false)", context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(S.onboard.d.allergens)', context)), ['peanut']);
  assert.equal(vm.runInContext('S.onboard.d.diet', context), 'veg');
  vm.runInContext("S.onboard.step = 1; onboardChip('meals', 'lunch', true); onboardChip('meals', 'dinner', true)", context);
  await assert.rejects(vm.runInContext("onboardNav('next')", context), /at least one meal/);
  vm.runInContext('S.onboard.step = 2', context);
  const budget = vm.runInContext('onboardingScreen()', context);
  assert.match(budget, /Enter your own limit/);
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
  assert.match(vm.runInContext('reminderRow()', context), /data-act="download-ics"/);
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
  const html = vm.runInContext('livePlacesScreen()', context);
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
  const html = vm.runInContext('livePlacesScreen()', context);
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
  assert.match(html, /existing orders remain active/);
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
  assert.match(vm.runInContext('livePlacesScreen()', context), /Load more matching dishes/);
  await vm.runInContext("searchLiveDishes('tiffin', true)", context);
  assert.equal(vm.runInContext('S.liveBrowseMenu.items.length', context), 2);
  assert.ok(requests[0].includes('restaurant_id=real-r') && requests[1].endsWith('offset=20'));
  const html = vm.runInContext('livePlacesScreen()', context);
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

test('rating reasons are one tap each, and what was learned shows with an undo', async () => {
  const { context } = fixture(); await context.bootPromise;
  const row = vm.runInContext(`rateRow({ session_id: 5, rating_given: null, reasons_given: ['late'] })`, context);
  for (const k of ['late', 'small', 'spicy', 'pricey', 'great']) assert.match(row, new RegExp(`data-reason="5:${k}"`));
  assert.match(row, /class="chip on" data-reason="5:late"/);
  vm.runInContext(`S.view.learned = [{ key: 'late:3', reason: 'late', text: 'Chennai Mess often arrives late — planned less', taps: 2 }]`, context);
  const line = vm.runInContext('learningLine()', context);
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
  assert.match(html, /Cart ready — tap to place in Swiggy/);
  assert.doesNotMatch(html, /Approve/);
  vm.runInContext(`S.orderQueue.order_enabled = true`, context);
  assert.match(vm.runInContext('ordersPanel()', context), /Approve ₹262.5 and place/);
});

test('the week says what it is planned from and offers the next step', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.view.source = { kind: 'sample', connected: true, label: 'Sample dishes (not real restaurants)', note: 'x' };`, context);
  assert.match(vm.runInContext('weekScreen()', context), /data-act="live-menus"/);
  vm.runInContext(`S.view.source = { kind: 'sample', connected: false, label: 'Sample dishes (not real restaurants)', note: 'x' };`, context);
  assert.match(vm.runInContext('weekScreen()', context), /data-act="swiggy-connect"/);
  vm.runInContext(`S.view.source = { kind: 'live', label: 'Live Swiggy menus · 1 restaurant, 3 dishes', note: 'Nutrition is estimated', fetched: '2026-11-02T08:00' };`, context);
  const html = vm.runInContext('weekScreen()', context);
  assert.match(html, /Live Swiggy menus/); assert.match(html, /Nutrition is estimated/);
  assert.match(vm.runInContext('budgetCard()', context), /live Swiggy prices/);
});

test('an unconnected Swiggy offers Connect, not Retry (409 swiggy_not_connected)', async () => {
  const { context } = fixture({ '/api/user/2/swiggy/addresses': { __status: 409, error: 'swiggy_not_connected',
    code: 'swiggy_not_connected', message: 'Connect your Swiggy account first (More → Swiggy connection).',
    action: { label: 'Connect Swiggy', act: 'swiggy-connect' } } });
  await context.bootPromise;
  await vm.runInContext("guard(() => api('/api/user/2/swiggy/addresses'))", context);
  const bar = vm.runInContext('errbar()', context);
  assert.match(bar, /data-act="swiggy-connect"/);
  assert.match(bar, /Connect your Swiggy account first/);
  assert.doesNotMatch(bar, /data-act="reload"/);
  // a real upstream failure keeps Retry
  vm.runInContext("S.error = 'Swiggy returned an error'; S.errorCode = 'swiggy_error'", context);
  assert.match(vm.runInContext('errbar()', context), /data-act="reload"/);
});
