'use strict';
// Run: node src/sourcing/menu-source.test.js
const assert = require('assert');
const { SimulatedMenuSource, SourcingOrchestrator } = require('./menu-source');

let pass = 0;
const test = (name, fn) => { fn(); pass++; console.log('  ✓ ' + name); };

const constraints = { diet: 'veg', excludeAllergens: ['peanut'], minRating: 4.2 };

test('serviceability gate — home excludes work-only outlets', () => {
  const s = new SimulatedMenuSource();
  const home = s.searchCandidates('home', 'L', constraints).map((d) => d.outlet);
  assert.ok(!home.includes('Anjappar'), 'Anjappar serves work only');
  assert.ok(home.includes('A2B'), 'A2B serves home');
});

test('serviceability gate — transit yields only the grab-and-go', () => {
  const s = new SimulatedMenuSource();
  const transit = s.searchCandidates('transit', 'D', constraints).map((d) => d.outlet);
  assert.deepStrictEqual(transit, ['Transit Kiosk']);
});

test('hard filters — allergen + rating floor remove unsafe/low picks', () => {
  const s = new SimulatedMenuSource();
  const bfast = s.searchCandidates('home', 'B', constraints).map((d) => d.id);
  assert.ok(!bfast.includes('peanut-x'), 'peanut dish filtered by allergen rule');
  const lunch = s.searchCandidates('home', 'L', constraints).map((d) => d.id);
  assert.ok(!lunch.includes('lowrate-x'), '★3.9 dish filtered by ★4.2 floor');
});

test('hard filter — maxPrice (a power-rule cap) excludes pricey dishes', () => {
  const s = new SimulatedMenuSource();
  const capped = s.searchCandidates('home', 'D', { ...constraints, maxPrice: 150 }).map((d) => d.id);
  assert.ok(!capped.includes('jrk-paneer'), 'Paneer ₹240 over the ₹150 cap');
});

test('surge pricing applies in peak windows', () => {
  const s = new SimulatedMenuSource();
  const dish = s.searchCandidates('home', 'D', constraints)[0];
  const peak = s.price(dish, 'home', { hour: 20, window: 'D' });
  const off = s.price(dish, 'home', { hour: 16, window: 'D' });
  assert.ok(peak.surge > off.surge, 'peak surge > off-peak');
});

test('arbitration — cheapest serviceable clearing the ★ floor wins', () => {
  const o = new SourcingOrchestrator([new SimulatedMenuSource()]);
  const r = o.resolveSlot({ area: 'home', window: 'D', constraints, time: { hour: 16 } });
  assert.strictEqual(r.ok, true);
  // cheapest valid home dinner is Idli ₹70 (★4.7), beating Curd ₹140 / Paneer ₹240
  assert.strictEqual(r.dish.id, 'mur-idli');
});

test('no serviceable delivery → cook-or-skip relief valve', () => {
  // a provider that serves nowhere relevant
  const empty = new SimulatedMenuSource({ menu: [] });
  const o = new SourcingOrchestrator([empty]);
  const r = o.resolveSlot({ area: 'home', window: 'L', constraints });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.fallback, 'cook-or-skip');
});

test('idempotency — same key never double-orders', () => {
  const o = new SourcingOrchestrator([new SimulatedMenuSource()]);
  const cart = { dishId: 'mur-idli', total: 90 };
  const a = o.placeOrder({ cart, idempotencyKey: 'k1' });
  const b = o.placeOrder({ cart, idempotencyKey: 'k1' });
  assert.strictEqual(a.ref.id, b.ref.id, 'same order returned for same key');
});

test('autonomy=propose → awaits approval, places nothing', () => {
  const o = new SourcingOrchestrator([new SimulatedMenuSource()]);
  const r = o.placeOrder({ cart: { total: 90 }, idempotencyKey: 'k2', autonomy: 'propose' });
  assert.strictEqual(r.status, 'awaiting-approval');
});

test('fallback ladder — all providers failing yields a deep-link hand-off', () => {
  const broken = new SimulatedMenuSource();
  broken.placeOrder = () => { throw new Error('provider down'); };
  const o = new SourcingOrchestrator([broken]);
  const r = o.placeOrder({ cart: { total: 90 }, idempotencyKey: 'k3' });
  assert.strictEqual(r.status, 'deeplink-handoff');
});

console.log(`\nmenu-source: ${pass} tests passed`);
