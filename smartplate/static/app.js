/* SmartPlate — vanilla JS SPA (no build step, no framework dependency).
   Four screens, in order of how often people need them:
     Today  — the next meal, when to order it, one tap to change or confirm
     Week   — seven rows; drag a meal (or tap "Move") onto another to swap
     Places — your usual restaurants (the only list you ever browse)
     More   — settings, orders, insights, cooking, expenses, community, profiles
   The optimiser, budget and hard rules run on the server after every tap. */
"use strict";

const S = {
  meta: null, users: [], userId: null, planId: null, view: null,
  tab: "today", more: null, exec: null, community: [], receipts: null, drawer: null,
  busy: false, error: null, hideCold: false, orderReview: null, cartReview: null,
  liveResults: null, liveFavourites: null, liveBrowseMenu: null, liveOrderReview: null, liveCart: null,
  checkoutReview: null, placedOrder: null, liveOrderStatus: null, liveOrderHistory: null, liveCartError: null,
  cartBudget: {}, pendingResume: null, lastLive: null, liveCartErrorCode: null,
  sheet: null, hhEdit: null, recap: null, swapPick: null, moving: null, onboard: null, places: null, calendar: null, welcome: false, signin: false, account: null,
  planView: "week", addrSheet: false, menuCat: null, liveLoading: null, loadedAt: null,
  offline: typeof navigator !== "undefined" && navigator.onLine === false,
  filters: { veg: false, budget: false, stock: false, flagged: false, fast: false },
};
// One-tap quick filters on live menus and restaurant lists, remembered on this device.
try { Object.assign(S.filters, JSON.parse(localStorage.getItem("smartplate.filters") || "{}") || {}); } catch (_) {}
const MEALS = ["breakfast", "lunch", "dinner"];
const MEAL_ICON = { breakfast: "☀", lunch: "◐", dinner: "☾" };
const WX_ICON = { clear: "☀", rain: "🌧", hot: "🔥", storm: "⛈" };
const rupee = (n) => "₹" + (Math.round((n || 0) * 100) / 100).toLocaleString("en-IN");
const rupee0 = (n) => "₹" + Math.round(n || 0).toLocaleString("en-IN");
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cap1 = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);
/* Money: an estimate is grey with a dotted underline and an "est." tag; a real Swiggy
   number (menu price, cart total, bill line) is solid. Never mix them in one figure. */
const est = (n, exact = false) => `<span class="est" title="Estimate">${(exact ? rupee : rupee0)(n)}<i>est.</i></span>`;
const real = (n, exact = true) => `<span class="real">${(exact ? rupee : rupee0)(n)}</span>`;
// A planned meal's cost is an estimate unless Swiggy's own cart total replaced it.
const cellMoney = (m) => m.real_bill ? real(m.cost) : est(m.cost);

/* Dish identity: a real Swiggy photo when Swiggy gives one (search_menu imageUrl), else an
   icon for the dish's kind, read from its name. Never one generic icon for everything. */
const DISH_KINDS = [
  ["dessert", 330, /payasam|kheer|halwa|kesari|gulab|jamun|rasmalai|rasgulla|jalebi|ice ?cream|kulfi|cake|brownie|pastry|sweet|mysore pak|laddu|falooda|sundae|dessert/i],
  ["drink", 190, /coffee|\btea\b|chai|juice|lassi|shake|smoothie|buttermilk|\bmoru\b|soda|mojito|lime|cola|beverage|drink/i],
  ["biryani", 40, /biryani|biriyani|pulao|pulav/i],
  ["pizza", 10, /pizza/i],
  ["burger", 15, /burger/i],
  ["wrap", 55, /wrap|roll|shawarma|frankie|kathi|sandwich|sub\b/i],
  ["noodles", 280, /noodle|chow ?mein|hakka|ramen|pasta|spaghetti|manchurian/i],
  ["dosa", 50, /dosa|dosai|uttapam|uthappam|appam|pesarattu|roast/i],
  ["tiffin", 100, /idli|idly|vada|vadai|pongal|upma|poori|puri\b|tiffin|kichadi|khichdi|poha|breakfast/i],
  ["bread", 30, /parotta|paratha|porotta|roti|naan|chapati|chapathi|kulcha|bun\b|bread|toast|kothu/i],
  ["fish", 200, /fish|meen|prawn|shrimp|crab|seafood|squid|nethili/i],
  ["egg", 48, /\begg|omelette|omlette|bhurji/i],
  ["curry", 20, /curry|gravy|masala|kurma|korma|kadai|kadhai|makhani|butter chicken|dal\b|daal|sambar|rasam|chole|rajma|paneer|kofta|chettinad|varuval|sukka/i],
  ["grill", 0, /kebab|kabab|tikka|tandoor|grill|bbq|65|lollipop|wings|fried chicken|chicken|mutton|lamb/i],
  ["thali", 160, /meals|thali|combo|curd rice|thayir|rice|lemon rice|sambar rice/i],
  ["bowl", 140, /bowl|salad|poke|buddha/i],
  ["snack", 35, /samosa|bajji|bhaji|pakoda|pakora|bonda|chaat|pani ?puri|fries|cutlet|puff|nuggets|momo|snack|starter/i],
];
const DISH_SVG = {
  dessert: '<path d="M8 11l4 10 4-10"/><path d="M7 11a5 5 0 0 1 10 0z"/>',
  drink: '<path d="M5 8h12v6a6 6 0 0 1-12 0z"/><path d="M17 10h2a2 2 0 0 1 0 4h-2"/><path d="M8 3.5v2M12 3.5v2"/>',
  biryani: '<path d="M4 11h16v2a7 7 0 0 1-7 7h-2a7 7 0 0 1-7-7z"/><path d="M2.5 11h19"/><path d="M9 4c-.8 1 .8 2 0 3.5M14.5 4c-.8 1 .8 2 0 3.5"/>',
  pizza: '<path d="M12 21L3.5 6.5a15 15 0 0 1 17 0z"/><path d="M5.2 9.4a12 12 0 0 1 13.6 0"/><circle cx="10" cy="12" r="1"/><circle cx="13.5" cy="15" r="1"/>',
  burger: '<path d="M4 11a8 6 0 0 1 16 0z"/><path d="M3 14.5h18"/><path d="M4.5 17.5h15a1 1 0 0 1-1 2h-13a1 1 0 0 1-1-2z"/>',
  wrap: '<path d="M6.5 20.5l-3-3L15 6a4.2 4.2 0 0 1 6 6z"/><path d="M13 8l3 3M10 11l3 3"/>',
  noodles: '<path d="M3 12h18a9 9 0 0 1-18 0z"/><path d="M13 3l-2.5 9M19 4l-5 8"/><path d="M7 15c2 1 8 1 10 0"/>',
  dosa: '<path d="M3 18L18.5 4.5l1.5 1.5L6 21z"/><path d="M6.5 15l3 3M10 12l3 3M13.5 9l3 3"/>',
  tiffin: '<ellipse cx="8" cy="11" rx="4.5" ry="2.6"/><ellipse cx="16" cy="11" rx="4.5" ry="2.6"/><path d="M3.5 11v2c0 1.5 2 2.6 4.5 2.6s4.5-1.1 4.5-2.6v-2M11.5 11v2c0 1.5 2 2.6 4.5 2.6s4.5-1.1 4.5-2.6v-2"/><path d="M3 19h18"/>',
  bread: '<ellipse cx="12" cy="12" rx="9" ry="7"/><path d="M7 9.5c3 1.5 7 1.5 10 0M6.5 13c3 1.5 8 1.5 11 0"/>',
  fish: '<path d="M2.5 12c3.5-5.5 10-6.5 14.5-2.5L21 6.5v11l-4-3c-4.5 4-11 3-14.5-2.5z"/><circle cx="7.5" cy="11" r=".9"/>',
  egg: '<path d="M12 3c4 0 7 6 7 10.5a7 7 0 0 1-14 0C5 9 8 3 12 3z"/><circle cx="12" cy="14" r="2.6"/>',
  curry: '<path d="M3 11h18a9 9 0 0 1-18 0z"/><path d="M6.5 11c1.2-1.6 2.8-1.6 4 0s2.8 1.6 4 0 2.3-1.6 3 0"/><path d="M10 3.5c-.7 1 .7 2 0 3M14 3.5c-.7 1 .7 2 0 3"/>',
  grill: '<path d="M4 20L20 4"/><rect x="6.5" y="9.5" width="5" height="5" rx="1.2" transform="rotate(45 9 12)"/><rect x="11.5" y="4.5" width="5" height="5" rx="1.2" transform="rotate(45 14 7)"/>',
  thali: '<circle cx="12" cy="12" r="9"/><circle cx="8.3" cy="9.2" r="2.1"/><circle cx="15.7" cy="9.2" r="2.1"/><path d="M7.5 15.2h9"/>',
  bowl: '<path d="M3 12h18a9 9 0 0 1-18 0z"/><path d="M8 12c-.2-3 1.6-5.5 4.5-6.5M12.5 12c.5-2.6 2.6-4.3 5.5-4.4"/>',
  snack: '<path d="M12 4l9 15H3z"/><path d="M8.2 13.5h7.6"/>',
  cook: '<path d="M4 10h14v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M18 12h3M2.5 10h17"/><path d="M8 3.5c-.7 1 .7 2 0 3M12.5 3.5c-.7 1 .7 2 0 3"/>',
  plate: '<circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="3"/><path d="M2.5 4v5a2 2 0 0 0 2 2v9M21.5 4c-1.5 1-2 3-2 6h2v10"/>',
};
function dishKind(name, cook) {
  if (cook) return ["cook", 120];
  const hit = DISH_KINDS.find(([, , re]) => re.test(String(name || "")));
  return hit ? [hit[0], hit[1]] : ["plate", 220];
}
function dishIcon(name, { cook = false, image = null, lg = false } = {}) {
  if (image) return `<img class="dphoto${lg ? " lg" : ""}" src="${esc(image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-dish="${esc(name)}">`;
  const [kind, hue] = dishKind(name, cook);
  return `<span class="dicon${lg ? " lg" : ""}" style="--h:${hue}" data-kind="${kind}" aria-hidden="true"><svg viewBox="0 0 24 24">${DISH_SVG[kind]}</svg></span>`;
}
const ICON = {
  today: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="13" r="7"/><circle cx="12" cy="13" r="3.2"/><path d="M4 4v4M20 4v4"/></svg>',
  plan: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4M8 14h2M14 14h2"/></svg>',
  you: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8.5" r="4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/></svg>',
  pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 13v7"/></svg>',
  place: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true" width="16" height="16"><path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/></svg>',
};
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
  del(k) { try { localStorage.removeItem(k); } catch (_) {} },
};

/* Private profiles: the key lives only in this browser ({id: {key, name}}). */
const keys = {
  all() { try { const v = JSON.parse(store.get("smartplate.keys") || "{}"); return v && typeof v === "object" && !Array.isArray(v) ? v : {}; } catch (_) { return {}; } },
  get(id) { return this.all()[id]?.key || null; },
  put(id, key, name) { const a = this.all(); a[id] = { key, name }; store.set("smartplate.keys", JSON.stringify(a)); },
  drop(id) { const a = this.all(); delete a[id]; store.set("smartplate.keys", JSON.stringify(a)); },
};
async function downloadPrivate(path, filename, mime) {
  const headers = {};
  const key = keys.get(S.userId);
  if (key) headers["X-SmartPlate-Key"] = key;
  const response = await fetch(path, { headers });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || response.statusText);
  }
  const url = URL.createObjectURL(new Blob([await response.blob()], { type: mime }));
  try {
    const link = document.createElement("a");
    link.href = url; link.download = filename;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch (error) { URL.revokeObjectURL(url); throw error; }
}
function mergeUsers(open) {
  const priv = Object.entries(keys.all()).map(([id, v]) => ({ id: Number(id), name: v.name || "My profile", city: "Chennai", setup_done: true, private: true }));
  return [...priv, ...open.filter(u => !priv.some(p => p.id === u.id))];
}

// Swiggy answers 409 with one of these when the user's own sign-in is missing or no longer
// accepted: the fix is a Connect button, not a Retry.
const SWIGGY_RECONNECT = new Set(["swiggy_not_connected", "swiggy_auth_expired"]);
async function api(path, method = "GET", body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  const k = S.userId ? keys.get(S.userId) : null;
  if (k) opt.headers["X-SmartPlate-Key"] = k;
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    if (SWIGGY_RECONNECT.has(data.code) && S.swiggy) {
      S.swiggy.connected = false; S.swiggy.expired = data.code === "swiggy_auth_expired";
      S.liveCart = null; S.checkoutReview = null;
    }
    // Never a bare status word: the server's own sentence, else the HTTP status.
    const err = new Error(data.message || data.error || `The server answered HTTP ${r.status}${r.statusText ? ` (${r.statusText})` : ""}`);
    err.code = data.code;
    err.status = r.status;
    err.data = data;
    throw err;
  }
  return r.json();
}
function toast(msg) {
  document.querySelectorAll(".toast").forEach(old => old.remove());   // one message at a time, never stacked
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; t.setAttribute("role", "status"); document.body.appendChild(t);
  setTimeout(() => t.remove(), 2800);
}

/* ---- busy + error states ---- */
function ensureBusyBar() {
  if (document.getElementById("busybar")) return;
  const d = document.createElement("div");
  d.className = "busybar"; d.id = "busybar"; d.innerHTML = "<i></i>";
  document.body.appendChild(d);
}
function setBusy(b) {
  S.busy = b;
  const el = document.getElementById("busybar");
  if (el) el.classList.toggle("on", b);
  document.body.setAttribute("aria-busy", String(b));
  document.querySelectorAll("button").forEach(button => {
    if (b && !button.disabled) {
      button.dataset.busyDisabled = "true";
      button.disabled = true;
    } else if (!b && button.dataset.busyDisabled === "true") {
      button.disabled = false;
      delete button.dataset.busyDisabled;
    }
  });
}
// Wrap every user-triggered action: show progress, surface errors instead of failing silently.
// `label` names the action ("Review “Veg Biryani”") so a failure says what failed and why;
// Retry re-runs exactly that action, never an unrelated reload.
function retryLast() {}                       // sentinel for the Retry button (data-act="reload")
async function guard(fn, label) {
  if (S.busy) return;
  const again = fn === retryLast ? (S.retry || { fn: reloadPlan, label: "Reload your plan" }) : { fn, label };
  setBusy(true); S.error = null; S.errorCode = null; S.lastLive = null; S.retry = null;
  try { await again.fn(); }
  catch (e) {
    const reason = e.message || String(e);
    S.error = again.label ? `${again.label} failed: ${reason}` : reason; S.errorCode = e.code || null;
    if (e.code === "profile_missing") { await profileGone(S.error); return; }   // no Retry can bring it back
    S.retry = again;
    // Not connected (or the sign-in expired): Connect, then pick up where the user was.
    if (SWIGGY_RECONNECT.has(S.errorCode) && S.lastLive) S.pendingResume = S.lastLive;
    render();
  }
  finally { setBusy(false); }
}
// The server no longer has this profile (free hosting erases its disk on a restart or
// redeploy). Every later call with this id would fail the same way, so go back to the
// welcome screen and say what happened instead of offering a Retry that cannot work.
async function profileGone(message) {
  if (S.userId) keys.drop(S.userId);
  store.del("smartplate.user");
  S.userId = null; S.view = null; S.planId = null; S.exec = null; S.swiggy = null; S.liveBrowseMenu = null;
  S.liveResults = null; S.liveOrderReview = null; S.liveCart = null; S.liveFavourites = null; S.retry = null;
  S.checkoutReview = null; S.pendingResume = null; S.tab = "today"; S.more = null;
  S.users = mergeUsers(await api("/api/users").catch(() => []));
  S.welcome = true; S.error = message; S.errorCode = "profile_missing";
  render();
}

/* ---- one "Connect Swiggy" path for every live surface ---- */
const CONNECT_MSG = "Connect Swiggy to use real menus and prices.";
function connectPrompt() {
  return `<div class="connect-swiggy" role="alert"><span>${CONNECT_MSG}</span>
    <button class="small primary" data-act="swiggy-connect">Connect Swiggy</button></div>`;
}
// The live actions that resume once after connecting (and choosing an address).
const RESUME = {
  "order-meal": { label: "reviewing your meal's live Swiggy item", run: (sid) => reviewCart(Number(sid)) },
  "order-week": { label: "checking that meal's real Swiggy cart",
    run: async (sid) => { S.tab = "week"; S.planView = "orders"; await checkQueueCart(sid); } },
  "live-menu": { label: "opening that Swiggy menu", run: (name) => { S.tab = "week"; S.planView = "places"; return openLiveMenu(name); } },
  "live-place": { label: "opening that Swiggy menu", run: (id, name) => { S.tab = "today"; return openLivePlace(id, name); } },
  "live-menus": { label: "planning from your Swiggy restaurants", run: () => { S.tab = "week"; return planFromLiveMenus(); } },
};
// What each button does, in the words an error banner uses ("Add to Swiggy cart failed: …").
const ACT_LABELS = {
  "confirm-live-cart": "Add to Swiggy cart", "confirm-cart": "Add to Swiggy cart",
  "review-live-checkout": "Review the order", "place-live-order": "Place the order",
  "track-live-order": "Track the order", "live-order-history": "Load your Swiggy orders",
  "refresh-live-cart": "Refresh Swiggy cart", "resolve-live-attempt": "Resolve the order attempt",
  "more-live-dishes": "Load more dishes", "restore-live-menu": "Browse the menu",
  "live-menus": "Plan from Swiggy menus", "swiggy-refresh-addresses": "Refresh addresses",
  "swiggy-connect": "Connect Swiggy", newweek: "Plan a new week", reopt: "Re-plan", exec: "Review orders",
  "addr-open": "Load your Swiggy addresses",
};
function liveAction(kind, ...args) { S.lastLive = { kind, args }; return RESUME[kind].run(...args); }
function rememberResume() {
  if (S.pendingResume) store.set("smartplate.resume", JSON.stringify({ user: S.userId, at: Date.now(), ...S.pendingResume }));
  else store.del("smartplate.resume");
}
function takeResume() {
  let saved = null;
  try { saved = JSON.parse(store.get("smartplate.resume") || "null"); } catch (_) { saved = null; }
  store.del("smartplate.resume");                         // runs once, whatever happens next
  const fresh = saved && typeof saved === "object" && saved.user === S.userId && RESUME[saved.kind]
    && Array.isArray(saved.args) && Date.now() - Number(saved.at) < 30 * 60 * 1000;
  return fresh ? { kind: saved.kind, args: saved.args } : null;
}
async function resumePending() {
  const p = S.pendingResume;
  if (!p || !swiggyReady()) return false;                 // still needs an address: wait for it
  S.pendingResume = null;
  await liveAction(p.kind, ...p.args);
  render();
  return true;
}
async function reloadPlan() { S.view = await api(`/api/plan/${S.planId}`); render(); }

/* ---------------------------------------------------------------- bootstrap */
async function boot() {
  ensureBusyBar();
  S.meta = await api("/api/meta");
  S.users = mergeUsers(await api("/api/users"));
  const saved = Number(store.get("smartplate.user"));
  S.userId = S.users.find(u => u.id === saved)?.id || null;
  if (!S.userId) { S.welcome = true; render(); return; }
  try { await loadOrCreatePlan(); }
  catch (e) {
    // The key no longer opens this profile, or the server's data was reset (free hosting).
    if (!/missing$/.test(e.code || "") && !/private|not found/i.test(e.message)) throw e;
    keys.drop(S.userId); store.del("smartplate.user"); S.userId = null; S.view = null;
    S.users = mergeUsers(await api("/api/users")); S.welcome = true; render(); return;
  }
  const params = typeof location !== "undefined" ? new URLSearchParams(location.search) : new Map();
  const wanted = params.get("tab");
  const TAB_ALIAS = { plan: "week", you: "more", places: "today" };
  if (["today", "week", "places", "more", "plan", "you"].includes(wanted)) S.tab = TAB_ALIAS[wanted] || wanted;
  if (params.get("swiggy") || params.get("swiggy_error")) {             // back from Swiggy sign-in
    S.tab = "more"; S.more = "connection"; S.swiggy = await api(`/api/user/${S.userId}/swiggy`);
    // Fixed messages only: the URL carries a code, never text to display (a crafted link can't fake warnings).
    const SWIGGY_ERRORS = { denied: "Swiggy sign-in was cancelled.",
      expired: "That Swiggy sign-in link expired or was already used. Tap Connect Swiggy again.",
      other_browser: "That Swiggy sign-in was started in a different browser. Tap Connect Swiggy again on this device.",
      failed: "Swiggy sign-in didn't complete. Tap Connect Swiggy to try again." };
    if (params.get("swiggy_error")) { S.error = SWIGGY_ERRORS[params.get("swiggy_error")] || SWIGGY_ERRORS.failed; store.del("smartplate.resume"); }
    else { toast("Connected to Swiggy"); S.pendingResume = takeResume(); }
    if (typeof history !== "undefined") history.replaceState(null, "", "/");
  }
  render();
  if (S.pendingResume) await guard(resumePending);
  scheduleAlerts().catch(() => {});
  syncPush().catch(() => {});
}
async function loadOrCreatePlan() {
  S.view = await api(`/api/user/${S.userId}/plan`);
  S.planId = S.view.plan.id; S.loadedAt = new Date();
  S.exec = await api(`/api/plan/${S.planId}/orders`);
  S.swiggy = await api(`/api/user/${S.userId}/swiggy`).catch(() => null);
  S.liveFavourites = swiggyReady() ? await api(`/api/user/${S.userId}/swiggy/favourites`).catch(() => null) : null;
  if (swiggyReady()) await refreshLiveCart(false);
}
function adoptView(view) { S.view = view; S.planId = view.plan.id; S.loadedAt = new Date(); scheduleAlerts().catch(() => {}); }

/* ---------------------------------------------------------------- actions */
async function switchUser(id) {
  S.userId = Number(id); S.exec = null; S.receipts = null; S.drawer = null; S.orderReview = null;
  S.sheet = null; S.moving = null; S.places = null; S.calendar = null; S.welcome = false; S.tab = "today"; S.more = null; S.account = null; S.cartReview = null;
  S.swiggy = null; S.swAddrs = null; S.liveMenu = null; S.carts = null; S.acctDraft = null;
  S.liveResults = null; S.liveFavourites = null; S.liveBrowseMenu = null; S.liveOrderReview = null; S.liveCart = null;
  S.checkoutReview = null; S.placedOrder = null; S.liveOrderStatus = null; S.liveOrderHistory = null;
  S.liveCartError = null;
  store.set("smartplate.user", String(S.userId));
  try { await loadOrCreatePlan(); }
  catch (e) {
    if (!/missing$/.test(e.code || "") && !/private|not found/i.test(e.message)) throw e;
    keys.drop(S.userId); store.del("smartplate.user"); S.userId = null; S.view = null;
    S.users = mergeUsers(await api("/api/users")); S.welcome = true;
    toast("That profile isn't on this server any more");
  }
  render();
}
async function newWeek() {
  adoptView(await api("/api/plan", "POST", { user_id: S.userId }));
  S.exec = null; S.receipts = null; S.orderReview = null; S.tab = "week"; S.more = null;
  toast(`Planned the week of ${S.view.plan.week_start}`); render();
}
async function setMode(mode) {
  adoptView(await api(`/api/plan/${S.planId}/optimize`, "POST", { mode }));
  toast(`${S.view.plan.mode_label}: ${S.meta.mode_outcomes?.[mode] || "re-planned"}`); render();
}
async function reoptimize() {
  adoptView(await api(`/api/plan/${S.planId}/optimize`, "POST", {}));
  toast("Re-planned"); render();
}
async function runCommand() {
  const inp = document.getElementById("cmd");
  const text = inp.value.trim(); if (!text) return;
  const res = await api(`/api/plan/${S.planId}/command`, "POST", { text });
  S.view = res.plan; inp.value = "";
  toast(res.result.effect && res.result.effect !== "none" ? res.result.effect : "No actionable change");
  render();
}
async function quickCmd(text) {
  const inp = document.getElementById("cmd");
  if (inp) inp.value = text;
  await runCommand();
}
async function execute() {
  const review = S.orderReview;
  S.orderReview = null;
  S.exec = await api(`/api/plan/${S.planId}/execute`, "POST", {
    expected_fingerprint: review.fingerprint, max_total: review.total,
  });
  const outcome = S.exec;
  S.exec = await api(`/api/plan/${S.planId}/orders`);
  S.view = await api(`/api/plan/${S.planId}`);
  S.tab = "week"; S.planView = "orders"; S.more = null;
  toast(`Simulated ${outcome.placed}/${outcome.attempted} orders · ${outcome.failed} not ordered`);
  render();
}
async function reviewOrders() {
  const review = await api(`/api/plan/${S.planId}/execute/preview`);
  if (!review.order_count) { toast("There are no delivery meals to order"); return; }
  S.orderReview = review; render();
}
async function genReceipts() {
  await api(`/api/plan/${S.planId}/receipts`, "POST", {});
  S.receipts = await api(`/api/receipts/${S.userId}`);
  toast("Expense records updated"); render();
}
async function loadCommunity() { S.community = await api("/api/community"); render(); }
async function adopt(id) { const t = await api(`/api/community/${id}/adopt`, "POST", {}); toast(`Saved interest in “${t.title}”`); loadCommunity(); }
async function saveTemplate() {
  const title = prompt("Template title:", `${S.view.user.name}'s ${S.view.plan.mode_label} week`);
  if (!title) return;
  // Shared weeks are public. The display name is only published when the person opts in.
  const show_name = confirm(`Show your name (“${S.view.user.name}”) on this shared week?\n\nOK = show my name · Cancel = share anonymously`);
  await api(`/api/plan/${S.planId}/save-template`, "POST", { title, show_name });
  toast("Saved to community"); if (S.more === "community") loadCommunity();
}
async function setSession(sid, status) {
  await api(`/api/session/${sid}/status`, "POST", { status });
  S.view = await api(`/api/plan/${S.planId}/optimize`, "POST", {});
  S.drawer = null; S.sheet = null;
  toast({ skipped: "Skipped — the rest of the week re-balanced", snoozed: "Snoozed", cooked: "Marked as cooked at home",
    active: "Back in the plan" }[status] || `Meal ${status}`);
  render();
}

/* ---- the everyday actions ---- */
async function openSheet(sid) {
  S.sheet = { sid: Number(sid), data: null }; render();
  S.sheet.data = await api(`/api/session/${sid}/options`); render();
}
async function choose(sid, body, msg) {
  adoptView(await api(`/api/session/${sid}/choose`, "POST", body));
  S.sheet = null; toast(msg || "Done — the rest of the week re-balanced"); render();
}
/* Epicure: "more like this" on the planned dish, ingredient swaps for cook days. */
const epicureOn = () => !!S.meta?.epicure?.available;
async function moreLikeThis(sid) {
  const r = await api(`/api/session/${sid}/more-like`, "POST", {});
  adoptView(r.plan);
  // the re-plan may have changed this very meal: show the sheet as it is now
  const data = await api(`/api/session/${sid}/options`);
  S.sheet = { ...S.sheet, data, similar: { dish: r.recorded, items: r.similar } };
  toast(`Got it: more like ${r.recorded}. Your open meals were re-planned.`); render();
}
async function forgetMoreLike(name) {
  adoptView(await api(`/api/user/${S.userId}/more-like/forget`, "POST", { name }));
  toast(`Removed ${name} from “more like this”.`); render();
}
async function openSwap(token, reason) {
  S.swapPick = { token, reason, data: null }; render();
  S.swapPick.data = await api(`/api/plan/${S.planId}/swaps?token=${encodeURIComponent(token)}`); render();
  // the picker sits above the recipes; on a phone the tapped grocery line is far below it
  document.querySelector(".swap-pick")?.scrollIntoView?.({ block: "center" });
}
async function setSwap(token, swapToken, reason) {
  adoptView(await api(`/api/plan/${S.planId}/grocery-swap`, "POST", { token, swap_token: swapToken, reason }));
  S.swapPick = null; toast(swapToken ? "Swap saved. Your recipe and grocery list show it." : "Back to the original ingredient."); render();
}
async function confirmMeal(sid) {
  adoptView(await api(`/api/session/${sid}/confirm`, "POST", {}));
  S.sheet = null; toast("Logged. How was it? Rate it below."); render();
}
async function rateMeal(sid, score) {
  const r = await api(`/api/session/${sid}/rate`, "POST", { score });
  adoptView(r.plan);
  if (score < 0) toast("Got it — we won't plan that dish again for a while");
  else if (r.suggest_favourite) {
    S.suggestFav = r.suggest_favourite;
    toast(`Liked. Add ${r.suggest_favourite.restaurant} to your usual places?`);
  } else toast("Liked — we'll remember");
  render();
}
async function swapMeals(a, b) {
  S.moving = null;
  adoptView(await api(`/api/plan/${S.planId}/swap`, "POST", { a: Number(a), b: Number(b) }));
  toast("Swapped — budget and nutrition re-balanced"); render();
}
async function toggleFav(rid) {
  const r = await api(`/api/user/${S.userId}/favourites/${rid}`, "POST", {});
  adoptView(r.plan);
  S.places = await api(`/api/restaurants?user_id=${S.userId}`);
  S.suggestFav = null;
  toast(r.favourite ? "Added to your usual places" : "Removed from your usual places"); render();
}

/* ---- one-tap toggles: order ↔ cook, pin, quick filters ---- */
const cellOf = (sid) => S.view.grid.flatMap(d => Object.values(d.meals)).find(m => m.session_id === Number(sid));
// Order ↔ cook in one tap: the best safe option the server offers for this meal (Change picks another).
async function toKind(sid, kind) {
  const cell = cellOf(sid);
  if (cell && cell.kind === kind) return;
  const d = await api(`/api/session/${sid}/options`);
  if (kind === "cook") {
    const r = d.cook[0];
    if (!r) throw new Error("No recipe fits your rules for this meal. Tap Change to pick a dish");
    return choose(sid, { recipe_key: r.recipe_key }, `Cook: ${r.name}. It's on your grocery list.`);
  }
  const dishes = [...d.usual.flatMap(g => g.dishes.map(x => ({ ...x, restaurant: g.restaurant }))), ...d.new];
  const x = dishes.find(x => x.fits) || null;
  if (!x) throw new Error("No dish nearby fits what's left of your budget for this meal. Tap Change to see all options");
  return choose(sid, { item_id: x.item_id }, `Order: ${x.name} from ${x.restaurant}.`);
}
async function togglePin(sid) {
  const c = cellOf(sid);
  if (!c) return;
  if (c.pinned) return choose(sid, { action: "auto" }, "Unpinned. Re-plans can change this meal.");
  const body = c.kind === "cook" && c.recipe_key ? { recipe_key: c.recipe_key } : c.item_id != null ? { item_id: c.item_id } : null;
  if (!body) throw new Error("This meal can't be pinned. Tap it to choose a dish");
  return choose(sid, body, "Pinned. Re-plans keep this meal.");
}
function toggleFilter(key) {
  S.filters[key] = !S.filters[key];
  store.set("smartplate.filters", JSON.stringify(S.filters));
  render();
}
async function openAddresses() {
  S.addrSheet = true; S.swAddrs = null; render();
  S.swAddrs = await api(`/api/user/${S.userId}/swiggy/addresses`); render();
}

/* ---------------------------------------------------------------- render */
/* Honest copy that follows what this server actually does (GET /api/meta). */
// Profiles on a temporary disk are erased on restart or redeploy: say so on every screen.
function storageBanner() {
  if (S.meta?.storage?.persistent !== false) return "";
  return `<div class="storage-warning" role="note"><strong>Trial server:</strong> profiles, plans and Swiggy links here are stored on a temporary disk and are erased whenever the server restarts or is updated, which can happen several times a day. Don't keep anything here you can't lose; use More → Profiles → Download my data to keep a copy.</div>`;
}
const swiggySignInOpen = () => !!S.meta?.swiggy_redirect_approved;
function welcomeLede() {
  // Without a Swiggy connection the planner uses the sample catalogue (everyday.py).
  // Real restaurants need Swiggy sign-in, which works only once Swiggy approves this
  // server's exact callback URL (SMARTPLATE_SWIGGY_REDIRECT_APPROVED).
  const plan = "Plan a week of meals around your diet, allergies and budget. Until a Swiggy account is connected, the weekly planner uses sample Chennai dishes and prices, not live Swiggy menus.";
  const swiggy = swiggySignInOpen()
    ? " Connect Swiggy to find real restaurants and dishes for your saved delivery address."
    : " Connecting a Swiggy account is not available on this server yet: it is waiting for Swiggy to approve its sign-in address. Order in the Swiggy app for now.";
  return plan + swiggy;
}

function render() {
  const app = document.getElementById("app");
  if (S.onboard) { app.innerHTML = storageBanner() + onboardingScreen(); wire(); return; }
  if (S.welcome || !S.view) { app.innerHTML = storageBanner() + welcomeScreen(); wire(); return; }
  app.innerHTML = storageBanner() + offlineBar() + `<div class="shell">${navBar()}<div class="col">${topbar()}`
    + `<main class="wrap" id="main">${errbar() + tabBody()}</main></div></div>`
    + (S.sheet ? sheetDialog() : "") + (S.addrSheet ? addressSheet() : "")
    + (S.orderReview ? orderReviewDialog() : "") + (S.cartReview ? cartReviewDialog() : "")
    + (S.liveOrderReview ? liveOrderReviewDialog() : "")
    + (S.checkoutReview ? checkoutReviewDialog() : "");
  wire();
}
// Offline: say so, and how old the plan on screen is. Nothing is cached as if it were live.
function offlineBar() {
  if (!S.offline) return "";
  const at = S.loadedAt ? S.loadedAt.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" }) : null;
  return `<div class="offline" role="status">You're offline.${at ? ` Showing your plan as loaded at ${esc(at)}.` : ""} Changes, live menus and your Swiggy cart need a connection.</div>`;
}

function welcomeScreen() {
  const samples = (S.users || []).filter(u => u.setup_done);
  return `<main class="welcome">
    <div class="brand big">Smart<em>Plate</em></div>
    <h1 class="hero">Meals from the places you like.</h1>
    <p class="lede">${welcomeLede()}</p>
    <ul class="promise">
      ${swiggySignInOpen() ? `<li><b>Remember your favourites.</b> Search local restaurants and browse their current Swiggy menus after connecting.</li>` : ""}
      <li><b>Review before ordering.</b> Check the restaurant, dish, delivery address and current payable total.</li>
      <li><b>Plans around your rules.</b> The sample planner filters declared allergies and caps estimated spend. Check ingredients and the final price in Swiggy before ordering.</li>
    </ul>
    ${S.signin ? "" : errbar(false)}
    <button class="primary big" data-act="start-onboard">Create my private profile</button>
    ${S.signin ? `<form id="signin" class="card signin">
        <h3 class="k">Sign in to your profile</h3>
        ${S.error ? `<p class="inline-err" role="alert">⚠ ${esc(S.error)}</p>` : ""}
        <label>Sign-in name<input id="si-login" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" value="${esc(S.signinDraft || "")}" required></label>
        <label>Password<input id="si-pw" type="password" autocomplete="current-password" required></label>
        <div class="row"><button type="submit" class="primary">Sign in</button><button type="button" class="ghost" data-act="signin-close">Cancel</button></div>
        <p class="fine">No sign-in yet? Open your profile where you made it, then More → Profiles → Sign in anywhere.</p></form>`
      : `<button class="ghost big" data-act="signin-open">I already have a profile · Sign in</button>`}
    ${samples.length ? `<details class="samples"><summary>Or look around a sample profile</summary>
      ${samples.map(u => `<button class="ghost" data-user="${u.id}">${esc(u.name)} · ${esc(u.city)}</button>`).join("")}</details>` : ""}
    <p class="fine">Live menus require an approved Swiggy connection. The optional weekly planner uses sample Chennai dishes and estimated prices. Ingredients and allergy safety must be checked with the restaurant. Every real order requires your confirmation.</p>
  </main>`;
}

function errbar(retry = true) {
  if (!S.error) return "";
  if (SWIGGY_RECONNECT.has(S.errorCode)) return `<div class="errbar connect" role="alert">${connectPrompt()}
    <button class="x" data-close-err="1" title="Dismiss" aria-label="Dismiss">✕</button></div>`;
  return `<div class="errbar" role="alert"><span>⚠ ${esc(S.error)}</span>
    ${retry ? `<button class="retry ghost" data-act="reload">Retry</button>` : ""}
    <button class="x" data-close-err="1" title="Dismiss" aria-label="Dismiss">✕</button></div>`;
}
// A live panel's own error: the shared Connect prompt when that is the fix, else the message.
function liveError(msg, code) {
  if (SWIGGY_RECONNECT.has(code)) return connectPrompt();
  return msg ? `<p class="fine" role="alert">${esc(msg)}</p>` : "";
}

function topbar() {
  return `<header class="topbar"><div class="inner">
    <div class="brand">SmartPlate</div>
    ${addressPill()}
    <span class="spacer"></span>
    <button class="avatar" data-go="more:profiles" title="Profile: ${esc(S.view.user?.name)}" aria-label="Profile and settings">${esc((S.view.user?.name || "?").slice(0, 1))}</button>
  </div></header>`;
}
// The delivery address every live call uses, always one tap away (real Swiggy addresses only).
function addressPill() {
  const sw = S.swiggy;
  if (!sw || !sw.connected) return "";
  if (!sw.address) return `<button class="addr-pill warn" data-act="addr-open" aria-haspopup="dialog">${ICON.place}<span>Choose delivery address</span></button>`;
  return `<button class="addr-pill" data-act="addr-open" aria-haspopup="dialog" aria-label="Delivering to ${esc(sw.address.label)}. Change address">${ICON.place}<span>${esc(sw.address.label)}</span> ▾</button>`;
}
function addressSheet() {
  const sw = S.swiggy || {}, list = S.swAddrs;
  return `<div class="modal-bg" data-close-addr="1"><section class="sheet" role="dialog" aria-modal="true" aria-labelledby="addr-title">
    <button class="close ghost" data-close-addr="1" aria-label="Close">✕</button>
    <h2 id="addr-title">Deliver to</h2>
    <p class="fine">Your saved Swiggy addresses. SmartPlate uses the one you pick for live menus, your cart and planning.</p>
    ${list ? `<div class="addrlist">${list.map(a => `<button class="mitem ${sw.address?.id === a.id ? "on" : ""}" data-swaddr="${esc(a.id)}" aria-pressed="${sw.address?.id === a.id}"><b>${esc(a.label)}</b><span>${esc(a.text)}</span></button>`).join("") || `<p class="fine">Swiggy returned no saved addresses.</p>`}</div>`
      : `<div class="skeleton" aria-busy="true"><i></i><i></i><i></i></div>`}
    <p class="fine">Address not listed? Add it in the Swiggy app (Account → Addresses), then <button class="small" data-act="swiggy-refresh-addresses">Refresh addresses</button></p>
  </section></div>`;
}

const TABS = [["today", "Today", ICON.today], ["week", "Plan", ICON.plan], ["more", "You", ICON.you]];
function navBar() {
  const cur = S.tab === "places" ? "today" : S.tab;
  return `<nav class="nav" aria-label="Main"><div class="brand">SmartPlate</div>${TABS.map(([k, l, i]) =>
    `<button class="navbtn ${cur === k ? "on" : ""}" data-tab="${k}" ${cur === k ? 'aria-current="page"' : ""}>${i}<span>${l}</span></button>`).join("")}</nav>`;
}

function tabBody() {
  if (S.tab === "today" || S.tab === "places") return todayScreen();
  if (S.tab === "week") return planScreen();
  return moreScreen();
}

/* ================================================================ TODAY */
function todayScreen() {
  const v = S.view, nu = v.next_up;
  const hello = greeting();
  const live = swiggyReady();
  const sample = live ? "" : v.user?.prefs?.sample ? `<div class="coldstart"><span class="i">Sample</span><span>This profile uses demonstration preferences and sample menu data. <a href="#" data-act="start-onboard">Set up your own</a>.</span></div>`
    : `<div class="coldstart"><span class="i">Demo plan</span><span>These weekly restaurant dishes and prices are sample data. ${swiggySignInOpen() ? `<a href="#" data-act="swiggy-connect">Connect Swiggy</a> to order from real restaurants near your saved address.` : "Order in the Swiggy app for now: connecting Swiggy here awaits Swiggy's approval."}</span></div>`;
  return `${sample}<h1 class="greet">${hello}${v.user?.name && v.user.name !== "Me" && !v.user?.prefs?.sample ? ", " + esc(v.user.name.split(" ")[0]) : ""}</h1>
    ${nu ? nextUpCard(nu) : `<section class="hero-card"><p class="eyebrow">This week</p><h2>Nothing left to plan this week.</h2><div class="actions"><button class="primary" data-act="newweek">Plan next week</button></div></section>`}
    ${todayRest(nu)}
    ${budgetCard()}
    ${headsUp()}
    ${live ? `<section class="order-area" aria-label="Order from your area">${livePlacesScreen()}</section>` : ""}
    ${reminderRow()}
    ${learningLine()}`;
}
function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}
function budgetCard() {
  const b = S.view.budget || {}, spend = b.spend || 0, cap = b.budget || 0;
  const left = cap - spend, pct = cap ? Math.min(100, (spend / cap) * 100) : 0;
  const modes = S.meta?.modes || {};
  return `<section class="budget card" aria-label="Budget">
    <div class="brow"><div><div class="lbl">${S.view.source?.kind === "live" ? "Plan estimate · live Swiggy prices" : "Sample plan estimate"}</div>
      <div class="big ${left < 0 ? "neg" : ""}">${est(Math.abs(left))} <small>${left < 0 ? "over budget" : "left of " + rupee0(cap)}</small></div></div>
      <div class="seg" role="group" aria-label="How tight is money this week?">${Object.entries(modes).map(([k, l]) =>
        `<button class="${S.view.plan.mode === k ? "on" : ""}" aria-pressed="${S.view.plan.mode === k}" data-mode="${k}" title="${esc(S.meta.mode_outcomes?.[k] || "")}">${esc(l)}</button>`).join("")}</div></div>
    <div class="bar"><i class="${left < 0 ? "over" : ""}" style="width:${pct}%"></i></div>
    <div class="fine">${b.prorated ? `Covers the rest of this week (${rupee0(cap)} of your ${rupee0(b.weekly_budget)} weekly budget). ` : ""}${b.daily_cap ? `Daily planning limit ${rupee0(b.daily_cap)}. ` : ""}Sample prices include estimated fees. Live prices and actual purchases are reviewed separately. A checked Swiggy cart counts at its real total.</div>
  </section>`;
}
// Why this pick, in plain words from real fields. Never a score.
function whyChips(c) {
  const u = S.view.user || {}, b = S.view.budget || {}, out = [];
  if (c.pinned) out.push(["Your pick", "ok"]);
  if (c.usual) out.push(["Your usual place", ""]);
  if (c.kind === "delivery" || c.kind === "cook") {
    if ((b.budget || 0) >= (b.spend || 0)) out.push(["Week stays in budget", "ok"]);
    else out.push(["Week is over budget", ""]);
  }
  const p = c.nutrition?.protein_g;
  if (p >= 15) out.push([`≈${Math.round(p)} g protein (estimate)`, ""]);
  const al = (u.allergens || []).map(a => a.replace("_", " "));
  if (al.length && c.kind === "delivery") out.push([S.view.source?.kind === "live" ? `No ${al.join(", ")} by dish name` : `${cap1(al.join(", "))} filtered out`, "ok"]);
  if (al.length && c.kind === "cook") out.push([`Recipe has no ${al.join(", ")}`, "ok"]);
  if (u.diet === "veg" || u.diet === "vegan") out.push([u.diet === "vegan" ? "Vegan plan" : "Vegetarian", ""]);
  if (c.rating && c.kind === "delivery") out.push([`${Number(c.rating).toFixed(1)}★`, ""]);
  return out.length ? `<ul class="why-chips" aria-label="Why this pick">${out.map(([t, k]) => `<li class="${k}">${esc(t)}</li>`).join("")}</ul>` : "";
}
function nextUpCard(nu) {
  const c = nu.cell, when = `${nu.when} · ${cap1(nu.meal)}`;
  const isCook = c.kind === "cook";
  if (c.status === "confirmed" || c.status === "ordered") {
    return `<section class="hero-card done"><div class="hero-top">${dishIcon(c.item, { cook: isCook, lg: true })}<div><div class="eyebrow">${esc(when)}</div>
      <h2>${esc(c.item)}</h2><p class="sub">${c.status === "ordered" ? "Ordered (simulation)" : "You had this"} · ${cellMoney(c)}</p></div></div>
      ${rateRow(c)}</section>`;
  }
  const order = c.order || {};
  const sampleDish = !isCook && S.view.source?.kind !== "live";
  const carted = (S.carts || {})[c.session_id];
  const canCart = !isCook && swiggyReady() && cartEligible() && !carted;
  const reasons = (c.reasons || []).filter(r => !/^Ordered |from .* \(₹/.test(r)).slice(0, 4);
  return `<section class="hero-card ${esc(c.kind)}" aria-label="Next meal">
    <div class="hero-top">${dishIcon(c.item, { cook: isCook, lg: true })}<div>
      <div class="eyebrow">${esc(when)}${sampleDish ? " · sample dish, not a real restaurant" : ""}</div>
      <h2>${esc(c.item)}</h2>
      <p class="sub">${isCook ? "Cook at home" : esc(c.restaurant)} · ${cellMoney(c)}</p></div></div>
    ${!isCook && order.order_at ? `<div class="when"><div><b>Order by ${esc(order.order_at)}</b><span>${esc(order.why)}</span></div></div>` : ""}
    ${whyChips(c)}
    ${reasons.length ? `<details class="why"><summary>Why this?</summary><ul>${reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul></details>` : ""}
    <div class="actions">
      ${canCart ? `<button class="primary" data-cart="${c.session_id}">Review &amp; add to Swiggy cart</button>`
        : !isCook && !carted && c.handoff_url ? `<a class="btn primary" href="${esc(c.handoff_url)}" target="_blank" rel="noopener" data-handoff="${c.session_id}">Order on Swiggy ↗</a>` : ""}
      <div class="two"><button data-sheet="${c.session_id}">Change</button>
        <button class="${isCook ? "primary" : ""}" data-confirm="${c.session_id}">${isCook ? "I cooked it ✓" : "I had it ✓"}</button></div>
      <div class="togs" role="group" aria-label="Quick changes">${kindToggle(c)}${pinToggle(c)}
        <button class="tg" data-sess="${c.session_id}:skipped">Skip this meal</button></div>
    </div>
    ${!isCook && swiggyReady() && !cartEligible() ? `<p class="fine">SmartPlate cannot verify your ingredient or medical rules on Swiggy's menu. Check this dish directly in Swiggy before ordering.</p>` : ""}
    ${cartNote(c)}
    ${S.handedOff === c.session_id ? `<div class="consent">Placed it on Swiggy? Tap <b>I had it</b> so your budget stays accurate.</div>` : ""}
  </section>`;
}
// One tap: order ↔ cook for this meal (the server picks the best safe option; Change picks another).
function kindToggle(c) {
  if (!["delivery", "cook"].includes(c.kind)) return "";
  return `<span class="seg2" role="group" aria-label="Order or cook"><button class="tg" data-kind="${c.session_id}:delivery" aria-pressed="${c.kind === "delivery"}">Order</button><button class="tg" data-kind="${c.session_id}:cook" aria-pressed="${c.kind === "cook"}">Cook</button></span>`;
}
function pinToggle(c) {
  if (!["delivery", "cook"].includes(c.kind)) return "";
  return `<button class="tg" data-pin="${c.session_id}" aria-pressed="${!!c.pinned}" aria-label="${c.pinned ? "Pinned: re-plans keep this meal. Tap to unpin" : "Pin this meal so re-plans keep it"}">${ICON.pin}<span>${c.pinned ? "Pinned" : "Pin"}</span></button>`;
}
function todayRest(nu) {
  if (!nu) return "";
  const day = S.view.grid[nu.day_index];
  const rest = MEALS.filter(m => day.meals[m] && m !== nu.meal && MEALS.indexOf(m) > MEALS.indexOf(nu.meal));
  if (!rest.length) return "";
  return `<section class="later"><h3 class="k">Later ${nu.when === "Today" ? "today" : esc(nu.when.toLowerCase())}</h3>
    ${rest.map(m => mealRow(day.meals[m], m, nu.day_index)).join("")}</section>`;
}
function reminderRow() {
  const push = pushState();
  let btn = "";
  if (push === "on") btn = `<button class="ghost small on" data-act="notify">Reminders on for this device</button>`;
  else if (push === "ready") btn = `<button class="ghost small" data-act="notify">Remind me at order time</button>`;
  else if (push === "ios-install") btn = `<span class="fine">On iPhone, tap Share → Add to Home Screen, then open SmartPlate from there to get reminders.</span>`;
  else if (typeof Notification !== "undefined") {
    const on = store.get("smartplate.notify") === "1" && Notification.permission === "granted";
    btn = `<button class="ghost small" data-act="notify">${on ? "Browser alerts on" : "Alert me in this browser"}</button>`;
  }
  return `<section class="remind" aria-label="Reminders"><span class="fine">Never miss an order-by time:</span>
    ${btn}
    <button class="btn ghost small" data-act="download-ics">Add to my calendar</button></section>`;
}

/* Push reminders arrive at the order-by time even when SmartPlate is closed
   (smartplate/push.py). Browsers without Web Push fall back to in-tab alerts. */
const hasPush = () => typeof navigator !== "undefined" && "serviceWorker" in navigator && typeof window !== "undefined"
  && "PushManager" in window && typeof Notification !== "undefined";
function pushState() {
  if (hasPush()) return store.get("smartplate.push") === String(S.userId) && Notification.permission === "granted" ? "on" : "ready";
  const ua = typeof navigator !== "undefined" ? navigator.userAgent || "" : "";
  const standalone = typeof navigator !== "undefined" && (navigator.standalone
    || (typeof matchMedia === "function" && matchMedia("(display-mode: standalone)").matches));
  return /iPhone|iPad|iPod/.test(ua) && !standalone ? "ios-install" : "none";
}
function keyBytes(b64) {
  const raw = atob((b64 + "=".repeat((4 - b64.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}
async function subscribePush() {
  const reg = await navigator.serviceWorker.ready;
  const st = await api(`/api/user/${S.userId}/push`);
  const old = await reg.pushManager.getSubscription();
  if (old) await old.unsubscribe().catch(() => {});        // the server key may have changed
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(st.public_key) });
  await api(`/api/user/${S.userId}/push/subscribe`, "POST", { subscription: sub.toJSON() });
  store.set("smartplate.push", String(S.userId));
}
async function syncPush() {                                 // re-register after a server reset
  if (!hasPush() || pushState() !== "on") return;
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  const st = sub && await api(`/api/user/${S.userId}/push?endpoint=${encodeURIComponent(sub.endpoint)}`);
  if (!st || !st.this_device) await subscribePush();
}

/* In-tab alerts: only while SmartPlate is open; used where Web Push isn't available. */
const alertTimers = [];
async function scheduleAlerts() {
  alertTimers.splice(0).forEach(clearTimeout);
  if (hasPush() || typeof Notification === "undefined" || Notification.permission !== "granted" || store.get("smartplate.notify") !== "1" || !S.userId) return 0;
  const due = await api(`/api/user/${S.userId}/reminders`);
  const now = Date.now();
  let n = 0;
  for (const r of due) {
    const ms = new Date(r.at).getTime() - now;
    if (ms <= 0 || ms > 24 * 3600 * 1000) continue;
    n += 1;
    alertTimers.push(setTimeout(() => {
      const note = new Notification(r.title, { body: r.body, tag: `smartplate-${r.session_id}` });
      if (r.link) note.onclick = () => window.open(r.link, "_blank", "noopener");
    }, ms));
  }
  return n;
}
async function toggleAlerts() {
  if (hasPush()) {
    if (pushState() === "on") {
      const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription();
      if (sub) { await api(`/api/user/${S.userId}/push/unsubscribe`, "POST", { endpoint: sub.endpoint }); await sub.unsubscribe().catch(() => {}); }
      store.del("smartplate.push"); toast("Reminders off for this device"); render(); return;
    }
    if (await Notification.requestPermission() !== "granted") { toast("Notifications are blocked for this site. Use the calendar instead."); return; }
    await subscribePush();
    await api(`/api/user/${S.userId}/push/test`, "POST", {}).catch(() => {});
    toast("Reminders on. You'll get a nudge at each order-by time."); render(); return;
  }
  if (store.get("smartplate.notify") === "1" && Notification.permission === "granted") {
    store.del("smartplate.notify"); alertTimers.splice(0).forEach(clearTimeout); toast("Browser alerts off"); render(); return;
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") { toast("Alerts are blocked in this browser. Use the calendar reminders instead."); return; }
  store.set("smartplate.notify", "1");
  const n = await scheduleAlerts();
  toast(n ? `${n} alert${n === 1 ? "" : "s"} set for the next 24 hours (while this tab is open)` : "Alerts on. Nothing due in the next 24 hours.");
  render();
}

function headsUp() {
  // Weather notes only when the forecast is real (Open-Meteo); the offline sample is not news.
  const hs = (S.view.heads_up || []).filter(h => h.kind !== "weather" || S.view.weather_source === "live");
  if (!hs.length) return "";
  return `<section class="heads"><h3 class="k">Heads-up</h3>${hs.map(h => `<div class="hu ${esc(h.level)}">
    <span class="ic" aria-hidden="true">${esc({ reconcile: "✓", budget: "₹", saving: "₹", holiday: "★", fast: "◐", weather: "☂", nutrition: "+" }[h.kind] || "•")}</span><div><b>${esc(h.title)}</b><p>${esc(h.body)}</p>
    ${h.kind === "reconcile" ? `<button class="link" data-tab="week">Review past meals →</button>` : ""}
    ${h.kind === "budget" ? `<button class="link" data-go="more:settings">Adjust budget →</button>` : ""}</div></div>`).join("")}</section>`;
}
function learningLine() {
  const l = S.view.learning || {};
  const src = S.view.weather_source === "live" ? "Live weather: Open-Meteo (CC BY 4.0)." : "Sample weather (offline).";
  const n = (k, one, many) => `${k || 0} ${k === 1 ? one : many}`;
  return learnedChips() + `<p class="fine center">Learning from ${n(l.favourites, "usual place", "usual places")} · ${n(l.ratings, "rating", "ratings")} · ${n(l.orders, "meal had", "meals had")}. ${src}</p>`;
}
function rateRow(c) {
  const g = c.rating_given;
  return `<div class="rate" role="group" aria-label="Rate this meal"><span>How was it?</span>
    <button class="${g === 1 ? "on" : ""}" data-rate="${c.session_id}:1" aria-pressed="${g === 1}">👍 Good</button>
    <button class="${g === -1 ? "on" : ""}" data-rate="${c.session_id}:-1" aria-pressed="${g === -1}">👎 Not again</button></div>
    <div class="reasons" role="group" aria-label="Why? (one tap)">${Object.entries(RATE_REASONS).map(([k, label]) =>
      `<button class="chip ${(c.reasons_given || []).includes(k) ? "on" : ""}" data-reason="${c.session_id}:${k}">${esc(label)}</button>`).join("")}</div>`;
}
const RATE_REASONS = { late: "Late", small: "Small portion", spicy: "Too spicy", pricey: "Too pricey", great: "Great" };
async function rateReason(sid, reason) {
  const r = await api(`/api/session/${sid}/rate`, "POST", { reasons: [reason] });
  adoptView(r.plan);
  toast({ late: "Noted — that place gets planned less", small: "Noted — that dish counts as less food",
    spicy: "Noted — less of that dish", pricey: "Noted — cheaper picks will weigh more", great: "Great — more like that" }[reason]);
  render();
}
function learnedChips() {
  const l = S.view.learned || [];
  if (!l.length) return "";
  return `<div class="learned" aria-label="What SmartPlate learned">${l.map(x => `<span class="chip">${esc(x.text)}
    <button class="x" data-unlearn="${esc(x.key)}" title="Undo" aria-label="Undo: ${esc(x.text)}">✕</button></span>`).join("")}</div>`;
}

/* ================================================================ PLAN */
// Plan = the week, cooking + groceries, the order list, and (sample profiles) usual places.
function planViews() {
  const v = [["week", "Week"], ["cook", "Cook & groceries"], ["orders", "Order list"]];
  if (!swiggyReady()) v.push(["places", "Usual places"]);
  return v;
}
function planScreen() {
  const views = planViews();
  const cur = views.some(([k]) => k === S.planView) ? S.planView : "week";
  const body = { week: weekScreen, cook: cookingPanel, orders: ordersPanel, places: placesScreen }[cur];
  return `<nav class="subnav" aria-label="Plan">${views.map(([k, l]) =>
    `<button data-plan-view="${k}" ${cur === k ? 'aria-current="page"' : ""}>${l}</button>`).join("")}</nav>${body()}`;
}
function weekScreen() {
  const v = S.view;
  const days = v.grid.map((d, i) => ({ d, i, ctx: (v.week_context || [])[i] || {} }));
  const gone = days.filter(({ d }) => Object.values(d.meals).every(m => m.status === "past" && m.kind === "past"));
  const shown = days.filter(x => !gone.includes(x));
  const modes = S.meta?.modes || {};
  const moving = S.moving ? `<div class="moving" role="status">Moving a meal: tap the meal to swap it with. <button class="ghost" data-act="cancel-move">Cancel</button></div>` : "";
  return `<div class="whead"><h1 class="greet">Week of ${esc(fmtDate(v.plan.week_start))}</h1>
      <button class="primary" data-act="reopt" title="Re-plan every meal that isn't pinned, ordered or in your cart">↻ Re-plan</button></div>
    <div class="seg" role="group" aria-label="How tight is money this week?">${Object.entries(modes).map(([k, l]) =>
      `<button class="${v.plan.mode === k ? "on" : ""}" aria-pressed="${v.plan.mode === k}" data-mode="${k}" title="${esc(S.meta.mode_outcomes?.[k] || "")}">${esc(l)}</button>`).join("")}</div>
    ${moving}
    ${sourceLine(v.source)}
    <p class="fine">Tap a meal to see options. On/Off, Order/Cook and Pin change it in one tap; drag a meal onto another (or use <b>Move</b>) to swap them. Re-plan never touches pinned, ordered or carted meals. Grey prices marked “est.” are estimates until Swiggy's cart shows the real total.</p>
    ${gone.length ? `<p class="fine gone">${esc(gone.map(x => x.d.day).join(", "))}: before this plan started.</p>` : ""}
    <div class="days">${shown.map(({ d, i, ctx }) => dayRow(d, i, ctx)).join("")}</div>
    <div class="row gap"><button data-act="newweek">Plan next week</button>
      ${S.meta.swiggy_provider === "simulated" ? `<button class="ghost" data-act="exec" title="Try SmartPlate placing every delivery for you (simulation)">Simulate auto-ordering…</button>` : ""}</div>`;
}
// What the plan is built from: live Swiggy menus, or clearly-labelled sample dishes.
function sourceLine(src) {
  if (!src) return "";
  if (src.kind === "live") return `<div class="source live" role="note"><b>${esc(src.label)}</b>
      <span class="fine">${esc(src.note)} Updated ${esc(src.fetched || "")}.</span>
      <span class="row gap"><button class="ghost small" data-act="live-menus">↻ Refresh live menus</button>
      <button class="ghost small" data-act="sample-menus">Use sample dishes</button></span></div>`;
  return `<div class="source sample" role="note"><b>${esc(src.label)}</b> <span class="fine">${esc(src.note)}</span>
      ${src.connected ? `<button class="small" data-act="live-menus">Plan from my Swiggy restaurants</button>` : connectPrompt()}</div>`;
}
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
function dayRow(day, i, ctx) {
  // Weather is shown only when it is real (Open-Meteo); the offline sample is not drawn as fact.
  const liveWx = S.view.weather_source === "live" && ctx.weather_source !== "sample";
  const wx = liveWx && ctx.temp_c ? `<span class="wx" title="${esc(ctx.weather_note || "")}${ctx.rain_prob ? " · rain " + Math.round(ctx.rain_prob) + "%" : ""}">${esc(WX_ICON[ctx.weather] || "")} ${Math.round(ctx.temp_c)}°</span>` : "";
  const fest = ctx.festival ? `<span class="tag fest" title="${esc(ctx.festival_note || "")}">${esc(ctx.festival)}${ctx.festival_approx ? "*" : ""}</span>` : "";
  const fast = ctx.your_fast ? `<span class="tag fast">${esc(ctx.your_fast)} fast</span>` : "";
  const cells = Object.values(day.meals).filter(m => ["delivery", "cook"].includes(m.kind) && m.status !== "past");
  const spend = cells.reduce((s, m) => s + (m.cost || 0), 0);
  const isToday = day.date && day.date === todayIso();
  return `<section class="day ${isToday ? "today" : ""}" aria-label="${esc(day.day)}">
    <header><b>${esc(day.day)}</b><span class="date">${esc(fmtDate(day.date))}</span>${isToday ? `<span class="tag">Today</span>` : ""}
      ${wx}${fest}${fast}<span class="spacer"></span><span class="dspend">${spend ? (cells.every(m => m.real_bill) ? real(spend, false) : est(spend)) : ""}</span></header>
    <div class="meals">${MEALS.filter(m => day.meals[m]).map(m => mealRow(day.meals[m], m, i)).join("")}</div>
  </section>`;
}
function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function mealRow(m, meal, dayIdx) {
  const locked = ["ordered", "confirmed"].includes(m.status);
  const past = m.status === "past";
  const movable = !locked && !past && !(m.kind === "cook" && !m.recipe_key && /Leftover/i.test(m.item));
  const target = S.moving && movable && S.moving !== m.session_id;
  const kindLabel = { delivery: "", cook: "cook", skip: "skip", skipped: "skipped", snoozed: "snoozed", cooked: "cooked", past: "" }[m.kind] ?? "";
  const status = m.status === "confirmed" ? `<span class="tag good">had it</span>` : m.status === "ordered" ? `<span class="tag good">ordered</span>`
    : past && ["delivery", "cook"].includes(m.kind) ? `<span class="tag warn">did you have it?</span>` : "";
  const fee = m.kind === "delivery" && m.real_bill ? " · real Swiggy bill"
    : m.kind === "delivery" && m.delivery_fee ? ` · delivery ₹${Math.round(m.delivery_fee.amount)} ${m.delivery_fee.estimated ? "estimated" : "from your bill"}` : "";
  const sub = m.kind === "delivery" ? `${esc(m.restaurant)}${fee}${m.order?.order_at && !locked && !past ? ` · order by ${esc(m.order.order_at)}` : ""}`
    : m.kind === "cook" ? (m.recipe_key ? "Home kitchen" : "From your fridge") : esc((m.reasons || [])[0] || "");
  const food = ["delivery", "cook"].includes(m.kind);
  const editable = !locked && !past;
  const offed = m.status === "skipped" || m.kind === "skipped";
  const togs = editable && (food || offed) ? `<div class="mtog" role="group" aria-label="${esc(cap1(meal))} quick changes">
      <button class="tg" data-sess="${m.session_id}:${offed ? "active" : "skipped"}" aria-pressed="${!offed}" aria-label="${esc(cap1(meal))} ${offed ? "off. Tap to plan it again" : "on. Tap to skip it"}">${offed ? "Off" : "On"}</button>
      ${food ? kindToggle(m) + pinToggle(m) : ""}</div>`
    : past && food ? `<div class="mtog"><button class="tg" data-confirm="${m.session_id}">I had it</button><button class="tg" data-sess="${m.session_id}:skipped">Skipped</button></div>` : "";
  return `<div class="mealwrap"><div class="meal ${esc(m.kind)} ${past ? "is-past" : ""} ${target ? "target" : ""} ${S.moving === m.session_id ? "lifted" : ""}"
      data-meal="${m.session_id}" ${movable ? 'draggable="true"' : ""} role="button" tabindex="0"
      aria-label="${esc(cap1(meal))}: ${esc(m.item)}${target ? ". Tap to swap here" : ""}">
    ${food ? dishIcon(m.item, { cook: m.kind === "cook" }) : `<span class="dicon" style="--h:0;opacity:.4" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M6 12h12"/></svg></span>`}
    <div class="mt"><div class="mlabel">${esc(cap1(meal))}</div><div class="mname">${esc(m.item)} ${m.pinned ? `<span class="pin" title="Your pick">${ICON.pin}</span>` : ""}${m.usual === false && m.kind === "delivery" ? '<span class="tag new">new</span>' : ""}${kindLabel ? `<span class="tag">${kindLabel}</span>` : ""}${status}</div>
      <div class="msub">${sub}</div></div>
    <span class="mcost">${food && m.cost ? cellMoney(m) : ""}</span>
  </div>${togs}</div>`;
}

/* ---- the choose sheet: a capped shortlist instead of an endless menu ---- */
function sheetDialog() {
  const d = S.sheet.data;
  if (!d) return `<div class="modal-bg" data-close-sheet="1"><section class="sheet" role="dialog" aria-modal="true" aria-label="Loading options" aria-busy="true"><p class="fine">Loading your options…</p><div class="skeleton"><i></i><i></i><i></i></div></section></div>`;
  const s = d.session, L = d.limits;
  const left = L.left_day != null ? Math.min(L.left_week, L.left_day) : L.left_week;
  const dishBtn = (x, place) => `<button class="dish ${x.fits ? "" : "over"} ${x.current ? "current" : ""}" data-pick="${x.item_id}" data-pick-name="${esc(x.name)}">
      ${dishIcon(x.name)}<span class="dn">${esc(x.name)}${place ? `<small>${esc(place)}</small>` : ""}</span>
      <span class="dp">${rupee0(x.price)}</span>
      <span class="dw">${esc(x.why)}${x.instructions?.length ? ` · will ask: “${esc(x.instructions.join("; "))}”` : ""}</span></button>`;
  const usual = d.usual.map(g => `<div class="place"><div class="ph"><b>${esc(g.restaurant)}</b><span>${Number(g.rating).toFixed(1)}★ · ~${g.eta_min} min${g.favourite ? "" : ""}</span></div>
      ${g.dishes.map(x => dishBtn(x)).join("")}${g.more ? `<p class="fine">+${g.more} more on their menu</p>` : ""}</div>`).join("");
  const hidden = [d.hidden.not_safe ? `${d.hidden.not_safe} not safe for your allergies or diet` : "",
    d.hidden.not_a_meal ? `${d.hidden.not_a_meal} treats hidden as standalone meals` : "",
    d.hidden.below_rating ? `${d.hidden.below_rating} below your ${Number(S.view.user.rating_floor).toFixed(1)}★ minimum` : "",
    d.hidden.not_again ? `${d.hidden.not_again} you said “not again” to` : ""].filter(Boolean).join(" · ");
  return `<div class="modal-bg" data-close-sheet="1"><section class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">
    <button class="close ghost" data-close-sheet="1" aria-label="Close">✕</button>
    <p class="eyebrow">${esc(s.day)} ${esc(fmtDate(s.date))} · ${rupee0(left)} left${L.left_day != null ? " today" : " this week"}</p>
    <h2 id="sheet-title">${esc(cap1(s.meal))}${d.current ? `: <span class="muted">${esc(d.current.item)}</span>` : ""}</h2>
    ${d.timing_tip ? `<p class="fine">⏱ ${esc(d.timing_tip)}</p>` : ""}
    ${moreLikeBlock(d, s)}
    <h3 class="k">${d.has_favourites ? "Your usual places" : "Good picks nearby"}</h3>
    ${usual || `<p class="fine">None of your usual places has a dish that fits. Try something new, or cook.</p>`}
    ${d.usual_more ? `<p class="fine">+${d.usual_more} more usual places. <a href="#" data-tab="places">Edit your list</a></p>` : ""}
    ${d.new.length ? `<h3 class="k">Something new</h3>${d.new.map(x => dishBtn(x, x.restaurant)).join("")}` : ""}
    ${d.cook.length ? `<h3 class="k">Cook at home</h3><div class="chips">${d.cook.map(c => `<button class="chip" data-cook="${esc(c.recipe_key)}">${esc(c.name)} · ${rupee0(c.price)}</button>`).join("")}</div>` : ""}
    ${eatersBlock(s)}
    <div class="sheet-foot">
      <button class="ghost" data-choose-auto="${s.id}">Let SmartPlate choose</button>
      <button class="ghost" data-sess="${s.id}:skipped">Skip this meal</button>
      <button class="ghost" data-move="${s.id}">Move to another day…</button>
    </div>
    ${hidden ? `<p class="fine">Hidden: ${esc(hidden)}.</p>` : ""}
  </section></div>`;
}

function eatersBlock(s) {
  const h = S.view.household;
  if (!h) return "";
  const cell = S.view.grid.flatMap(d => Object.values(d.meals)).find(m => m.session_id === s.id);
  const on = new Set(cell?.eaters || []);
  return `<h3 class="k">Who's eating</h3><div class="chips">${h.people.map(p => `<button class="${on.has(p.id) ? "on" : ""}" aria-pressed="${on.has(p.id)}" data-eater="${s.id}:${p.id}">${esc(p.name)}</button>`).join("")}</div>
    <p class="fine">Used to split the cost by who eats. Shared meals stay safe for everyone in the household either way.</p>`;
}
async function toggleEater(sid, pid) {
  const cell = S.view.grid.flatMap(d => Object.values(d.meals)).find(m => m.session_id === sid);
  const cur = new Set(cell?.eaters || []);
  if (cur.has(pid)) cur.delete(pid); else cur.add(pid);
  if (!cur.size) { toast("At least one person eats each meal. Skip the meal instead."); return; }
  adoptView(await api(`/api/session/${sid}/eaters`, "POST", { eaters: [...cur] })); render();
}
function moreLikeBlock(d, s) {
  if (!epicureOn() || d.current?.kind !== "delivery") return "";
  const sim = S.sheet.similar;
  if (!sim) return `<div class="row"><button class="ghost small" data-more-like="${s.id}">More like ${esc(d.current.item)}</button>
    <span class="fine">SmartPlate will favour similar dishes in the meals you haven't had yet.</span></div>`;
  return `<h3 class="k">Like ${esc(sim.dish)}</h3>${sim.items.length ? sim.items.map(x => `<button class="dish" data-pick="${x.item_id}" data-pick-name="${esc(x.name)}">
      ${dishIcon(x.name)}<span class="dn">${esc(x.name)}<small>${esc(x.restaurant)}</small></span><span class="dp">${rupee0(x.price)}</span>
      <span class="dw">similar flavours</span></button>`).join("")
    : `<p class="fine">No other dish nearby that fits your rules is close enough in flavour.</p>`}`;
}

/* ================================================================ PLACES */
function placesScreen() {
  if (swiggyReady()) return livePlacesScreen();
  if (!S.places) return `<p class="fine">Loading places…</p>`;
  const favs = S.places.filter(p => p.favourite), rest = S.places.filter(p => !p.favourite);
  const card = (p) => `<div class="pcard ${p.favourite ? "fav" : ""} ${p.dishes_fit ? "" : "none"}">
    <button class="star" data-fav="${p.id}" aria-pressed="${p.favourite}" aria-label="${p.favourite ? "Remove from" : "Add to"} usual places: ${esc(p.name)}">${p.favourite ? "★" : "☆"}</button>
    <div><b>${esc(p.name)}</b><div class="fine">${Number(p.rating).toFixed(1)}★ · ${esc(p.cuisines.join(", "))} · ~${p.eta_min} min</div>
    <div class="fine">${p.dishes_fit ? `${p.dishes_fit} of ${p.dishes_total} dishes fit you · ${rupee0(p.price_from)}–${rupee0(p.price_to)}` : "Nothing here fits your diet and allergies"}</div>
    ${swiggyReady() && p.favourite ? `<button class="link" data-live-menu="${esc(p.name)}">Today's Swiggy menu →</button>` : ""}</div></div>`;
  return `<h2 class="sec">Your usual places</h2>
    ${S.liveMenu ? liveMenuCard() : ""}
    <p class="fine">Plans come mostly from starred places, plus a few new ones as your variety setting allows. Swiggy's connector shares only your last ~5 orders, so SmartPlate learns your usual places from these stars and your 👍/👎 ratings.</p>
    ${S.suggestFav ? `<div class="consent">You liked a dish from <b>${esc(S.suggestFav.restaurant)}</b>. <button class="small" data-fav="${S.suggestFav.restaurant_id}">★ Add it</button></div>` : ""}
    <div class="plist">${favs.map(card).join("") || `<p class="fine">No usual places yet. Star a few below.</p>`}</div>
    <h3 class="k">Nearby (sample Chennai list)</h3><div class="plist">${rest.map(card).join("")}</div>`;
}

// Swiggy doesn't document the unit of a bare menu price; the server reads either form and
// marks inferred ones, shown as approximate. Only a truly missing price says "in cart".
function livePrice(price, estimated) {
  if (price == null) return "Price in cart";
  return estimated ? `<span class="est" title="Price unit inferred from Swiggy's menu">≈${rupee(price)}<i>est.</i></span>` : real(price);
}
// Restaurant ETA in minutes when Swiggy gave one (a number or text like "25-30 mins").
function etaMinutes(eta) {
  const m = /\d+/.exec(String(eta ?? ""));
  return m ? Number(m[0]) : null;
}
const FAST_MIN = 30;
function filterChip(key, label, extra = "") {
  return `<button class="chip" data-filter="${key}" aria-pressed="${!!S.filters[key]}" ${extra}>${label}</button>`;
}
function liveCartBlock() {
  const c = S.liveCart;
  if (!c) return "";
  // Already shown, with its bill, on Today's meal card: don't repeat it below.
  if (S.tab !== "week" && Object.values(S.carts || {}).some(r => r && r.item === c.item && r.restaurant === c.restaurant) && S.view?.next_up
      && (S.carts || {})[S.view.next_up.cell.session_id]) return "";
  return `<div class="consent cartnote" role="status"><div><b>In your Swiggy cart:</b> ${esc(c.item)} · ${esc(c.restaurant)}.
      ${c.to_pay == null ? "Check the final total in Swiggy." : `Current total ${rupee(c.to_pay)}.`}</div>
    ${c.bill ? `<div class="billcard"><p class="eyebrow">Swiggy's bill · real</p>${billLines(c.bill)}</div>` : ""}
    <div class="row">${S.swiggy.order_enabled && c.orderable !== false ? `<button class="small" data-act="review-live-checkout">Review and place order</button>` : ""}
      <a class="btn small" href="${esc(c.checkout_url)}" target="_blank" rel="noopener">Open Swiggy checkout ↗</a></div>
    ${c.other_address ? `<p class="fine">This cart is for ${esc(c.other_address.label)}, not ${esc(S.swiggy.address.label)}.${c.other_address.id ? ` <button class="small" data-swaddr="${esc(c.other_address.id)}">Deliver there instead</button>` : ""} Or clear the cart in Swiggy.</p>`
      : c.orderable === false ? `<p class="fine">This cart differs from the item reviewed here. Check or clear it in Swiggy before selecting another item.</p>` : ""}
    <p class="fine">SmartPlate never pays for you: you pay in Swiggy${S.swiggy.order_enabled ? " or confirm Cash on Delivery here" : ""}.</p></div>`;
}
function livePlacesScreen() {
  const fav = S.liveFavourites || [];
  const results = S.liveResults?.restaurants || [];
  const starred = new Set(fav.map(x => x.id));
  const fastOk = (r) => !S.filters.fast || (etaMinutes(r.eta) != null && etaMinutes(r.eta) <= FAST_MIN);
  const place = (r) => `<div class="pcard"><button class="star" data-live-fav="${esc(r.id)}" data-live-name="${esc(r.name)}"
      aria-label="${starred.has(r.id) ? "Remove" : "Add"} ${esc(r.name)} ${starred.has(r.id) ? "from" : "to"} favourites">${starred.has(r.id) ? "★" : "☆"}</button>
      <div><b>${esc(r.name)}</b><div class="fine">${r.area ? esc(String(r.area)) + " · " : ""}${r.rating ? esc(String(r.rating)) + "★ · " : ""}${r.eta ? esc(String(r.eta)) : ""}</div>
      ${r.availability ? `<div class="fine">${esc(r.availability)}</div>` : ""}
      <button class="link" data-live-place="${esc(r.id)}" data-live-name="${esc(r.name)}">See current menu →</button></div></div>`;
  const shownResults = results.filter(fastOk);
  const anyEta = results.some(r => etaMinutes(r.eta) != null);
  const sw = S.swiggy;
  return `<h2 class="sec">Order from your area</h2><p class="sub">Live Swiggy restaurants and menus for ${esc(sw.address.label)}. Choose a real item and review the cart before checkout.</p>
    ${S.liveCartError || S.liveCartErrorCode ? liveError(S.liveCartError, S.liveCartErrorCode) : ""}
    ${!S.liveCart && S.liveCartEmpty ? `<p class="fine">Your Swiggy cart for ${esc(S.liveCartEmpty.address)} is empty${S.liveCartEmpty.address_verified ? "" : " (Swiggy didn't confirm the address; check in Swiggy before ordering)"}.</p>` : ""}
    ${S.placedOrder ? `<div class="consent cartnote"><div><b>Swiggy order confirmed:</b> ${esc(S.placedOrder.order_id)} · ${esc(S.placedOrder.item)} · ${rupee(S.placedOrder.to_pay)}.</div>
      ${S.placedOrder.message ? `<p class="fine">${esc(S.placedOrder.message)}</p>` : ""}
      <button class="small" data-act="track-live-order">Check delivery status</button></div>` : ""}
    ${S.liveOrderStatus ? liveTrackingCard() : ""}
    ${liveCartBlock()}
    ${S.liveLoading ? `<section class="card livemenu" aria-busy="true"><p class="eyebrow">Opening ${esc(S.liveLoading)}…</p><div class="skeleton"><i></i><i></i><i></i></div></section>` : ""}
    ${S.liveBrowseMenu ? liveBrowseCard() : ""}
    <h3 class="k">Your live favourites</h3><div class="plist">${fav.length ? fav.map(place).join("") : `<p class="fine">Search and star real restaurants for this address.</p>`}</div>
    <form id="live-search" class="livesearch"><input id="live-query" type="text" enterkeyhint="search" aria-label="Restaurant or cuisine" placeholder="Restaurant or cuisine" maxlength="80" required><button>Search Swiggy</button></form>
    ${S.liveResults ? `<h3 class="k">Swiggy results for ${esc(S.liveResults.query)}</h3>
      ${anyEta ? `<div class="filters" role="group" aria-label="Filter restaurants">${filterChip("fast", `Fast (≤${FAST_MIN} min)`)}</div>` : ""}
      <div class="plist">${shownResults.length ? shownResults.map(place).join("") : results.length ? `<p class="fine">None of these say they deliver within ${FAST_MIN} minutes. Turn off “Fast” to see all ${results.length}.</p>` : `<p class="fine">No live restaurants returned for this address and search.</p>`}</div>` : ""}
    <div class="row"><button class="ghost small" data-act="live-order-history">Check recent Swiggy orders</button><button class="ghost small" data-act="refresh-live-cart">Refresh Swiggy cart</button></div>
    ${S.liveOrderHistory ? `<section class="card"><h3>Recent Swiggy orders</h3><p class="fine">Orders at ${esc(S.liveOrderHistory.address)}. If a placement failed or timed out, check here and in Swiggy before trying again.</p>
      ${S.liveOrderHistory.attempts.some(a => a.state === "unknown" || a.state === "started") ? `<p class="fine">A SmartPlate order attempt has an uncertain result. Check Swiggy or contact support before ordering the same cart again.</p>
      <button class="small" data-act="resolve-live-attempt">I checked Swiggy: no order was placed</button>` : ""}
      ${S.liveOrderHistory.provider_orders.length ? S.liveOrderHistory.provider_orders.map(o => `<p><b>${esc(o.restaurant)}</b> · ${esc(o.item)} · ${esc(o.status)} · ${esc(o.total)} · ${esc(o.ordered_time)}<br><span class="fine">Order ${esc(o.order_id)}</span>${S.liveOrderHistory.attempts.some(a => a.order_id === o.order_id) ? ` <button class="small" data-live-track="${esc(o.order_id)}">Check delivery status</button>` : ""}</p>`).join("") : `<p class="fine">Swiggy returned no recent orders for this address.</p>`}</section>` : ""}`;
}
// A restaurant's live menu: Swiggy's own categories, veg marks, bestsellers, real photos when
// Swiggy sends them, and allergen flags read from dish names (Swiggy menus list none).
function liveBrowseCard() {
  const menu = S.liveBrowseMenu, u = S.view.user || {};
  const mine = new Set(u.allergens || []);
  const vegUser = ["veg", "vegan"].includes(u.diet);
  const b = S.view.budget || {}, left = Math.max(0, (b.budget || 0) - (b.spend || 0));
  const f = S.filters;
  const flagged = (i) => (i.name_allergens || []).filter(a => mine.has(a));
  const keep = (i) => (!f.veg || vegUser || i.veg === true) && (!f.stock || !(i.in_stock === false || i.in_stock === 0))
    && (!f.budget || (i.price != null && i.price <= left)) && (!f.flagged || !flagged(i).length);
  const items = menu.items.filter(keep);
  const home = (i) => (i.categories || []).find(c => c !== "Recommended") || (i.categories || [])[0] || "Dishes";
  const cats = (menu.categories || []).filter(c => c !== "Recommended" && menu.items.some(i => home(i) === c));
  const cat = S.menuCat;
  const inCat = (i) => !cat || (cat === "Recommended" ? i.bestseller || (i.categories || []).includes("Recommended") : home(i) === cat);
  const groups = [];
  for (const i of items.filter(inCat)) {
    const g = home(i);
    let grp = groups.find(x => x.name === g);
    if (!grp) { grp = { name: g, items: [] }; groups.push(grp); }
    grp.items.push(i);
  }
  const order = menu.categories || [];
  groups.sort((x, y) => (order.indexOf(x.name) + 1 || 999) - (order.indexOf(y.name) + 1 || 999));
  const row = (i) => {
    const out = i.in_stock === false || i.in_stock === 0;
    const fl = flagged(i);
    return `<div class="lmrow ${out ? "unavail" : ""}" data-name="${esc(String(i.name).toLowerCase())}">
      ${dishIcon(i.name, { image: i.image })}
      <div class="lmt">${i.veg === true ? '<span class="vegmark" role="img" aria-label="Veg"></span>' : i.veg === false ? '<span class="vegmark nonveg" role="img" aria-label="Non-veg"></span>' : ""}<b>${esc(i.name)}</b>
        <div class="lmp">${livePrice(i.price, i.price_estimated)}</div>
        <div class="kv">${i.bestseller ? '<span class="tag">Bestseller</span>' : ""}${out ? '<span class="tag">unavailable</span>' : ""}${i.has_options ? '<span class="tag">options in Swiggy</span>' : ""}${fl.map(a => `<span class="tag warn">Name suggests ${esc(a.replace("_", " "))}</span>`).join("")}</div></div>
      <button class="small" data-live-item="${esc(i.id)}" data-live-item-name="${esc(i.name)}" ${out ? "disabled" : ""}>Review</button></div>`;
  };
  const hiddenByFilter = menu.items.length - items.length;
  return `<section class="card livemenu" aria-label="Swiggy menu"><button class="close ghost" data-act="close-live-browse" aria-label="Close menu">✕</button>
    <p class="eyebrow">Swiggy menu · ${esc(menu.restaurant.name)} · ${esc(menu.address)}</p><h3>${menu.search ? `${menu.items.length} dishes matching “${esc(menu.search.query)}”` : `${menu.items.length} current dishes`}</h3>
    <div class="filters" role="group" aria-label="Quick filters">${vegUser ? "" : filterChip("veg", "Veg only")}${filterChip("stock", "In stock")}${left > 0 ? filterChip("budget", `Within ${rupee0(left)} left`, `title="Your plan's estimated money left this week"`) : ""}${mine.size ? filterChip("flagged", "Hide name-flagged") : ""}</div>
    ${cats.length > 1 && !menu.search ? `<div class="filters" role="group" aria-label="Menu categories">
      <button class="chip" data-cat="" aria-pressed="${!cat}">All</button>${menu.items.some(i => i.bestseller) ? `<button class="chip" data-cat="Recommended" aria-pressed="${cat === "Recommended"}">Bestsellers</button>` : ""}${cats.map(c => `<button class="chip" data-cat="${esc(c)}" aria-pressed="${cat === c}">${esc(c)}</button>`).join("")}</div>` : ""}
    <div class="menutools">
      <label>Filter this list<input id="menu-filter" type="search" placeholder="Type to filter what's shown" autocomplete="off"></label>
      <form id="dish-search"><label>Search this restaurant<input id="dish-query" value="${esc(menu.search?.query || "")}" placeholder="Dish name" required minlength="2" maxlength="80"></label><button type="submit">Find dishes</button>${menu.search ? `<button type="button" class="ghost" data-act="restore-live-menu">Browse menu</button>` : ""}</form>
    </div>
    <p class="fine">Checked ${esc(menu.fetched)}. ${menu.truncated ? "Swiggy's menu browse returns at most 150 dishes and this restaurant has more: use <b>Find dishes</b> to search all of them. " : ""}${menu.hidden_nonveg ? `${menu.hidden_nonveg} marked non-veg dishes hidden. ` : ""}${hiddenByFilter ? `${hiddenByFilter} hidden by your filters. ` : ""}Swiggy menus don't list allergens: flags come from the dish name only, so no flag doesn't mean safe. Photos appear only where Swiggy sends one. Final price, fees, options and availability may change.</p>
    <div class="lmlist">${groups.length ? groups.map(g => `${groups.length > 1 || g.name !== "Dishes" ? `<p class="lmcat">${esc(g.name)}</p>` : ""}${g.items.map(row).join("")}`).join("")
      : `<p class="fine">No dishes returned for this restaurant and search${hiddenByFilter ? " with these filters" : ""}.</p>`}</div>
    ${menu.search?.has_more ? `<button data-act="more-live-dishes">Load more matching dishes</button>` : ""}</section>`;
}

// Everything read for one delivery address; dropped whenever the address changes.
function forgetAddressState() {
  S.swAddrs = null; S.liveMenu = null; S.liveResults = null; S.liveBrowseMenu = null; S.liveCart = null;
  S.liveCartEmpty = null; S.liveCartError = null; S.liveCartErrorCode = null; S.checkoutReview = null;
  S.placedOrder = null; S.liveOrderStatus = null; S.liveOrderHistory = null; S.liveOrderReview = null;
}
async function refreshSwiggyAddresses() {
  const r = await api(`/api/user/${S.userId}/swiggy/addresses/refresh`, "POST", {});
  S.swiggy = { ...S.swiggy, ...r.status };
  if (r.dropped) {
    forgetAddressState();
    toast(`${r.dropped} is no longer in your Swiggy account. Choose a delivery address.`);
  } else toast("Addresses refreshed from Swiggy");
  S.swAddrs = r.addresses;
  render();
}
async function searchLivePlaces(query) {
  S.liveResults = await api(`/api/user/${S.userId}/swiggy/restaurants?query=${encodeURIComponent(query)}`);
  S.liveBrowseMenu = null; render();
}
async function refreshLiveCart(show = true) {
  try {
    const r = await api(`/api/user/${S.userId}/swiggy/live-cart`);
    S.liveCart = r.cart;
    S.liveCartEmpty = r.cart ? null : { address: r.address || S.swiggy?.address?.label || "this address",
                                         address_verified: r.address_verified !== false };
    S.liveCartError = null; S.liveCartErrorCode = null;
    if (show && !r.cart) toast("Your Swiggy cart is empty");
  } catch (error) { S.liveCart = null; S.liveCartEmpty = null; S.liveCartError = error.message; S.liveCartErrorCode = error.code || null; }
  if (show) render();
}
async function resolveLiveAttempt() {
  if (!confirm("Only continue if you checked the Swiggy app and no order was placed for this attempt.")) return;
  const r = await api(`/api/user/${S.userId}/swiggy/attempts/resolve`, "POST", { confirmation: "NO_ORDER_IN_SWIGGY" });
  toast(r.message);
  await loadLiveOrderHistory();
}
async function searchLiveDishes(query, more = false) {
  const menu = S.liveBrowseMenu;
  if (!menu) throw new Error("Choose a live restaurant first.");
  const offset = more ? menu.search?.next_offset : 0;
  if (offset == null) throw new Error("There is no next dish page.");
  const result = await api(`/api/user/${S.userId}/swiggy/dishes?restaurant_id=${encodeURIComponent(menu.restaurant.id)}&restaurant_name=${encodeURIComponent(menu.restaurant.name)}&query=${encodeURIComponent(query)}&offset=${offset}`);
  const previous = more ? menu.items : [];
  const seen = new Set(previous.map(item => item.id));
  menu.items = [...previous, ...result.items.filter(item => !seen.has(item.id))];
  menu.search = { query: result.query, has_more: result.has_more, next_offset: result.next_offset };
  menu.hidden_nonveg = (more ? menu.hidden_nonveg : 0) + result.hidden_nonveg;
  menu.fetched = result.fetched; menu.truncated = result.has_more;
  S.liveOrderReview = null;
  render();
}
async function toggleLiveFavourite(id, name) {
  const r = await api(`/api/user/${S.userId}/swiggy/favourites`, "POST", { restaurant_id: id, restaurant_name: name });
  S.liveFavourites = r.restaurants; toast(r.favourite ? "Added to live favourites" : "Removed from live favourites"); render();
}
async function openLivePlace(id, name) {
  S.liveLoading = name; S.liveBrowseMenu = null; S.menuCat = null; render();
  try { S.liveBrowseMenu = await api(`/api/user/${S.userId}/swiggy/live-menu?restaurant_id=${encodeURIComponent(id)}&restaurant_name=${encodeURIComponent(name)}`); }
  finally { S.liveLoading = null; }
  render();
  document.querySelector?.(".livemenu")?.scrollIntoView?.({ block: "start" });
}
async function reviewLiveItem(id, name) {
  const r = S.liveBrowseMenu.restaurant;
  S.liveOrderReview = await api(`/api/user/${S.userId}/swiggy/live-cart/preview`, "POST",
    { restaurant_id: r.id, restaurant_name: r.name, item_id: id, item_name: name });
  render();
}
async function addLiveItemToCart() {
  const r = S.liveOrderReview;
  if (!r || r.orderable === false) { S.liveOrderReview = null; render(); return; }
  try {
    S.liveCart = await api(`/api/user/${S.userId}/swiggy/live-cart`, "POST", {
      restaurant_id: r.restaurant_id, restaurant_name: r.restaurant, item_id: r.item_id,
      item_name: r.item, expected_fingerprint: r.fingerprint });
    S.placedOrder = null;
    S.liveOrderStatus = null;
    S.liveCartError = null;
  } finally { S.liveOrderReview = null; }
  render();
}
async function reviewLiveCheckout() {
  S.checkoutReview = await api(`/api/user/${S.userId}/swiggy/checkout/preview`);
  render();
}
async function placeLiveOrder() {
  const review = S.checkoutReview;
  S.checkoutReview = null;
  try {
    S.placedOrder = await api(`/api/user/${S.userId}/swiggy/checkout`, "POST",
      { expected_fingerprint: review.fingerprint });
    S.liveCart = null;
    S.liveOrderHistory = null;
    render();
  } catch (error) {
    S.liveOrderHistory = null;
    render();
    throw error;
  }
}
async function loadLiveOrderHistory() {
  S.liveOrderHistory = await api(`/api/user/${S.userId}/swiggy/order-history`);
  render();
}
function liveTrackingCard() {
  const t = S.liveOrderStatus.tracking || {};
  return `<section class="card"><p class="eyebrow">Order ${esc(S.liveOrderStatus.order_id)}</p>
    <h3>${esc(t.title || t.status || "Check delivery in Swiggy")}</h3>
    <p>${esc(t.subtitle || t.message || "Swiggy did not return a current delivery update.")}</p>
    ${t.eta ? `<p>${esc(t.eta)}</p>` : ""}</section>`;
}
async function trackLiveOrder(orderId = S.placedOrder?.order_id) {
  if (!orderId) throw new Error("Choose a recorded order to track.");
  S.liveOrderStatus = await api(`/api/user/${S.userId}/swiggy/orders/${encodeURIComponent(orderId)}`);
  render();
}
function checkoutReviewDialog() {
  const r = S.checkoutReview;
  return `<div class="modal-bg" data-close-checkout-review="1"><section class="checkout" role="dialog" aria-modal="true" aria-labelledby="checkout-review-title">
    <button class="close ghost" data-close-checkout-review="1" aria-label="Close order review">✕</button>
    <p class="eyebrow">Real Swiggy order</p><h2 class="sec" id="checkout-review-title">Place this order now?</h2>
    <div class="checkout-list"><div class="checkout-row"><div><strong>${esc(r.item)}</strong><span>${esc(r.restaurant)} · Quantity ${esc(r.quantity)} · ${esc(r.address)}</span></div><b>${rupee(r.to_pay)}</b></div></div>
    <p class="fine">Payment: ${esc(r.payment_label)}. This places a real order to the address shown and you may owe the full amount. Check ingredients with the restaurant if needed. An uncertain result will not be retried automatically.</p>
    <div class="checkout-actions"><button class="ghost" data-close-checkout-review="1">Cancel</button><button class="primary" data-act="place-live-order" autofocus>Confirm and place order · ${rupee(r.to_pay)}</button></div>
  </section></div>`;
}
function liveOrderReviewDialog() {
  const r = S.liveOrderReview;
  return `<div class="modal-bg" data-close-live-review="1"><section class="checkout" role="dialog" aria-modal="true" aria-labelledby="live-review-title">
    <button class="close ghost" data-close-live-review="1" aria-label="Close live item review">✕</button>
    <p class="eyebrow">Fresh Swiggy item</p><h2 class="sec" id="live-review-title">${r.orderable === false ? "Review this dish" : "Add this exact item?"}</h2>
    <div class="checkout-list"><div class="checkout-row"><div class="rowmain">${dishIcon(r.item, { image: r.image })}<div><strong>${esc(r.item)}</strong><span>${esc(r.restaurant)} · Delivering to ${esc(r.address)}</span></div></div>
      <b>${r.menu_price == null ? "Verify price in cart" : livePrice(r.menu_price, r.price_estimated)}</b></div></div>
    ${r.price_estimated ? `<p class="fine">≈ Menu price as read from Swiggy; the cart shows the exact payable total with fees.</p>` : ""}
    ${r.orderable === false ? `<p class="fine" role="note">${esc(r.reason)}</p>
    <div class="checkout-actions"><button class="ghost" data-close-live-review="1">Close</button><a class="btn primary" href="${esc(r.handoff_url)}" target="_blank" rel="noopener">Open in Swiggy ↗</a></div>` : `
    <p class="fine">SmartPlate cannot verify all ingredients or cross-contact. The cart will show the current payable total. Check the details before placing an order.</p>
    <div class="checkout-actions"><button class="ghost" data-close-live-review="1">Cancel</button><button class="primary" data-act="confirm-live-cart" autofocus>Add to Swiggy cart</button></div>`}
  </section></div>`;
}

/* Live Swiggy menus (integrations/swiggy_live.py): only once signed in with an address. */
const swiggyReady = () => !!(S.swiggy && S.swiggy.connected && S.swiggy.address);
const cartEligible = () => !S.view?.user?.allergens?.length && !S.view?.user?.medical?.length && S.view?.user?.diet !== "vegan";
function liveMenuCard() {
  const m = S.liveMenu;
  const rows = m.items.map(i => `<div class="lmrow"><span>${i.veg === true ? "🟢 " : i.veg === false ? "🔴 " : ""}${esc(i.name)}</span><b>${i.price != null ? (i.price_estimated ? "≈" : "") + rupee0(i.price) : "–"}</b></div>`).join("");
  return `<section class="card livemenu" aria-label="Swiggy menu"><button class="close ghost" data-act="close-live-menu" aria-label="Close menu">✕</button>
    <p class="eyebrow">Live on Swiggy · ${esc(m.swiggy.name)}${m.swiggy.eta ? " · " + esc(String(m.swiggy.eta)) : ""}</p>
    <h3>${m.items.length} dishes from Swiggy</h3>
    <p class="fine">Menu checked ${esc(m.fetched || "recently")}${m.cached ? " (cached)" : ""}. A dash means Swiggy did not provide an unambiguous price. Prices and availability can change before checkout.${m.hidden_nonveg ? ` ${m.hidden_nonveg} non-veg dish${m.hidden_nonveg === 1 ? "" : "es"} hidden.` : ""}${m.diet === "vegan" ? " Swiggy's veg mark doesn't mean vegan." : ""} Swiggy menus don't list allergens, so check with the restaurant if it matters.</p>
    <div class="lmlist">${rows}</div></section>`;
}
async function openLiveMenu(name) {
  S.liveMenu = await api(`/api/user/${S.userId}/swiggy/menu?restaurant=${encodeURIComponent(name)}`);
  render(); if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
}
async function planFromLiveMenus() {
  adoptView(await api(`/api/plan/${S.planId}/live-menus`, "POST", {})); toast("Planned from your live Swiggy menus"); render();
}
async function checkQueueCart(sid) {
  const p = await api(`/api/session/${sid}/swiggy-cart/preview`);
  const c = await api(`/api/session/${sid}/order/cart`, "POST", { expected_fingerprint: p.fingerprint });
  S.cartBudget = { ...S.cartBudget, [sid]: c.budget };
  toast(c.budget?.over ? `In your Swiggy cart: ${rupee(c.to_pay)}. That's over this week's budget.` : `In your Swiggy cart: ${rupee(c.to_pay)} to pay`);
  S.orderQueue = await api(`/api/plan/${S.planId}/order-queue`); render();
}
async function placeQueued(sid, overBudgetOk) {
  const body = overBudgetOk ? { over_budget_ok: true } : {};
  if (S.orderQueue?.order_enabled) body.expected_fingerprint = (await api(`/api/user/${S.userId}/swiggy/checkout/preview`)).fingerprint;
  let r;
  try { r = await api(`/api/session/${sid}/order/place`, "POST", body); }
  catch (e) {
    if (e.code === "over_budget") { S.cartBudget = { ...S.cartBudget, [sid]: e.data.budget }; render(); return; }
    throw e;
  }
  S.cartBudget = { ...S.cartBudget, [sid]: null };
  if (r.mode === "tap_to_place") window.open?.(r.url, "_blank");
  toast(r.mode === "placed" ? "Order placed" : r.message);
  S.orderQueue = await api(`/api/plan/${S.planId}/order-queue`); render();
}
async function replanRemaining(sid) {
  const r = await api(`/api/plan/${S.planId}/replan-remaining`, "POST", { session_id: sid });
  adoptView(r.plan); S.orderQueue = r.queue; S.cartBudget = { ...S.cartBudget, [sid]: r.budget };
  if (S.carts?.[sid]) S.carts[sid] = { ...S.carts[sid], budget: r.budget };
  toast(r.budget.over ? `Re-planned. Still ${rupee(r.budget.over_by)} over: this cart alone is more than the money left.`
    : `Re-planned the rest of the week around this cart. ${rupee(r.budget.left)} left.`);
  render();
}
async function reviewCart(sid) {
  S.cartReview = await api(`/api/session/${sid}/swiggy-cart/preview`);
  render();
}
async function fillCart() {
  const review = S.cartReview;
  let r;
  try {
    r = await api(`/api/session/${review.session_id}/swiggy-cart`, "POST",
      { expected_fingerprint: review.fingerprint });
  } catch (err) {
    S.cartReview = null;
    throw err;
  }
  S.cartReview = null;
  const sid = review.session_id;
  S.carts = { ...(S.carts || {}), [sid]: r };
  // The same cart, shown once in Today's live section too (not a stale "empty").
  S.liveCartEmpty = null; S.liveCartError = null; S.liveCartErrorCode = null;
  S.liveCart = { item: r.item, restaurant: r.restaurant, to_pay: r.to_pay, bill: r.bill, orderable: true, checkout_url: r.checkout_url };
  render();
}
function cartReviewDialog() {
  const r = S.cartReview;
  return `<div class="modal-bg" data-close-cart-review="1"><section class="checkout" role="dialog" aria-modal="true" aria-labelledby="cart-review-title">
    <button class="close ghost" data-close-cart-review="1" aria-label="Close live item review">✕</button>
    <p class="eyebrow">Fresh Swiggy item · review before cart</p>
    <h2 class="sec" id="cart-review-title">Add this exact item?</h2>
    <div class="checkout-list"><div class="checkout-row"><div class="rowmain">${dishIcon(r.item, { image: r.image })}<div><strong>${esc(r.item)}</strong><span>${esc(r.restaurant)} · ${esc(r.address)}</span></div></div>
      <b>${r.menu_price == null ? "Price to verify" : r.price_estimated ? livePrice(r.menu_price, true) : rupee(r.menu_price)}</b></div></div>
    <p class="fine">Your plan estimated ${est(r.planned_cost, true)}. Swiggy's final payable total, including fees and taxes, appears after the cart is updated. Check ingredients and the final total in Swiggy before paying.</p>
    <div class="checkout-actions"><button class="ghost" data-close-cart-review="1">Cancel</button><button class="primary" data-act="confirm-cart" autofocus>Add to Swiggy cart</button></div>
  </section></div>`;
}
function cartNote(c) {
  const r = (S.carts || {})[c.session_id];
  if (!r) return "";
  const diff = r.over_plan == null ? "" : r.over_plan > 0 ? ` · ${rupee0(r.over_plan)} more than planned` : " · within plan";
  const warn = r.budget?.over && !r.budgetOk;
  return `<div class="consent cartnote" role="status"><div><b>In your Swiggy cart:</b> ${esc(r.item)} · ${esc(r.restaurant)}.</div>
    ${r.bill ? `<div class="billcard"><p class="eyebrow">Swiggy's bill · real</p>${billLines(r.bill)}</div>`
      : r.to_pay != null ? `<div>To pay <b>${rupee(r.to_pay)}</b> (Swiggy's total).</div>` : "<div>Open Swiggy to see the total.</div>"}
    ${r.planned_cost != null && r.to_pay != null ? `<p class="fine">Your plan estimated ${est(r.planned_cost)}${diff}. The real total now counts in your week.</p>` : ""}
    ${warn ? budgetWarn(c.session_id, r.budget)
      : `<a class="btn primary" href="${esc(r.checkout_url)}" target="_blank" rel="noopener" data-handoff="${c.session_id}">Open Swiggy to review and pay ↗</a>`}
    <p class="fine">SmartPlate never pays for you: you pay in Swiggy.</p></div>`;
}
// The real bill takes the week over budget: say by how much, and let the user choose.
function budgetWarn(sid, b) {
  return `<div class="overbudget" role="alert"><div><b>Over budget.</b> ${esc(b.message)}</div>
    <div class="two"><button data-over-ok="${sid}">Approve anyway</button>
    <button data-replan="${sid}">Re-plan the remaining meals</button></div>
    <p class="fine">Both are fine: you decide. Re-plan keeps ordered, pinned and carted meals and changes the rest.</p></div>`;
}

/* ================================================================ MORE */
/* ================================================================ YOU */
// Grouped: what happened (money, food), how SmartPlate plans for you, and your account.
function youGroups() {
  const sw = S.swiggy;
  const swState = !sw ? "" : sw.connected ? (sw.address ? `Connected · ${sw.address.label}` : "Connected · choose an address") : sw.expired ? "Sign-in expired" : "Not connected";
  return [
    ["Your week", [["recap", "This week", "What you spent and ate, against your plan"], ["receipts", "Expenses", "Real Swiggy bills and estimates; CSV export"],
      ["insights", "Nutrition & insights", "Planned daily averages vs targets (estimates)"]]],
    ["How SmartPlate plans", [["settings", "Settings", "Food rules, budget, meals, goal"], ["connection", "Swiggy connection", swState || "What's live and what's not"],
      ["household", "Household", "People you cook and order for"], ["calendar", "Coming up", "Holidays, festivals, your fasts"]]],
    ["Account", [["profiles", "Profiles & your data", "Switch, sign in anywhere, download or delete"], ["community", "Community weeks", "Plans others shared"]]],
  ];
}
function moreScreen() {
  const credits = `<a class="mitem" href="/credits"><b>Credits &amp; licences</b><span>Data and models SmartPlate uses</span></a>`;
  if (!S.more) {
    return `<h1 class="greet">You</h1>${youGroups().map(([g, items], gi) => `<section class="mgroup" aria-label="${esc(g)}"><h3 class="k">${esc(g)}</h3><div class="mlist">${items.map(([k, t, d]) =>
      `<button class="mitem" data-go="more:${k}"><b>${t}</b><span>${esc(d)}</span></button>`).join("")}${gi === 2 ? credits : ""}</div></section>`).join("")}`;
  }
  if (S.more === "orders" || S.more === "cooking") { S.tab = "week"; S.planView = S.more === "orders" ? "orders" : "cook"; S.more = null; return planScreen(); }
  const back = `<button class="ghost small back" data-go="more:">← You</button>`;
  const body = { settings: settingsPanel, calendar: calendarPanel, insights, household: householdPanel, recap: recapPanel,
    receipts: receiptsPanel, community: communityPanel, connection: connectionPanel, profiles: profilesPanel }[S.more];
  return back + (body ? body() : "");
}

function calendarPanel() {
  const rows = S.calendar;
  if (!rows) return `<p class="fine">Loading…</p>`;
  return `<h2 class="sec">Coming up</h2><p class="sub">Holidays change delivery demand and opening hours. Fasts appear only if you keep them (Settings → Fasts you keep).</p>
    <div class="card">${rows.length ? rows.map(r => `<div class="calrow"><b>${esc(r.when)}</b><span>${esc(r.name)}${r.approx ? " <small>(date may shift a day)</small>" : ""}</span><span class="fine">${esc(r.note)}</span></div>`).join("")
      : `<p class="fine">Nothing in the next 60 days.</p>`}</div>`;
}

function profilesPanel() {
  const opts = S.users.map(u => `<button class="mitem ${u.id === S.userId ? "on" : ""}" data-user="${u.id}"><b>${esc(u.name)}${u.private ? " 🔒" : ""}</b><span>${u.private ? "Private to this browser" : "Sample · open to anyone"}${u.id === S.userId ? " · current" : ""}</span></button>`).join("");
  const k = keys.get(S.userId);
  const recovery = k ? `<div class="card"><h3 class="k">Recovery code</h3>
      <p class="sub">This profile is private. Its key is stored only in this browser. To open it on another device, or after clearing your browser, you need this code. Keep it somewhere safe.</p>
      <div class="row" style="align-items:center"><code class="rcode">${esc(`${S.userId}.${k}`)}</code><button class="small" data-act="copy-recovery">Copy</button></div>
      <details><summary>Replace a shared or lost code</summary><p>A new code invalidates all earlier recovery codes and signs out every other device. Save the new code after replacing it. Change your password too if someone else knows it.</p><button data-act="rotate-recovery">Generate a new recovery code</button></details></div>` : "";
  return `<h2 class="sec">Profiles</h2><div class="mlist">${opts}</div>
    <button class="primary" data-act="start-onboard">+ Set up a new profile</button>
    ${accountCard()}
    ${recovery}
    ${k ? `<section class="card"><h3 class="k">Your data</h3>
      <p>Download your saved SmartPlate profile, plans, favourites and order records. Authentication secrets are excluded.</p>
      <button data-act="download-profile">Download my data</button>
      <details><summary>Delete my SmartPlate profile</summary><p>This permanently removes your profile, sign-in, device access and saved records from this app. Your Swiggy account and existing orders remain active. Download your data first if you want a copy.</p>
      <form id="delete-profile"><label>Type DELETE to confirm<input id="delete-confirmation" autocomplete="off" required pattern="DELETE"></label><button class="ghost" type="submit">Permanently delete this profile</button></form></details></section>` : ""}
    <div class="card"><h3 class="k">Open a profile from another device</h3>
      <form id="recover" class="row" style="align-items:center"><input id="rcode" type="text" placeholder="Paste recovery code" style="flex:1;min-width:200px" autocomplete="off"><button type="submit">Open</button></form></div>`;
}
/* Sign in anywhere: an optional name + password for a private profile (accounts.py). */
function accountCard() {
  if (!keys.get(S.userId)) return `<div class="card"><h3 class="k">Sign in anywhere</h3>
    <p class="sub">Sample profiles are shared by everyone, so they can't have a sign-in. Set up your own profile to add one.</p></div>`;
  const a = S.account;
  if (!a) return "";
  const devs = a.devices.map(d => `<div class="devrow"><b>${esc(d.label)}${d.this_device ? " · this device" : ""}</b>
    <span class="fine">Last used ${esc(fmtDate(d.last_seen.slice(0, 10)))}</span>
    <button class="small ghost" data-rm-device="${d.id}">Sign out</button></div>`).join("");
  return `<div class="card"><h3 class="k">Sign in anywhere</h3>
    <p class="sub">${a.login ? `Sign in as <b>${esc(a.login)}</b> on any phone or computer to open this profile. There's no email: if you forget the password, open the profile here or with its recovery code and set a new one.`
      : "Choose a name and password to open this profile on your other devices. No email needed."}</p>
    <form id="account" class="settings-grid">
      <label>Sign-in name<input id="acct-login" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" value="${esc(S.acctDraft ?? a.login ?? "")}" required minlength="3" maxlength="64"></label>
      <label>${a.login ? "New password" : "Password"}<input id="acct-pw" type="password" autocomplete="new-password" required minlength="8" maxlength="200"></label>
      <button type="submit">${a.login ? "Change password" : "Save sign-in"}</button></form>
    ${devs ? `<h3 class="k">Signed-in devices</h3>${devs}` : ""}
    <button class="ghost small" data-act="sign-out">${a.devices.some(d => d.this_device) ? "Sign out of this device" : "Forget this profile on this browser"}</button></div>`;
}
async function saveAccount() {
  const login = document.getElementById("acct-login").value, password = document.getElementById("acct-pw").value;
  const had = S.account?.login;
  S.acctDraft = login;                                       // kept if the save fails
  S.account = await api(`/api/user/${S.userId}/account`, "POST", { login, password });
  S.acctDraft = null;
  toast(had ? "Password changed" : "Sign-in saved. Use it on your other devices."); render();
}
async function signIn(form) {
  const login = form.querySelector("#si-login").value, password = form.querySelector("#si-pw").value;
  S.signinDraft = login;
  const device = /iPhone|iPad|Android|Mobile/i.test(navigator.userAgent || "") ? "Phone" : "Computer";
  const r = await api("/api/signin", "POST", { login, password, device });
  keys.put(r.user_id, r.key, r.name);
  S.users = mergeUsers(await api("/api/users")); S.signin = false;
  await switchUser(r.user_id); toast(`Signed in. Hello, ${r.name.split(" ")[0]}.`);
}
async function signOut() {
    await api(`/api/user/${S.userId}/signout`, "POST", {});
    keys.drop(S.userId); store.del("smartplate.user");
  clearCurrentProfile();
  S.users = mergeUsers(await api("/api/users")); S.welcome = true; render(); toast("Signed out of this device");
}
function clearCurrentProfile() {
  for (const key of ["userId", "planId", "view", "account", "more", "exec", "receipts", "drawer", "sheet",
    "moving", "places", "calendar", "orderReview", "cartReview", "swiggy", "swAddrs", "liveMenu", "carts",
    "liveResults", "liveFavourites", "liveBrowseMenu", "liveOrderReview", "liveCart", "liveCartError", "liveCartEmpty",
    "checkoutReview", "placedOrder", "liveOrderStatus", "liveOrderHistory", "acctDraft", "onboard"])
    S[key] = null;
  S.tab = "today"; S.welcome = true;
}
async function deleteProfile(form) {
  const confirmation = form.querySelector("#delete-confirmation").value;
  await api(`/api/user/${S.userId}`, "DELETE", { confirmation });
  keys.drop(S.userId); store.del("smartplate.user");
  clearCurrentProfile();
  S.users = mergeUsers(await api("/api/users"));
  render(); toast("Your SmartPlate profile was deleted.");
}
async function rotateRecoveryCode() {
  const probe = "smartplate.storage-check";
  const marker = `${Date.now()}-${Math.random()}`;
  store.set(probe, marker);
  if (store.get(probe) !== marker) throw new Error("Enable browser storage before replacing your recovery code.");
  store.del(probe);
  const result = await api(`/api/user/${S.userId}/account/rotate-key`, "POST", { confirmation: "ROTATE" });
  keys.put(result.user_id, result.key, result.name);
  S.checkoutReview = null;
  S.account = await api(`/api/user/${S.userId}/account`);
  render(); toast("New recovery code saved here. Copy it somewhere safe.");
}
async function removeDevice(id) {
  const mine = S.account?.devices.find(d => d.id === Number(id))?.this_device;
  if (mine) return signOut();
  S.account = await api(`/api/user/${S.userId}/devices/${id}/remove`, "POST", {});
  toast("Signed out that device"); render();
}
async function useRecoveryCode(code) {
  const m = /^\s*(\d+)\.([A-Za-z0-9_-]{16,})\s*$/.exec(code || "");
  if (!m) throw new Error("That doesn't look like a recovery code (it looks like 12.AbC…)");
  const id = Number(m[1]), key = m[2];
  const prev = keys.all()[id];
  keys.put(id, key, prev?.name);
  try {
    const r = await fetch(`/api/user/${id}/plan`, { headers: { "X-SmartPlate-Key": key } });
    if (!r.ok) throw new Error(r.status === 401 ? "That code doesn't match a profile here" : "Profile not found");
    const view = await r.json();
    keys.put(id, key, view.user.name);
  } catch (e) { if (prev) keys.put(id, prev.key, prev.name); else keys.drop(id); throw e; }
  S.users = mergeUsers(await api("/api/users"));
  await switchUser(id); toast("Profile opened on this device");
}

/* Explicit checkout hand-off: planning is reversible, ordering is not. */
function orderReviewDialog() {
  const review = S.orderReview;
  const rows = review.items;
  return `<div class="modal-bg" data-close-review="1">
    <section class="checkout" role="dialog" aria-modal="true" aria-labelledby="checkout-title">
      <button class="close ghost" data-close-review="1" aria-label="Close order review">✕</button>
      <p class="eyebrow">Final review · ${rows.length} scheduled deliveries</p>
      <h2 class="sec" id="checkout-title">Approve before anything is ordered</h2>
      <p class="sub">Try SmartPlate ordering these meals for you. This simulation runs now. It does not schedule or send real deliveries.</p>
      <div class="checkout-list">${rows.map(m => `<div class="checkout-row">
        <div><strong>${esc(S.view.grid[m.day]?.day || "Scheduled")} · ${esc(m.meal)}</strong><span>${esc(m.item)} · ${esc(m.restaurant || "Restaurant pending")}</span></div>
        <b>${rupee(m.amount)}</b>
      </div>`).join("")}</div>
      <div class="checkout-total"><span>Maximum approved total</span><strong>${rupee(review.total)}</strong></div>
      <p class="fine">Exact amounts, including estimated fees to the paisa; the week view rounds them to the nearest rupee. Meals whose time has passed, home-cooked and skipped meals are not ordered.</p>
      <div class="consent"><span aria-hidden="true">🛡</span><span><strong>Substitution limits.</strong> A replacement must pass your filters and cost no more than the reviewed price for that meal. Otherwise it is left unordered.</span></div>
      ${S.meta.swiggy_provider === "simulated" ? `<div class="demo-warning"><strong>Demo mode:</strong> confirming creates simulated orders only. No payment or restaurant order will occur.</div>` : ""}
      <div class="checkout-actions"><button class="ghost" data-close-review="1">Keep editing</button><button class="primary" data-act="confirm-exec" autofocus>Simulate ${rows.length} orders · ${rupee(review.total)}</button></div>
    </section></div>`;
}

/* ---- insights ---- */
function insights() {
  const v = S.view, N = v.nutrition;
  const macro = (k, unit) => {
    const cur = N.daily_avg[k] || 0, tgt = N.daily_target[k] || 1;
    const pct = Math.min(140, (cur / tgt) * 100);
    return `<div class="nbar"><span>${k.replace("_g", "")}</span>
      <div class="track"><i class="${pct > 115 ? "over" : ""}" style="width:${Math.min(100, pct)}%"></i></div>
      <span class="mono">${Math.round(cur)} / ${Math.round(tgt)}${unit}</span></div>`;
  };
  const working = (v.user.prefs?.targets_working || []).map(w => `<li>${esc(w)}</li>`).join("");
  const wctx = (v.week_context || []).map(d => `<tr><td>${d.day}</td><td>${WX_ICON[d.weather] || ""} ${esc(d.weather_note)}${d.weather_source === "live" ? "" : " <small>(sample)</small>"}</td>
    <td>${d.festival ? esc(d.festival) : "—"}</td></tr>`).join("");
  const hh = v.household ? `<div class="card"><h3 class="k">Household · ${esc(v.household.name)}</h3>
    <div class="sub">Shared meals meet every member's rules.</div>
    <div class="kv"><span class="tag">members: ${v.household.members.map(esc).join(", ")}</span>
      ${v.household.merged_allergens.map(a => `<span class="tag warn">no ${esc(a)}</span>`).join("")}</div>
    <button class="ghost small" data-go="more:household">Manage household</button></div>` : "";
  return `<h2 class="sec">Nutrition & insights</h2><div class="grid-cards" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">
    <div class="card"><h3 class="k">Planned meals · average per day (${N.days || 7} days)</h3>
      ${macro("kcal", "")}${macro("protein_g", "g")}${macro("carbs_g", "g")}${macro("fat_g", "g")}${macro("sugar_g", "g")}
      ${working ? `<ul class="fine working">${working}</ul>` : ""}
      <div class="callout">Covers only the meals SmartPlate plans. Targets are general wellness estimates, not medical advice.</div></div>
    <div class="card"><h3 class="k">Weather & calendar this week</h3>
      <table><tr><th>day</th><th>weather</th><th>holiday</th></tr>${wctx}</table></div>
    <div class="card"><h3 class="k">Carbon estimate</h3>
      <div class="val serif" style="font-size:34px">${v.carbon.total_kg}<small style="font-size:15px;color:var(--muted)"> kg CO₂e</small></div>
      <div class="sub">Rough estimate for the week (band: ${v.carbon.band}).</div></div>
    ${hh}</div>`;
}

/* ---- orders / substitution / idempotency ---- */
/* ---- order any meal / the whole week: pick slots and days, then cart check → approve → place ---- */
const DAY3 = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function billLines(bill) {
  if (!bill) return `<p class="fine">Swiggy didn't return an itemised bill; the total is what Swiggy shows.</p>`;
  return `<table class="bill">${bill.lines.map(l => `<tr><td>${esc(l.label)}</td><td class="num real">${rupee(l.amount)}</td></tr>`).join("")}
    <tr class="total"><td><b>To pay</b></td><td class="num"><b>${rupee(bill.to_pay)}</b></td></tr></table>`;
}
function orderQueuePanel() {
  const q = S.orderQueue;
  if (!q) return "";
  const queued = new Set(q.meals.filter(m => m.queued).map(m => `${m.meal}:${m.day_index}`));
  const slots = ["breakfast", "lunch", "dinner"].filter(meal => q.meals.some(m => m.meal === meal));
  const pickedMeals = slots.filter(meal => q.meals.some(m => m.queued && m.meal === meal));
  const pickedDays = [...new Set(q.meals.filter(m => m.queued).map(m => m.day_index))];
  const step = m => m.state === "placed" ? `<span class="tag good">placed</span>`
    : m.state === "handed_off" ? `<span class="tag good">in Swiggy</span>`
    : m.state === "cart_ready" && S.cartBudget[m.session_id]?.over ? `<span class="row gap"><b>${rupee(m.to_pay)}</b></span>${budgetWarn(m.session_id, S.cartBudget[m.session_id])}`
    : m.state === "cart_ready" ? `<span class="row gap"><b>${rupee(m.to_pay)}</b>
        <button class="small primary" data-oq-place="${m.session_id}">${q.order_enabled ? `Approve ${rupee(m.to_pay)} and place` : "Cart ready — tap to place in Swiggy"}</button></span>`
    : m.queued ? `<button class="small" data-oq-cart="${m.session_id}">Check the real cart</button>` : "";
  const rows = q.meals.filter(m => m.queued).map(m => `<div class="oq-row"><div><b>${DAY3[m.day_index]} ${esc(m.meal)}</b> · ${esc(m.item)}
      <span class="fine">${esc(m.restaurant || "")} · planned ${rupee0(m.planned_cost)}${m.order_at ? ` · order by ${esc(m.order_at)}` : ""}</span></div>
      ${step(m)}${m.bill && m.state === "cart_ready" ? billLines(m.bill) : ""}</div>`).join("");
  return `<section class="card oq" aria-label="Order this week"><h2 class="sec">Order this week</h2>
    <p class="sub">Pick the meals and days to order. Each one is checked in your real Swiggy cart (with delivery, fees and GST) and needs your approval of that exact total.</p>
    <fieldset class="row gap"><legend class="fine">Meals</legend>${slots.map(meal => `<label><input type="checkbox" name="oq-meal" value="${meal}" ${pickedMeals.includes(meal) ? "checked" : ""}> ${cap1(meal)}</label>`).join("")}</fieldset>
    <fieldset class="row gap"><legend class="fine">Days</legend>${DAY3.map((d, i) => `<label><input type="checkbox" name="oq-day" value="${i}" ${pickedDays.includes(i) ? "checked" : ""}> ${d}</label>`).join("")}</fieldset>
    <button data-act="oq-save">Add to my order list</button>
    ${rows ? `<div class="oq-list">${rows}</div><p class="fine">${q.queued} meal${q.queued === 1 ? "" : "s"} · planned ${rupee0(q.planned_total)}${q.confirmed_total ? ` · checked in Swiggy ${rupee(q.confirmed_total)}` : ""}</p>` : ""}
    <p class="fine">${esc(q.scheduling.why)}</p></section>`;
}
function ordersPanel() {
  return orderQueuePanel() + ordersPanelBody();
}
function ordersPanelBody() {
  if (S.meta.swiggy_provider !== "simulated") return `<h2 class="sec">Real Swiggy orders</h2>
    <p class="sub">The sample weekly planner cannot schedule or place real orders. Choose a restaurant and exact item for your saved address in Places, then review its live cart.</p>
    <button data-go="places">Open live Places (Today → Order from your area)</button>`;
  const head = `<h2 class="sec">Auto-ordering (simulation)</h2>
    <div class="sub">This shows the future "SmartPlate orders for me" mode. You approve a maximum total. If a dish is unavailable, it is replaced only with one that passes your filters and costs no more. No restaurant receives these orders.</div>
    <div class="row" style="margin:12px 0"><button data-act="exec">Review simulated orders</button></div>`;
  if (!S.exec?.attempted) return head + `<div class="card empty">No simulated orders yet.</div>`;
  const e = S.exec;
  const rows = e.results.map(r => `<tr>
    <td>${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][r.day]} ${r.meal}</td>
    <td>${esc(r.item)}<br><span class="mono" style="font-size:11px;color:var(--muted)">${esc(r.idempotency_key)}</span></td>
    <td>${r.substituted ? `<span class="b sub">substituted</span>` : (r.placed ? 'simulated' : 'not ordered')}</td>
    <td>${esc(r.provider_order_id || "—")}</td>
    <td>${esc(r.state)}</td></tr>`).join("");
  return head + `<div class="stats">
      <div class="stat"><div class="label">Placed</div><div class="val">${e.placed}<small>/${e.attempted}</small></div></div>
      <div class="stat"><div class="label">Substituted</div><div class="val">${e.substituted}</div></div>
      <div class="stat"><div class="label">Failed</div><div class="val">${e.failed}</div></div>
    </div>
    <div class="card scroll-x"><table><tr><th>meal</th><th>item / idempotency key</th><th>outcome</th><th>order id</th><th>state</th></tr>${rows}</table></div>`;
}

/* ---- cooking coach ---- */
function cookingPanel() {
  const c = S.view.coach;
  const ing = (i) => i.swap
    ? `<span class="tag swapped">${esc(i.swap.name)} <small>for ${esc(i.name)}</small> <button class="x" data-unswap="${esc(i.token)}" aria-label="Use ${esc(i.name)} again">✕</button></span>`
    : (i.swappable ? `<button class="tag" data-swap-ing="${esc(i.token)}" title="Swap ${esc(i.name)}">${esc(i.name)} ⇄</button>` : `<span class="tag">${esc(i.name)}</span>`);
  const recipes = c.recipes.map(r => `<div class="card"><h3 class="k">${esc(r.session)} · ${rupee(r.cost)}${r.servings > 1 ? ` a serving · ${r.servings} eating` : ""}</h3>
    <div style="font-weight:600;margin-bottom:6px">${esc(r.name)}</div>
    ${r.ingredients?.length ? `<div class="kv ingredients">${r.ingredients.map(ing).join("")}</div>` : ""}
    <ol style="margin-left:18px">${r.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol></div>`).join("") || `<div class="card empty">No cook days this week. Set how often you cook in Settings.</div>`;
  const unit = (b) => b.unit === "g" ? (b.need >= 1000 ? `${(b.need / 1000).toFixed(1).replace(/\.0$/, "")} kg` : `${b.need} g`) : `${b.need} ${b.unit}`;
  const label = (b) => b.swap ? `<s>${esc(b.name)}</s> → <b>${esc(b.swap.name)}</b>${b.swap.reason === "out_of_stock" ? " <small>(out of stock)</small>" : ""}`
    : (b.have ? `<s>${esc(b.name)}</s>` : esc(b.name));
  const basket = c.basket.items.map(b => `<tr class="${b.have ? "have" : ""}"><td>${b.qty > 1 ? `${b.qty} × ` : ""}${label(b)}
      <small class="fine">needs ${esc(unit(b))} for ${b.servings} serving${b.servings === 1 ? "" : "s"}</small></td><td>${esc(b.recipe)}</td>
    <td>${b.have ? "—" : rupee(b.price)}</td>
    <td><label class="check have-it"><input type="checkbox" data-have="${esc(b.name)}" ${b.have ? "checked" : ""}> Have it</label>
      ${b.swap ? `<button class="ghost small" data-unswap="${esc(b.token)}">Undo</button>`
        : (b.swappable && !b.have ? `<button class="ghost small" data-oos="${esc(b.token)}">Out of stock?</button>` : "")}</td></tr>`).join("");
  const swapped = c.basket.items.some(b => b.swap);
  const safe = (c.safe_with_swap || []).map(f => `<li><b>${esc(f.name)}</b>: ${f.swaps.map(x => `use ${esc(x.to_name)} instead of ${esc(x.from_name)}`).join(", ")}</li>`).join("");
  return `<h2 class="sec">Cooking & groceries</h2><p class="sub">${esc(c.headline)}</p>
    ${S.swapPick ? swapPicker() : ""}
    <div class="row"><div style="flex:2;min-width:280px"><div class="grid-cards">${recipes}</div>
      ${safe ? `<div class="card"><h3 class="k">Also safe with a swap</h3><ul class="fine">${safe}</ul>
        <p class="fine">These recipes are left out of your plan as written. With these swaps nobody's allergies or diet are broken.</p></div>` : ""}</div>
      <div style="flex:1;min-width:240px"><div class="card"><h3 class="k">Grocery list · ${rupee(c.basket.total)}</h3>
        <table><tr><th>item</th><th>for</th><th>₹</th><th></th></tr>${basket || `<tr><td class="empty" colspan="4">Nothing left to buy for this week's cooking.</td></tr>`}</table>
        <p class="fine">For the cook meals still ahead${S.view.household ? ", for everyone eating each one" : ""}, rounded up to whole packs. Tick what you already have.</p>
        ${swapped ? `<p class="fine">Prices are for the original items. Check the swap's price in the shop.</p>` : ""}</div></div>
    </div>`;
}
function swapPicker() {
  const p = S.swapPick, d = p.data;
  const what = d?.name || p.token.replace(/_/g, " ");
  const head = p.reason === "out_of_stock" ? `${esc(what)} is out of stock. Use instead:` : `Instead of ${esc(what)}, use:`;
  if (!d) return `<div class="card"><p class="fine">Finding swaps…</p></div>`;
  return `<div class="card swap-pick" role="group" aria-label="Swap ${esc(what)}"><h3 class="k">${head}</h3>
    ${d.options.length ? `<div class="chips">${d.options.map(o => `<button data-swap-to="${esc(o.token)}">${esc(o.name)}</button>`).join("")}</div>`
      : `<p class="fine">No swap that does the same job is safe for everyone eating.</p>`}
    <p class="fine">Only ingredients that fit everyone's allergies and diet are shown${d.hidden_unsafe ? ` (${d.hidden_unsafe} left out)` : ""}. Closest in flavour first.</p>
    <button class="ghost small" data-act="swap-cancel">Cancel</button></div>`;
}

/* ---- household: the people you cook and order for ---- */
const ALLERGY_LIST = ['peanut', 'dairy', 'gluten', 'egg', 'soy', 'shellfish', 'fish', 'sesame', 'tree_nut'];
const DIET_NAME = { veg: "Vegetarian", vegan: "Vegan", nonveg: "Non-vegetarian" };
function personForm(id, m = {}) {
  const meals = S.view.user.meals || MEALS;
  const box = (name, v, on, label) => `<label class="check"><input type="checkbox" name="${name}" value="${v}" ${on ? "checked" : ""}>${esc(label)}</label>`;
  return `<form id="${id}" class="card preferences">
    <div class="settings-grid"><label>Name<input name="name" maxlength="40" value="${esc(m.name || "")}" required></label>
    <label>Diet<select name="diet">${Object.entries(DIET_NAME).map(([k, v]) => `<option value="${k}" ${(m.diet || "nonveg") === k ? "selected" : ""}>${v}</option>`).join("")}</select></label></div>
    <fieldset><legend>Allergies (always excluded from shared meals)</legend><div class="checks">${ALLERGY_LIST.map(a => box("allergens", a, (m.allergens || []).includes(a), cap1(a.replace("_", " ")))).join("")}</div></fieldset>
    <fieldset><legend>Medical filters</legend><div class="checks">${["diabetes", "hypertension", "celiac"].map(c => box("medical", c, (m.medical || []).includes(c), cap1(c))).join("")}</div></fieldset>
    <fieldset><legend>Usually eats with you</legend><div class="checks">${meals.map(x => box("meals", x, (m.meals || meals).includes(x), cap1(x))).join("")}</div></fieldset>
    <div class="row"><button type="submit" class="primary">${m.id ? "Save" : "Add to household"}</button>
      ${m.id ? `<button type="button" class="ghost" data-act="hh-edit-cancel">Cancel</button>` : ""}</div></form>`;
}
function readPerson(form) {
  const f = new FormData(form);
  return { name: f.get("name"), diet: f.get("diet"), allergens: f.getAll("allergens"), medical: f.getAll("medical"), meals: f.getAll("meals") };
}
function householdPanel() {
  const h = S.view.household;
  if (!h) {
    if (!keys.get(S.userId)) return `<h2 class="sec">Household</h2><div class="card"><p>Sample profiles are shared by every visitor. <a href="#" data-act="start-onboard">Set up your own profile</a> to plan for a household.</p></div>`;
    return `<h2 class="sec">Household</h2><p class="sub">Cooking or ordering for more than you? Add the people you share meals with. Every shared meal will meet all their allergies and diets, and you can split the cost.</p>
      <form id="hh-create" class="card row" style="align-items:flex-end"><label style="flex:1">Household name<input name="name" maxlength="40" value="Home" required></label>
        <button type="submit" class="primary">Create household</button></form>`;
  }
  const r = h.rules;
  const rule = (x, label) => `<span class="tag warn">${esc(label)} · ${esc(x.who.join(", "))}</span>`;
  const rules = [...r.allergens.map(x => rule(x, `No ${x.rule.replace("_", " ")}`)), ...r.medical.map(x => rule(x, cap1(x.rule))),
    ...(r.diet.rule !== "nonveg" ? [rule(r.diet, DIET_NAME[r.diet.rule])] : [])].join("");
  const people = h.people.map(p => S.hhEdit === p.id ? personForm("hh-edit", p) : `<div class="card person"><div class="row" style="align-items:center">
      <div style="flex:1"><b>${esc(p.name)}</b>${p.you ? " <small>(you)</small>" : ""}
        <div class="sub">${DIET_NAME[p.diet] || esc(p.diet)}${p.allergens.length ? ` · no ${p.allergens.map(a => esc(a.replace("_", " "))).join(", ")}` : ""}${p.medical.length ? ` · ${p.medical.map(esc).join(", ")}` : ""} · eats ${p.meals.map(esc).join(", ") || "no planned meals"}</div></div>
      ${p.managed ? `<button class="ghost small" data-hh-edit="${p.id}">Edit</button><button class="ghost small" data-hh-remove="${p.id}" data-hh-name="${esc(p.name)}">Remove</button>`
        : (p.you ? `<button class="ghost small" data-go="more:settings">Your settings</button>` : `<span class="fine">Has their own profile</span>`)}</div></div>`).join("");
  const shares = h.split.map(x => `<tr><td>${esc(x.member)}</td><td class="mono">${rupee(x.share)}</td></tr>`).join("");
  return `<h2 class="sec">Household · ${esc(h.name)}</h2>
    <div class="card"><h3 class="k">Shared meals meet everyone's rules</h3>
      <div class="kv">${rules || `<span class="fine">Nobody here has an allergy, medical filter or diet limit.</span>`}</div>
      <p class="fine">Planned restaurant dishes and recipes that break any of these are never picked for the household.</p></div>
    <h3 class="k">People</h3>${people}
    ${S.hhEdit ? "" : `<details class="card"><summary>Add someone you cook or order for</summary>${personForm("hh-add")}</details>`}
    <div class="card"><h3 class="k">Splitting this week's planned spend</h3>
      <label>Split costs<select id="hh-split"><option value="even" ${h.split_method === "even" ? "selected" : ""}>Evenly</option>
        <option value="by_consumption" ${h.split_method === "by_consumption" ? "selected" : ""}>By who eats each meal</option></select></label>
      <table><tr><th>person</th><th>share</th></tr>${shares}</table>
      <p class="fine">${h.split_method === "by_consumption" ? "Each meal's cost is divided among the people eating it: those who usually eat that meal, or whoever you tick in the meal's sheet." : "The week's planned spend divided equally."} Planned estimate, ${rupee(S.view.budget.spend)} in total.</p></div>
    <details class="card"><summary>Stop sharing</summary><p>People without their own profile are removed. Your plan goes back to your own rules only.</p>
      <button class="ghost" data-act="hh-leave">Stop sharing this household</button></details>`;
}
async function householdCall(path, method, body, msg) {
  adoptView(await api(`/api/user/${S.userId}/household${path}`, method, body));
  S.hhEdit = null; toast(msg); render();
}

/* ---- weekly recap ---- */
function recapPanel() {
  const r = S.recap;
  if (!r) return `<h2 class="sec">This week</h2><p class="fine">Loading…</p>`;
  const s = r.spend, m = r.meals, n = r.nutrition;
  const pct = (a, b) => b ? Math.round(100 * a / b) : 0;
  const vsLast = s.last_week != null ? `<div class="sub">Last week you spent ${rupee0(s.last_week)}.</div>` : "";
  const nut = n.days_logged ? `<div class="nbar"><span>kcal</span><div class="track"><i style="width:${Math.min(100, pct(n.avg.kcal, n.target.kcal))}%"></i></div><span class="mono">${Math.round(n.avg.kcal)} / ${Math.round(n.target.kcal)}</span></div>
      <div class="nbar"><span>protein</span><div class="track"><i style="width:${Math.min(100, pct(n.avg.protein_g, n.target.protein_g))}%"></i></div><span class="mono">${Math.round(n.avg.protein_g)} / ${Math.round(n.target.protein_g)}g</span></div>
      <p class="fine">Average per day on the ${n.days_logged} day${n.days_logged === 1 ? "" : "s"} you logged meals. Protein target met on ${n.protein_days}.</p>`
    : `<p class="fine">No meals logged yet this week. Tap “I had it” on a meal, or log what you ate, and it shows here.</p>`;
  const split = r.household ? `<div class="card"><h3 class="k">${esc(r.household.name)} · who owes what so far</h3>
      <table>${r.household.split.map(x => `<tr><td>${esc(x.member)}</td><td class="mono">${rupee(x.share)}</td></tr>`).join("")}</table>
      <p class="fine">${r.household.split_method === "by_consumption" ? "By who ate each meal you marked as had." : "Spent so far, split evenly."}</p></div>` : "";
  return `<h2 class="sec">This week</h2><p class="sub">From what actually happened: meals you marked as had or ordered.</p>
    <div class="stats">
      <div class="stat"><div class="label">Spent</div><div class="val">${rupee0(s.spent)}<small>/${rupee0(s.budget)}</small></div></div>
      <div class="stat"><div class="label">Still planned</div><div class="val">${rupee0(s.planned)}</div></div>
      <div class="stat"><div class="label">${s.left < 0 ? "Over" : "Left"}</div><div class="val">${rupee0(Math.abs(s.left))}</div></div></div>
    <div class="grid-cards" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr))">
    <div class="card"><h3 class="k">Meals</h3>
      <p>${m.had} had (${m.ordered} ordered, ${m.cooked} cooked) · ${m.ahead} still ahead · ${m.skipped} skipped</p>
      ${m.unmarked ? `<p class="fine">${m.unmarked} past meal${m.unmarked === 1 ? " isn't" : "s aren't"} marked. They count as neither spent nor skipped until you mark them.</p>` : ""}
      ${r.top_place ? `<p class="fine">Most ordered from: ${esc(r.top_place.name)} (${r.top_place.times}×)</p>` : ""}
      <p class="fine">Spent on delivery ${rupee0(s.delivery)}, on cooking ${rupee0(s.cooked)}.</p>${vsLast}</div>
    <div class="card"><h3 class="k">Nutrition</h3>${nut}<div class="callout">General wellness estimates, not medical advice.</div></div>
    ${split}</div>`;
}

/* ---- community ---- */
function communityPanel() {
  const rows = S.community.map(t => `<div class="card"><div class="row" style="align-items:center">
    <div style="flex:1"><div style="font-weight:600">${esc(t.title)}</div>
      <div class="sub">by ${esc(t.author)} · ${esc(t.city)} · ${esc(t.mode)} · ${rupee(t.budget)}/wk · ${t.adopts} adopts</div></div>
    <button data-adopt="${t.id}">Adopt</button></div></div>`).join("") || `<div class="card empty">No templates yet. Nobody has shared a week here so far. Share yours to be the first.</div>`;
  return `<h2 class="sec">Community weeks</h2>
    <div class="sub">Weeks people shared in this trial. “Adopt” records interest. It does not replace your plan.</div>
    <div class="row" style="margin-bottom:12px"><button class="primary" data-act="savetpl">Share my current week</button></div>
    <div class="grid-cards">${rows}</div>`;
}

/* ---- receipts ---- */
function receiptsPanel() {
  const r = S.receipts;
  const rows = r && r.rows.length ? r.rows.map(x => `<tr><td>${esc(x.iso_date)}</td>
    <td>${esc(x.note)}</td><td><span class="tag ${x.category === "business" ? "amb" : ""}">${esc(x.category)}</span></td>
    <td>${x.real ? `${real(x.amount)} <span class="tag good">real bill</span>` : est(x.amount, true)}</td></tr>`).join("") : "";
  return `<h2 class="sec">Expenses</h2>
    <div class="sub">Meals you confirmed and simulated orders. A real Swiggy bill is solid; anything else is the plan's estimate. These are not tax invoices. Check the business/personal suggestions before you use them.</div>
    <div class="row" style="margin-bottom:12px"><button class="primary" data-act="genrcpt">Update from this week</button>
      <button class="btn ghost" data-act="download-csv">Export CSV ↓</button></div>
    ${r ? `<div class="stats"><div class="stat"><div class="label">Total</div><div class="val">${rupee(r.total)}</div></div>
      <div class="stat"><div class="label">Business</div><div class="val">${rupee(r.business_total)}</div></div>
      <div class="stat"><div class="label">From real Swiggy bills</div><div class="val">${rupee(r.rows.filter(x => x.real).reduce((t, x) => t + x.amount, 0))}</div></div></div>
      <div class="card scroll-x"><table><tr><th>date</th><th>item</th><th>category</th><th>amount</th></tr>${rows || `<tr><td class="empty" colspan="4">No expenses yet. Confirm a meal with “I had it”.</td></tr>`}</table></div>` : `<div class="card empty">Loading…</div>`}`;
}

/* ---- settings: the same five answers as onboarding, editable ---- */
const RHYTHM = [["order", "I order it"], ["cook", "I cook it"], ["skip", "I skip it"]];
function rhythmOf(u) {
  const p = u.prefs || {}, meals = u.meals || p.meals || MEALS;
  return p.rhythm || Object.fromEntries(MEALS.map(m => [m, meals.includes(m) ? "order" : "skip"]));
}
function settingsPanel() { return settingsPanelBody() + tuningForm(); }
// Your rhythm, calorie split, targets and variety: the planner's assumptions, yours to change.
function tuningForm() {
  const u = S.view.user, nt = u.nutrition_targets || {}, ht = u.health_targets || {}, r = rhythmOf(u);
  const share = { breakfast: 0.25, lunch: 0.4, dinner: 0.35, ...(nt.meal_share || {}) };
  const pct = m => Math.round(share[m] * 100);
  const t = S.view.nutrition?.daily_target || {};
  return `<form id="tuning" class="card tuning"><h3>Rhythm & targets</h3><h3 class="k">How you eat</h3>
    <div class="settings-grid">${MEALS.map(m => `<label>${cap1(m)}<select name="rh_${m}">${RHYTHM.map(([k, l]) =>
      `<option value="${k}" ${r[m] === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>`).join("")}</div>
    <h3 class="k">Planner settings</h3>
    <div class="settings-grid">${MEALS.map(m => `<label>${cap1(m)} share of calories (%)<input name="sh_${m}" type="number" min="10" max="70" value="${pct(m)}"></label>`).join("")}
      <label>Calories a day<input name="kcal" type="number" min="1000" max="4500" value="${nt.kcal ?? t.kcal ?? ""}"></label>
      <label>Protein a day (g)<input name="protein_g" type="number" min="20" max="250" value="${nt.protein_g ?? t.protein_g ?? ""}"></label>
      <label>Same dish at most (times a week)<input name="max_repeat" type="number" min="1" max="7" value="${ht.max_item_repeat ?? 2}"></label></div>
    <p class="fine">The split must add up to 100%. Want the same lunch every day? Set "same dish" to 5 or more.</p>
    <button class="primary">Save how I eat</button></form>`;
}
function readTuning(form) {
  const f = new FormData(form), n = k => Number(f.get(k));
  const rhythm = Object.fromEntries(MEALS.map(m => [m, f.get(`rh_${m}`)]));
  const meal_share = Object.fromEntries(MEALS.map(m => [m, n(`sh_${m}`) / 100]));
  const tuning = { meal_share, max_repeat: n("max_repeat") };
  if (f.get("kcal")) tuning.kcal = n("kcal");
  if (f.get("protein_g")) tuning.protein_g = n("protein_g");
  return { rhythm, tuning };
}
function settingsPanelBody() {
  const u = S.view.user, n = S.view.nutrition.daily_target, p = u.prefs || {};
  const meals = u.meals || MEALS;
  const body = p.body || {};
  const number = (key, label, value, min, max, step = 1, extra = "") => `<label>${label}<input name="${key}" type="number" inputmode="decimal" min="${min}" max="${max}" step="${step}" value="${value ?? ""}" ${extra}></label>`;
  const checks = (key, values, labels = {}) => values.map(v => `<label class="check"><input type="checkbox" name="${key}" value="${v}" ${(u[key] || []).includes(v) ? 'checked' : ''}>${esc(labels[v] || v.replace('_', ' '))}</label>`).join('');
  const mealChecks = MEALS.map(m => `<label class="check"><input type="checkbox" name="meals" value="${m}" ${meals.includes(m) ? "checked" : ""}>${cap1(m)}</label>`).join("");
  const opt = (name, cur, pairs) => `<select name="${name}">${pairs.map(([k, v]) => `<option value="${k}" ${cur === k ? "selected" : ""}>${v}</option>`).join("")}</select>`;
  return `<h2 class="sec">Settings</h2><p class="sub">Every setting here changes your plan. Saving re-plans the meals that haven't happened yet; meals you've ordered or had stay as they are. Your delivery address is in the address pill at the top.</p>
    <form id="preferences" class="card preferences">
      <div class="settings-grid"><label>Name<input name="name" maxlength="80" value="${esc(u.name)}" required></label></div>
      <fieldset><legend>Food rules (always applied)</legend><div class="settings-grid">
      <label>Diet${opt("diet", u.diet, [['nonveg', 'Non-vegetarian'], ['veg', 'Vegetarian'], ['vegan', 'Vegan']])}</label></div></fieldset>
      <fieldset><legend>Allergies (always excluded)</legend><div class="checks">${checks('allergens', ['peanut', 'dairy', 'gluten', 'egg', 'soy', 'shellfish', 'fish', 'sesame', 'tree_nut'])}</div></fieldset>
      <fieldset><legend>Medical filters</legend><div class="checks">${checks('medical', ['diabetes', 'hypertension', 'celiac'])}</div><p class="sub">Simple menu-label rules. They can't guarantee a restaurant dish is medically suitable or free of cross-contact.</p></fieldset>
      <fieldset><legend>Fasts you keep (optional)</legend><div class="checks">${checks('observances', ['navratri', 'ramadan', 'karva_chauth'], { navratri: "Navratri", ramadan: "Ramadan", karva_chauth: "Karva Chauth" })}</div><p class="sub">On those days only dinner is planned. We never assume this from your name or area.</p></fieldset>
      <fieldset><legend>Money</legend><div class="settings-grid">
      ${number('weekly_budget', 'Weekly food budget (₹)', u.weekly_budget, 100, 100000)}
      ${number('daily_cap', 'Daily limit (₹, optional)', p.daily_cap || "", 0, 20000, 1, 'placeholder="none"')}</div></fieldset>
      <fieldset><legend>What to plan</legend><div class="checks">${mealChecks}</div><div class="settings-grid" style="margin-top:10px">
      <label>How often you cook${opt("cook", p.cook || (u.health_targets?.max_cook_per_week ? "sometimes" : "never"), [["never", "Never"], ["sometimes", "1–2 times a week"], ["often", "3–5 times a week"], ["most", "Most days"]])}</label>
      <label>Variety${opt("variety", u.nutrition_targets?.variety || "light", [["usual", "Just my usuals"], ["light", "Mostly usual, a little new"], ["mixed", "Half new"], ["adventurous", "Surprise me often"]])}</label>
      ${number('rating_floor', 'Minimum restaurant rating', u.rating_floor, 0, 5, 0.1)}
      <label>Goal${opt("goal", p.goal || "none", [["none", "No goal"], ["protein", "More protein"], ["lose", "Lose weight gently"], ["maintain", "Maintain weight"], ["gain", "Build muscle"]])}</label></div></fieldset>
      ${tasteFieldset(p)}
      <details ${body.weight_kg ? "open" : ""}><summary>Personalise nutrition (optional)</summary><div class="settings-grid">
        ${number('weight_kg', 'Weight (kg)', body.weight_kg, 30, 300, 0.1)}${number('height_cm', 'Height (cm)', body.height_cm, 120, 230)}
        ${number('age', 'Age', body.age, 18, 100)}
        <label>Sex (for the formula)${opt("sex", body.sex || "unspecified", [["unspecified", "Prefer not to say"], ["female", "Female"], ["male", "Male"]])}</label>
        <label>Activity${opt("activity", body.activity || "light", [["sedentary", "Mostly sitting"], ["light", "Light (walks)"], ["moderate", "Moderate (3–5 workouts)"], ["active", "Active (daily training)"], ["athlete", "Athlete"]])}</label></div>
        <p class="sub">Current target: ${Math.round(n.kcal)} kcal, ${Math.round(n.protein_g)} g protein a day. Mifflin–St Jeor estimate for adults. Not medical advice.</p></details>
      <button type="submit" class="primary">Save &amp; re-plan</button>
    </form>
    <div class="card"><h3 class="k">Tell SmartPlate in words</h3><div class="row" style="align-items:center">
      <input type="text" id="cmd" placeholder="e.g. 'skip friday dinner'" style="flex:1;min-width:200px"><button data-act="cmd">Send</button></div>
      <div class="qbar"><span class="qchip" data-cmd="skip friday dinner">Skip Fri dinner</span><span class="qchip" data-cmd="switch to survival">Tight week</span></div></div>`;
}
function tasteFieldset(p) {
  if (!epicureOn()) return "";
  const t = p.cuisine_tilt || {};
  const cuisines = Object.entries(S.meta.epicure.cuisines || {});
  const likes = (p.more_like || []).map(m => `<span class="tag">${esc(m.name)} <button type="button" class="x" data-forget-like="${esc(m.name)}" aria-label="Stop favouring dishes like ${esc(m.name)}">✕</button></span>`).join("");
  return `<fieldset><legend>Taste</legend><div class="settings-grid">
      <label>Lean toward a cuisine<select name="tilt_cuisine"><option value="">No lean</option>${cuisines.map(([k, v]) => `<option value="${k}" ${t.cuisine === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
      <label>How much<select name="tilt_strength"><option value="light" ${t.strength !== "strong" ? "selected" : ""}>A little</option><option value="strong" ${t.strength === "strong" ? "selected" : ""}>A lot</option></select></label></div>
      <p class="sub">${likes ? `More like: ${likes}` : "Tap “More like …” on a planned dish to get more dishes like it."}</p></fieldset>`;
}
function readSettings(form) {
  const f = new FormData(form);
  const body = { name: f.get("name"), diet: f.get("diet"), weekly_budget: Number(f.get("weekly_budget")),
    rating_floor: Number(f.get("rating_floor")), cook: f.get("cook"), variety: f.get("variety"), goal: f.get("goal"),
    meals: f.getAll("meals"), allergens: f.getAll("allergens"), medical: f.getAll("medical"), observances: f.getAll("observances") };
  const cap = Number(f.get("daily_cap"));
  body.daily_cap = cap > 0 ? cap : null;
  if (f.has("tilt_cuisine")) body.cuisine_tilt = f.get("tilt_cuisine") ? { cuisine: f.get("tilt_cuisine"), strength: f.get("tilt_strength") || "light" } : null;
  const w = f.get("weight_kg"), h = f.get("height_cm"), a = f.get("age");
  if (w && h && a) body.body = { weight_kg: Number(w), height_cm: Number(h), age: Number(a), sex: f.get("sex"), activity: f.get("activity") };
  return body;
}

function connectionPanel() {
  const sw = S.swiggy;
  const notListed = `<p class="fine" data-address-help>Address not listed? Add it in the Swiggy app (Account → Addresses), then <button class="small ghost" data-act="swiggy-refresh-addresses">Refresh addresses</button></p>`;
  const addr = sw && sw.connected ? (S.swAddrs ? `<p class="fine">Choose the address SmartPlate uses for your cart, live menus and plans.</p><div class="mlist">${S.swAddrs.map(a => `<button class="mitem ${sw.address?.id === a.id ? "on" : ""}" data-swaddr="${esc(a.id)}"><b>${esc(a.label)}</b><span>${esc(a.text)}</span>${sw.address?.id === a.id ? `<span class="tag good">SmartPlate delivers here</span>` : ""}</button>`).join("")}</div>${notListed}`
      : sw.address ? `<p>Delivering to <b>${esc(sw.address.label)}</b> <button class="small ghost" data-act="swiggy-addresses">Change</button></p>${notListed}`
      : `<p><button class="primary" data-act="swiggy-addresses">Choose delivery address</button></p><p class="fine">Needed for live menus and your cart.</p>${notListed}`) : "";
  const live = !sw ? `<p class="fine">Checking Swiggy connection…</p>` : sw.connected ? `
    <div class="card"><span class="tag good">Connected</span>
      <h3>Signed in to Swiggy${sw.server?.name ? ` · ${esc(sw.server.name)}` : ""}</h3>
      ${addr}
      <p class="sub">With an address set, <b>Today</b> and <b>Places</b> search real Swiggy restaurants for that address. Star your favourites, choose an exact live item, and review the cart. ${sw.order_enabled ? "Where Swiggy offers Cash on Delivery, you can confirm and place a real order here, then track it." : "Real order placement here is awaiting Swiggy approval and durable storage; complete checkout in Swiggy for now."}</p>
      <p class="fine">Protocol ${esc(sw.protocol_version || "?")} · ${sw.tools.length} tools (${sw.read_tools} read, ${sw.write_tools} write)${sw.expires ? ` · sign-in expires ${esc(sw.expires.slice(0, 16).replace("T", " "))}` : ""}.</p>
      <details><summary>What Swiggy offers this account</summary>${sw.tools.map(t => `<div class="calrow"><b>${esc(t.name)}</b><span class="tag ${t.kind === "write" ? "warn" : ""}">${t.kind}</span><span class="fine">${esc(t.description)}</span></div>`).join("")}</details>
      <div class="row gap"><button data-act="swiggy-discover">Refresh tools</button><button class="ghost" data-act="swiggy-disconnect">Disconnect</button></div></div>`
    : sw.requires_private_profile ? `<div class="card"><h3>Use your own private profile</h3><p>Sample profiles are shared by every visitor. Create or sign in to a private SmartPlate profile to keep your Swiggy account and delivery addresses private.</p><button class="primary" data-act="start-onboard">Create my profile</button><button class="ghost" data-act="signin-open">Sign in</button></div>`
    : `<div class="card"><span class="tag">${sw.needs_reconnect ? "Reconnect required" : sw.expired ? "Sign-in expired" : "Not connected"}</span>
      <h3>Connect your Swiggy account</h3>
      ${sw.needs_reconnect ? `<p class="sub">This server can no longer read the saved Swiggy sign-in. Connect again.</p>` : ""}
      <p class="sub">You sign in on Swiggy's own page (phone + OTP). SmartPlate can then search real restaurants and menus for your saved address, remember favourites, and let you review a live item before adding it to your cart. Swiggy sign-ins last about 5 days.</p>
      <button class="primary" data-act="swiggy-connect">Connect Swiggy</button>
      <p class="fine">Swiggy requires production access and an exact-match allowlisted HTTPS redirect. ${sw.callback_url ? `For this deployment, request <code>${esc(sw.callback_url)}</code> from Swiggy Builders Club. ` : ""}A Render URL is an HTTPS redirect; it still needs Swiggy approval. ${swiggySignInOpen() ? "" : "Swiggy has not approved this server's callback yet, so sign-in is expected to fail here. "}This connection has only been tested against a fake server.</p></div>`;
  return `<h2 class="sec">Swiggy connection</h2>${live}<div class="card"><span class="tag">Hand-off mode</span>
    <h3>The public deployment uses Swiggy checkout until real placement is approved.</h3>
    <p><b>Today:</b> “Order on Swiggy” opens Swiggy's search for that restaurant and dish. You check the real price there and order. Then tap “I had it” so your budget and nutrition stay accurate.</p>
    <p><b>Signed in:</b> Review the exact live dish and confirm before SmartPlate adds it to your cart. For allergies, medical rules, vegan diets, uncertain stock, or dishes needing options, choose the dish directly in Swiggy. Check the final total and pay there. Scheduled orders stay off until Swiggy's terms clearly allow them.</p>
    <p class="sub">Swiggy's Food tools cover addresses, restaurant and menu search, cart, payment, orders and tracking. The available tools can change; inspect the discovered list after connecting. SmartPlate keeps its own record of your usual places.</p>
    <a href="https://mcp.swiggy.com/builders/docs/start/authenticate/" target="_blank" rel="noopener">Swiggy sign-in documentation ↗</a>
    </div>`;
}

/* ================================================================ ONBOARDING */
const OB_STEPS = ["What you eat", "Your meals", "Budget", "Goal"];
function startOnboard() {
  S.onboard = { step: 0,
    d: { name: "", diet: "nonveg", allergens: [], medical: [], observances: [], meals: ["lunch", "dinner"], cook: "sometimes",
      rhythm: { breakfast: "skip", lunch: "order", dinner: "order" },
      favourites: [], weekly_budget: null, daily_cap: null, goal: "none", body: null } };
  S.welcome = false; S.sheet = null; render();
}
function chip(group, value, label, on, multi = true) {
  return `<button type="button" class="chip ${on ? "on" : ""}" data-ob="${group}" data-val="${esc(value)}" data-multi="${multi ? 1 : 0}" aria-pressed="${on}">${label}</button>`;
}
function onboardingScreen() {
  const o = S.onboard, d = o.d, st = o.step;
  const dots = OB_STEPS.map((t, i) => `<span class="${i === st ? "on" : i < st ? "done" : ""}" title="${t}"></span>`).join("");
  let body = "";
  if (st === 0) {
    body = `<h1>What do you eat?</h1>
      <div class="chips">${[["veg", "Vegetarian"], ["nonveg", "Non-vegetarian"], ["vegan", "Vegan"]].map(([k, l]) => chip("diet", k, l, d.diet === k, false)).join("")}</div>
      <h3 class="k">Anything you must avoid?</h3><p class="fine">These exclusions filter the sample planner. Swiggy menus cannot verify all ingredients or cross-contact; confirm with the restaurant before a real order.</p>
      <div class="chips">${["peanut", "dairy", "gluten", "egg", "soy", "shellfish", "fish", "sesame", "tree_nut"].map(a => chip("allergens", a, cap1(a.replace("_", " ")), d.allergens.includes(a))).join("")}</div>
      <details><summary>Medical needs or fasts (optional)</summary>
        <div class="chips">${[["diabetes", "Diabetes (low sugar)"], ["hypertension", "Blood pressure (less salt)"], ["celiac", "Celiac"]].map(([k, l]) => chip("medical", k, l, d.medical.includes(k))).join("")}</div>
        <p class="fine">Fasts you keep. On those days we plan dinner only:</p>
        <div class="chips">${[["navratri", "Navratri"], ["ramadan", "Ramadan"], ["karva_chauth", "Karva Chauth"]].map(([k, l]) => chip("observances", k, l, d.observances.includes(k))).join("")}</div>
      </details>`;
  } else if (st === 1) {
    body = `<h1>How do you eat on a normal day?</h1><p class="fine">For each meal: do you order it, cook it, or skip it? We plan only what you tell us.</p>
      ${MEALS.map(m => `<div class="rhythm-row"><b>${MEAL_ICON[m]} ${cap1(m)}</b>
        <div class="chips">${RHYTHM.map(([k, l]) => chip(`rh_${m}`, k, l, d.rhythm[m] === k, false)).join("")}</div></div>`).join("")}
      <h3 class="k">On days you order, swap in a cheap home-cook sometimes?</h3>
      <div class="chips">${[["never", "No"], ["sometimes", "1–2× a week"], ["often", "3–5× a week"], ["most", "Most days"]].map(([k, l]) => chip("cook", k, l, d.cook === k, false)).join("")}</div>
      <p class="fine">Meals you cook get simple recipe ideas and one grocery list.</p>`;
  } else if (st === 2) {
    body = `<h1>What's your weekly food budget?</h1>
      <p class="fine">Enter your own limit. After you connect Swiggy and choose a delivery address, check current menu prices and the final cart total. The sample planner cannot quote live Swiggy prices.</p>
      <label class="bigin">₹ per week<input id="ob-budget" type="number" inputmode="numeric" min="100" max="100000" value="${d.weekly_budget ?? ""}" placeholder="e.g. 2000"></label>
      <details ${d.daily_cap ? "open" : ""}><summary>Also cap a single day (optional)</summary>
        <label class="bigin">₹ per day<input id="ob-daily" type="number" inputmode="numeric" min="0" max="20000" value="${d.daily_cap ?? ""}" placeholder="no daily limit"></label>
        <p class="fine">Useful when money is tight until payday. Live Swiggy totals can differ from the sample plan.</p></details>`;
  } else {
    const b = d.body || {};
    body = `<h1>Any food goal?</h1><p class="fine">Optional. With no goal, we just keep meals balanced.</p>
      <div class="chips">${[["none", "No goal"], ["protein", "More protein"], ["lose", "Lose weight gently"], ["maintain", "Maintain"], ["gain", "Build muscle"]].map(([k, l]) => chip("goal", k, l, d.goal === k, false)).join("")}</div>
      <details ${b.weight_kg ? "open" : ""}><summary>Personalise with height & weight (optional)</summary>
        <div class="settings-grid">
          <label>Weight (kg)<input id="ob-w" type="number" inputmode="decimal" min="30" max="300" value="${b.weight_kg ?? ""}"></label>
          <label>Height (cm)<input id="ob-h" type="number" inputmode="numeric" min="120" max="230" value="${b.height_cm ?? ""}"></label>
          <label>Age<input id="ob-a" type="number" inputmode="numeric" min="18" max="100" value="${b.age ?? ""}"></label>
          <label>Sex (for the formula)<select id="ob-s"><option value="unspecified">Prefer not to say</option><option value="female" ${b.sex === "female" ? "selected" : ""}>Female</option><option value="male" ${b.sex === "male" ? "selected" : ""}>Male</option></select></label>
          <label>Activity<select id="ob-act">${[["sedentary", "Mostly sitting"], ["light", "Light"], ["moderate", "Moderate"], ["active", "Active"], ["athlete", "Athlete"]].map(([k, l]) => `<option value="${k}" ${(b.activity || "light") === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        </div><p class="fine">Adults only. General wellness estimate, not medical advice.</p></details>
      <label class="bigin">Your name (optional)<input id="ob-name" maxlength="80" value="${esc(d.name)}" placeholder="Me"></label>`;
  }
  const last = st === OB_STEPS.length - 1;
  return `<main class="onboard"><div class="obtop"><button class="ghost small" data-ob-nav="back">${st ? "← Back" : "Cancel"}</button>
      <div class="dots" aria-label="Step ${st + 1} of ${OB_STEPS.length}">${dots}</div><span class="fine">${st + 1}/${OB_STEPS.length}</span></div>
    ${errbar()}<form class="obbody" id="obform" novalidate>${body}</form>
    <div class="obfoot"><button class="primary big" data-ob-nav="next">${last ? "Create profile →" : "Continue"}</button>
      </div></main>`;
}
function readOnboardInputs() {
  const o = S.onboard, d = o.d, val = (id) => document.getElementById(id)?.value;
  if (o.step === 2) {
    const b = Number(val("ob-budget")); d.weekly_budget = b > 0 ? b : null;
    const c = Number(val("ob-daily")); d.daily_cap = c > 0 ? c : null;
  }
  if (o.step === 3) {
    d.name = (val("ob-name") || "").trim();
    const w = val("ob-w"), h = val("ob-h"), a = val("ob-a");
    d.body = w && h && a ? { weight_kg: Number(w), height_cm: Number(h), age: Number(a), sex: val("ob-s"), activity: val("ob-act") } : null;
  }
}
async function onboardNav(dir) {
  const o = S.onboard;
  readOnboardInputs();
  if (dir === "back") {
    if (o.step === 0) { S.onboard = null; S.welcome = !S.view; render(); return; }
    o.step -= 1; S.error = null; render(); return;
  }
  const d = o.d;
  if (o.step === 1 && MEALS.every(m => d.rhythm[m] === "skip")) throw new Error("Pick at least one meal you order or cook");
  if (o.step === 2) {
    if (!d.weekly_budget) throw new Error("Enter a weekly budget greater than ₹0");
    if (d.weekly_budget < 100 || d.weekly_budget > 100000) throw new Error("Weekly budget must be between ₹100 and ₹1,00,000");
    if (d.daily_cap && (d.daily_cap < 50 || d.daily_cap > 20000)) throw new Error("Daily limit must be between ₹50 and ₹20,000");
    if (d.daily_cap && d.daily_cap > d.weekly_budget) throw new Error("Daily limit can't be more than your weekly budget");
  }
  if (o.step === OB_STEPS.length - 1) {
    const body = { ...d, name: d.name || "Me" };
    delete body.meals;                     // the rhythm says which meals are planned
    if (!body.body) delete body.body;
    if (!body.daily_cap) delete body.daily_cap;
    const view = await api("/api/profiles", "POST", body);
    if (view.access_key) keys.put(view.user.id, view.access_key, view.user.name);
    S.users = mergeUsers(await api("/api/users"));
    S.onboard = null; S.userId = view.user.id; store.set("smartplate.user", String(S.userId));
    adoptView(view); S.exec = await api(`/api/plan/${S.planId}/orders`);
    S.swiggy = await api(`/api/user/${S.userId}/swiggy`);
    S.tab = "more"; S.more = "connection"; toast(swiggySignInOpen() ? "Profile saved. Connect Swiggy to use real restaurants." : "Profile saved."); render(); return;
  }
  o.step += 1; S.error = null; render();
  render();
}
function onboardChip(group, value, multi) {
  const d = S.onboard.d;
  readOnboardInputs();
  if (group === "budgetpick") { d.weekly_budget = Number(value); render(); return; }
  if (group.startsWith("rh_")) { d.rhythm[group.slice(3)] = value; render(); return; }
  if (group === "favourites") value = Number(value);
  if (multi) {
    const arr = d[group];
    const i = arr.indexOf(value);
    if (i >= 0) arr.splice(i, 1); else arr.push(value);
  } else d[group] = value;
  render();
}

/* ---------------------------------------------------------------- wiring */
function wire() {
  // Actions may render new controls before their request finishes. Keep those
  // controls disabled too, while preserving stock/rule-based disabled states.
  setBusy(S.busy);
  const on = (sel, ev, fn) => document.querySelectorAll(sel).forEach(el => el.addEventListener(ev, fn));
  on("[data-tab]", "click", (e) => { e.preventDefault(); guard(() => goTab(e.currentTarget.dataset.tab)); });
  on("[data-plan-view]", "click", (e) => { const k = e.currentTarget.dataset.planView; guard(() => goPlanView(k)); });
  on("[data-kind]", "click", (e) => { e.stopPropagation(); const [sid, k] = e.currentTarget.dataset.kind.split(":");
    guard(() => toKind(Number(sid), k), k === "cook" ? "Cook this meal" : "Order this meal"); });
  on("[data-pin]", "click", (e) => { e.stopPropagation(); const sid = Number(e.currentTarget.dataset.pin); guard(() => togglePin(sid), "Pin this meal"); });
  on("[data-filter]", "click", (e) => toggleFilter(e.currentTarget.dataset.filter));
  on("[data-cat]", "click", (e) => { S.menuCat = e.currentTarget.dataset.cat || null; render(); });
  on("[data-close-addr]", "click", (e) => { if (e.target.dataset.closeAddr) { S.addrSheet = false; render(); } });
  const menuFilter = document.getElementById("menu-filter");
  if (menuFilter) menuFilter.addEventListener("input", () => {        // filters the rows in place: no re-render, focus stays
    const q = menuFilter.value.trim().toLowerCase();
    document.querySelectorAll(".lmrow[data-name]").forEach(r => { r.hidden = !!q && !r.dataset.name.includes(q); });
  });
  on("[data-go]", "click", (e) => { const [t, sub] = e.currentTarget.dataset.go.split(":"); guard(() => goTab(t, sub || null)); });
  on("[data-mode]", "click", (e) => guard(() => setMode(e.currentTarget.dataset.mode)));
  on("[data-user]", "click", (e) => guard(() => switchUser(e.currentTarget.dataset.user)));
  on("[data-sheet]", "click", (e) => guard(() => openSheet(e.currentTarget.dataset.sheet)));
  on("[data-confirm]", "click", (e) => { e.stopPropagation(); guard(() => confirmMeal(e.currentTarget.dataset.confirm)); });
  on("[data-reason]", "click", (e) => { const [sid, k] = e.currentTarget.dataset.reason.split(":"); guard(() => rateReason(sid, k)); });
  on("[data-unlearn]", "click", (e) => { const key = e.currentTarget.dataset.unlearn; guard(async () => {
    const r = await api(`/api/user/${S.userId}/learned/undo`, "POST", { key }); adoptView(r.plan); toast("Undone"); render(); }); });
  on("[data-rate]", "click", (e) => { const [sid, sc] = e.currentTarget.dataset.rate.split(":"); guard(() => rateMeal(sid, Number(sc))); });
  on("[data-handoff]", "click", (e) => { S.handedOff = Number(e.currentTarget.dataset.handoff); setTimeout(render, 50); });
  on("[data-fav]", "click", (e) => guard(() => toggleFav(e.currentTarget.dataset.fav)));
  on("[data-pick]", "click", (e) => guard(() => choose(S.sheet.sid, { item_id: Number(e.currentTarget.dataset.pick) },
    `${e.currentTarget.dataset.pickName} it is. The rest of the week re-balanced.`)));
  on("[data-cook]", "click", (e) => guard(() => choose(S.sheet.sid, { recipe_key: e.currentTarget.dataset.cook }, "Cook day. It's on your grocery list.")));
  on("[data-choose-auto]", "click", (e) => guard(() => choose(e.currentTarget.dataset.chooseAuto, { action: "auto" }, "SmartPlate will choose this one")));
  on("[data-move]", "click", (e) => { S.moving = Number(e.currentTarget.dataset.move); S.sheet = null; S.tab = "week"; render(); });
  on("[data-close-sheet]", "click", (e) => { if (e.target.dataset.closeSheet) { S.sheet = null; render(); } });
  on("[data-meal]", "click", (e) => onMealTap(e.currentTarget.dataset.meal));
  on("[data-meal]", "keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onMealTap(e.currentTarget.dataset.meal); } });
  on("[data-meal][draggable]", "dragstart", (e) => { e.dataTransfer.setData("text/plain", e.currentTarget.dataset.meal); e.currentTarget.classList.add("lifted"); });
  on("[data-meal]", "dragover", (e) => { e.preventDefault(); e.currentTarget.classList.add("over"); });
  on("[data-meal]", "dragleave", (e) => e.currentTarget.classList.remove("over"));
  on("[data-meal]", "drop", (e) => { e.preventDefault(); const from = e.dataTransfer.getData("text/plain"), to = e.currentTarget.dataset.meal;
    if (from && from !== to) guard(() => swapMeals(from, to)); });
  on("[data-oq-cart]", "click", (e) => { const sid = e.currentTarget.dataset.oqCart; guard(() => liveAction("order-week", sid)); });
  on("[data-oq-place]", "click", (e) => { const sid = e.currentTarget.dataset.oqPlace; guard(() => placeQueued(sid, false)); });
  on("[data-over-ok]", "click", (e) => { const sid = Number(e.currentTarget.dataset.overOk); guard(async () => {
    if (S.carts?.[sid]) { S.carts[sid] = { ...S.carts[sid], budgetOk: true }; render(); return; }   // hand-off: open Swiggy next
    await placeQueued(sid, true); }); });
  on("[data-replan]", "click", (e) => { const sid = Number(e.currentTarget.dataset.replan); guard(() => replanRemaining(sid)); });
  on("[data-close-err]", "click", () => { S.error = null; S.errorCode = null; render(); });
  on("[data-close-review]", "click", (e) => { if (e.target.dataset.closeReview) { S.orderReview = null; render(); } });
  on("[data-close-cart-review]", "click", (e) => { if (e.target.dataset.closeCartReview) { S.cartReview = null; render(); } });
  on("[data-close-live-review]", "click", (e) => { if (e.target.dataset.closeLiveReview) { S.liveOrderReview = null; render(); } });
  on("[data-close-checkout-review]", "click", (e) => { if (e.target.dataset.closeCheckoutReview) { S.checkoutReview = null; render(); } });
  on("[data-cmd]", "click", (e) => guard(() => quickCmd(e.currentTarget.dataset.cmd)));
  on("[data-eater]", "click", (e) => { const [sid, pid] = e.currentTarget.dataset.eater.split(":").map(Number); guard(() => toggleEater(sid, pid)); });
  on("[data-have]", "change", (e) => guard(async () => { adoptView(await api(`/api/plan/${S.planId}/grocery-have`, "POST", { item: e.currentTarget.dataset.have, have: e.currentTarget.checked })); render(); }));
  on("[data-hh-edit]", "click", (e) => { S.hhEdit = Number(e.currentTarget.dataset.hhEdit); render(); });
  on("[data-hh-remove]", "click", (e) => { const t = e.currentTarget; guard(() => householdCall(`/members/${t.dataset.hhRemove}`, "DELETE", {}, `${t.dataset.hhName} removed. Upcoming meals re-planned.`)); });
  const hhCreate = document.getElementById("hh-create");
  if (hhCreate) hhCreate.onsubmit = (e) => { e.preventDefault(); guard(() => householdCall("", "POST", { name: new FormData(hhCreate).get("name") }, "Household created. Add the people you cook for.")); };
  const hhAdd = document.getElementById("hh-add");
  if (hhAdd) hhAdd.onsubmit = (e) => { e.preventDefault(); const p = readPerson(hhAdd); guard(() => householdCall("/members", "POST", p, `${p.name} added. Upcoming meals re-planned for everyone's rules.`)); };
  const hhEdit = document.getElementById("hh-edit");
  if (hhEdit) hhEdit.onsubmit = (e) => { e.preventDefault(); guard(() => householdCall(`/members/${S.hhEdit}`, "PATCH", readPerson(hhEdit), "Saved. Upcoming meals re-planned.")); };
  const hhSplit = document.getElementById("hh-split");
  if (hhSplit) hhSplit.onchange = () => guard(() => householdCall("", "PATCH", { split: hhSplit.value }, "Split updated."));
  on("[data-more-like]", "click", (e) => guard(() => moreLikeThis(e.currentTarget.dataset.moreLike)));
  on("[data-forget-like]", "click", (e) => { e.preventDefault(); guard(() => forgetMoreLike(e.currentTarget.dataset.forgetLike)); });
  on("[data-swap-ing]", "click", (e) => guard(() => openSwap(e.currentTarget.dataset.swapIng, "swap")));
  on("[data-oos]", "click", (e) => guard(() => openSwap(e.currentTarget.dataset.oos, "out_of_stock")));
  on("[data-swap-to]", "click", (e) => guard(() => setSwap(S.swapPick.token, e.currentTarget.dataset.swapTo, S.swapPick.reason)));
  on("[data-unswap]", "click", (e) => guard(() => setSwap(e.currentTarget.dataset.unswap, null, "swap")));
  on("[data-adopt]", "click", (e) => guard(() => adopt(e.currentTarget.dataset.adopt)));
  on("[data-sess]", "click", (e) => { e.stopPropagation(); const [id, st] = e.currentTarget.dataset.sess.split(":"); guard(() => setSession(id, st)); });
  on("[data-ob]", "click", (e) => { e.preventDefault(); const t = e.currentTarget; onboardChip(t.dataset.ob, t.dataset.val, t.dataset.multi === "1"); });
  on("[data-ob-nav]", "click", (e) => { e.preventDefault(); guard(() => onboardNav(e.currentTarget.dataset.obNav)); });
  const obform = document.getElementById("obform");
  if (obform) obform.onsubmit = (e) => { e.preventDefault(); guard(() => onboardNav("next")); };
  const cmd = document.getElementById("cmd"); if (cmd) cmd.addEventListener("keydown", (e) => { if (e.key === "Enter") guard(runCommand); });
  const acts = {
    "download-profile": () => downloadPrivate(`/api/user/${S.userId}/data.json`, "smartplate-profile.json", "application/json"),
    cmd: runCommand, reopt: reoptimize, exec: reviewOrders, "confirm-exec": execute,
    "confirm-cart": fillCart, "confirm-live-cart": addLiveItemToCart,
    "review-live-checkout": reviewLiveCheckout, "place-live-order": placeLiveOrder,
    "track-live-order": trackLiveOrder, "live-order-history": loadLiveOrderHistory, savetpl: saveTemplate,
    "refresh-live-cart": refreshLiveCart, "resolve-live-attempt": resolveLiveAttempt,
    "more-live-dishes": () => searchLiveDishes(S.liveBrowseMenu.search.query, true),
    "restore-live-menu": () => openLivePlace(S.liveBrowseMenu.restaurant.id, S.liveBrowseMenu.restaurant.name),
    "download-ics": () => downloadPrivate(`/api/user/${S.userId}/reminders.ics`, "smartplate-reminders.ics", "text/calendar"),
    "download-csv": () => downloadPrivate(`/api/receipts/${S.userId}/export.csv`, "smartplate-expenses.csv", "text/csv"),
    genrcpt: genReceipts, reload: retryLast, newweek: newWeek,
    "start-onboard": async () => startOnboard(), notify: toggleAlerts,
    "oq-save": async () => {
      const vals = n => [...document.querySelectorAll(`input[name="${n}"]:checked`)].map(i => i.value);
      S.orderQueue = await api(`/api/plan/${S.planId}/order-queue`, "POST", { meals: vals("oq-meal"), days: vals("oq-day").map(Number) });
      toast(`${S.orderQueue.queued} meal${S.orderQueue.queued === 1 ? "" : "s"} on your order list`); render(); },
    "live-menus": () => liveAction("live-menus"),
    "sample-menus": async () => { S.view = await api(`/api/plan/${S.planId}/sample-menus`, "POST", {}); toast("Back to sample dishes"); render(); },
    "swiggy-connect": async () => { const r = await api(`/api/user/${S.userId}/swiggy/connect`, "POST", {}); rememberResume(); location.href = r.authorize_url; },
    "swiggy-discover": async () => { S.swiggy = await api(`/api/user/${S.userId}/swiggy/discover`, "POST", {}); toast("Tool list refreshed"); render(); },
    "swiggy-addresses": async () => { S.swAddrs = await api(`/api/user/${S.userId}/swiggy/addresses`); render(); },
    "addr-open": openAddresses,
    "swiggy-refresh-addresses": refreshSwiggyAddresses,
    "close-live-menu": async () => { S.liveMenu = null; render(); },
    "close-live-browse": async () => { S.liveBrowseMenu = null; render(); },
    "swiggy-disconnect": async () => { S.swiggy = await api(`/api/user/${S.userId}/swiggy/disconnect`, "POST", {}); S.liveMenu = null; S.carts = null; S.swAddrs = null; S.liveResults = null; S.liveFavourites = null; S.liveBrowseMenu = null; S.liveCart = null; S.liveCartEmpty = null; S.checkoutReview = null; S.placedOrder = null; S.liveOrderStatus = null; S.liveOrderHistory = null; S.liveCartError = null; toast("Disconnected from Swiggy"); render(); },
    "signin-open": async () => { S.signin = true; S.error = null; render(); document.getElementById("si-login")?.focus(); },
    "signin-close": async () => { S.signin = false; S.error = null; render(); },
    "sign-out": signOut,
    "rotate-recovery": rotateRecoveryCode,
    "copy-recovery": async () => { await navigator.clipboard.writeText(`${S.userId}.${keys.get(S.userId)}`); toast("Recovery code copied"); }, "cancel-move": async () => { S.moving = null; render(); },
    "hh-edit-cancel": async () => { S.hhEdit = null; render(); },
    "hh-leave": () => householdCall("/leave", "POST", {}, "Stopped sharing. Your plan uses your own rules again."),
    "swap-cancel": async () => { S.swapPick = null; render(); },
  };
  on("[data-act]", "click", (e) => { e.preventDefault(); const a = e.currentTarget.dataset.act, f = acts[a]; if (f) guard(f, ACT_LABELS[a]); });
  const deleteForm = document.getElementById("delete-profile");
  if (deleteForm) deleteForm.onsubmit = (e) => { e.preventDefault(); guard(() => deleteProfile(deleteForm)); };
  if (S.orderReview || S.cartReview || S.liveOrderReview || S.checkoutReview) document.querySelector(".checkout [autofocus]")?.focus();
  if (S.sheet?.data) document.querySelector(".sheet .close")?.focus();
  on("[data-swaddr]", "click", (e) => { const addressId = e.currentTarget.dataset.swaddr; guard(async () => {
    S.swiggy = await api(`/api/user/${S.userId}/swiggy/address`, "POST", { address_id: addressId });
    forgetAddressState();
    S.liveFavourites = await api(`/api/user/${S.userId}/swiggy/favourites`);
    await refreshLiveCart(false);
    S.addrSheet = false;
    toast("Delivery address saved"); render();
    await resumePending(); }, "Choose delivery address"); });
  on("[data-live-menu]", "click", (e) => { const name = e.currentTarget.dataset.liveMenu; guard(() => liveAction("live-menu", name), `Open the Swiggy menu for “${name}”`); });
  on("[data-live-fav]", "click", (e) => { const t = e.currentTarget; guard(() => toggleLiveFavourite(t.dataset.liveFav, t.dataset.liveName), `Update favourite “${t.dataset.liveName}”`); });
  on("[data-live-place]", "click", (e) => { const t = e.currentTarget; guard(() => liveAction("live-place", t.dataset.livePlace, t.dataset.liveName), `Open the menu for “${t.dataset.liveName}”`); });
  on("[data-live-item]", "click", (e) => { const t = e.currentTarget; guard(() => reviewLiveItem(t.dataset.liveItem, t.dataset.liveItemName), `Review “${t.dataset.liveItemName}”`); });
  on("[data-live-track]", "click", (e) => { const id = e.currentTarget.dataset.liveTrack; guard(() => trackLiveOrder(id), "Track order"); });
  const liveSearch = document.getElementById("live-search");
  if (liveSearch) liveSearch.onsubmit = (e) => { e.preventDefault(); const q = document.getElementById("live-query").value; guard(() => searchLivePlaces(q), `Search Swiggy for “${q}”`); };
  const dishSearch = document.getElementById("dish-search");
  if (dishSearch) dishSearch.onsubmit = (e) => { e.preventDefault(); const q = document.getElementById("dish-query").value; guard(() => searchLiveDishes(q), `Find dishes for “${q}”`); };
  on("[data-cart]", "click", (e) => guard(() => liveAction("order-meal", Number(e.currentTarget.dataset.cart))));
  on("[data-rm-device]", "click", (e) => guard(() => removeDevice(e.currentTarget.dataset.rmDevice)));
  const signin = document.getElementById("signin");
  if (signin) signin.onsubmit = (e) => { e.preventDefault(); guard(() => signIn(signin)); };
  const account = document.getElementById("account");
  if (account) account.onsubmit = (e) => { e.preventDefault(); guard(saveAccount); };
  const recover = document.getElementById("recover");
  if (recover) recover.onsubmit = (e) => { e.preventDefault(); guard(() => useRecoveryCode(document.getElementById("rcode").value)); };
  const tuning = document.getElementById("tuning");
  if (tuning) tuning.onsubmit = e => { e.preventDefault(); const body = readTuning(tuning);
    guard(async () => { adoptView(await api(`/api/user/${S.userId}/setup`, "PATCH", body)); toast("Saved. Upcoming meals re-planned."); render(); }); };
  const preferences = document.getElementById('preferences');
  if (preferences) preferences.onsubmit = e => {
    e.preventDefault();
    const body = readSettings(preferences);
    guard(async () => {
      adoptView(await api(`/api/user/${S.userId}/setup`, 'PATCH', body));
      if (keys.get(S.userId)) keys.put(S.userId, keys.get(S.userId), S.view.user.name);
      S.users = mergeUsers(await api('/api/users')); S.orderReview = null;
      S.tab = 'today'; S.more = null; toast('Saved. Upcoming meals re-planned.'); render();
    });
  };
}
function onMealTap(sid) {
  sid = Number(sid);
  if (S.moving) {
    if (S.moving === sid) { S.moving = null; render(); return; }
    guard(() => swapMeals(S.moving, sid)); return;
  }
  const cell = S.view.grid.flatMap(d => Object.values(d.meals)).find(m => m.session_id === sid);
  if (!cell || cell.status === "past" || ["ordered", "confirmed"].includes(cell.status)) {
    if (cell && ["ordered", "confirmed"].includes(cell.status)) { toast("Already recorded. Rate it on Today."); }
    return;
  }
  guard(() => openSheet(sid));
}
// Each Plan view loads only what it shows.
async function loadPlanView() {
  if (S.planView === "places" && !swiggyReady() && !S.places) S.places = await api(`/api/restaurants?user_id=${S.userId}`);
  if (S.planView === "orders") {
    S.exec = await api(`/api/plan/${S.planId}/orders`);
    S.orderQueue = await api(`/api/plan/${S.planId}/order-queue`).catch(() => null);
  }
}
async function goPlanView(view) { S.planView = view; await loadPlanView(); render(); }
async function goTab(tab, sub = null) {
  if (tab === "plan") tab = "week";
  if (tab === "you") tab = "more";
  if (tab === "places") { if (swiggyReady()) tab = "today"; else { tab = "week"; S.planView = "places"; } }
  if (tab === "more" && (sub === "orders" || sub === "cooking")) { tab = "week"; S.planView = sub === "orders" ? "orders" : "cook"; sub = null; }
  S.tab = tab; S.sheet = null;
  if (tab === "more") S.more = sub;
  if (tab === "today" && swiggyReady() && !S.liveFavourites)
    S.liveFavourites = await api(`/api/user/${S.userId}/swiggy/favourites`);
  if (tab === "week") await loadPlanView();
  if (S.more === "community") S.community = await api("/api/community");
  if (S.more === "receipts") S.receipts = await api(`/api/receipts/${S.userId}`);
  if (S.more === "recap") S.recap = await api(`/api/plan/${S.planId}/recap`);
  if (S.more === "calendar") S.calendar = await api(`/api/user/${S.userId}/calendar`);
  if (S.more === "connection") S.swiggy = await api(`/api/user/${S.userId}/swiggy`);
  if (S.more === "profiles") S.account = keys.get(S.userId) ? await api(`/api/user/${S.userId}/account`) : null;
  render();
  if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && (S.orderReview || S.cartReview || S.liveOrderReview || S.checkoutReview || S.sheet || S.moving || S.addrSheet)) {
    S.orderReview = null; S.cartReview = null; S.liveOrderReview = null; S.checkoutReview = null; S.sheet = null; S.moving = null; S.addrSheet = false; render();
  }
  if (e.key === 'Tab' && (S.orderReview || S.cartReview || S.liveOrderReview || S.checkoutReview || S.sheet || S.addrSheet)) {
    const items = [...document.querySelectorAll('[role=dialog] button:not([disabled]), [role=dialog] a[href]')];
    if (!items.length) return;
    const first = items[0], last = items.at(-1);
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
function bootFailed(e) {
  const offline = (typeof navigator !== "undefined" && navigator.onLine === false) || /fetch|network/i.test(e?.message || "");
  document.getElementById("app").innerHTML = offline
    ? `<main class="welcome"><div class="brand big">Smart<em>Plate</em></div><h1 class="hero">You're offline.</h1>
       <p class="lede">Plans and budgets are always live, so SmartPlate needs a connection. It will reload by itself when you're back online.</p>
       <button class="primary big" id="retry-boot">Try again</button></main>`
    : `<div class="boot">Failed to start: ${esc(e.message)}</div>`;
  if (offline && typeof window !== "undefined") window.addEventListener("online", () => location.reload(), { once: true });
  document.getElementById("retry-boot")?.addEventListener("click", () => location.reload());
}
if (typeof window !== "undefined" && window.addEventListener) {
  window.addEventListener("offline", () => { S.offline = true; if (S.view) render(); });
  window.addEventListener("online", () => { S.offline = false; if (S.view) render(); });
}
// A Swiggy photo that fails to load becomes the dish's category icon.
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img && img.tagName === "IMG" && img.classList?.contains("dphoto")) {
    const lg = img.classList.contains("lg");
    img.outerHTML = dishIcon(img.dataset.dish || "", { lg });
  }
}, true);
if (typeof navigator !== "undefined" && "serviceWorker" in navigator)
  navigator.serviceWorker.register("/sw.js").catch(() => {});
boot().catch(bootFailed);
