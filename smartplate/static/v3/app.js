/* Ziggy v3 — "tonight's meal" (experiment, branch ui/v3-tonight).
   One job: tonight's meal (dinner first), decided inside your weekly food budget and your
   hard diet rules, one tap to a real Swiggy cart. The week is planned quietly behind it.

   Three tabs:  Tonight — the pick, two or three alternatives, Add to Swiggy cart, the real bill
                Week    — money left this week, reconciled from real bills; what's planned
                More    — your rules, Swiggy, reminders, account, and everything else
                          (household, cooking, festivals, insights…) in the classic app.
   Same API, same private-profile keys (localStorage) and same visual language as the
   classic app (/static/styles.css). Vanilla JS, no build step. Delete smartplate/static/v3/
   and its two routes in app.py to remove it. */
"use strict";

/* ================================================================ pure helpers (unit-tested) */
const MEALS = ["breakfast", "lunch", "dinner"];
const cap1 = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rupee = (n) => {
  const v = Math.round((Number(n) || 0) * 100) / 100;
  return "₹" + v.toLocaleString("en-IN", { minimumFractionDigits: Number.isInteger(v) ? 0 : 2, maximumFractionDigits: 2 });
};
const rupee0 = (n) => "₹" + Math.round(Number(n) || 0).toLocaleString("en-IN");
/* Money has exactly two looks (same rule as the classic app):
   est()  — "≈₹180 est.": plan and menu prices, anything not read back from Swiggy's cart
   real() — exact to the paisa: ONLY numbers from Swiggy's cart or bill. */
const est = (n) => `<span class="est" title="Estimate">≈${rupee0(n)}<i>est.</i></span>`;
const real = (n) => `<span class="real" title="From your Swiggy bill">${rupee(n)}</span>`;
const SPENT = new Set(["confirmed", "ordered"]);

/* The week's money, split by how sure each rupee is. Uses the server's own envelope
   (budget − spend = left) and only explains it: every planned or had meal in `spend` lands
   in exactly one bucket. A past meal nobody confirmed is not money spent: it is listed
   under `unconfirmed` for the person to settle (Yes, I had it / No). */
function weekMoney(view) {
  const b = (view && view.budget) || {};
  const out = { budget: Number(b.budget) || 0, left: (Number(b.budget) || 0) - (Number(b.spend) || 0),
    spend: Number(b.spend) || 0, hadReal: 0, hadEst: 0, cartReal: 0, planned: 0, unconfirmed: [],
    prorated: !!b.prorated, weekly: Number(b.weekly_budget) || 0, dailyCap: b.daily_cap || null };
  for (const day of (view && view.grid) || []) {
    for (const meal of MEALS) {
      const c = day.meals && day.meals[meal];
      if (!c || !["delivery", "cook"].includes(c.kind)) continue;
      const cost = Number(c.cost) || 0;
      if (c.status === "past") { out.unconfirmed.push({ ...c, meal, day: day.day, date: day.date }); continue; }
      if (c.status === "skipped") continue;
      if (SPENT.has(c.status)) { if (c.real_bill) out.hadReal += cost; else out.hadEst += cost; }
      else if (c.real_bill) out.cartReal += cost;
      else out.planned += cost;
    }
  }
  for (const k of ["hadReal", "hadEst", "cartReal", "planned", "left", "spend"]) out[k] = Math.round(out[k] * 100) / 100;
  return out;
}

/* What the hero is about: "Tonight" for today's dinner, otherwise the meal and the day. */
function pickTitle(nu) {
  if (!nu) return "";
  if (nu.when === "Today") return nu.meal === "dinner" ? "Tonight" : `${cap1(nu.meal)} today`;
  if (nu.when === "Tomorrow") return nu.meal === "dinner" ? "Tomorrow night" : `${cap1(nu.meal)} tomorrow`;
  return `${cap1(nu.meal)} · ${nu.when}`;
}

/* Two or three other dishes for the same meal, from places the person actually uses:
   their pool for that meal first, then the best dish of each usual place (variety), then
   anything else that fits. Over-budget dishes only when nothing fits. Never the current one. */
function alternatives(opts, n = 3) {
  if (!opts || opts.failed) return [];
  const seen = new Set(), out = [];
  const add = (x, restaurant, source) => {
    if (!x || x.current || seen.has(x.item_id)) return;
    seen.add(x.item_id); out.push({ ...x, restaurant: x.restaurant || restaurant, source });
  };
  (opts.pool || []).forEach(x => add(x, x.restaurant, "pool"));
  const groups = opts.usual || [];
  groups.forEach(g => add((g.dishes || []).find(x => !x.current && x.fits), g.restaurant, "usual"));
  groups.forEach(g => (g.dishes || []).forEach(x => x.fits && add(x, g.restaurant, "usual")));
  (opts.new || []).forEach(x => x.fits && add(x, x.restaurant, "new"));
  const fits = out.filter(x => x.fits !== false);
  if (fits.length >= n) return fits.slice(0, n);
  groups.forEach(g => (g.dishes || []).forEach(x => add(x, g.restaurant, "usual")));
  return [...out.filter(x => x.fits !== false), ...out.filter(x => x.fits === false)].slice(0, n);
}

/* Swiggy menus don't list ingredients: no cart fill when anyone eating has an allergy, a
   medical rule or a vegan diet (the server refuses too: swiggy_live.cart_preview). */
const ruleBound = (p) => !!(p && ((p.allergens || []).length || (p.medical || []).length || p.diet === "vegan"));
function cartEligible(view, c) {
  if (ruleBound(view && view.user)) return false;
  const on = new Set((c && c.eaters) || []);
  return !(((view && view.household) || {}).people || []).some(p => !p.you && on.has(p.id) && ruleBound(p));
}

/* The setup screen in one place: what it sends to POST /api/profiles. No account here:
   the Ziggy account (email + password) is the LAST onboarding step, after the first meal. */
function setupBody(d) {
  const rhythm = {};
  MEALS.forEach(m => { rhythm[m] = (d.meals || []).includes(m) ? "order" : "skip"; });
  return { name: "Me", diet: d.diet, allergens: d.allergens || [], medical: d.medical || [],
    weekly_budget: Number(d.weekly_budget), rhythm, cook: "never" };
}
/* Same shape the server accepts (smartplate/accounts.py EMAIL_RE); the server decides. */
const EMAIL_RE = /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z]{2,63}$/;
function accountProblem(email, password) {
  const e = String(email || "").trim().toLowerCase();
  if (!e || e.length > 254 || e.includes("..") || !EMAIL_RE.test(e)) return "Enter a valid email address, like name@example.com.";
  if (String(password || "").length < 8) return "Use a password of at least 8 characters.";
  return null;
}
/* Sign in: an email, or the free-form name an older account was made with. */
const signInBody = (id, password) => String(id || "").includes("@")
  ? { email: String(id).trim(), password } : { login: String(id || "").trim(), password };
function setupProblem(d) {
  if (!d.diet) return "Choose what you eat: vegetarian, non-vegetarian or vegan.";
  const b = Number(d.weekly_budget);
  if (!(b > 0)) return "Enter a weekly food budget.";
  if (b < 100 || b > 100000) return "Weekly budget must be between ₹100 and ₹1,00,000.";
  if (!(d.meals || []).length) return "Pick at least one meal for Ziggy to decide.";
  return null;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { weekMoney, pickTitle, alternatives, ruleBound, cartEligible, setupBody, setupProblem,
    accountProblem, signInBody, rupee, rupee0 };
}

/* ================================================================ app (browser only) */
if (typeof document !== "undefined" && document.getElementById && document.getElementById("app")) (function () {

const S = {
  meta: null, userId: null, view: null, swiggy: null, screen: "boot", tab: "tonight",
  setup: null, signin: null, sheet: null, carts: {}, opts: null, liveCart: null, sync: null,
  addrs: null, busy: false, error: null, errorRule: null, retry: null, adding: null, account: null,
};
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
  del(k) { try { localStorage.removeItem(k); } catch (_) {} },
};
/* The same private-profile keys as the classic app, so a profile opens in both. */
const keys = {
  all() { try { const v = JSON.parse(store.get("smartplate.keys") || "{}"); return v && typeof v === "object" && !Array.isArray(v) ? v : {}; } catch (_) { return {}; } },
  get(id) { return this.all()[id]?.key || null; },
  put(id, key, name) { const a = this.all(); a[id] = { key, name }; store.set("smartplate.keys", JSON.stringify(a)); },
  drop(id) { const a = this.all(); delete a[id]; store.set("smartplate.keys", JSON.stringify(a)); },
};
/* Same light / dark / system choice as the classic app. */
(function applyLook() {
  const theme = store.get("ziggy.theme") || "system", palette = store.get("ziggy.palette") || "ember", root = document.documentElement;
  if (theme !== "system") root.setAttribute("data-theme", theme);
  if (palette === "mesh") root.setAttribute("data-palette", "mesh");
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0F0E13" : "#F5F3F0");
})();

/* ---------------------------------------------------------------- icons + elephant (shared look) */
const svg = (body, w = 2) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const ICON = {
  tonight: svg('<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>'),
  week: svg('<rect x="4" y="5" width="16" height="15" rx="3"/><path d="M8 3v4M16 3v4M4 10h16"/>'),
  more: svg('<circle cx="6" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="18" cy="12" r="1.4"/>', 2.4),
  place: svg('<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>'),
  down: svg('<path d="M6 9l6 6 6-6"/>', 2.4),
  right: svg('<path d="M9 6l6 6-6 6"/>'),
  back: svg('<path d="M15 5l-7 7 7 7"/>'),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>', 2.2),
  out: svg('<path d="M7 17L17 7"/><path d="M8 7h9v9"/>', 2.2),
  bell: svg('<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>', 1.8),
  swap: svg('<path d="M7 4L3 8l4 4"/><path d="M3 8h14"/><path d="M17 20l4-4-4-4"/><path d="M21 16H7"/>'),
  check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 2.6),
};
const EARS = '<path class="ear" d="M21 14C12 14 5.5 21 5.5 29.5c0 7.2 5.2 12.5 11.5 12.5 3.2 0 5.4-1.5 6.6-3.6z"/><path class="ear" d="M43 14c9 0 15.5 7 15.5 15.5 0 7.2-5.2 12.5-11.5 12.5-3.2 0-5.4-1.5-6.6-3.6z"/>';
function mascot(cls = "", trunkCls = "wave") {
  return `<div class="mascot ${cls}" aria-hidden="true"><svg viewBox="0 0 64 64">${EARS}<ellipse class="head" cx="32" cy="28" rx="14.5" ry="15.5"/>
    <path class="trunk ${trunkCls}" d="M32 39v9c0 5.5 3.6 8.5 7.6 8.5 3.2 0 5.2-2.2 5.2-4.8"/>
    <circle class="eye" cx="26.5" cy="27" r="2.4"/><circle class="eye" cx="37.5" cy="27" r="2.4"/>
    <circle cx="27.3" cy="26.2" r=".8" fill="#fff"/><circle cx="38.3" cy="26.2" r=".8" fill="#fff"/>
    <ellipse class="cheek" cx="22.5" cy="33.5" rx="2.8" ry="2"/><ellipse class="cheek" cx="41.5" cy="33.5" rx="2.8" ry="2"/></svg></div>`;
}
const WHITE_ELEPHANT = '<path d="M21 14C12 14 5.5 21 5.5 29.5c0 7.2 5.2 12.5 11.5 12.5 3.2 0 5.4-1.5 6.6-3.6z" fill="#fff" opacity=".8"/><path d="M43 14c9 0 15.5 7 15.5 15.5 0 7.2-5.2 12.5-11.5 12.5-3.2 0-5.4-1.5-6.6-3.6z" fill="#fff" opacity=".8"/><ellipse cx="32" cy="28" rx="14.5" ry="15.5" fill="#fff"/><path d="M32 39v9c0 5.5 3.6 8.5 7.6 8.5 3.2 0 5.2-2.2 5.2-4.8" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round"/><circle cx="26.5" cy="27" r="2.6" fill="#18161D"/><circle cx="37.5" cy="27" r="2.6" fill="#18161D"/>';
const mark = () => `<span class="mark" role="img" aria-label="Ziggy"><svg viewBox="0 0 64 64" aria-hidden="true">${WHITE_ELEPHANT}</svg></span>`;
const roller = () => `<span class="roller" aria-hidden="true"><svg viewBox="0 0 64 64">${WHITE_ELEPHANT}</svg></span>`;
/* Dish identity: Swiggy's own photo when it gave one, else an icon for the dish's kind. */
const DISH_KINDS = [
  ["dessert", 330, /payasam|kheer|halwa|kesari|gulab|jamun|rasmalai|jalebi|ice ?cream|kulfi|cake|brownie|sweet|laddu|dessert/i],
  ["drink", 190, /coffee|\btea\b|chai|juice|lassi|shake|smoothie|buttermilk|soda|beverage/i],
  ["biryani", 40, /biryani|biriyani|pulao|pulav/i], ["pizza", 10, /pizza/i], ["burger", 15, /burger/i],
  ["wrap", 55, /wrap|roll|shawarma|frankie|kathi|sandwich/i],
  ["noodles", 280, /noodle|chow ?mein|hakka|ramen|pasta|manchurian/i],
  ["dosa", 50, /dosa|dosai|uttapam|uthappam|appam|pesarattu|roast/i],
  ["tiffin", 100, /idli|idly|vada|vadai|pongal|upma|poori|puri\b|tiffin|khichdi|kichadi|poha/i],
  ["bread", 30, /parotta|paratha|porotta|roti|naan|chapati|kulcha|bhature|bread|toast/i],
  ["fish", 200, /fish|meen|prawn|shrimp|crab|seafood/i], ["egg", 48, /\begg|omelette|bhurji/i],
  ["curry", 20, /curry|gravy|masala|kurma|korma|kadai|makhani|dal\b|daal|sambar|rasam|chole|rajma|paneer|kofta/i],
  ["grill", 0, /kebab|kabab|tikka|tandoor|grill|65|wings|chicken|mutton/i],
  ["thali", 160, /meals|thali|combo|curd rice|rice/i], ["bowl", 140, /bowl|salad|poke/i],
  ["snack", 35, /samosa|bajji|pakoda|pakora|bonda|chaat|fries|cutlet|momo|snack/i],
];
const DISH_SVG = {
  dessert: '<path d="M8 11l4 10 4-10"/><path d="M7 11a5 5 0 0 1 10 0z"/>',
  drink: '<path d="M5 8h12v6a6 6 0 0 1-12 0z"/><path d="M17 10h2a2 2 0 0 1 0 4h-2"/><path d="M8 3.5v2M12 3.5v2"/>',
  biryani: '<path d="M4 11h16v2a7 7 0 0 1-7 7h-2a7 7 0 0 1-7-7z"/><path d="M2.5 11h19"/><path d="M9 4c-.8 1 .8 2 0 3.5M14.5 4c-.8 1 .8 2 0 3.5"/>',
  pizza: '<path d="M12 21L3.5 6.5a15 15 0 0 1 17 0z"/><path d="M5.2 9.4a12 12 0 0 1 13.6 0"/><circle cx="10" cy="12" r="1"/><circle cx="13.5" cy="15" r="1"/>',
  burger: '<path d="M4 11a8 6 0 0 1 16 0z"/><path d="M3 14.5h18"/><path d="M4.5 17.5h15a1 1 0 0 1-1 2h-13a1 1 0 0 1-1-2z"/>',
  wrap: '<path d="M6.5 20.5l-3-3L15 6a4.2 4.2 0 0 1 6 6z"/><path d="M13 8l3 3M10 11l3 3"/>',
  noodles: '<path d="M3 12h18a9 9 0 0 1-18 0z"/><path d="M13 3l-2.5 9M19 4l-5 8"/><path d="M7 15c2 1 8 1 10 0"/>',
  dosa: '<path d="M3 16.5L18.5 8a2.6 2.6 0 0 1 2.4 4.6L4.5 18.5z"/><path d="M2.5 21h19"/><path d="M9 13.4l1.1 2.1M13.6 10.9l1.1 2.1"/>',
  tiffin: '<ellipse cx="8" cy="11" rx="4.5" ry="2.6"/><ellipse cx="16" cy="11" rx="4.5" ry="2.6"/><path d="M3.5 11v2c0 1.5 2 2.6 4.5 2.6s4.5-1.1 4.5-2.6v-2M11.5 11v2c0 1.5 2 2.6 4.5 2.6s4.5-1.1 4.5-2.6v-2"/><path d="M3 19h18"/>',
  bread: '<ellipse cx="12" cy="12" rx="9" ry="7"/><path d="M7 9.5c3 1.5 7 1.5 10 0M6.5 13c3 1.5 8 1.5 11 0"/>',
  fish: '<path d="M2.5 12c3.5-5.5 10-6.5 14.5-2.5L21 6.5v11l-4-3c-4.5 4-11 3-14.5-2.5z"/><circle cx="7.5" cy="11" r=".9"/>',
  egg: '<path d="M12 3c4 0 7 6 7 10.5a7 7 0 0 1-14 0C5 9 8 3 12 3z"/><circle cx="12" cy="14" r="2.6"/>',
  curry: '<path d="M3 11h18a9 9 0 0 1-18 0z"/><path d="M6.5 11c1.2-1.6 2.8-1.6 4 0s2.8 1.6 4 0 2.3-1.6 3 0"/><path d="M10 3.5c-.7 1 .7 2 0 3M14 3.5c-.7 1 .7 2 0 3"/>',
  grill: '<path d="M4 20L20 4"/><rect x="6.5" y="9.5" width="5" height="5" rx="1.2" transform="rotate(45 9 12)"/><rect x="11.5" y="4.5" width="5" height="5" rx="1.2" transform="rotate(45 14 7)"/>',
  thali: '<circle cx="12" cy="12" r="9.5"/><circle cx="7.4" cy="10" r="2"/><circle cx="12" cy="7.3" r="2"/><circle cx="16.6" cy="10" r="2"/><path d="M7 15.3c1.4-1.9 8.6-1.9 10 0-1.6 2-8.4 2-10 0z"/>',
  bowl: '<path d="M3 12h18a9 9 0 0 1-18 0z"/><path d="M8 12c-.2-3 1.6-5.5 4.5-6.5M12.5 12c.5-2.6 2.6-4.3 5.5-4.4"/>',
  snack: '<path d="M12 4l9 15H3z"/><path d="M8.2 13.5h7.6"/>',
  cook: '<path d="M4 10h14v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M18 12h3M2.5 10h17"/><path d="M8 3.5c-.7 1 .7 2 0 3M12.5 3.5c-.7 1 .7 2 0 3"/>',
  plate: '<circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="3"/><path d="M2.5 4v5a2 2 0 0 0 2 2v9M21.5 4c-1.5 1-2 3-2 6h2v10"/>',
};
function dishIcon(name, { cook = false, image = null, lg = false } = {}) {
  if (image) return `<img class="dphoto${lg ? " lg" : ""}" src="${esc(image)}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
  const hit = cook ? ["cook", 120] : (DISH_KINDS.find(([, , re]) => re.test(String(name || ""))) || ["plate", 220]);
  return `<span class="dicon${lg ? " lg" : ""}" style="--h:${hit[1]}" data-kind="${hit[0]}" aria-hidden="true"><svg viewBox="0 0 24 24">${DISH_SVG[hit[0]]}</svg></span>`;
}

/* ---------------------------------------------------------------- server */
const RECONNECT = new Set(["swiggy_not_connected", "swiggy_auth_expired"]);
async function api(path, method = "GET", body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  const k = S.userId ? keys.get(S.userId) : null;
  if (k) opt.headers["X-SmartPlate-Key"] = k;
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    if (RECONNECT.has(data.code) && S.swiggy) { S.swiggy.connected = false; S.liveCart = null; }
    const err = new Error(data.message || data.error || `The server answered HTTP ${r.status}`);
    err.code = data.code; err.status = r.status; err.data = data;
    throw err;
  }
  return r.json();
}
function toast(msg, undo) {
  document.querySelectorAll(".toast").forEach(t => t.remove());
  const t = document.createElement("div");
  t.className = "toast"; t.setAttribute("role", "status");
  const span = document.createElement("span"); span.textContent = msg; t.appendChild(span);
  if (undo) {
    const b = document.createElement("button"); b.textContent = "Undo";
    b.addEventListener("click", () => { t.remove(); guard(undo, "Undo"); }); t.appendChild(b);
  }
  document.body.appendChild(t);
  setTimeout(() => t.remove(), undo ? 5000 : 2800);
}
function setBusy(b) {
  S.busy = b;
  document.getElementById("busybar")?.classList.toggle("on", b);
  document.body.setAttribute("aria-busy", String(b));
}
/* Every tap goes through here: progress, and a failure says what failed, why, and the fix. */
async function guard(fn, label) {
  if (S.busy) return;
  setBusy(true); S.error = null; S.errorRule = null;
  try { await fn(); }
  catch (e) {
    S.error = label ? `${label} didn't work: ${e.message || e}` : (e.message || String(e));
    S.errorRule = e.data?.rule || null; S.errorCode = e.code || null; S.retry = { fn, label };
    if (e.code === "profile_missing") { forgetProfile(); S.screen = "welcome"; }
    render();
  } finally { setBusy(false); }
}

/* ---------------------------------------------------------------- loading */
const swiggyReady = () => !!(S.swiggy && S.swiggy.connected && S.swiggy.address);
const signInOpen = () => !!S.meta?.swiggy_redirect_approved;
async function boot() {
  const bar = document.createElement("div"); bar.className = "busybar"; bar.id = "busybar"; bar.innerHTML = "<i></i>";
  document.body.appendChild(bar);
  S.meta = await api("/api/meta");
  const saved = Number(store.get("smartplate.user"));
  S.userId = saved && keys.get(saved) ? saved : null;
  const params = new URLSearchParams(location.search);
  if (!S.userId) { S.screen = "welcome"; render(); return; }
  try { await loadProfile(); }
  catch (e) {
    if (!/missing$/.test(e.code || "") && e.status !== 403 && e.status !== 404) throw e;
    forgetProfile(); S.screen = "welcome"; render(); return;
  }
  if (params.get("swiggy_error")) {
    const msg = { denied: "Swiggy sign-in was cancelled.", expired: "That Swiggy sign-in link expired. Tap Connect Swiggy again.",
      other_browser: "That Swiggy sign-in started in another browser. Tap Connect Swiggy again here." };
    S.error = msg[params.get("swiggy_error")] || "Swiggy sign-in didn't complete. Tap Connect Swiggy to try again.";
  } else if (params.get("swiggy")) toast("Swiggy connected");
  if (params.has("swiggy") || params.has("swiggy_error")) history.replaceState(null, "", "/v3");
  S.screen = nextScreen();
  render();
  if (S.screen === "address") await guard(loadAddresses, "Load your Swiggy addresses");
  else await afterReady();
}
async function loadProfile() {
  S.view = await api(`/api/user/${S.userId}/plan`);
  S.swiggy = await api(`/api/user/${S.userId}/swiggy`).catch(() => null);
  S.account = await api(`/api/user/${S.userId}/account`).catch(() => null);
  if (swiggyReady()) await refreshLiveCart();
}
// Onboarding is over once the profile exists; Swiggy is the one step that may remain.
function nextScreen() {
  if (!S.view) return "welcome";
  if (signInOpen() && S.swiggy && !S.swiggy.connected && !store.get(`ziggy.v3.noswiggy.${S.userId}`)) return "connect";
  if (S.swiggy?.connected && !S.swiggy.address) return "address";
  return "app";
}
// Live menus for the delivery address are read once, by themselves, then the pick follows.
async function afterReady() {
  const src = S.view?.source || {};
  if (swiggyReady() && src.kind === "none" && src.needs === "sync" && !S.sync) {
    S.sync = "loading"; render();
    try { S.view = await api(`/api/plan/${S.view.plan.id}/live-menus`, "POST", {}); S.sync = null; }
    catch (e) { S.sync = { error: e.message }; }
    render();
  }
  loadOptions();
}
async function refreshLiveCart() {
  try {
    const r = await api(`/api/user/${S.userId}/swiggy/live-cart`);
    S.liveCart = r.cart || null;
  } catch (_) { S.liveCart = null; }
  // Swiggy's cart already holds tonight's pick (added on another device or before a reload)
  const nu = S.view?.next_up?.cell, c = S.liveCart;
  if (nu && c && c.orderable !== false && nu.kind === "delivery" && !SPENT.has(nu.status) && c.item === nu.item
      && (!c.restaurant || !nu.restaurant || c.restaurant === nu.restaurant)) {
    const planned = nu.planned_cost ?? nu.cost;
    S.carts[nu.session_id] = { ...c, planned_cost: planned, over_plan: c.to_pay != null ? Math.round((c.to_pay - planned) * 100) / 100 : null };
  }
}
// The alternatives under tonight's pick: one request per pick, then one tap each.
async function loadOptions() {
  const c = S.view?.next_up?.cell;
  if (!c || c.status !== "active" || c.kind !== "delivery" || S.carts[c.session_id]) return;
  const key = `${c.session_id}:${c.item_id}`;
  if (S.opts?.key === key) return;
  S.opts = { key, sid: c.session_id, data: null }; render();
  let data;
  try { data = await api(`/api/session/${c.session_id}/options`); } catch (_) { data = { failed: true }; }
  if (S.opts?.key !== key) return;
  S.opts.data = data; render();
}
function forgetProfile() {
  if (S.userId) keys.drop(S.userId);
  store.del("smartplate.user");
  Object.assign(S, { userId: null, view: null, swiggy: null, carts: {}, opts: null, liveCart: null, sheet: null, account: null, tab: "tonight" });
}

/* ---------------------------------------------------------------- actions */
function startSetup() {
  S.setup = { diet: null, allergens: [], medical: [], weekly_budget: 2500, meals: ["dinner"] };
  S.screen = "setup"; S.error = null; render();
}
function readSetup() {
  const d = S.setup, val = (id) => document.getElementById(id)?.value;
  if (!d || !document.getElementById("setup")) return;
  d.weekly_budget = Number(val("su-budget")) || null;
}
async function finishSetup() {
  readSetup();
  const problem = setupProblem(S.setup);
  if (problem) throw new Error(problem);
  S.screen = "planning"; render();
  let view;
  try { view = await api("/api/profiles", "POST", setupBody(S.setup)); }
  catch (e) { S.screen = "setup"; throw e; }
  keys.put(view.user.id, view.access_key, view.user.name);
  S.userId = view.user.id; store.set("smartplate.user", String(S.userId));
  S.view = view; S.setup = null;
  S.swiggy = await api(`/api/user/${S.userId}/swiggy`).catch(() => null);
  S.account = await api(`/api/user/${S.userId}/account`).catch(() => null);
  S.screen = nextScreen(); S.tab = "tonight"; render();
  if (S.screen === "app") await afterReady();
}
async function signIn() {
  const id = document.getElementById("si-email").value, password = document.getElementById("si-pw").value;
  const device = /iPhone|iPad|Android|Mobile/i.test(navigator.userAgent || "") ? "Phone" : "Computer";
  const r = await api("/api/signin", "POST", { ...signInBody(id, password), device });
  keys.put(r.user_id, r.key, r.name);
  S.userId = r.user_id; store.set("smartplate.user", String(S.userId));
  await loadProfile(); S.signin = null;
  S.screen = nextScreen(); render();
  if (S.screen === "address") await loadAddresses(); else await afterReady();
}
async function connectSwiggy() {
  const r = await api(`/api/user/${S.userId}/swiggy/connect`, "POST", { ui: "v3" });
  location.href = r.authorize_url;
}
async function loadAddresses() { S.addrs = await api(`/api/user/${S.userId}/swiggy/addresses`); render(); }
async function chooseAddress(id) {
  S.swiggy = await api(`/api/user/${S.userId}/swiggy/address`, "POST", { address_id: id });
  S.view = await api(`/api/plan/${S.view.plan.id}`);
  S.carts = {}; S.opts = null; S.sync = null; S.sheet = null; S.addrs = null;
  await refreshLiveCart();
  S.screen = "app"; S.tab = "tonight";
  toast(`Delivering to ${S.swiggy.address?.label || "this address"}`); render();
  await afterReady();
}
function adopt(view) { S.view = view; S.opts = null; }
async function addToCart(sid) {
  if (S.adding) return;
  S.adding = sid; render();
  let r;
  try {
    const preview = await api(`/api/session/${sid}/swiggy-cart/preview`);
    r = await api(`/api/session/${sid}/swiggy-cart`, "POST", { expected_fingerprint: preview.fingerprint });
  } finally { S.adding = null; }
  S.carts[sid] = r; S.fresh = sid;
  S.liveCart = { item: r.item, restaurant: r.restaurant, to_pay: r.to_pay, bill: r.bill, checkout_url: r.checkout_url };
  S.view = await api(`/api/plan/${S.view.plan.id}`);          // the real bill now counts in the week
  toast("In your Swiggy cart. Check the bill, then pay in Swiggy.");
  render();
}
async function choose(sid, body, msg) {
  adopt(await api(`/api/session/${sid}/choose`, "POST", body));
  S.sheet = null; toast(msg); render(); loadOptions();
}
async function confirmMeal(sid) {
  adopt(await api(`/api/session/${sid}/confirm`, "POST", {}));
  delete S.carts[sid]; S.sheet = null;
  toast("Marked as had. Your week's money is up to date."); render(); loadOptions();
}
async function setStatus(sid, status, undo = true) {
  await api(`/api/session/${sid}/status`, "POST", { status });
  adopt(await api(`/api/plan/${S.view.plan.id}/optimize`, "POST", {}));
  S.sheet = null;
  toast(status === "skipped" ? "Skipped. That money is back in your week." : "Back in the plan.",
    undo && status === "skipped" ? () => setStatus(sid, "active", false) : null);
  render(); loadOptions();
}
async function rate(sid, score) {
  const r = await api(`/api/session/${sid}/rate`, "POST", { score });
  adopt(r.plan); toast(score > 0 ? "Noted: more like this." : "Noted: Ziggy won't plan it again for a while."); render();
}
async function replanRest(sid) {
  const r = await api(`/api/plan/${S.view.plan.id}/replan-remaining`, "POST", { session_id: sid });
  adopt(r.plan);
  if (S.carts[sid]) S.carts[sid] = { ...S.carts[sid], budget: r.budget };
  toast(r.budget.over ? `Still ${rupee(r.budget.over_by)} over: this cart alone is more than the money left.` : `Re-planned the rest of the week. ${rupee(r.budget.left)} left.`);
  render();
}
async function openSheet(sid) {
  S.sheet = { kind: "change", sid: Number(sid), data: null }; render();
  S.sheet.data = await api(`/api/session/${sid}/options`); render();
}
async function saveRules() {
  const f = document.getElementById("rules");
  const pick = (name) => [...f.querySelectorAll(`[data-rule="${name}"][aria-pressed="true"]`)].map(b => b.dataset.val);
  const body = { diet: pick("diet")[0], allergens: pick("allergens"), medical: pick("medical"),
    weekly_budget: Number(document.getElementById("rl-budget").value) };
  const cap = Number(document.getElementById("rl-cap").value);
  body.daily_cap = cap > 0 ? cap : null;
  const meals = pick("meals");
  if (!meals.length) throw new Error("Pick at least one meal for Ziggy to decide.");
  body.rhythm = Object.fromEntries(MEALS.map(m => [m, meals.includes(m) ? "order" : "skip"]));
  adopt(await api(`/api/user/${S.userId}/setup`, "PATCH", body));
  S.sheet = null; toast("Saved. The rest of the week re-planned inside your rules."); render(); loadOptions();
}
/* The last onboarding step: the Ziggy account, by email. Asked only after the first meal,
   and never mixed up with Swiggy's own sign-in. */
async function saveAccount() {
  const email = document.getElementById("ac-email").value, password = document.getElementById("ac-pw").value;
  const name = (document.getElementById("ac-name")?.value || "").trim();
  const problem = accountProblem(email, password);
  if (problem) throw new Error(problem);
  S.account = await api(`/api/user/${S.userId}/account`, "POST", { email: email.trim(), password });
  if (name) {
    adopt(await api(`/api/user/${S.userId}/setup`, "PATCH", { name }));
    keys.put(S.userId, keys.get(S.userId), S.view.user.name);
  }
  toast(`Saved. Sign in with ${S.account.email || S.account.login} on any device.`); render();
}
async function signOut() {
  await api(`/api/user/${S.userId}/signout`, "POST", {}).catch(() => {});
  forgetProfile(); S.screen = "welcome"; render(); toast("Signed out of this device");
}
/* The nudge at order time: Web Push at the order-by time (even with Ziggy closed); in-tab
   alerts where push isn't available. Same server endpoints as the classic app. */
const hasPush = () => "serviceWorker" in navigator && "PushManager" in window && typeof Notification !== "undefined";
const remindersOn = () => typeof Notification !== "undefined" && Notification.permission === "granted"
  && (store.get("smartplate.push") === String(S.userId) || store.get("smartplate.notify") === "1");
function keyBytes(b64) {
  const raw = atob((b64 + "=".repeat((4 - b64.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}
async function turnOnReminders() {
  if (typeof Notification === "undefined") throw new Error("This browser can't show reminders. Add them to your calendar from More.");
  if (await Notification.requestPermission() !== "granted") throw new Error("Notifications are blocked for this site. Add reminders to your calendar from More.");
  if (hasPush()) {
    const reg = await navigator.serviceWorker.ready;
    const st = await api(`/api/user/${S.userId}/push`);
    const old = await reg.pushManager.getSubscription();
    if (old) await old.unsubscribe().catch(() => {});
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(st.public_key) });
    await api(`/api/user/${S.userId}/push/subscribe`, "POST", { subscription: sub.toJSON() });
    store.set("smartplate.push", String(S.userId));
  } else store.set("smartplate.notify", "1");
  toast("Reminders on. Ziggy nudges you at each order-by time."); render();
}
async function downloadIcs() {
  const r = await fetch(`/api/user/${S.userId}/reminders.ics`, { headers: { "X-SmartPlate-Key": keys.get(S.userId) || "" } });
  if (!r.ok) throw new Error("The calendar file isn't available right now.");
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a"); a.href = url; a.download = "ziggy-order-times.ics";
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
}

/* ================================================================ render */
function render() {
  const app = document.getElementById("app");
  const y = window.scrollY, focus = document.activeElement?.id;
  readSetup();
  let html;
  if (S.screen === "boot") html = `<div class="boot">Loading Ziggy…</div>`;
  else if (S.screen === "welcome") html = S.signin ? signInScreen() : welcomeScreen();
  else if (S.screen === "setup") html = setupScreen();
  else if (S.screen === "planning") html = planningScreen();
  else if (S.screen === "connect") html = connectScreen();
  else if (S.screen === "address") html = addressScreen();
  else html = shell();
  app.innerHTML = html + (S.sheet ? sheet() : "");
  document.body.classList.toggle("sheet-open", !!S.sheet);
  if (focus && document.getElementById(focus)) document.getElementById(focus).focus({ preventScroll: true });
  if (!S.sheet) window.scrollTo(0, y);
}
function errbar() {
  if (!S.error) return "";
  const rule = S.errorRule;
  return `<div class="errbar" role="alert"><div class="stack" style="gap:4px;flex:1"><span>${esc(S.error)}</span>
    ${rule?.fix ? `<span class="fine">${esc(rule.fix)}</span>` : ""}</div>
    ${RECONNECT.has(S.errorCode) ? `<button class="small" data-act="connect">Connect Swiggy</button>` : S.retry ? `<button class="small" data-act="retry">Try again</button>` : ""}
    <button class="icon-btn" data-act="dismiss-error" aria-label="Dismiss">${ICON.close}</button></div>`;
}

/* ---- first run: one screen of rules, then Swiggy ---- */
function welcomeScreen() {
  return `<main class="welcome v3-welcome">${mascot("float")}
    <h1 class="hero-h">Tonight's dinner, decided.</h1>
    <p class="sub">Inside your weekly food budget and your diet rules, from places you already order from. One tap puts it in your real Swiggy cart.</p>
    <ul class="promise"><li>Set your budget and rules in under a minute</li><li>See Swiggy's real bill before you pay, in Swiggy</li><li>Know what's left this week, from real bills</li></ul>
    ${errbar()}<div class="grow"></div>
    <button class="primary big" data-act="start">Get started</button>
    <button class="link" data-act="signin-open">I already have a profile</button>
    <a class="fine center" href="/">Open the classic Ziggy</a></main>`;
}
function signInScreen() {
  return `<main class="welcome"><button class="back secondary" data-act="signin-close">${ICON.back}Back</button>
    <h1 class="h1">Sign in</h1>${errbar()}
    <p class="sub">Your Ziggy account email. (Older accounts: your sign-in name works too.)</p>
    <form id="signin" class="stack card"><label>Email<input id="si-email" type="email" inputmode="email" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
      <label>Password<input id="si-pw" type="password" autocomplete="current-password" required></label>
      <button class="primary big" type="submit">Sign in</button></form></main>`;
}
const ALLERGIES = ["peanut", "dairy", "gluten", "egg", "soy", "shellfish", "fish", "sesame", "tree_nut"];
const MEDICAL = [["diabetes", "Low sugar (diabetes)"], ["hypertension", "Less salt (BP)"], ["celiac", "Celiac"]];
const DIETS = [["veg", "Veg"], ["nonveg", "Non-veg"], ["vegan", "Vegan"]];
const PRESETS = [1500, 2500, 4000];
const tog = (group, val, label, on) => `<button type="button" class="chip" data-su="${group}" data-val="${esc(val)}" aria-pressed="${!!on}">${label}</button>`;
function setupScreen() {
  const d = S.setup, n = Math.max(1, (d.meals || []).length * 7);
  const per = d.weekly_budget > 0 ? `About ${rupee0(d.weekly_budget / n)} a meal for ${n} meals a week` : "Enter an amount";
  return `<main class="onboard v3-setup"><div class="ob-top"><button class="icon-btn" data-act="setup-cancel" aria-label="Back">${ICON.back}</button>
      <span class="wordmark" style="font-size:18px">Your rules</span><span class="fine" style="margin-left:auto">Step 1 of 3</span></div>
    <form id="setup" class="stack" novalidate style="gap:22px">
      <fieldset><legend>What do you eat?</legend>
        <div class="seg v3-diet" role="group" aria-label="Diet">${DIETS.map(([k, l]) => `<button type="button" data-su="diet" data-val="${k}" aria-pressed="${d.diet === k}"><span class="dot ${k === "nonveg" ? "nonveg" : ""}"></span>${l}</button>`).join("")}</div></fieldset>
      <fieldset><legend>Never show me</legend>
        <div class="chips">${ALLERGIES.map(a => tog("allergens", a, cap1(a.replace("_", " ")), d.allergens.includes(a))).join("")}</div>
        <details${d.medical.length ? " open" : ""}><summary>Medical rules</summary><div class="chips">${MEDICAL.map(([k, l]) => tog("medical", k, l, d.medical.includes(k))).join("")}</div></details>
        <p class="fine">Hard rules, never bent. Swiggy menus don't list ingredients, so with an allergy or medical rule Ziggy opens the dish in Swiggy instead of filling the cart.</p></fieldset>
      <fieldset><legend>Weekly food budget</legend>
        <div class="v3-budget"><label class="sr" for="su-budget">Weekly budget in rupees</label><span class="money v3-rs" aria-hidden="true">₹</span>
          <input id="su-budget" class="money" type="number" inputmode="numeric" min="100" max="100000" value="${d.weekly_budget ?? ""}"></div>
        <div class="chips">${PRESETS.map(p => tog("budget", p, rupee0(p), d.weekly_budget === p)).join("")}</div>
        <p class="fine">${per}. Delivery fees count. Swiggy's cart shows the exact bill before you pay.</p></fieldset>
      <fieldset><legend>Meals Ziggy decides</legend>
        <div class="chips">${[["dinner", "Dinner"], ["lunch", "Lunch"], ["breakfast", "Breakfast"]].map(([k, l]) => tog("meals", k, l, d.meals.includes(k))).join("")}</div></fieldset>
    </form>${errbar()}<div class="grow"></div>
    <button class="primary big" data-act="setup-done">Plan tonight</button></main>`;
}
function planningScreen() {
  return `<main class="planning" aria-busy="true" aria-live="polite">${mascot("bob")}<h1 class="h1">Fitting tonight into your week</h1>
    <div class="steps"><div><span class="tick">✓</span><span>Reading your rules</span></div><div><span class="tick">✓</span><span>Spreading your budget across the week</span></div>
    <div><span class="tick">✓</span><span>Setting order-by times</span></div></div></main>`;
}
function connectScreen() {
  return `<main class="welcome v3-connect">${mascot("float")}<p class="eyebrow">Step 2 of 3 · Swiggy</p>
    <h1 class="h1">Connect Swiggy for real dishes and real bills</h1>
    <p class="sub">Ziggy reads menus near your address and fills your Swiggy cart. You sign in on Swiggy's own page, and you always pay in Swiggy. Ziggy never orders or pays for you.</p>
    ${errbar()}<div class="grow"></div>
    <button class="primary big" data-act="connect">Connect Swiggy</button>
    <button class="link" data-act="connect-later">Not now</button></main>`;
}
function addressScreen() {
  const list = S.addrs;
  return `<main class="welcome v3-connect"><p class="eyebrow">Step 2 of 3 · Swiggy connected</p><h1 class="h1">Where should dinner come to?</h1>
    <p class="sub">Your saved Swiggy addresses. Menus and prices depend on it.</p>${errbar()}
    <div class="stack" style="gap:10px">${!list ? `<div class="skeleton" aria-busy="true"><i></i><i></i><i></i></div>`
      : list.length ? list.map(a => `<button class="opt" data-addr="${esc(a.id)}">${ICON.place}<span><b>${esc(a.label)}</b><span class="muted">${esc(a.text || "")}</span></span></button>`).join("")
      : `<p class="sub">Swiggy has no saved address for this account. Add one in the Swiggy app, then come back.</p><button class="secondary" data-act="addr-refresh">Check again</button>`}</div></main>`;
}

/* ---- the app: three tabs ---- */
function shell() {
  const tabs = [["tonight", "Tonight"], ["week", "Week"], ["more", "More"]];
  const body = S.tab === "week" ? weekScreen() : S.tab === "more" ? moreScreen() : tonightScreen();
  return `<div class="shell v3-shell">
    <nav class="nav" aria-label="Main"><div class="brand-rail">${mark()}<span class="wordmark">Ziggy</span></div>
      ${tabs.map(([k, l]) => `<button class="tab" data-tab="${k}" ${S.tab === k ? 'aria-current="page"' : ""}>${ICON[k]}<span>${l}</span></button>`).join("")}</nav>
    <div class="col">${topbar()}<main class="main" id="main">${errbar()}${body}</main></div></div>`;
}
function topbar() {
  const m = weekMoney(S.view), sw = S.swiggy;
  const addr = sw?.connected ? (sw.address ? `<button class="addr secondary" data-act="addr-open" aria-label="Delivering to ${esc(sw.address.label)}. Change">${ICON.place}<span>${esc(sw.address.label.split(" · ")[0])}</span>${ICON.down}</button>`
    : `<button class="addr secondary warn" data-act="addr-open">${ICON.place}<span>Choose address</span></button>`) : "";
  return `<header class="topbar">${mark()}${addr}<span class="spacer"></span>
    <button class="v3-left ${m.left < 0 ? "over" : ""}" data-tab="week" aria-label="${m.left < 0 ? `${rupee0(-m.left)} over this week's budget` : `${rupee0(m.left)} left this week`}. See the week">
      <b>${rupee0(Math.abs(m.left))}</b><span>${m.left < 0 ? "over" : "left this week"}</span></button></header>`;
}

/* ---- Tonight ---- */
function tonightScreen() {
  const v = S.view, nu = v.next_up, src = v.source || {};
  if (S.sync === "loading") return `<section class="card hero" aria-busy="true"><p class="eyebrow">Swiggy · ${esc(S.swiggy?.address?.label || "")}</p>
    <h2 class="h2">Reading real menus near you…</h2><div class="skeleton" aria-hidden="true"><i></i><i></i><i></i></div></section>`;
  if (S.sync?.error) return `<section class="empty" role="status">${mascot()}<h2 class="h2">No live pick yet</h2><p class="sub">${esc(S.sync.error)}</p>
    <button class="primary" data-act="sync-again">Try again</button></section>`;
  if (src.kind === "none") {
    const needs = src.needs || (src.connected ? "sync" : "connect");
    if (needs === "address") return `<section class="empty" role="status">${mascot()}<h2 class="h2">Pick a delivery address</h2><p class="sub">Real dishes and prices depend on where Swiggy delivers.</p><button class="primary" data-act="addr-open">Choose address</button></section>`;
    if (needs === "sync") return `<section class="empty" role="status">${mascot()}<h2 class="h2">See real dishes near you</h2><button class="primary" data-act="sync-again">Read Swiggy menus</button></section>`;
    return `<section class="empty" role="status">${mascot()}<h2 class="h2">Ziggy picks from real places only</h2>
      <p class="sub">${esc(src.note || "Connect Swiggy so tonight's pick comes from real menus near you, with real prices.")}</p>
      ${signInOpen() ? `<button class="primary" data-act="connect">Connect Swiggy</button>` : ""}</section>`;
  }
  if (!nu) return `<section class="empty">${mascot()}<h2 class="h2">Nothing left to decide this week</h2><p class="sub">Ziggy plans next week as soon as it starts.</p></section>`;
  const c = nu.cell;
  return `${src.kind === "sample" ? `<p class="fine">Sample dishes (test data)</p>` : ""}
    ${SPENT.has(c.status) ? hadCard(nu) : S.carts[c.session_id] ? billCard(nu) : pickCard(nu)}
    ${laterLine(nu)}`;
}
function why(c) {
  const u = S.view.user || {}, m = weekMoney(S.view), out = [];
  if (c.pinned) out.push(["Your pick", "ok"]);
  if (c.pooled) out.push([`From your ${c.meal || "meal"} list`, "ok"]);
  else if (c.usual) out.push(["A place you use", ""]);
  out.push(m.left >= 0 ? [`Week stays in budget`, "ok"] : [`Week is ${rupee0(-m.left)} over`, "warn"]);
  if (u.diet === "veg") out.push(["Vegetarian", "ok"]); else if (u.diet === "vegan") out.push(["Vegan", "ok"]);
  const al = (u.allergens || []).map(a => a.replace("_", " "));
  if (al.length) out.push([`No ${al.join(", ")} by dish name`, "ok"]);
  return `<ul class="why" aria-label="Why this pick">${out.map(([t, k]) => `<li class="${k}">${esc(t)}</li>`).join("")}</ul>`;
}
function orderBy(c) {
  const o = c.order || {};
  if (!o.order_at) return "";
  const nudge = remindersOn() ? `<span class="fine v3-on">${ICON.bell}Reminder on</span>`
    : typeof Notification !== "undefined" ? `<button class="small ghost" data-act="remind">${ICON.bell}Remind me</button>` : "";
  return `<div class="v3-orderby"><span><b>Order by ${esc(o.order_at)}</b><span class="muted"> · ${esc(o.why || "")}</span></span>${nudge}</div>`;
}
function pickCard(nu) {
  const c = nu.cell, isCook = c.kind === "cook", v = S.view, live = swiggyReady();
  const adding = S.adding === c.session_id;
  let cta;
  if (isCook) cta = `<button class="primary" data-confirm="${c.session_id}">I cooked it</button>`;
  else if (live && cartEligible(v, c)) cta = `<button class="primary" data-cart="${c.session_id}" ${adding ? 'aria-busy="true"' : ""}>${adding ? `${roller()}<span>Adding to your cart…</span>` : (c.portions || 1) > 1 ? `Add ${c.portions} to Swiggy cart` : "Add to Swiggy cart"}</button>`;
  else if (!live && signInOpen() && v.source?.kind === "live") cta = `<button class="primary" data-act="connect">Connect Swiggy to add</button>`;
  else if (c.handoff_url) cta = `<a class="btn primary" href="${esc(c.handoff_url)}" target="_blank" rel="noopener">Open in Swiggy ${ICON.out}</a>`;
  else cta = "";
  const ruleNote = !isCook && live && !cartEligible(v, c)
    ? `<p class="fine">Swiggy's menu can't prove this dish is safe for ${ruleBound(v.user) ? "your" : "everyone's"} rules, so Ziggy won't fill the cart. Check with the restaurant in Swiggy, then tap “I ordered it”.</p>
       <button class="secondary" data-confirm="${c.session_id}">I ordered it</button>` : "";
  return `<section class="card hero v3-hero rise" aria-label="${esc(pickTitle(nu))}">
    <p class="eyebrow">${esc(pickTitle(nu))}</p>
    <div class="dish-row">${dishIcon(c.item, { cook: isCook, lg: true, image: c.image })}<div class="t">
      <h1 class="h2">${esc(c.item)}</h1>
      <span class="muted">${isCook ? "Cook at home" : esc(c.restaurant)}${!isCook && c.rating ? ` · ${Number(c.rating).toFixed(1)}★` : ""}</span></div></div>
    <div class="price-line"><span class="money">${c.real_bill ? real(c.cost) : est(c.cost)}</span><span class="fine">${isCook ? "groceries, estimated" : c.real_bill ? "Swiggy's bill" : "with delivery · Swiggy shows the exact bill"}</span></div>
    ${why({ ...c, meal: nu.meal })}
    ${isCook ? "" : orderBy(c)}
    ${cta}${ruleNote}
    ${alternativesBlock(c, nu.meal)}
    <div class="v3-links"><button class="link" data-sheet="${c.session_id}">More choices</button><button class="link" data-skip="${c.session_id}">Skip ${esc(nu.meal)}</button></div>
  </section>`;
}
function alternativesBlock(c, meal) {
  const o = S.opts;
  if (!o || o.sid !== c.session_id) return "";
  if (!o.data) return `<div class="v3-alts" aria-busy="true"><p class="k">Or instead</p><div class="skeleton"><i></i><i></i></div></div>`;
  const alts = alternatives(o.data, 3);
  if (!alts.length) return "";
  const cur = Number(c.cost) || 0;
  return `<div class="v3-alts" role="group" aria-labelledby="alts-h"><p class="k" id="alts-h">Or instead · one tap swaps ${esc(meal)}</p>
    ${alts.map(x => { const d = Math.round(x.price - cur);
      return `<button class="v3-alt${x.fits === false ? " over" : ""}" data-pick="${x.item_id}" data-pick-name="${esc(x.name)}" data-pick-sid="${c.session_id}"
        aria-label="${esc(`${x.name} from ${x.restaurant}, about ${rupee0(x.price)}${x.fits === false ? ", over budget" : ""}`)}">
        ${dishIcon(x.name, { image: x.image })}<span class="t"><b>${esc(x.name)}</b><span>${x.source === "pool" ? "Your list · " : ""}${esc(x.restaurant)}</span></span>
        <span class="pr">${est(x.price)}<span class="${d > 0 ? "up" : "dn"}">${d === 0 ? "same" : d > 0 ? `+${rupee0(d)}` : `−${rupee0(-d)}`}</span></span></button>`; }).join("")}
  </div>`;
}
function billCard(nu) {
  const c = nu.cell, r = S.carts[c.session_id], m = weekMoney(S.view);
  const over = r.budget?.over && !r.budgetOk;
  const diff = r.over_plan == null ? "" : r.over_plan > 0 ? `${rupee(r.over_plan)} more than the estimate` : r.over_plan < 0 ? `${rupee(-r.over_plan)} less than the estimate` : "same as the estimate";
  return `<section class="card hero v3-hero rise" aria-label="${esc(pickTitle(nu))}: in your Swiggy cart">
    <p class="eyebrow">${esc(pickTitle(nu))} · in your Swiggy cart</p>
    <div class="dish-row">${dishIcon(c.item, { lg: true, image: c.image })}<div class="t"><h1 class="h2">${esc(r.item || c.item)}</h1><span class="muted">${esc(r.restaurant || c.restaurant)}${r.quantity > 1 ? ` · ${r.quantity} portions` : ""}</span></div></div>
    <div class="bill-card${S.fresh === c.session_id ? " fresh" : ""}" role="status"><div class="ok-head">${mascot(S.fresh === c.session_id ? "hop" : "", "")}Swiggy's bill</div>
      ${r.bill ? `<table class="bill">${r.bill.lines.map(l => `<tr><td>${esc(l.label)}</td><td class="num">${real(l.amount)}</td></tr>`).join("")}
        <tr class="total"><td><b>To Pay</b></td><td class="num"><b>${real(r.bill.to_pay)}</b></td></tr></table>`
        : r.to_pay != null ? `<div>To Pay ${real(r.to_pay)}</div>` : `<p class="fine">Swiggy didn't return a total. Check it in Swiggy.</p>`}
      ${r.planned_cost != null && r.to_pay != null ? `<p class="fine">Plan estimate ${est(r.planned_cost)}: ${diff}.</p>` : ""}</div>
    <p class="v3-after">${m.left >= 0 ? `With this bill, <b>${rupee0(m.left)}</b> is left for the rest of the week` : `With this bill the week is <b>${rupee0(-m.left)}</b> over budget`}<span class="fine"> (rest of the week is estimated)</span></p>
    ${over ? `<div class="overbudget" role="alert"><div><b>Over budget.</b> ${esc(r.budget.message || "")}</div>
      <div class="two"><button data-over-ok="${c.session_id}">Keep it anyway</button><button data-replan="${c.session_id}">Re-plan the rest</button></div></div>`
      : `<a class="btn primary" href="${esc(r.checkout_url || "https://www.swiggy.com/checkout")}" target="_blank" rel="noopener" data-paid="${c.session_id}">Pay in Swiggy ${ICON.out}</a>`}
    <button class="secondary" data-confirm="${c.session_id}">${ICON.check}I've ordered it</button>
    <p class="fine">${esc(r.cancellation_note || "You pay in Swiggy, and Swiggy's cancellation policy applies. Ziggy never pays for you.")}</p>
  </section>`;
}
function hadCard(nu) {
  const c = nu.cell, g = c.rating_given;
  return `<section class="card hero v3-hero rise" aria-label="${esc(pickTitle(nu))}: had">
    <p class="eyebrow">${esc(pickTitle(nu))} · ${c.status === "ordered" ? "ordered" : "had"}</p>
    <div class="dish-row">${dishIcon(c.item, { cook: c.kind === "cook", lg: true, image: c.image })}<div class="t"><h1 class="h2">${esc(c.item)}</h1><span class="muted">${c.real_bill ? real(c.cost) : est(c.cost)}</span></div></div>
    <p class="muted">How was it? One tap teaches Ziggy.</p>
    <div class="two"><button class="secondary" data-rate="${c.session_id}:1" aria-pressed="${g === 1}">Good</button><button class="secondary" data-rate="${c.session_id}:-1" aria-pressed="${g === -1}">Not again</button></div></section>
    ${accountStep()}`;
}
/* Step 3 of 3, once the first meal is done: save the Ziggy account (email + password). */
function accountStep() {
  if (!S.account || S.account.login || store.get(`ziggy.v3.noaccount.${S.userId}`)) return "";
  return `<section class="card v3-account rise2" aria-labelledby="acct-step-h"><p class="eyebrow">Last step · your Ziggy account</p>
    <h2 class="h2" id="acct-step-h">Keep this plan on every device</h2>
    <p class="sub">Save it with your email. This is your <b>Ziggy</b> account: it holds your rules, budget and week. Swiggy stays signed in separately, on Swiggy's page.</p>
    ${accountForm(true)}
    <button class="link" data-act="account-later">Not now</button></section>`;
}
function accountForm(primary) {
  return `<form id="account" class="stack" novalidate>
      <label>Email<input id="ac-email" type="email" inputmode="email" autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="254" required placeholder="name@example.com"></label>
      <label>Password<input id="ac-pw" type="password" autocomplete="new-password" minlength="8" maxlength="200" required placeholder="8 or more characters"></label>
      ${S.view?.user?.name === "Me" ? `<label>Your name (optional)<input id="ac-name" maxlength="80" autocomplete="given-name"></label>` : ""}
      <button class="${primary ? "primary big" : "secondary"}" type="submit">Save my Ziggy account</button>
      <p class="fine">Ziggy never emails you or shares it. Forgot the password? Open Ziggy on a signed-in device and set a new one.</p></form>`;
}
function laterLine(nu) {
  // One quiet line about what comes next: the week is planned, not shown.
  const grid = S.view.grid || [];
  const after = [];
  for (let i = nu.day_index; i < grid.length && after.length < 1; i++) {
    for (const m of MEALS) {
      if (i === nu.day_index && MEALS.indexOf(m) <= MEALS.indexOf(nu.meal)) continue;
      const c = grid[i].meals[m];
      if (c && c.kind === "delivery" && c.status === "active") { after.push({ c, m, day: i === nu.day_index ? "Later today" : i === nu.day_index + 1 ? "Tomorrow" : grid[i].day }); break; }
    }
  }
  if (!after.length) return "";
  const { c, m, day } = after[0];
  return `<button class="v3-next" data-tab="week"><span class="k">${esc(day)} · ${esc(m)}</span><span><b>${esc(c.item)}</b> <span class="muted">· ${esc(c.restaurant)}</span></span><span class="pr">${est(c.cost)}${ICON.right}</span></button>`;
}

/* ---- Week: money left, reconciled from real bills ---- */
function weekScreen() {
  const v = S.view, m = weekMoney(v), grid = v.grid || [];
  const pct = (x) => m.budget > 0 ? Math.max(0, Math.min(100, (x / m.budget) * 100)) : 0;
  const settle = m.unconfirmed.length ? `<section class="card rise2" aria-labelledby="settle-h"><h2 class="h3" id="settle-h">Did you have these?</h2>
      <p class="fine">Past meals count only once you say so, so the money left stays true.</p>
      ${m.unconfirmed.map(c => `<div class="v3-settle"><span class="t"><b>${esc(c.item)}</b><span class="fine">${esc(c.day)} ${esc(c.meal)} · ${est(c.cost)}</span></span>
        <button class="small secondary" data-confirm="${c.session_id}">Yes</button><button class="small ghost" data-skip-quiet="${c.session_id}">No</button></div>`).join("")}</section>` : "";
  const days = grid.map((d, i) => {
    const rows = MEALS.map(meal => {
      const c = d.meals[meal];
      if (!c) return "";
      const money = c.kind === "skip" || c.status === "skipped" ? `<span class="fine">skipped</span>` : c.real_bill ? real(c.cost) : est(c.cost);
      const state = SPENT.has(c.status) ? "had" : c.status === "past" ? "check" : c.real_bill ? "in cart" : "";
      const tappable = c.status === "active" && ["delivery", "cook"].includes(c.kind);
      return `<${tappable ? `button data-sheet="${c.session_id}"` : "div"} class="mrow v3-mrow${c.status === "skipped" || c.kind === "skip" ? " off" : ""}">
        ${dishIcon(c.item, { cook: c.kind === "cook", image: c.image })}<span class="t"><span class="lbl">${esc(meal)}${state ? ` · ${state}` : ""}</span><b>${esc(c.kind === "skip" ? "Not planned" : c.item)}</b>
        <span class="s">${esc(c.kind === "cook" ? "Cook at home" : c.restaurant || "")}</span></span><span class="p">${money}</span></${tappable ? "button" : "div"}>`;
    }).join("");
    if (!rows) return "";
    return `<section class="v3-day" aria-label="${esc(d.day)} ${esc(d.date)}"><h3 class="k">${esc(d.day)} · ${esc(new Date(d.date + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" }))}</h3><div class="card tight">${rows}</div></section>`;
  }).join("");
  return `<section class="card v3-money rise" aria-labelledby="money-h">
      <p class="k" id="money-h">This week's food money</p>
      <div class="v3-big"><span class="money ${m.left < 0 ? "bad" : ""}">${rupee0(Math.abs(m.left))}</span><span class="muted">${m.left < 0 ? "over" : "left"} of ${rupee0(m.budget)}${m.prorated ? " (this part of the week)" : ""}</span></div>
      <div class="v3-bar" role="img" aria-label="${esc(`Paid from Swiggy bills ${rupee0(m.hadReal + m.cartReal)}, estimated ${rupee0(m.hadEst)}, still planned ${rupee0(m.planned)}, left ${rupee0(Math.max(0, m.left))}`)}">
        <i class="r" style="width:${pct(m.hadReal + m.cartReal)}%"></i><i class="e" style="width:${pct(m.hadEst)}%"></i><i class="p" style="width:${pct(m.planned)}%"></i></div>
      <dl class="v3-key">
        <div><dt><i class="r"></i>From Swiggy bills</dt><dd>${real(m.hadReal + m.cartReal)}</dd></div>
        ${m.hadEst ? `<div><dt><i class="e"></i>Had, no bill seen</dt><dd>${est(m.hadEst)}</dd></div>` : ""}
        <div><dt><i class="p"></i>Still planned</dt><dd>${est(m.planned)}</dd></div>
        ${m.unconfirmed.length ? `<div><dt><i class="u"></i>Not confirmed yet</dt><dd>${m.unconfirmed.length} meal${m.unconfirmed.length === 1 ? "" : "s"}</dd></div>` : ""}
      </dl>
      ${m.dailyCap ? `<p class="fine">Daily cap ${rupee0(m.dailyCap)}.</p>` : ""}
      <p class="fine">Bills are Swiggy's own totals for carts Ziggy filled. Everything else is an estimate until Swiggy prices it.</p></section>
    ${settle}
    <h2 class="h3 v3-sec">What's planned</h2><p class="fine" style="margin-top:-10px">Tap a meal to change it. The rest re-balances inside your budget.</p>
    ${days}`;
}

/* ---- More: rules, Swiggy, reminders, account; everything else in the classic app ---- */
function moreScreen() {
  const u = S.view.user || {}, sw = S.swiggy, a = S.account;
  const rules = [{ veg: "Vegetarian", vegan: "Vegan", nonveg: "Non-veg" }[u.diet], ...(u.allergens || []).map(x => `no ${x.replace("_", " ")}`), ...(u.medical || [])].filter(Boolean).join(" · ");
  const row = (attrs, label, value) => `<button class="set" ${attrs}><span>${label}</span><span><span>${value}</span>${ICON.right}</span></button>`;
  const classic = (tab, label, value) => `<a class="set" href="/?tab=${tab}"><span>${label}</span><span><span>${value}</span>${ICON.out}</span></a>`;
  return `<h1 class="h1 rise">More</h1>
    <section class="card tight rise2" aria-label="Your rules">
      ${row('data-act="rules"', "Diet &amp; budget", esc(rules || "Your rules") + ` · ${rupee0(u.weekly_budget)}/wk`)}
      ${row(sw?.connected ? 'data-act="addr-open"' : 'data-act="connect"', "Swiggy", sw?.connected ? (sw.address ? `Delivering to ${esc(sw.address.label.split(" · ")[0])}` : "Choose an address") : "Not connected")}
      <div class="set"><span>Order-time reminders</span>${remindersOn() ? `<span class="fine">On</span>` : `<button class="small secondary" data-act="remind">Turn on</button>`}</div>
      ${row('data-act="ics"', "Add order times to my calendar", ".ics")}
    </section>
    <section class="card rise2" aria-labelledby="acct-h"><h2 class="k" id="acct-h">Your Ziggy account</h2>
      ${a?.login ? `<p class="sub">Signed in as <b>${esc(a.login)}</b>${a.email ? "" : " (an older sign-in name)"}. Use it on any phone or computer.</p>
        ${a.email ? "" : `<p class="fine">Move to an email sign-in:</p>${accountForm(false)}`}`
        : `<p class="sub">Not saved yet: this plan lives only in this browser.</p>${accountForm(false)}`}
      <button class="ghost small" data-act="signout">Sign out of this device</button></section>
    <section class="card tight rise3" aria-labelledby="classic-h"><h2 class="k" id="classic-h" style="padding-top:14px">In the classic app</h2>
      ${classic("saved", "Meal lists &amp; saved places", "Per-meal pools")}
      ${classic("week", "Cooking, groceries &amp; recipes", "Cook days")}
      ${classic("more", "Household &amp; fair split", "Who's eating")}
      ${classic("more", "Festivals, fasts &amp; weather", "Heads-up")}
      ${classic("more", "Nutrition &amp; insights", "Estimates")}
      ${classic("week", "Order several meals at once", "Week")}
      ${classic("more", "Receipts &amp; CSV", "Spending")}
    </section>
    <p class="fine center">This is Ziggy v3, an experiment. <a href="/">Switch to classic Ziggy</a> · <a href="/credits">Credits</a></p>`;
}

/* ---- sheets ---- */
function sheet() {
  const s = S.sheet;
  let body = "";
  if (s.kind === "change") body = changeSheet();
  else if (s.kind === "rules") body = rulesSheet();
  else if (s.kind === "addr") body = `<div class="sheet-head"><h2 class="h2" id="sheet-title">Deliver to</h2><button class="icon-btn" data-close-sheet aria-label="Close">${ICON.close}</button></div>
    <div class="stack" style="gap:10px">${!S.addrs ? `<div class="skeleton"><i></i><i></i></div>` : S.addrs.map(a => `<button class="opt" data-addr="${esc(a.id)}" aria-pressed="${S.swiggy?.address?.id === a.id}">${ICON.place}<span><b>${esc(a.label)}</b><span class="muted">${esc(a.text || "")}</span></span></button>`).join("")}</div>
    <p class="fine">Changing the address re-reads menus there and re-plans the rest of the week.</p>`;
  return `<div class="sheet-bg" data-close-sheet><section class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title"><span class="grab" aria-hidden="true"></span>${body}</section></div>`;
}
function changeSheet() {
  const d = S.sheet.data;
  if (!d) return `<p class="fine" id="sheet-title">Loading choices…</p><div class="skeleton"><i></i><i></i><i></i></div>`;
  const s = d.session, L = d.limits || {};
  const left = L.left_day != null ? Math.min(L.left_week, L.left_day) : L.left_week;
  const rows = (list, place) => list.filter(x => !x.current).map(x => `<button class="pick ${x.fits ? "" : "over"}" data-pick="${x.item_id}" data-pick-name="${esc(x.name)}" data-pick-sid="${s.id}">
      ${dishIcon(x.name, { image: x.image })}<span class="t"><b>${esc(x.name)}</b><span>${esc(x.restaurant || place || "")}</span>
      ${x.fits ? "" : `<span class="pillnote warn">Over by ${rupee0(Math.max(0, x.price - left))}</span>`}</span><b class="pr">${est(x.price)}</b></button>`).join("");
  const groups = (d.usual || []).map(g => { const r = rows(g.dishes || [], g.restaurant); return r ? `<p class="k">${esc(g.restaurant)}${g.rating ? ` · ${Number(g.rating).toFixed(1)}★` : ""}</p><div class="stack" style="gap:8px">${r}</div>` : ""; }).join("");
  const hidden = [d.hidden?.not_safe ? `${d.hidden.not_safe} not safe for your rules` : "", d.hidden?.not_a_meal ? `${d.hidden.not_a_meal} sides and drinks` : "",
    d.hidden?.not_again ? `${d.hidden.not_again} you said “not again” to` : ""].filter(Boolean).join(" · ");
  return `<div class="sheet-head"><div><p class="eyebrow">${esc(s.day)} ${esc(s.meal)} · ${rupee0(left)} to spend on it</p><h2 class="h2" id="sheet-title">Change ${esc(s.meal)}</h2>
      ${d.current ? `<p class="muted">Now: ${esc(d.current.item)}</p>` : ""}</div><button class="icon-btn" data-close-sheet aria-label="Close">${ICON.close}</button></div>
    ${(d.pool || []).length ? `<p class="k">Your ${esc(s.meal)} list</p><div class="stack" style="gap:8px">${rows(d.pool)}</div>` : ""}
    ${groups || `<p class="fine">Nothing else nearby fits your rules for this meal.</p>`}
    ${(d.new || []).length ? `<p class="k">Something new</p><div class="stack" style="gap:8px">${rows(d.new)}</div>` : ""}
    <div class="two"><button class="secondary" data-auto="${s.id}">Let Ziggy choose</button><button class="secondary" data-skip="${s.id}">Skip this meal</button></div>
    ${hidden ? `<p class="fine">Hidden: ${esc(hidden)}.</p>` : ""}
    <p class="fine">More places, cooking and meal lists are in the <a href="/?tab=saved">classic app</a>.</p>`;
}
function rulesSheet() {
  const u = S.view.user || {}, meals = (u.prefs?.meals || u.meals || ["dinner"]);
  const t = (group, val, label, on, multi = true) => `<button type="button" class="chip" data-rule="${group}" data-val="${esc(val)}" data-multi="${multi ? 1 : 0}" aria-pressed="${!!on}">${label}</button>`;
  return `<div class="sheet-head"><h2 class="h2" id="sheet-title">Diet &amp; budget</h2><button class="icon-btn" data-close-sheet aria-label="Close">${ICON.close}</button></div>
    <form id="rules" class="stack" style="gap:18px">
      <fieldset><legend>What you eat</legend><div class="chips">${DIETS.map(([k, l]) => t("diet", k, l, u.diet === k, false)).join("")}</div></fieldset>
      <fieldset><legend>Never show me</legend><div class="chips">${ALLERGIES.map(a => t("allergens", a, cap1(a.replace("_", " ")), (u.allergens || []).includes(a))).join("")}</div>
        <div class="chips">${MEDICAL.map(([k, l]) => t("medical", k, l, (u.medical || []).includes(k))).join("")}</div></fieldset>
      <fieldset><legend>Money</legend><div class="grid2"><label>Weekly budget (₹)<input id="rl-budget" type="number" inputmode="numeric" min="100" max="100000" value="${esc(u.weekly_budget)}"></label>
        <label>Daily cap (₹, optional)<input id="rl-cap" type="number" inputmode="numeric" min="0" max="20000" value="${esc(u.prefs?.daily_cap || "")}" placeholder="No daily cap"></label></div></fieldset>
      <fieldset><legend>Meals Ziggy decides</legend><div class="chips">${[["dinner", "Dinner"], ["lunch", "Lunch"], ["breakfast", "Breakfast"]].map(([k, l]) => t("meals", k, l, meals.includes(k))).join("")}</div></fieldset>
    </form>${errbar()}<button class="primary big" data-act="rules-save">Save and re-plan</button>`;
}

/* ================================================================ wiring (one delegated listener) */
const ACTS = {
  start: () => startSetup(),
  "signin-open": () => { S.signin = true; S.error = null; render(); },
  "signin-close": () => { S.signin = null; S.error = null; render(); },
  "setup-cancel": () => { S.setup = null; S.screen = "welcome"; S.error = null; render(); },
  "setup-done": () => guard(finishSetup, "Planning your week"),
  connect: () => guard(connectSwiggy, "Connect Swiggy"),
  "connect-later": () => { store.set(`ziggy.v3.noswiggy.${S.userId}`, "1"); S.screen = "app"; render(); },
  "addr-open": () => { S.sheet = { kind: "addr" }; S.addrs = null; render(); guard(loadAddresses, "Load your Swiggy addresses"); },
  "addr-refresh": () => guard(loadAddresses, "Load your Swiggy addresses"),
  "sync-again": () => { S.sync = null; guard(afterReady, "Read Swiggy menus"); },
  remind: () => guard(turnOnReminders, "Turn on reminders"),
  ics: () => guard(downloadIcs, "Download the calendar file"),
  rules: () => { S.sheet = { kind: "rules" }; render(); },
  "rules-save": () => guard(saveRules, "Save your rules"),
  signout: () => guard(signOut, "Sign out"),
  retry: () => { const r = S.retry; S.retry = null; if (r) guard(r.fn, r.label); },
  "dismiss-error": () => { S.error = null; S.retry = null; render(); },
  reload: () => location.reload(),
  "account-later": () => { store.set(`ziggy.v3.noaccount.${S.userId}`, "1"); render(); },
};
document.addEventListener("click", (e) => {
  const t = e.target.closest("button, a[data-paid]");
  if (!t) {
    if (e.target.matches?.("[data-close-sheet]")) { S.sheet = null; render(); }
    return;
  }
  const ds = t.dataset;
  if (ds.act && ACTS[ds.act]) { e.preventDefault(); ACTS[ds.act](); return; }
  if (ds.closeSheet !== undefined) { S.sheet = null; render(); return; }
  if (ds.tab) { S.tab = ds.tab; S.sheet = null; S.error = null; render(); window.scrollTo(0, 0);
    if (ds.tab === "more" && !S.account) api(`/api/user/${S.userId}/account`).then(a => { S.account = a; render(); }).catch(() => {});
    return; }
  if (ds.su) { // setup chips
    const d = S.setup; readSetup();
    if (ds.su === "diet") d.diet = ds.val;
    else if (ds.su === "budget") d.weekly_budget = Number(ds.val);
    else { const arr = d[ds.su], i = arr.indexOf(ds.val); if (i >= 0) arr.splice(i, 1); else arr.push(ds.val); }
    render(); return;
  }
  if (ds.rule) { // rules sheet chips (no re-render: the form keeps its typed values)
    if (ds.multi === "0") t.parentElement.querySelectorAll("[data-rule]").forEach(b => b.setAttribute("aria-pressed", String(b === t)));
    else t.setAttribute("aria-pressed", String(t.getAttribute("aria-pressed") !== "true"));
    return;
  }
  if (ds.addr) { guard(() => chooseAddress(ds.addr), "Choose that address"); return; }
  if (ds.cart) { guard(() => addToCart(Number(ds.cart)), "Add to Swiggy cart"); return; }
  if (ds.pick) { guard(() => choose(Number(ds.pickSid), { item_id: Number(ds.pick) }, `${ds.pickName} it is. The week re-balanced.`), `Switch to ${ds.pickName}`); return; }
  if (ds.auto) { guard(() => choose(Number(ds.auto), { action: "auto" }, "Ziggy will choose this one."), "Let Ziggy choose"); return; }
  if (ds.sheet) { guard(() => openSheet(ds.sheet), "Load choices"); return; }
  if (ds.skip) { guard(() => setStatus(Number(ds.skip), "skipped"), "Skip this meal"); return; }
  if (ds.skipQuiet) { guard(() => setStatus(Number(ds.skipQuiet), "skipped", false), "Mark as not had"); return; }
  if (ds.confirm) { guard(() => confirmMeal(Number(ds.confirm)), "Mark as had"); return; }
  if (ds.rate) { const [sid, sc] = ds.rate.split(":"); guard(() => rate(Number(sid), Number(sc)), "Rate this meal"); return; }
  if (ds.overOk) { S.carts[ds.overOk] = { ...S.carts[ds.overOk], budgetOk: true }; render(); return; }
  if (ds.replan) { guard(() => replanRest(Number(ds.replan)), "Re-plan the rest"); return; }
});
document.addEventListener("submit", (e) => {
  e.preventDefault();
  if (e.target.id === "signin") guard(signIn, "Sign in");
  else if (e.target.id === "account") guard(saveAccount, "Save sign-in");
  else if (e.target.id === "setup") guard(finishSetup, "Planning your week");
});
document.addEventListener("input", (e) => {
  if (e.target.id === "su-budget" && S.setup) {           // the per-meal hint follows the typing
    S.setup.weekly_budget = Number(e.target.value) || null;
    const n = Math.max(1, S.setup.meals.length * 7), hint = e.target.closest("fieldset")?.querySelector(".fine");
    if (hint) hint.textContent = `${S.setup.weekly_budget > 0 ? `About ${rupee0(S.setup.weekly_budget / n)} a meal for ${n} meals a week` : "Enter an amount"}. Delivery fees count. Swiggy's cart shows the exact bill before you pay.`;
    e.target.closest("fieldset")?.querySelectorAll("[data-su=budget]").forEach(b => b.setAttribute("aria-pressed", String(Number(b.dataset.val) === S.setup.weekly_budget)));
  }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.sheet) { S.sheet = null; render(); } });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
boot().catch((e) => {
  document.getElementById("app").innerHTML = `<main class="welcome">${mascot()}<h1 class="h1">Ziggy can't reach its server</h1>
    <p class="sub">${esc(e.message || e)}</p><button class="primary big" data-act="reload">Reload</button></main>`;
});
})();
