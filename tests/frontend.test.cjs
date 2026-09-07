// Test browser-independent UI state without a browser dependency or CDN.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('smartplate/static/app.js', 'utf8').replace(
  'boot().catch', 'globalThis.bootPromise = boot().catch');

function fixture() {
  const calls = [];
  const element = { innerHTML: '', classList: { toggle() {} }, appendChild() {}, remove() {} };
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
