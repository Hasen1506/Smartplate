// W3 frontend fixes (Back button, offline copy, Saved empty state, closed places, onboarding
// budget, recovery code, plain words, focus, Tamil). Browser-free and deterministic: a fake
// history/window, no network, no real clock dependence. Run: node --test tests/frontend_w03.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('smartplate/static/app.js', 'utf8').replace(
  'boot().catch', 'globalThis.bootPromise = boot().catch');
const swSource = fs.readFileSync('smartplate/static/sw.js', 'utf8');

function fixture(overrides = {}, { storage = {}, headers = {} } = {}) {
  const calls = [];
  const element = { innerHTML: '', classList: { toggle() {} }, appendChild() {}, remove() {}, addEventListener() {}, setAttribute() {} };
  const view = {
    plan: { id: 42, mode: 'balanced', mode_label: 'Balanced', week_start: '2026-11-02' },
    user: { id: 2, name: 'Meera', city: 'Chennai', diet: 'veg', rating_floor: 4.0, weekly_budget: 1500, allergens: [], medical: [], health_targets: {} },
    budget: { spend: 1000, budget: 1500 }, counts: { delivery: 1, cook: 0, skip: 0 }, summary_reasons: [], week_context: [], grid: [],
    nutrition: { daily_target: { kcal: 1800 }, daily_avg: {} },
  };
  const replies = {
    '/api/meta': { modes: { balanced: 'Balanced' }, mode_outcomes: {}, version: '1', swiggy_provider: 'live' },
    '/api/users': [{ id: 2, name: 'Meera' }],
    '/api/user/2/plan': view,
    '/api/plan/42/orders': { attempted: 0, placed: 0, results: [] },
    '/api/user/2/swiggy': { connected: false },
  };
  Object.assign(replies, overrides);
  const store = { 'smartplate.user': '2', ...storage };
  // A history stack: pushState adds, go(n) moves and fires popstate (async, like a browser).
  const hist = { stack: [null], at: 0, pushes: 0 };
  const listeners = {};
  const win = {
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    scrollTo() {},
  };
  const history = {
    pushState(st) { hist.stack.splice(hist.at + 1); hist.stack.push(st); hist.at += 1; hist.pushes += 1; },
    replaceState() {},
    go(n) { hist.at = Math.max(0, Math.min(hist.stack.length - 1, hist.at + n)); setImmediate(() => (listeners.popstate || []).forEach(f => f({ state: hist.stack[hist.at] }))); },
    get length() { return hist.stack.length; },
  };
  const back = () => history.go(-1);
  const context = vm.createContext({
    document: { getElementById: () => element, querySelectorAll: () => [], querySelector: () => null,
      createElement: () => element, addEventListener() {}, body: element },
    window: win, history,
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch: async (url, options) => { calls.push({ url, options });
      const body = replies[url];
      return { ok: !!body && !body.__status, status: body ? (body.__status || 200) : 404,
        headers: { get: (h) => (headers[url] || {})[h] || null },
        json: async () => { if (body && body.__html) throw new SyntaxError('Unexpected token <'); return body || { error: 'Unexpected route' }; } }; },
    setTimeout: (fn) => 0, clearTimeout() {}, console, Date, setImmediate,
  });
  vm.runInContext(source, context);
  return { context, calls, element, hist, back, store };
}
const run = (ctx, code) => vm.runInContext(code, ctx);
const tick = () => new Promise(r => setImmediate(() => setImmediate(r)));

/* 1. Back button ------------------------------------------------------------------------- */
test('Back closes the open sheet first, then leaves the tab, then has nothing left of ours', async () => {
  const { context, hist, back } = fixture(); await context.bootPromise;
  assert.equal(run(context, 'NAV.depth'), 0);
  run(context, 'S.tab = "week"; render()');
  assert.equal(hist.at, 1, 'a tab other than Today is one Back away');
  run(context, 'S.budgetSheet = { value: 1500 }; render()');
  assert.equal(hist.at, 2, 'an open sheet adds one more');
  back(); await tick();
  assert.equal(run(context, 'S.budgetSheet'), null, 'Back closed the sheet');
  assert.equal(run(context, 'S.tab'), 'week', '…and stayed on Week');
  back(); await tick();
  assert.equal(run(context, 'S.tab'), 'today', 'the next Back returns to Today');
  assert.equal(hist.at, 0, 'no Ziggy entries left: the next Back leaves the app');
});

test('closing a sheet with its own X pops the entry it pushed (no dead Back press)', async () => {
  const { context, hist } = fixture(); await context.bootPromise;
  run(context, 'S.connectSheet = true; render()');
  assert.equal(hist.at, 1);
  run(context, 'S.connectSheet = false; render()');       // the X / Not now button does exactly this
  await tick();
  assert.equal(hist.at, 0);
  assert.equal(run(context, 'NAV.skip'), 0, 'our own history.go() was swallowed, not treated as Back');
  assert.equal(run(context, 'S.tab'), 'today');
});

test('Back on a Me sub-page goes to Me; on a Week sub-page goes to Week', async () => {
  const { context, back } = fixture(); await context.bootPromise;
  run(context, 'S.tab = "more"; S.more = "settings"; render()');
  assert.equal(run(context, 'NAV.depth'), 2);
  back(); await tick(); await tick();
  assert.equal(run(context, 'S.tab'), 'more');
  assert.equal(run(context, 'S.more'), null);
  run(context, 'S.tab = "week"; S.weekView = "orders"; render()');
  back(); await tick();
  assert.equal(run(context, 'S.weekView'), null);
  assert.equal(run(context, 'S.tab'), 'week');
});

test('Back during onboarding goes to the previous step, and from step 1 to the welcome screen', async () => {
  const { context, back } = fixture({}, { storage: { 'smartplate.user': '' } }); await context.bootPromise;
  run(context, 'startOnboard(); S.onboard.d.diet = "veg"; S.onboard.step = 2; render()');
  assert.equal(run(context, 'NAV.depth'), 3);
  back(); await tick(); await tick();
  assert.equal(run(context, 'S.onboard.step'), 1);
  back(); await tick(); await tick();
  back(); await tick(); await tick();
  assert.equal(run(context, 'S.onboard'), null);
  assert.equal(run(context, 'S.welcome'), true);
});

test('Escape and Back share one close order (dish over Change sheet)', async () => {
  const { context } = fixture(); await context.bootPromise;
  run(context, 'S.sheet = { sid: 1 }; S.dish = { key: "x" }; closeTopDialog = closeTopDialog;');
  run(context, 'S.render = render; render = () => {}; closeTopDialog(); render = S.render');
  assert.equal(run(context, 'S.dish'), null);
  assert.ok(run(context, 'S.sheet'), 'the sheet under the dish stays open');
});

/* 2. Offline copy ------------------------------------------------------------------------ */
test('a plan served from the service worker copy shows the offline banner with its age', async () => {
  const at = '2020-11-02T07:35:00.000Z';
  const { context } = fixture({}, { headers: { '/api/user/2/plan': { 'X-Ziggy-Cached-At': at } } });
  await context.bootPromise;
  assert.equal(run(context, 'S.offline'), true);
  assert.equal(run(context, 'S.stale'), true);
  assert.equal(run(context, 'S.loadedAt.toISOString()'), at);
  assert.match(run(context, 'offlineBar()'), /You're offline\. Showing your plan as loaded at 2 Nov, /);
});

test('a live plan is not marked offline or stale', async () => {
  const { context } = fixture(); await context.bootPromise;
  assert.equal(run(context, 'S.offline'), false);
  assert.equal(run(context, 'S.stale'), false);
});

test('the service worker caches only read-only plan answers, never Swiggy carts or writes', () => {
  const ctx = vm.createContext({ self: { addEventListener() {}, location: { origin: 'https://z' } }, caches: {}, URL, Headers: class {}, Response: class {} });
  vm.runInContext(swSource, ctx);
  const ok = (p) => vm.runInContext(`cacheableApi(new URL(${JSON.stringify('https://z' + p)}))`, ctx);
  for (const p of ['/api/meta', '/api/users', '/api/user/2/plan', '/api/plan/42', '/api/plan/42/orders', '/api/user/2/swiggy', '/api/user/2/saved'])
    assert.equal(ok(p), true, p);
  for (const p of ['/api/user/2/swiggy/live-cart', '/api/user/2/swiggy/live-menu?restaurant_id=1', '/api/user/2/account',
    '/api/user/2/data.json', '/api/plan/42/recap', '/api/user/2/plan?x=1', '/api/receipts/2'])
    assert.equal(ok(p), false, p);
  assert.match(swSource, /X-Ziggy-Cached-At/);
});

test('signing out drops the offline plan copy', async () => {
  const { context } = fixture(); await context.bootPromise;
  run(context, 'globalThis.deleted = []; globalThis.caches = { delete: async (n) => { deleted.push(n); return true; } }');
  run(context, 'clearCurrentProfile()');
  assert.deepEqual(JSON.parse(run(context, 'JSON.stringify(deleted)')), ['ziggy-api-v1']);
});

test('a host wake-up page (HTML, or a bare 502) says "waking up", not "offline" or a crash', async () => {
  const { context, element } = fixture({ '/api/meta': { __html: true } });
  await context.bootPromise;
  assert.match(element.innerHTML, /Ziggy is waking up/);
  assert.doesNotMatch(element.innerHTML, /You're offline|Failed to start/);
  const two = fixture({ '/api/meta': { __status: 502 } });
  await two.context.bootPromise;
  assert.match(two.element.innerHTML, /Ziggy is waking up/);
});

/* 3. Saved: one empty state -------------------------------------------------------------- */
test('Saved never shows two different "nothing saved" messages', async () => {
  const { context } = fixture(); await context.bootPromise;
  run(context, `S.swiggy = { connected: true, address: { id: 'a1', label: 'Home' } }; S.liveFavourites = [];
    S.pools = { address: 'Home', meals: { breakfast: [], lunch: [], dinner: [] }, places: [{ id: 'r2', name: 'Green Bowl', saved: false }] };`);
  const meals = run(context, 'S.savedTab = "meals"; savedScreen()');
  const places = run(context, 'S.savedTab = "places"; savedScreen()');
  assert.doesNotMatch(meals, /haven't saved any places/);
  assert.match(meals, /Places Ziggy found near you\./);
  assert.match(places, /You haven't saved any places yet\. Tap ♥ on a place you like and Ziggy plans from it\./);
  assert.doesNotMatch(places + meals, /Nothing saved yet/);
});

/* 4. Closed places ----------------------------------------------------------------------- */
test('closed places are greyed, last, say when they open, and have no Menu button', async () => {
  const { context } = fixture(); await context.bootPromise;
  run(context, `S.swiggy = { connected: true, address: { id: 'a1', label: 'Home' } };
    S.liveResults = { browse: true, address: 'Home', restaurants: [
      { id: 'r6', name: 'Night Owl Parotta', availability: 'CLOSED', rating: 4.0, eta: 30 },
      { id: 'r7', name: 'Late Dosa', availability: 'CLOSED', opens_at: '18:00' },
      { id: 'r1', name: 'Annapoorna', availability: 'OPEN', rating: 4.4, eta: 25 }] };`);
  const html = run(context, 'liveResultsBlock()');
  assert.ok(html.indexOf('Annapoorna') < html.indexOf('Night Owl'), 'open places first');
  assert.match(html, /mrow closed[^]*Night Owl Parotta <span class="tag">Closed<\/span>[^]*Closed now/);
  assert.match(html, /Opens at 18:00/);
  assert.doesNotMatch(html, /data-live-place="r6"/);
  assert.match(html, /data-live-place="r1"/);
});

test('opening a place that turns out closed is information, not a failure with Retry', async () => {
  const { context } = fixture({ '/api/user/2/swiggy/live-menu?restaurant_id=r6&restaurant_name=Night%20Owl': {
    __status: 409, error: 'This restaurant is closed right now. Search again later.', code: 'restaurant_closed' } });
  await context.bootPromise;
  run(context, 'S.swiggy = { connected: true, address: { id: "a1", label: "Home" } }');
  await run(context, 'guard(() => openLivePlace("r6", "Night Owl"), "Open the menu for Night Owl")');
  assert.equal(run(context, 'S.error'), null, 'no error banner');
  assert.equal(run(context, 'S.retry'), null, 'no Retry');
  assert.equal(run(context, 'S.closedPlaces.has("r6")'), true);
  assert.equal(run(context, 'S.liveLoading'), null);
  assert.match(run(context, 'placeRow({ id: "r6", name: "Night Owl" }, { starred: false, live: true })'), /Closed now/);
});

/* 5. Onboarding -------------------------------------------------------------------------- */
test('onboarding: no diet is preselected and Continue needs a choice', async () => {
  const { context } = fixture(); await context.bootPromise;
  run(context, 'startOnboard()');
  assert.equal(run(context, 'S.onboard.d.diet'), null);
  const html = run(context, 'onboardingScreen()');
  assert.doesNotMatch(html, /data-ob="diet" data-val="[a-z]+" data-multi="0" aria-pressed="true"/);
  assert.match(html, /data-ob-nav="next" disabled/);
  await assert.rejects(run(context, 'onboardNav("next")'), /Choose what you eat/);
  run(context, 'onboardChip("diet", "veg", false)');
  assert.doesNotMatch(run(context, 'onboardingScreen()'), /data-ob-nav="next" disabled/);
});

test('onboarding budget: typing updates the per-meal line, the chip, and warns below ₹60 a meal', async () => {
  const { context } = fixture(); await context.bootPromise;
  run(context, 'startOnboard(); S.onboard.step = 2; S.onboard.d.rhythm = { breakfast: "order", lunch: "order", dinner: "order" }');
  // a tiny DOM for the three live regions
  run(context, `globalThis.els = { 'ob-per': { textContent: '' }, 'ob-floor': { innerHTML: '', querySelectorAll: () => [] } };
    globalThis.chips = [1500, 3000, 5000].map(v => ({ dataset: { val: String(v) }, closest: () => null, attrs: {}, setAttribute(k, x) { this.attrs[k] = x; } }));
    document.getElementById = (id) => els[id] || null;
    document.querySelectorAll = (sel) => sel.includes('budgetpick') ? chips : [];`);
  run(context, 'S.onboard.d.weekly_budget = 3000; obBudgetInput({ value: "300" })');
  assert.equal(run(context, 'S.onboard.d.weekly_budget'), 300);
  assert.equal(run(context, 'els["ob-per"].textContent'), 'about ₹14 a meal for 21 meals');
  assert.equal(run(context, 'chips[1].attrs["aria-pressed"]'), 'false', 'the ₹3,000 chip lets go');
  const warn = run(context, 'els["ob-floor"].innerHTML');
  assert.match(warn, /₹300 is ₹14 a meal/);
  assert.match(warn, /covers about 5 of your 21 meals/);
  assert.match(warn, /data-val="1500"[^>]*>Use ₹1,500</);        // 60 × 21 = ₹1,260 → up to the next ₹250 step
  run(context, 'obBudgetInput({ value: "1500" })');
  assert.equal(run(context, 'els["ob-floor"].innerHTML'), '');
  assert.equal(run(context, 'chips[0].attrs["aria-pressed"]'), 'true');
});

/* 6. Account wording --------------------------------------------------------------------- */
test('recovery code is hidden until asked, with Copy and Download; the button says Sign out', async () => {
  const { context } = fixture({}, { storage: { 'smartplate.keys': JSON.stringify({ 2: { key: 'abcdefghijklmnopqrst', name: 'Meera' } }) } });
  await context.bootPromise;
  run(context, 'S.account = { login: null, devices: [{ id: 1, label: "Phone", last_seen: "2026-11-02T08:00", this_device: false }] }');
  // W8: the browser no longer keeps the key; the code exists only right after it was made or rotated.
  assert.doesNotMatch(run(context, 'profilesPanel()'), /data-act="copy-recovery"/);
  run(context, 'S.freshCode = "2.abcdefghijklmnopqrst"');
  const html = run(context, 'profilesPanel()');
  assert.match(html, /<details class="recovery"><summary>Show recovery code<\/summary><code class="code">2\.abcdefghijklmnopqrst<\/code><\/details>/);
  assert.match(html, /data-act="copy-recovery">Copy code/);
  assert.match(html, /data-act="download-recovery">Download as a file/);
  assert.match(html, /data-act="sign-out">Sign out of this device/);
  assert.doesNotMatch(html, /Forget this profile/);
});

/* 7. Plain words ------------------------------------------------------------------------- */
test('no "pool" jargon in what people read', async () => {
  const { context } = fixture(); await context.bootPromise;
  run(context, `S.swiggy = { connected: true, address: { id: 'a1', label: 'Home' } };
    S.pools = { address: 'Home', meals: { breakfast: [], lunch: [], dinner: [] }, places: [] };`);
  const board = run(context, 'poolsBoard()').replace(/(data-[a-z-]+|class|id|aria-labelledby)="[^"]*"/g, '');
  assert.doesNotMatch(board, /\bpools?\b/i);
  assert.match(board, /aria-label="Usual lunches"/);
  assert.match(run(context, 'whyChips({ pooled: true, meal: "lunch" })'), /<li class="ok">From your usual lunches<\/li>/);
});

/* 8. Focus and Tamil --------------------------------------------------------------------- */
test('a newly opened sheet takes focus; its opener is remembered by a data attribute', async () => {
  const { context } = fixture(); await context.bootPromise;
  const key = run(context, 'openerKey({ attributes: [{ name: "class", value: "x" }, { name: "data-act", value: "budget" }] })');
  assert.equal(key, '[data-act="budget"]');
  run(context, `globalThis.focused = null;
    globalThis.dlg = { attrs: {}, contains: () => false, querySelector: () => null, hasAttribute(k) { return k in this.attrs; },
      setAttribute(k, v) { this.attrs[k] = v; }, focus() { focused = 'dialog'; } };
    focusSheet({ querySelector: () => dlg });`);
  assert.equal(run(context, 'focused'), 'dialog');
  assert.equal(run(context, 'dlg.attrs.tabindex'), '-1');
});

test('Tamil: the switch translates Today, Week and onboarding strings; English stays the default', async () => {
  const { context, store } = fixture(); await context.bootPromise;
  assert.equal(run(context, 'lang()'), 'en');
  assert.match(run(context, 'navBar()'), /<span>Today<\/span>/);
  run(context, 'setLang("ta")');
  assert.equal(store['ziggy.lang'], 'ta');
  assert.match(run(context, 'navBar()'), /<span>இன்று<\/span>[^]*<span>வாரம்<\/span>/);
  assert.match(run(context, 'todayScreen()'), /இந்த வாரம் ₹500 மீதம்/);
  run(context, 'startOnboard()');
  const ob = run(context, 'onboardingScreen()');
  assert.match(ob, /நீங்கள் என்ன சாப்பிடுவீர்கள்\?/);
  assert.match(ob, /சைவம்/);
  assert.equal(run(context, 't("A string nobody translated")'), 'A string nobody translated');
  assert.equal(run(context, 't("{x} left", { x: "₹5" })'), '₹5 மீதம்');
  run(context, 'S.onboard = null; setLang("xx")');
  assert.equal(run(context, 'lang()'), 'en');
});
