// Ziggy v3: the pure helpers behind Tonight, Week and the account step (no browser needed).
const test = require('node:test');
const assert = require('node:assert/strict');
const v3 = require('../smartplate/static/v3/app.js');

const cell = (o) => ({ kind: 'delivery', status: 'active', cost: 100, item: 'Dish', restaurant: 'Place', session_id: 1, ...o });
const view = (meals, budget) => ({
  budget: { budget: 2500, spend: 0, weekly_budget: 2500, ...budget },
  grid: meals.map((m, i) => ({ day: 'Mon', date: `2026-11-0${i + 2}`, meals: { dinner: m } })),
});

test('week money splits the server envelope by how sure each rupee is', () => {
  const v = view([cell({ status: 'confirmed', real_bill: true, cost: 125 }), cell({ status: 'confirmed', cost: 140 }),
    cell({ real_bill: true, cost: 130 }), cell({ cost: 200 }), cell({ status: 'past', cost: 90 }),
    cell({ status: 'skipped', cost: 150 }), { kind: 'skip', status: 'active', cost: 0 }], { spend: 595 });
  const m = v3.weekMoney(v);
  assert.equal(m.hadReal, 125); assert.equal(m.hadEst, 140); assert.equal(m.cartReal, 130); assert.equal(m.planned, 200);
  assert.equal(m.hadReal + m.hadEst + m.cartReal + m.planned, m.spend);          // nothing double-counted or lost
  assert.equal(m.left, 2500 - 595);
  assert.deepEqual(m.unconfirmed.map(c => c.cost), [90]);                        // a past meal is not money spent
});

test('week money says "over" honestly', () => {
  assert.equal(v3.weekMoney(view([cell({ cost: 3000 })], { spend: 3000 })).left, -500);
});

test('the hero title is tonight for today\'s dinner, plain words otherwise', () => {
  assert.equal(v3.pickTitle({ when: 'Today', meal: 'dinner' }), 'Tonight');
  assert.equal(v3.pickTitle({ when: 'Today', meal: 'lunch' }), 'Lunch today');
  assert.equal(v3.pickTitle({ when: 'Tomorrow', meal: 'dinner' }), 'Tomorrow night');
  assert.equal(v3.pickTitle({ when: 'Wed', meal: 'lunch' }), 'Lunch · Wed');
});

test('alternatives: own list first, one per usual place, never the current dish, fits before over', () => {
  const opts = {
    pool: [{ item_id: 9, name: 'Pooled', fits: true, restaurant: 'P' }],
    usual: [{ restaurant: 'A', dishes: [{ item_id: 1, name: 'Cur', current: true, fits: true }, { item_id: 2, name: 'A2', fits: true }, { item_id: 3, name: 'A3', fits: true }] },
            { restaurant: 'B', dishes: [{ item_id: 4, name: 'B1', fits: false }, { item_id: 5, name: 'B2', fits: true }] }],
    new: [{ item_id: 6, name: 'N', fits: true, restaurant: 'C' }],
  };
  assert.deepEqual(v3.alternatives(opts).map(x => x.name), ['Pooled', 'A2', 'B2']);
  assert.deepEqual(v3.alternatives({ usual: [{ restaurant: 'B', dishes: [{ item_id: 4, name: 'B1', fits: false }] }] }).map(x => x.name), ['B1']);
  assert.deepEqual(v3.alternatives({ failed: true }), []);
});

test('no cart fill when anyone eating has a rule Swiggy menus cannot prove', () => {
  assert.equal(v3.cartEligible({ user: { diet: 'veg', allergens: [] } }, cell()), true);
  assert.equal(v3.cartEligible({ user: { diet: 'veg', allergens: ['peanut'] } }, cell()), false);
  assert.equal(v3.cartEligible({ user: { diet: 'vegan', allergens: [] } }, cell()), false);
  const hh = { user: { diet: 'nonveg' }, household: { people: [{ id: 7, you: false, medical: ['celiac'] }] } };
  assert.equal(v3.cartEligible(hh, cell({ eaters: [7] })), false);
  assert.equal(v3.cartEligible(hh, cell({ eaters: [] })), true);
});

test('setup sends rules and budget only: the account is the last step, not the first', () => {
  const d = { diet: 'veg', allergens: ['egg'], medical: [], weekly_budget: 2500, meals: ['dinner'] };
  assert.equal(v3.setupProblem(d), null);
  const body = v3.setupBody(d);
  assert.deepEqual(body.rhythm, { breakfast: 'skip', lunch: 'skip', dinner: 'order' });
  assert.ok(!('login' in body) && !('email' in body) && !('password' in body));
  assert.match(v3.setupProblem({ ...d, diet: null }), /what you eat/);
  assert.match(v3.setupProblem({ ...d, weekly_budget: 50 }), /between/);
  assert.match(v3.setupProblem({ ...d, meals: [] }), /at least one meal/);
});

test('the account step asks for a real email and a long-enough password', () => {
  assert.equal(v3.accountProblem(' Priya.K@Example.com ', 'dosa-every-night'), null);
  for (const bad of ['priya', 'priya@example', 'a..b@example.com', 'pri ya@example.com', ''])
    assert.match(v3.accountProblem(bad, 'dosa-every-night'), /valid email/, bad);
  assert.match(v3.accountProblem('priya@example.com', 'short'), /8 characters/);
});

test('sign in by email, or by the name an older account was made with', () => {
  assert.deepEqual(v3.signInBody(' Priya@Example.com ', 'pw'), { email: 'Priya@Example.com', password: 'pw' });
  assert.deepEqual(v3.signInBody('priya_k', 'pw'), { login: 'priya_k', password: 'pw' });
});

test('the service worker keeps the v3 page and the classic page apart offline', async () => {
  // Before: every navigation was cached under "/", so opening /v3 replaced the classic app's
  // offline copy (and the other way round).
  const fs = require('node:fs'), vm = require('node:vm');
  const handlers = {}, puts = [];
  const self = { location: { origin: 'https://z.example' }, addEventListener: (k, f) => { handlers[k] = f; } };
  const caches = { open: async () => ({ put: (k) => puts.push(k), addAll: async () => {} }), match: async () => null };
  vm.runInNewContext(fs.readFileSync('smartplate/static/sw.js', 'utf8'), { self, caches, URL,
    fetch: async () => ({ ok: true, clone() { return this; } }) });
  for (const path of ['/v3', '/', '/?tab=week']) {
    let done;
    handlers.fetch({ request: { method: 'GET', mode: 'navigate', url: `https://z.example${path}` }, respondWith: (p) => { done = p; } });
    await done; await new Promise(r => setImmediate(r));
  }
  assert.deepEqual(puts, ['/v3', '/', '/']);
});
