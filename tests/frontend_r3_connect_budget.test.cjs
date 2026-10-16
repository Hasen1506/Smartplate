// Fix round 3 "connect-budget": the Connect Swiggy sheet (in-sheet consent, visible errors,
// never a native confirm), one budget model (Day/Week, eaters, relative presets, visible
// Undo), the recovery code after onboarding, weather credit, landing copy and theme name.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const v3 = fs.readFileSync('smartplate/static/v3/v3.js', 'utf8');
const app = fs.readFileSync('smartplate/static/app.js', 'utf8').replace('boot().catch', 'globalThis.bootPromise = boot().catch');

const TODAY = {
  date: '2026-10-16', weekday: 'Friday', meals: ['lunch', 'dinner'], label: 'L+D',
  budget: { amount: 3000, source: 'week', default: 3000, basis: 'Your weekly ₹21,000 ÷ 7', presets: [2250, 3000, 3750], picked: 0, left: 3000, unpriced: [] },
  weather: { known: false },
  rules: { diet: 'nonveg', allergens: [], medical: [], min_rating: 4, max_eta: 30, nutrition: false },
  pools: { breakfast: [], lunch: [], dinner: [] },
  picks: {}, modes: { favourites: 'From my favourites only' },
  price_compare: null, swiggy: { connected: false, address: null }, account_saved: false, share_text: 'Today',
};

function world({ replies: extra = {}, today = TODAY, meta = {} } = {}) {
  const calls = [];
  const element = { innerHTML: '', classList: { toggle() {}, add() {} }, appendChild() {}, remove() {}, addEventListener() {}, setAttribute() {} };
  const store = { 'smartplate.user': '2' };
  const view = {
    plan: { id: 42, mode: 'balanced', mode_label: 'Balanced', week_start: '2026-10-12' },
    user: { id: 2, name: 'Me', diet: 'nonveg', weekly_budget: 21000, allergens: [], medical: [], health_targets: {} },
    budget: { spend: 0, budget: 9000, weekly_budget: 21000, prorated: true, covers: 'Fri–Sun', daily: 3000, days_covered: 3 },
    counts: {}, grid: [], nutrition: { daily_target: { kcal: 1800 }, daily_avg: { kcal: 900 } },
  };
  const replies = {
    '/api/meta': { modes: {}, mode_outcomes: {}, version: '1.1.0', swiggy_provider: 'live', fixture_data: false, swiggy_redirect_approved: true, ...meta },
    '/api/users': [{ id: 2, name: 'Me' }],
    '/api/user/2/plan': view, '/api/plan/42/orders': { results: [] }, '/api/user/2/swiggy': { connected: false },
    '/api/user/2/today': today,
    '/api/privacy/notice': { purposes: { swiggy: { title: 'Connect Swiggy', data: ['a Swiggy access token (encrypted)', 'your saved delivery addresses'], why: 'To show real menus and prices.', without: 'Ziggy plans without live Swiggy menus.' } } },
    ...extra,
  };
  const loc = { search: '', href: 'https://app.example/', origin: 'https://app.example' };
  const context = vm.createContext({
    document: { getElementById: () => element, querySelectorAll: () => [], querySelector: () => null,
      createElement: () => element, addEventListener() {}, body: element },
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    location: loc, URLSearchParams, history: { replaceState() {} },
    // A native confirm() must never be used for the Swiggy notice again.
    confirm: () => { throw new Error('native confirm() used'); },
    fetch: async (url, options) => { calls.push({ url, options });
      const r = typeof replies[url] === 'function' ? replies[url](options) : replies[url];
      return { ok: !!r && !r.__status, status: r ? (r.__status || 200) : 404, headers: { get: () => null },
        json: async () => r || { error: 'Unexpected route' } }; },
    setTimeout: () => {}, clearTimeout: () => {}, console,
  });
  vm.runInContext(v3, context);
  vm.runInContext(app, context);
  return { context, calls, element, store, loc, replies };
}
const run = (w, code) => vm.runInContext(code, w.context);

test('Connect Swiggy: the notice is a tick inside the sheet; Continue waits for it; no native confirm', async () => {
  const w = world(); await w.context.bootPromise;
  run(w, 'S.connectSheet = true; S.swiggy = { connected: false }');
  let html = run(w, 'connectSheet()');
  assert.match(html, /id="connect-consent"/);
  assert.match(html, /data-act="swiggy-connect" disabled>Continue with Swiggy/);
  assert.match(html, /href="\/privacy"/);
  await run(w, 'connectSwiggy()');                                   // not ticked: nothing sent, said in the sheet
  assert.equal(w.calls.filter(c => c.url.endsWith('/swiggy/connect')).length, 0);
  assert.match(run(w, 'connectSheet()'), /role="alert" id="connect-error">Tick the line above/);
  run(w, 'S.connectConsent = true');
  html = run(w, 'connectSheet()');
  assert.match(html, /data-act="swiggy-connect">Continue with Swiggy/);
  assert.match(html, /id="connect-consent" checked/);
});

test('Connect Swiggy: a server refusal shows inside the sheet instead of doing nothing', async () => {
  const w = world({ replies: { '/api/user/2/swiggy/connect': { __status: 503, error: "Swiggy hasn't approved Ziggy's sign-in address yet", code: 'swiggy_redirect_not_whitelisted' } } });
  await w.context.bootPromise;
  run(w, 'S.connectSheet = true; S.swiggy = { connected: false }; S.connectConsent = true');
  await run(w, 'connectSwiggy()');
  const post = w.calls.find(c => c.url === '/api/user/2/swiggy/connect');
  assert.equal(post.options.method, 'POST');
  assert.deepEqual(JSON.parse(post.options.body), { consent: true });
  assert.match(run(w, 'connectSheet()'), /id="connect-error">Swiggy hasn&#39;t approved Ziggy&#39;s sign-in address yet|id="connect-error">Swiggy hasn't approved/);
  assert.equal(w.loc.href, 'https://app.example/');                   // stayed: nothing to go to
  assert.equal(run(w, 'S.connectSheet'), true);
});

test('Connect Swiggy: consent given and the server answers → goes to Swiggy sign-in', async () => {
  const w = world({ replies: { '/api/user/2/swiggy/connect': { authorize_url: 'https://mcp.swiggy.com/auth/authorize?x=1' } } });
  await w.context.bootPromise;
  run(w, 'S.connectSheet = true; S.connectConsent = true');
  await run(w, 'connectSwiggy()');
  assert.equal(w.loc.href, 'https://mcp.swiggy.com/auth/authorize?x=1');
});

test('onboarding #15: a day or a week, how many eat, presets that scale; the weekly budget is one number', async () => {
  const created = { plan: { id: 42, week_start: '2026-10-12' }, user: { id: 2, name: 'Me' }, access_key: 'k', recovery_code: '2.secret-code', budget: {} };
  const w = world({ replies: { '/api/profiles': created } });
  await w.context.bootPromise;
  run(w, 'ZiggyV3.startOnboard(); S.v3ob.step = 1; S.v3ob.diet = "veg"');
  let html = run(w, 'ZiggyV3.screen()');
  assert.match(html, /Your food budget/);
  assert.match(html, /aria-label="How many eat"/);
  assert.match(html, /data-v3-ob="eaters:4"[^>]*>4\+/);
  assert.match(html, /data-v3-ob="per:week"/);
  assert.match(html, /data-v3-ob="preset:250"/);                       // 1 eater, a day
  await run(w, 'ZiggyV3._test.obTap("eaters:2")');
  assert.match(run(w, 'ZiggyV3.screen()'), /data-v3-ob="preset:1200"/);   // 600 × 2
  await run(w, 'ZiggyV3._test.obTap("preset:800")');
  w.element.value = '800';                                             // the input shows the preset
  await run(w, 'ZiggyV3._test.obTap("per:week")');
  w.element.value = '5600';                     // the amount converts, not resets
  assert.equal(run(w, 'S.v3ob.budget'), 5600);
  html = run(w, 'ZiggyV3.screen()');
  assert.match(html, /About ₹800 a day/);
  assert.match(html, /data-v3-ob="preset:5500"/);                      // 400 × 2 × 7 ≈ 5,500
  run(w, 'S.v3ob.meals = ["lunch"]; S.v3ob.consent = true');
  await run(w, 'ZiggyV3._test.finishOb()');
  const body = JSON.parse(w.calls.find(c => c.url === '/api/profiles').options.body);
  assert.equal(body.weekly_budget, 5600);                              // a week typed as a week
  assert.equal(body.eaters, 2);
  assert.equal(w.calls.filter(c => c.url.endsWith('/today/budget')).length, 0);   // today follows the week
});

test('onboarding: a day budget becomes 7× in the weekly budget', async () => {
  const created = { plan: { id: 42, week_start: '2026-10-12' }, user: { id: 2, name: 'Me' }, access_key: 'k', recovery_code: '2.secret-code', budget: {} };
  const w = world({ replies: { '/api/profiles': created } });
  await w.context.bootPromise;
  run(w, 'ZiggyV3.startOnboard(); S.v3ob.step = 1; S.v3ob.diet = "veg"; S.v3ob.budget = 3000; S.v3ob.meals = ["lunch", "dinner"]; S.v3ob.consent = true');
  await run(w, 'ZiggyV3._test.finishOb()');
  const body = JSON.parse(w.calls.find(c => c.url === '/api/profiles').options.body);
  assert.equal(body.weekly_budget, 21000);
  assert.equal(body.eaters, 1);
});

test('#12: the recovery code is shown on the first Today after onboarding, until "I\'ve saved it"', async () => {
  const w = world(); await w.context.bootPromise;
  await run(w, 'ZiggyV3._test.load()');
  run(w, 'S.freshCode = "2.secret-code"');
  let html = run(w, 'tabBody()');
  assert.match(html, /Save your recovery code/);
  assert.match(html, /2\.secret-code/);
  assert.match(html, /data-act="copy-recovery"/);
  assert.match(html, /data-act="download-recovery"/);
  run(w, 'S.codeAcked = S.userId');
  html = run(w, 'tabBody()');
  assert.doesNotMatch(html, /Save your recovery code/);
  run(w, 'S.codeAcked = null; S.freshCode = "9.other-profile"');      // never another profile's code
  assert.doesNotMatch(run(w, 'tabBody()'), /Save your recovery code/);
});

test('budget: Change budget opens on the same weekly number, ₹300 stays ₹300 a week, and Undo stays visible', async () => {
  let patched = [];
  const after = (wb) => ({ plan: { id: 42, mode: 'balanced', mode_label: 'Balanced', week_start: '2026-10-12' },
    user: { id: 2, weekly_budget: wb }, grid: [], counts: {},
    budget: { spend: 0, budget: Math.round(wb * 3 / 7 * 100) / 100, weekly_budget: wb, prorated: true, covers: 'Fri–Sun' } });
  const w = world({ replies: { '/api/user/2/setup': (o) => { const b = JSON.parse(o.body); patched.push(b.weekly_budget); return after(b.weekly_budget); } } });
  await w.context.bootPromise;
  run(w, 'openBudget()');
  assert.equal(run(w, 'S.budgetSheet.value'), 21000);
  run(w, 'S.budgetSheet.per = "day"');
  let sheet = run(w, 'budgetSheet()');
  assert.match(sheet, /id="budget-in"[^>]*value="3000"/);              // the ₹3,000 a day from onboarding
  assert.match(sheet, /Now ₹3,000 a day · ₹21,000 a week/);
  run(w, 'S.budgetSheet = { value: 300, per: "week" }');
  sheet = run(w, 'budgetSheet()');
  assert.match(sheet, /₹43 a day · ₹300 a week · ₹129 for Fri–Sun/);
  await run(w, 'saveBudget()');
  assert.deepEqual(patched, [300]);
  assert.equal(run(w, 'S.view.budget.weekly_budget'), 300);
  const week = run(w, 'weekScreen()');
  assert.match(week, /planned of ₹129 for Fri–Sun/);
  assert.match(week, /Fri–Sun only, from ₹300 a week/);
  assert.match(week, /Budget changed to ₹300 a week \(was ₹21,000\)\. <button class="link" data-act="budget-undo">Undo/);
  await run(w, 'undoBudget()');
  assert.deepEqual(patched, [300, 21000]);
  assert.doesNotMatch(run(w, 'weekScreen()'), /budget-undo/);
});

test('weather: no tile without a live forecast; a live one says where and whose', async () => {
  const w = world({ today: { ...TODAY, weather: { known: true, condition: 'rain', temp_c: 30, rain_prob: 90, source: 'sample' } } });
  await w.context.bootPromise; await run(w, 'ZiggyV3._test.load()');
  let html = run(w, 'tabBody()');
  assert.doesNotMatch(html, /Weather today|sample data/);
  assert.match(html, /class="v3-top solo"/);
  const w2 = world({ today: { ...TODAY, weather: { known: true, condition: 'rain', temp_c: 30, rain_prob: 90, source: 'live', place: 'Chennai', attribution: 'Open-Meteo', attribution_url: 'https://open-meteo.com/' } } });
  await w2.context.bootPromise; await run(w2, 'ZiggyV3._test.load()');
  html = run(w2, 'tabBody()');
  assert.match(html, /For Chennai · <a href="https:\/\/open-meteo.com\/"[^>]*>Open-Meteo<\/a>/);
});

test('#36/#24/#37: theme is "Saffron", hero no longer says "already decided"', async () => {
  const w = world(); await w.context.bootPromise;
  assert.equal(run(w, 'PALETTES.map(p => p[1]).join()'), 'Ember,Saffron');
  const src = fs.readFileSync('smartplate/static/app.js', 'utf8') + fs.readFileSync('smartplate/static/index.html', 'utf8')
    + fs.readFileSync('smartplate/static/manifest.webmanifest', 'utf8');
  assert.doesNotMatch(src, /already decided|Zomato × Swiggy|Swiggy approves connecting/);
  const css = fs.readFileSync('smartplate/static/v3/v3.css', 'utf8');
  assert.match(css, /@media \(max-width:480px\)\{\.v3-top\{grid-template-columns:minmax\(0,1fr\)\}\}/);
});
