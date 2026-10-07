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
  vm.runInContext("S.onboard.step = 1", context);
  const rhythm = vm.runInContext('onboardingScreen()', context);
  assert.match(rhythm, /How do you eat on a normal day/);
  for (const m of ['breakfast', 'lunch', 'dinner']) for (const k of ['order', 'cook', 'skip'])
    assert.match(rhythm, new RegExp(`data-ob="rh_${m}" data-val="${k}"`));
  vm.runInContext("onboardChip('rh_breakfast', 'cook', false)", context);
  assert.equal(vm.runInContext('S.onboard.d.rhythm.breakfast', context), 'cook');
  vm.runInContext("onboardChip('rh_breakfast', 'skip', false); onboardChip('rh_lunch', 'skip', false); onboardChip('rh_dinner', 'skip', false)", context);
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
  assert.match(html, /Every shared meal will meet all their allergies and diets/);
});

test('household panel: everyone\'s rules with who they are for, people, and the split', async () => {
  const { context } = fixture(); await context.bootPromise;
  context.HH = HOUSEHOLD;
  vm.runInContext('S.view.household = HH; S.view.user.meals = ["lunch", "dinner"]', context);
  const html = vm.runInContext('householdPanel()', context);
  assert.match(html, /No dairy · Meera/);
  assert.match(html, /No peanut · Dev/);
  assert.match(html, /Vegan · Meera/);
  assert.match(html, /<b>Meera<\/b> <small>\(you\)<\/small>/);
  assert.match(html, /data-hh-edit="7"/);
  assert.ok(!html.includes('data-hh-edit="2"'));                                // your own row goes to settings
  assert.match(html, /eats dinner/);
  assert.match(html, /<option value="by_consumption" selected>By who eats each meal<\/option>/);
  assert.match(html, /<td>Dev<\/td><td class="mono">₹400<\/td>/);
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
  assert.match(html, /class="on" aria-pressed="true" data-eater="9:2">Meera/);
  assert.match(html, /class="" aria-pressed="false" data-eater="9:7">Dev/);
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
  assert.match(html, /₹450<small>\/₹3,000<\/small>/);
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

test('More lists Credits & licences, linking to the credits page', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext('S.more = null', context);
  assert.match(vm.runInContext('moreScreen()', context), /<a class="mitem" href="\/credits"><b>Credits &amp; licences<\/b>/);
});

test('settings: rhythm, calorie split, targets and variety cap are editable', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext(`S.view.user.prefs = { rhythm: { breakfast: 'cook', lunch: 'order', dinner: 'skip' } };
    S.view.user.nutrition_targets = { meal_share: { breakfast: 0.2, lunch: 0.5, dinner: 0.3 }, kcal: 2100 };
    S.view.user.health_targets = { max_item_repeat: 5 }`, context);
  const html = vm.runInContext('tuningForm()', context);
  assert.match(html, /name="rh_breakfast"[^]*value="cook" selected/);
  assert.match(html, /name="sh_lunch" type="number" min="10" max="70" value="50"/);
  assert.match(html, /name="kcal" type="number" min="1000" max="4500" value="2100"/);
  assert.match(html, /name="max_repeat" type="number" min="1" max="7" value="5"/);
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

test('community: an empty list says nobody has shared yet, never shows sample members', async () => {
  const { context } = fixture(); await context.bootPromise;
  vm.runInContext('S.community = []', context);
  const html = vm.runInContext('communityPanel()', context);
  assert.match(html, /Nobody has shared a week here so far/);
  assert.doesNotMatch(html, /Sample weeks/);
});
