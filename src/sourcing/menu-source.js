'use strict';
/**
 * SmartPlate — Menu Sourcing & Ordering layer (SPEC.md §2).
 *
 * The optimizer never talks to a provider directly; it talks to MenuSource.
 * Location/serviceability is a HARD gate applied before scoring, exactly like
 * allergens. Real providers (Swiggy/Zomato partner or aggregator APIs) drop in
 * behind the same interface; here we ship a Simulated source + an orchestrator
 * that implements the fallback ladder and multi-provider arbitration.
 *
 * Plain CommonJS, no build step — runs under node directly.
 *
 * @typedef {'home'|'work'|'transit'} Area
 * @typedef {'B'|'L'|'D'} Window
 *
 * @typedef {Object} Dish
 * @property {string} id
 * @property {string} name
 * @property {string} outlet
 * @property {string} cuisine
 * @property {number} rating        // ★, 0..5
 * @property {boolean} veg
 * @property {string[]} allergens   // e.g. ['peanut']
 * @property {number} basePrice     // ₹ before fees/surge
 * @property {number} kcal
 * @property {Area[]} serves        // areas this outlet delivers to
 * @property {Window[]} windows     // meal windows offered
 *
 * @typedef {Object} Constraints
 * @property {('veg'|'any')} [diet]
 * @property {string[]} [excludeAllergens]
 * @property {number} [minRating]   // ★ floor (hard)
 * @property {number} [maxPrice]    // per-order ceiling (hard) — e.g. a power-rule cap
 *
 * @typedef {Object} PriceQuote
 * @property {number} price
 * @property {number} fee
 * @property {number} surge         // multiplier applied to price, >= 1
 * @property {number} total
 * @property {number} etaMin
 */

/** A provider must implement this surface. */
class MenuSource {
  /** @returns {string} */
  get name() { throw new Error('not implemented'); }
  /** @returns {'partner'|'aggregator'|'deeplink'|'simulated'} */
  get kind() { throw new Error('not implemented'); }
  /** Can this provider deliver to `area` at `time`? @returns {boolean} */
  serviceable(area, time) { return false; }                       // eslint-disable-line no-unused-vars
  /** Hard-filtered + serviceable candidates. @returns {Dish[]} */
  searchCandidates(area, window, constraints, time) { return []; } // eslint-disable-line no-unused-vars
  /** @returns {PriceQuote} */
  price(dish, area, time) { throw new Error('not implemented'); }  // eslint-disable-line no-unused-vars
  /** Idempotent. @returns {{id:string,provider:string,status:string,total:number,idempotencyKey:string}} */
  placeOrder(cart, opts) { throw new Error('not implemented'); }   // eslint-disable-line no-unused-vars
  /** @returns {string} */
  status(orderRefId) { throw new Error('not implemented'); }       // eslint-disable-line no-unused-vars
}

const PEAK = { L: [12, 13, 14], D: [19, 20, 21] }; // surge windows by hour

/** Demo Chennai-veg provider; mirrors the wireframe's outlets + serviceability. */
class SimulatedMenuSource extends MenuSource {
  /** @param {{name?:string, kind?:string, menu?:Dish[]}} [cfg] */
  constructor(cfg = {}) {
    super();
    this._name = cfg.name || 'Simulated';
    this._kind = cfg.kind || 'simulated';
    this._menu = cfg.menu || SimulatedMenuSource.defaultMenu();
    this._orders = new Map();      // orderId -> order
    this._idemIndex = new Map();   // idempotencyKey -> orderId  (no-double-order)
    this._seq = 0;
  }
  get name() { return this._name; }
  get kind() { return this._kind; }

  static defaultMenu() {
    const D = (id, name, outlet, cuisine, rating, basePrice, kcal, serves, windows, allergens = []) =>
      ({ id, name, outlet, cuisine, rating, veg: true, allergens, basePrice, kcal, serves, windows });
    return [
      D('a2b-thali', 'Mini Veg Thali', 'A2B', 'South Indian', 4.5, 180, 550, ['home', 'work'], ['L']),
      D('sang-curd', 'Curd Rice Bowl', 'Sangeetha', 'South Indian', 4.4, 140, 380, ['home', 'work'], ['L', 'D']),
      D('pon-biry', 'Veg Biryani', 'Ponnusamy', 'South Indian', 4.4, 210, 650, ['work'], ['L', 'D']),
      D('mur-idli', 'Idli ×3 + Chutney', 'Murugan Idli', 'South Indian', 4.7, 70, 320, ['home', 'work'], ['B', 'D']),
      D('jrk-paneer', 'Paneer Butter Masala', 'Jr. Kuppanna', 'North Indian', 4.6, 240, 700, ['home'], ['D']),
      D('anj-chett', 'Chettinad Veg Meals', 'Anjappar', 'Chettinad', 4.5, 260, 600, ['work'], ['L']),
      D('lowrate-x', 'Deep-fried Combo', 'QuickBite', 'Fast food', 3.9, 120, 900, ['home', 'work'], ['L', 'D']),
      D('peanut-x', 'Peanut Masala Dosa', 'StreetCart', 'South Indian', 4.3, 90, 480, ['home', 'work'], ['B'], ['peanut']),
      D('gg-transit', 'Grab-and-go counter', 'Transit Kiosk', 'Mixed', 4.2, 120, 500, ['transit'], ['B', 'L', 'D']),
    ];
  }

  serviceable(area, time) {
    return this._menu.some((d) => d.serves.includes(area));
  }

  /** Applies the HARD filters (diet/allergen/rating/price) + serviceability. */
  searchCandidates(area, window, constraints = {}, time = {}) {
    const c = constraints;
    return this._menu.filter((d) => {
      if (!d.serves.includes(area)) return false;                 // serviceability gate
      if (!d.windows.includes(window)) return false;
      if (c.diet === 'veg' && !d.veg) return false;
      if (c.excludeAllergens && c.excludeAllergens.some((a) => d.allergens.includes(a))) return false;
      if (typeof c.minRating === 'number' && d.rating < c.minRating) return false;
      if (typeof c.maxPrice === 'number' && this.price(d, area, time).total > c.maxPrice) return false;
      return true;
    });
  }

  price(dish, area, time = {}) {
    const hour = typeof time.hour === 'number' ? time.hour : 13;
    const win = time.window;
    const peak = win && PEAK[win] && PEAK[win].includes(hour);
    const surge = peak ? 1.2 : 1.0;
    const fee = area === 'transit' ? 0 : 20;
    const price = Math.round(dish.basePrice * surge);
    return { price, fee, surge, total: price + fee, etaMin: area === 'transit' ? 10 : (peak ? 45 : 30) };
  }

  placeOrder(cart, opts = {}) {
    const key = opts.idempotencyKey;
    if (key && this._idemIndex.has(key)) {
      return this._orders.get(this._idemIndex.get(key)); // no double order
    }
    const id = `${this._name}-ORD-${++this._seq}`;
    const order = { id, provider: this._name, status: 'placed', total: cart.total, idempotencyKey: key || null };
    this._orders.set(id, order);
    if (key) this._idemIndex.set(key, id);
    return order;
  }

  status(orderRefId) {
    const o = this._orders.get(orderRefId);
    return o ? o.status : 'unknown';
  }
}

/**
 * Orchestrates providers per SPEC §2.6/§2.7:
 *  - serviceability gate (else cook-or-skip relief valve)
 *  - multi-provider arbitration: cheapest serviceable that clears the ★ floor within ETA
 *  - fallback ladder: partner → aggregator → deeplink → simulated/manual
 *  - idempotent ordering with a graceful deep-link hand-off
 */
class SourcingOrchestrator {
  /** @param {MenuSource[]} providers ordered by preference */
  constructor(providers) {
    if (!providers || !providers.length) throw new Error('at least one provider required');
    this.providers = providers;
  }

  /**
   * Resolve the best serviceable candidate for a slot, or signal the cook/skip valve.
   * @returns {{ ok:true, provider:string, dish:Dish, quote:PriceQuote }
   *          | { ok:false, reason:'no-serviceable-delivery', fallback:'cook-or-skip' }}
   */
  resolveSlot({ area, window, constraints = {}, time = {}, etaBudgetMin = 60 }) {
    const t = { ...time, window };
    /** @type {{provider:string,dish:Dish,quote:PriceQuote}[]} */
    const offers = [];
    for (const p of this.providers) {
      if (!p.serviceable(area, t)) continue;
      for (const dish of p.searchCandidates(area, window, constraints, t)) {
        const quote = p.price(dish, area, t);
        if (quote.etaMin > etaBudgetMin) continue;             // ETA budget (hard)
        offers.push({ provider: p.name, dish, quote });
      }
    }
    if (!offers.length) {
      return { ok: false, reason: 'no-serviceable-delivery', fallback: 'cook-or-skip' };
    }
    // arbitration: cheapest total; ties broken by higher rating then shorter ETA
    offers.sort((a, b) =>
      a.quote.total - b.quote.total ||
      b.dish.rating - a.dish.rating ||
      a.quote.etaMin - b.quote.etaMin);
    const best = offers[0];
    return { ok: true, provider: best.provider, dish: best.dish, quote: best.quote };
  }

  /**
   * Place an order down the fallback ladder. Never double-orders (idempotencyKey).
   * If no provider can execute, returns a deep-link hand-off the user confirms in-app.
   */
  placeOrder({ providerName, cart, idempotencyKey, autonomy = 'auto' }) {
    if (autonomy === 'propose') {
      return { status: 'awaiting-approval', cart, idempotencyKey };
    }
    const ordered = providerName
      ? [this.providers.find((p) => p.name === providerName), ...this.providers]
      : this.providers;
    for (const p of ordered.filter(Boolean)) {
      try {
        return { status: 'placed', ref: p.placeOrder(cart, { idempotencyKey }) };
      } catch (e) { /* fall down the ladder */ }
    }
    // last resort: hand a pre-filled cart to the user's provider app (no order liability)
    return { status: 'deeplink-handoff', cart, idempotencyKey };
  }
}

module.exports = { MenuSource, SimulatedMenuSource, SourcingOrchestrator };
