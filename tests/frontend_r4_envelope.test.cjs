// Fix round 4: the week's envelope in the v3 UI. Week strip day chips (₹ planned + a tick at the
// fair share), one slider on tap (min = spent + picked, a guide line at the share, a live
// rest-of-week preview), Plan/Off, Today's card opening the same control, and the impact of a
// pick shown with its options — never a block.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const v3 = fs.readFileSync('smartplate/static/v3/v3.js', 'utf8');
const app = fs.readFileSync('smartplate/static/app.js', 'utf8').replace('boot().catch', 'globalThis.bootPromise = boot().catch');

const day = (i, extra = {}) => ({
  date: `2026-11-0${2 + i}`, weekday: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][i], state: 'plan',
  target: 286, target_exact: 285.71, guide: 286, guide_exact: 285.71, reserve: 0, flex: 285.71, spent: 0, committed: 0,
  set: null, mode: null, planned: 250, flags: [], off_meals: [], eaters: null, is_today: i === 0, min: 0, ...extra });
const ENV = {
  envelope: 2000, spent: 0, committed: 350, flexible: 1650, reserve: 0, unallocated: 0, over: 0, is_over: false,
  week_start: '2026-11-02', override: null, kept: null, low: null, settings: { scope: 'all', carry: false },
  days: [day(0, { target: 350, target_exact: 350, committed: 350, min: 350, planned: 350 }),
    ...[1, 2, 3, 4, 5, 6].map(i => day(i, { target: 275, target_exact: 275 }))],
};
const TODAY = {
  date: '2026-11-02', weekday: 'Monday', meals: ['lunch', 'dinner'], label: 'L+D',
  budget: { amount: 350, guide: 286, default: 286, source: 'week', state: 'plan', date: '2026-11-02', presets: [200, 300, 350], picked: 350, picked_dishes: 350, left: 0, unpriced: [] },
  weather: { known: false }, rules: { diet: 'nonveg', allergens: [], medical: [], min_rating: 0, max_eta: 45, nutrition: false },
  pools: { breakfast: [], lunch: [], dinner: [] }, picks: {}, modes: {}, price_compare: null,
  swiggy: { connected: false, address: null }, account_saved: true, share_text: 'Today',
};

function world(extra = {}) {
  const calls = [];
  const element = { innerHTML: '', classList: { toggle() {}, add() {} }, appendChild() {}, remove() {}, addEventListener() {}, setAttribute() {} };
  const store = { 'smartplate.user': '2' };
  const view = {
    plan: { id: 42, mode: 'balanced', mode_label: 'Balanced', week_start: '2026-11-02' },
    user: { id: 2, name: 'Vijay', diet: 'nonveg', weekly_budget: 2000, allergens: [], medical: [], health_targets: {} },
    budget: { spend: 1900, budget: 2000, weekly_budget: 2000, envelope: ENV }, counts: {}, grid: [], nutrition: {},
  };
  const replies = {
    '/api/meta': { modes: {}, mode_outcomes: {}, version: '1', swiggy_provider: 'simulated', fixture_data: false },
    '/api/users': [{ id: 2, name: 'Vijay' }], '/api/user/2/plan': view, '/api/plan/42': view,
    '/api/plan/42/orders': { results: [] }, '/api/user/2/swiggy': { connected: false }, '/api/user/2/today': TODAY,
    '/api/user/2/envelope?week=2026-11-04': ENV, '/api/user/2/envelope?week=2026-11-07': ENV,
    '/api/user/2/day-budget': { applied: true, envelope: ENV },
    '/api/user/2/day-plan': { applied: true, envelope: ENV },
    '/api/user/2/week-override': { applied: true, envelope: { ...ENV, envelope: 2065, override: 2065 } },
    ...extra,
  };
  const context = vm.createContext({
    document: { getElementById: () => element, querySelectorAll: () => [], querySelector: () => null,
      createElement: () => element, addEventListener() {}, body: element },
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    location: { search: '', href: 'https://app.example/', origin: 'https://app.example' }, URLSearchParams, history: { replaceState() {} },
    fetch: async (url, options) => { calls.push({ url, options });
      const r = replies[url];
      return { ok: !!r, status: r ? 200 : 404, headers: { get: () => null }, json: async () => r || { error: 'Unexpected route' } }; },
    setTimeout: () => {}, clearTimeout: () => {}, console, Date,
  });
  vm.runInContext(v3, context);
  vm.runInContext(app, context);
  return { context, calls };
}
const run = (w, code) => vm.runInContext(code, w.context);

test('Week strip: seven day chips with ₹ planned and a fair-share tick; no wall of sliders', async () => {
  const w = world(); await w.context.bootPromise;
  const html = run(w, 'ZiggyV3._test.envStrip()');
  assert.equal((html.match(/data-v3-day="/g) || []).length, 7);
  assert.match(html, /aria-label="Mon: ₹350 planned, share ₹286"/);
  assert.match(html, /<s style="bottom:\d+%"><\/s>/);                     // the guide tick
  assert.doesNotMatch(html, /type="range"/);
  assert.match(html, /₹2,000 this week: ₹0 spent · ₹350 picked · ₹1,650 for Ziggy to fill/);
  assert.match(html, /a guide, not a limit/);
});

test('Tap a day: one slider, min = spent + picked, guide line at the share, live rest-of-week preview', async () => {
  const w = world(); await w.context.bootPromise;
  await run(w, 'ZiggyV3._test.openDay("2026-11-04")');
  const html = run(w, 'ZiggyV3._test.daySheet()');
  assert.match(html, /Wednesday's budget/);
  assert.match(html, /type="range" min="0" max="\d+" step="10" value="275"/);
  assert.match(html, /class="v3-guide" style="left:/);
  assert.match(html, /data-v3-dayplan="off"/);
  const e = run(w, 'S.v3.env'), d = e.days[2];
  // Wednesday 275 → 400: the other five flexible days share ₹1,375 - 125 = ₹1,250
  assert.equal(run(w, `ZiggyV3._test.restPreview(S.v3.env, S.v3.env.days[2], 400)`), 'Rest of week: ₹1,250 over 5 days ≈ ₹250/day');
  assert.match(run(w, `ZiggyV3._test.restPreview(S.v3.env, S.v3.env.days[2], 3000)`), /over this week's ₹2,000/);
  assert.ok(d);
});

test('Save sends the day amount with set_at (last write wins) and Off turns the day off', async () => {
  const w = world(); await w.context.bootPromise;
  await run(w, 'ZiggyV3._test.openDay("2026-11-04")');
  await run(w, 'ZiggyV3._test.saveDay(400)');
  const put = w.calls.find(c => c.url === '/api/user/2/day-budget');
  const body = JSON.parse(put.options.body);
  assert.equal(put.options.method, 'PUT');
  assert.equal(body.date, '2026-11-04'); assert.equal(body.amount, 400); assert.equal(body.mode, 'soft');
  assert.ok(body.set_at);
  await run(w, 'ZiggyV3._test.openDay("2026-11-07")');
  await run(w, 'ZiggyV3._test.setDayPlan("off")');
  const off = JSON.parse(w.calls.find(c => c.url === '/api/user/2/day-plan').options.body);
  assert.equal(off.state, 'off'); assert.equal(off.date, '2026-11-07');
});

test("Today's card opens the same day control and shows the fair share", async () => {
  const w = world(); await w.context.bootPromise;
  await run(w, 'ZiggyV3._test.load()');
  const html = run(w, 'ZiggyV3._test.today()');
  assert.match(html, /data-v3-day="2026-11-02">Other…/);
  assert.match(html, /Fair share today ₹286/);
});

test('A pick over the share shows its impact with options; "Make this week" sends a one-week override', async () => {
  const w = world(); await w.context.bootPromise;
  run(w, `ZiggyV3.impact({ impact: { date: '2026-11-02', text: "₹64 above Monday's share. Rest of week ₹1,650 over 6 days ≈ ₹275/day.",
    default: 'take_from_week', options: [{ act: 'take_from_week', label: 'Take it from the week' }, { act: 'raise_week', label: 'Make this week ₹2,065', amount: 2065 }] } })`);
  const html = run(w, 'ZiggyV3._test.envStrip()');
  assert.match(html, /₹64 above Monday&#39;s share/);
  assert.match(html, /class="small primary" data-v3-impact="take_from_week"/);
  assert.match(html, /data-v3-impact="raise_week" data-v3-amount="2065">Make this week ₹2,065/);
  await run(w, 'ZiggyV3._test.impactAct("raise_week", 2065)');
  const put = w.calls.find(c => c.url === '/api/user/2/week-override');
  assert.deepEqual({ ...JSON.parse(put.options.body), set_at: 1 }, { week_start: '2026-11-02', amount: 2065, set_at: 1 });
  assert.equal(run(w, 'S.v3.impact'), null);
});
