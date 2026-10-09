/* Ziggy — vanilla JS app (no build step, no framework).
   Three tabs, in order of how often people need them:
     Today — the next meal, when to order it, one tap into the Swiggy cart
     Week  — seven days; tap any meal to change it
     Saved — places you like, dishes you rated Good, what you had recently
   Me (the avatar) holds settings, money, Swiggy, reminders and your account.
   Two sheets do every change: "Change a meal" and "Connect Swiggy / Deliver to".
   The optimiser, budget and hard rules run on the server after every tap. */
"use strict";

const S = {
  meta: null, users: [], userId: null, planId: null, view: null,
  tab: "today", more: null, exec: null, receipts: null, recap: null,
  busy: false, error: null, orderReview: null,
  liveResults: null, liveFavourites: null, liveBrowseMenu: null, liveOrderReview: null, liveCart: null,
  checkoutReview: null, placedOrder: null, liveOrderStatus: null, liveOrderHistory: null, liveCartError: null,
  cartBudget: {}, pendingResume: null, lastLive: null, liveCartErrorCode: null, carts: null,
  sheet: null, hhEdit: null, swapPick: null, moving: null, onboard: null, places: null, calendar: null,
  welcome: false, signin: false, account: null, planning: false, connectSheet: false, addrSheet: false,
  weekDay: null, weekView: null, savedTab: "places", saved: null, menuCat: null, liveLoading: null, loadedAt: null,
  adding: null, ask: false, handedOff: null,
  offline: typeof navigator !== "undefined" && navigator.onLine === false,
  filters: { veg: false, budget: false, stock: false, flagged: false, fast: false },
};
const MEALS = ["breakfast", "lunch", "dinner"];
const DAY3 = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WX_ICON = { clear: "☀", rain: "☂", hot: "☀", storm: "☂" };

/* ---------------------------------------------------------------- storage */
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
  del(k) { try { localStorage.removeItem(k); } catch (_) {} },
};
// One-tap quick filters on live menus and restaurant lists, remembered on this device.
try { Object.assign(S.filters, JSON.parse(store.get("smartplate.filters") || "{}") || {}); } catch (_) {}

/* Private profiles: the key lives only in this browser ({id: {key, name}}). */
const keys = {
  all() { try { const v = JSON.parse(store.get("smartplate.keys") || "{}"); return v && typeof v === "object" && !Array.isArray(v) ? v : {}; } catch (_) { return {}; } },
  get(id) { return this.all()[id]?.key || null; },
  put(id, key, name) { const a = this.all(); a[id] = { key, name }; store.set("smartplate.keys", JSON.stringify(a)); },
  drop(id) { const a = this.all(); delete a[id]; store.set("smartplate.keys", JSON.stringify(a)); },
};

/* Appearance: light, dark or the phone's setting; Ember or Zomato × Swiggy colours. */
const THEMES = [["system", "Auto"], ["light", "Light"], ["dark", "Dark"]];
const PALETTES = [["ember", "Ember", "#D63E1C"], ["mesh", "Zomato × Swiggy", "linear-gradient(135deg,#E23744,#FC8019)"]];
const look = () => ({ theme: store.get("ziggy.theme") || "system", palette: store.get("ziggy.palette") || "ember" });
function applyLook() {
  if (typeof document === "undefined" || !document.documentElement || !document.documentElement.setAttribute) return;
  const { theme, palette } = look(), root = document.documentElement;
  if (theme === "system") root.removeAttribute?.("data-theme"); else root.setAttribute("data-theme", theme);
  if (palette === "mesh") root.setAttribute("data-palette", "mesh"); else root.removeAttribute?.("data-palette");
  const dark = theme === "dark" || (theme === "system" && typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.querySelector?.('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0F0E13" : "#F5F3F0");
}
applyLook();
function setLook(kind, value) {
  store.set(`ziggy.${kind}`, value);
  applyLook(); render();
}

/* ---------------------------------------------------------------- formatting */
// Exact to the paisa, as Swiggy returns it: ₹21.58 stays ₹21.58, ₹160 stays ₹160.
const rupee = (n) => {
  const v = Math.round((Number(n) || 0) * 100) / 100;
  return "₹" + v.toLocaleString("en-IN", { minimumFractionDigits: Number.isInteger(v) ? 0 : 2, maximumFractionDigits: 2 });
};
const rupee0 = (n) => "₹" + Math.round(n || 0).toLocaleString("en-IN");
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cap1 = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);
/* Money has exactly two looks, everywhere:
   est()  — "≈₹180 est.", grey with a dotted underline: plan prices, menu prices, grocery
            estimates. Anything not read back from Swiggy's cart.
   real() — solid, exact to the paisa: ONLY numbers read back from Swiggy's cart (bill
            lines, the payable total). Never mix them in one figure. */
const est = (n, exact = false) => `<span class="est" title="Estimate">≈${(exact ? rupee : rupee0)(n)}<i>est.</i></span>`;
const real = (n) => `<span class="real" title="From your Swiggy cart">${rupee(n)}</span>`;
// A planned meal's cost is an estimate unless Swiggy's own cart total replaced it.
const cellMoney = (m) => m.real_bill ? real(m.cost) : est(m.cost);
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
// A server timestamp ("2026-11-02T08:00") as people say it: "today at 08:00", "2 Nov, 08:00".
function fmtWhen(iso) {
  if (!iso) return "recently";
  const d = new Date(String(iso).slice(0, 16));
  if (isNaN(d)) return String(iso);
  const time = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return day === todayIso() ? `today at ${time}` : `${d.toLocaleDateString("en-IN", { day: "numeric", month: "short" })}, ${time}`;
}
// Minutes from now until "19:45" today (null when it isn't a time, or already passed).
function minutesUntil(hhmm) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || ""));
  if (!m) return null;
  const now = new Date(), at = new Date(now);
  at.setHours(Number(m[1]), Number(m[2]), 0, 0);
  const mins = Math.round((at - now) / 60000);
  return mins >= 0 ? mins : null;
}
function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

/* ---------------------------------------------------------------- icons */
const svg = (body, w = 2) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const ICON = {
  today: svg('<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2.5"/>'),
  week: svg('<rect x="4" y="5" width="16" height="15" rx="3"/><path d="M8 3v4M16 3v4M4 10h16"/>'),
  saved: svg('<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>'),
  heart: svg('<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>'),
  place: svg('<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>'),
  down: svg('<path d="M6 9l6 6 6-6"/>', 2.4),
  right: svg('<path d="M9 6l6 6-6 6"/>'),
  back: svg('<path d="M15 5l-7 7 7 7"/>'),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>', 2.2),
  swap: svg('<path d="M7 4L3 8l4 4"/><path d="M3 8h14"/><path d="M17 20l4-4-4-4"/><path d="M21 16H7"/>'),
  out: svg('<path d="M7 17L17 7"/><path d="M8 7h9v9"/>', 2.2),
  check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 2.6),
  search: svg('<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>'),
  cart: svg('<path d="M4 5h2l2 10h10l2-7H7"/><circle cx="10" cy="19" r="1.3"/><circle cx="17" cy="19" r="1.3"/>'),
  cook: svg('<path d="M4 10h14v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M18 12h3M2.5 10h17"/><path d="M8 3.5c-.7 1 .7 2 0 3M12.5 3.5c-.7 1 .7 2 0 3"/>', 1.8),
  bag: svg('<path d="M6 8h12l-1 12H7z"/><path d="M9 8a3 3 0 0 1 6 0"/>', 1.8),
  pin: svg('<path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 13v7"/>', 1.8),
  plus: svg('<path d="M5 12h14M12 5v14"/>', 2.2),
  minus: svg('<path d="M5 12h14"/>', 2.2),
  bell: svg('<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>', 1.8),
  coins: svg('<ellipse cx="12" cy="7" rx="7" ry="3"/><path d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7"/><path d="M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5"/>', 1.8),
};
/* Ziggy the elephant: big soft ears, a trunk that curls up like a smile. */
const EARS = '<path class="ear" d="M21 14C12 14 5.5 21 5.5 29.5c0 7.2 5.2 12.5 11.5 12.5 3.2 0 5.4-1.5 6.6-3.6z"/><path class="ear" d="M43 14c9 0 15.5 7 15.5 15.5 0 7.2-5.2 12.5-11.5 12.5-3.2 0-5.4-1.5-6.6-3.6z"/>';
function mascot(cls = "", trunkCls = "wave") {
  return `<div class="mascot ${cls}" aria-hidden="true"><svg viewBox="0 0 64 64">${EARS}<ellipse class="head" cx="32" cy="28" rx="14.5" ry="15.5"/>
    <path class="trunk ${trunkCls}" d="M32 39v9c0 5.5 3.6 8.5 7.6 8.5 3.2 0 5.2-2.2 5.2-4.8"/>
    <circle class="eye" cx="26.5" cy="27" r="2.4"/><circle class="eye" cx="37.5" cy="27" r="2.4"/>
    <circle cx="27.3" cy="26.2" r=".8" fill="#fff"/><circle cx="38.3" cy="26.2" r=".8" fill="#fff"/>
    <ellipse class="cheek" cx="22.5" cy="33.5" rx="2.8" ry="2"/><ellipse class="cheek" cx="41.5" cy="33.5" rx="2.8" ry="2"/></svg></div>`;
}
// The white elephant on the app icon, the rail logo and the rolling Order button.
const WHITE_ELEPHANT = '<path d="M21 14C12 14 5.5 21 5.5 29.5c0 7.2 5.2 12.5 11.5 12.5 3.2 0 5.4-1.5 6.6-3.6z" fill="#fff" opacity=".8"/><path d="M43 14c9 0 15.5 7 15.5 15.5 0 7.2-5.2 12.5-11.5 12.5-3.2 0-5.4-1.5-6.6-3.6z" fill="#fff" opacity=".8"/><ellipse cx="32" cy="28" rx="14.5" ry="15.5" fill="#fff"/><path d="M32 39v9c0 5.5 3.6 8.5 7.6 8.5 3.2 0 5.2-2.2 5.2-4.8" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round"/><circle cx="26.5" cy="27" r="2.6" fill="#18161D"/><circle cx="37.5" cy="27" r="2.6" fill="#18161D"/>';
const mark = () => `<span class="mark" role="img" aria-label="Ziggy"><svg viewBox="0 0 64 64" aria-hidden="true">${WHITE_ELEPHANT}</svg></span>`;
const roller = () => `<span class="roller" aria-hidden="true"><svg viewBox="0 0 64 64">${WHITE_ELEPHANT}</svg></span>`;

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
function dishKind(name, cook) {
  if (cook) return ["cook", 120];
  const hit = DISH_KINDS.find(([, , re]) => re.test(String(name || "")));
  return hit ? [hit[0], hit[1]] : ["plate", 220];
}
const BROKEN_PHOTOS = new Set();          // photos that failed once stay icons on every re-render
function dishIcon(name, { cook = false, image = null, lg = false } = {}) {
  if (image && !BROKEN_PHOTOS.has(image)) return `<img class="dphoto${lg ? " lg" : ""}" src="${esc(image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-dish="${esc(name)}">`;
  const [kind, hue] = dishKind(name, cook);
  return `<span class="dicon${lg ? " lg" : ""}" style="--h:${hue}" data-kind="${kind}" aria-hidden="true"><svg viewBox="0 0 24 24">${DISH_SVG[kind]}</svg></span>`;
}

// A dish's photo: Swiggy's own, from the plan (seen before) or fetched for what Today shows.
const photoOf = (x) => x?.image || (x?.item_id != null ? (S.photos || {})[x.item_id] : null) || null;
// Today asks Swiggy for the photos of the dishes it shows (the server caches them; a dish
// Swiggy shows no photo for keeps its icon). Only with live menus and a Swiggy sign-in.
async function loadPhotos(items) {
  if (S.view?.source?.kind !== "live" || !swiggyReady()) return;
  S.photos = S.photos || {}; S.photoAsked = S.photoAsked || new Set();
  const ids = items.filter(x => x && x.item_id != null && !x.image && !S.photoAsked.has(x.item_id))
    .map(x => x.item_id).slice(0, 6);
  if (!ids.length) return;
  ids.forEach(i => S.photoAsked.add(i));
  try {
    const r = await api(`/api/user/${S.userId}/swiggy/photos`, "POST", { item_ids: ids });
    let got = false;
    for (const [k, v] of Object.entries(r.photos || {})) if (v) { S.photos[k] = v; got = true; }
    if (got) render();
  } catch { /* the icons stay */ }
}

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

/* ---------------------------------------------------------------- server */
// Swiggy answers 409 with one of these when the user's own sign-in is missing or no longer
// accepted: the fix is a Connect button, not a Retry.
const SWIGGY_RECONNECT = new Set(["swiggy_not_connected", "swiggy_auth_expired"]);
async function api(path, method = "GET", body, { timeoutMs } = {}) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  const k = S.userId ? keys.get(S.userId) : null;
  if (k) opt.headers["X-SmartPlate-Key"] = k;
  if (body) opt.body = JSON.stringify(body);
  let timer = null;
  if (timeoutMs && typeof AbortController !== "undefined") {   // boot never waits forever
    const ctl = new AbortController(); opt.signal = ctl.signal;
    timer = setTimeout(() => ctl.abort(), timeoutMs);
  }
  let r;
  try { r = await fetch(path, opt); }
  catch (e) {
    if (e?.name !== "AbortError") throw e;
    const err = new Error("The server took too long to answer. It may be waking up or unable to reach its database.");
    err.code = "server_timeout";
    throw err;
  } finally { if (timer) clearTimeout(timer); }
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
// One message at a time, never stacked; an optional Undo runs through guard() like any tap.
function toast(msg, undo) {
  if (typeof document === "undefined" || !document.querySelectorAll) return;
  document.querySelectorAll(".toast").forEach(old => old.remove());
  const t = document.createElement("div");
  t.className = "toast"; t.setAttribute("role", "status");
  const span = document.createElement("span"); span.textContent = msg; t.appendChild?.(span);
  if (!t.appendChild) t.textContent = msg;
  if (undo && t.appendChild) {
    const b = document.createElement("button");
    b.textContent = "Undo"; b.addEventListener?.("click", () => { t.remove(); guard(undo, "Undo"); });
    t.appendChild(b);
  }
  document.body.appendChild(t);
  setTimeout(() => t.remove(), undo ? 5000 : 2800);
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
  const again = fn === retryLast ? (S.retry && typeof S.retry.fn === "function" ? S.retry : { fn: reloadPlan, label: "Reload your plan" }) : { fn, label };
  setBusy(true); S.error = null; S.errorCode = null; S.errorRule = null; S.lastLive = null; S.retry = null;
  try { await again.fn(); }
  catch (e) {
    const reason = e.message || String(e);
    S.error = again.label ? `${again.label} failed: ${reason}` : reason; S.errorCode = e.code || null; S.errorRule = e.data?.rule || null;
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
  S.checkoutReview = null; S.pendingResume = null; S.tab = "today"; S.more = null; S.sheet = null;
  S.connectSheet = false; S.addrSheet = false;
  S.users = mergeUsers(await api("/api/users").catch(() => []));
  S.welcome = true; S.error = message; S.errorCode = "profile_missing";
  render();
}

/* ---- one "Connect Swiggy" path for every live surface ---- */
const CONNECT_MSG = "Connect Swiggy to use real menus and prices.";
function connectPrompt() {
  return `<div class="connect-swiggy" role="alert"><span>${CONNECT_MSG}</span>
    <button class="small primary" data-act="connect-open">Connect Swiggy</button></div>`;
}
// The live actions that resume once after connecting (and choosing an address).
const RESUME = {
  "order-meal": { label: "adding your meal to the Swiggy cart", run: (sid) => { S.tab = "today"; return orderMeal(Number(sid)); } },
  "order-week": { label: "checking that meal's real Swiggy cart",
    run: async (sid) => { S.tab = "week"; S.weekView = "orders"; await checkQueueCart(sid); } },
  "live-place": { label: "opening that Swiggy menu", run: (id, name, dish) => openLivePlace(id, name, dish) },
  "live-menus": { label: "planning from your Swiggy restaurants", run: () => planFromLiveMenus() },
};
// What each button does, in the words an error banner uses ("Add to Swiggy cart failed: …").
const ACT_LABELS = {
  "confirm-live-cart": "Add to Swiggy cart",
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
async function reloadPlan() {
  if (!S.planId) { if (S.userId) await loadOrCreatePlan(); render(); return; }
  S.view = await api(`/api/plan/${S.planId}`); render();
}

/* ---------------------------------------------------------------- bootstrap */
const BOOT_TIMEOUT_MS = 25000;
const TAB_ALIAS = { plan: "week", you: "more", me: "more", places: "saved" };
async function boot() {
  ensureBusyBar();
  S.meta = await api("/api/meta", "GET", null, { timeoutMs: BOOT_TIMEOUT_MS });
  S.users = mergeUsers(await api("/api/users", "GET", null, { timeoutMs: BOOT_TIMEOUT_MS }));
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
  if (["today", "week", "saved", "more", "plan", "you", "me", "places"].includes(wanted)) S.tab = TAB_ALIAS[wanted] || wanted;
  if (params.get("swiggy") || params.get("swiggy_error")) {             // back from Swiggy sign-in
    S.tab = "today"; S.swiggy = await api(`/api/user/${S.userId}/swiggy`);
    // Fixed messages only: the URL carries a code, never text to display (a crafted link can't fake warnings).
    const SWIGGY_ERRORS = { denied: "Swiggy sign-in was cancelled.",
      expired: "That Swiggy sign-in link expired or was already used. Tap Connect Swiggy again.",
      other_browser: "That Swiggy sign-in was started in a different browser. Tap Connect Swiggy again on this device.",
      failed: "Swiggy sign-in didn't complete. Tap Connect Swiggy to try again." };
    if (params.get("swiggy_error")) { S.error = SWIGGY_ERRORS[params.get("swiggy_error")] || SWIGGY_ERRORS.failed; store.del("smartplate.resume"); }
    else {
      toast("Swiggy connected"); S.pendingResume = takeResume();
      // Quick connection: straight on to "Deliver to", then whatever you were doing.
      if (S.swiggy?.connected && !S.swiggy.address) { S.addrSheet = true; S.swAddrs = null; }
    }
    if (typeof history !== "undefined") history.replaceState(null, "", "/");
  }
  render();
  if (S.addrSheet && !S.swAddrs) await guard(loadAddresses, "Load your Swiggy addresses");
  if (S.pendingResume) await guard(resumePending);
  autoSyncLive().catch(() => {});
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
  matchCartToNextMeal();
}
// Swiggy's cart already holds the next meal (added on another device, or before a reload):
// show its real bill on the meal instead of offering "Add" again.
function matchCartToNextMeal() {
  const nu = S.view?.next_up?.cell, c = S.liveCart;
  if (!nu || !c || c.orderable === false || nu.kind !== "delivery" || ["confirmed", "ordered"].includes(nu.status)) return;
  if (c.item !== nu.item || (c.restaurant && nu.restaurant && c.restaurant !== nu.restaurant)) return;
  const planned = nu.planned_cost ?? nu.cost;
  S.carts = { ...(S.carts || {}), [nu.session_id]: { item: c.item, restaurant: c.restaurant, to_pay: c.to_pay, bill: c.bill,
    checkout_url: c.checkout_url, cancellation_note: c.cancellation_note, planned_cost: planned,
    over_plan: c.to_pay != null ? Math.round((c.to_pay - planned) * 100) / 100 : null } };
}
function adoptView(view) { S.view = view; S.planId = view.plan.id; S.loadedAt = new Date(); scheduleAlerts().catch(() => {}); }

/* ---------------------------------------------------------------- actions */
function resetProfileState() {
  for (const key of ["exec", "receipts", "recap", "orderReview", "sheet", "moving", "places", "calendar", "account", "swiggy",
    "swAddrs", "carts", "acctDraft", "liveResults", "liveFavourites", "liveBrowseMenu", "liveOrderReview", "liveCart",
    "checkoutReview", "placedOrder", "liveOrderStatus", "liveOrderHistory", "liveCartError", "liveCartEmpty", "saved",
    "weekDay", "weekView", "orderQueue", "handedOff"]) S[key] = null;
  S.welcome = false; S.tab = "today"; S.more = null; S.connectSheet = false; S.addrSheet = false; S.ask = false;
}
async function switchUser(id) {
  S.userId = Number(id);
  resetProfileState();
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
  S.exec = null; S.receipts = null; S.orderReview = null; S.tab = "week"; S.weekDay = 0; S.weekView = null; S.more = null;
  toast(`Planned the week of ${fmtDate(S.view.plan.week_start)}`); render();
}
async function setMode(mode) {
  adoptView(await api(`/api/plan/${S.planId}/optimize`, "POST", { mode }));
  toast(`${S.view.plan.mode_label}: ${S.meta.mode_outcomes?.[mode] || "re-planned"}`); render();
}
async function reoptimize() {
  adoptView(await api(`/api/plan/${S.planId}/optimize`, "POST", {}));
  toast("Re-planned the rest of the week"); render();
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
  S.tab = "week"; S.weekView = "orders"; S.more = null;
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
  [S.receipts, S.recap] = await Promise.all([api(`/api/receipts/${S.userId}`), api(`/api/plan/${S.planId}/recap`)]);
  toast("Expenses updated"); render();
}
const mealName = (sid) => { const c = cellOf(sid); return c ? cap1(MEALS.find(m => S.view.grid.some(d => d.meals[m]?.session_id === Number(sid))) || "meal") : "Meal"; };
async function setSession(sid, status, { undo = true } = {}) {
  const before = cellOf(sid);
  await api(`/api/session/${sid}/status`, "POST", { status });
  S.view = await api(`/api/plan/${S.planId}/optimize`, "POST", {});
  S.sheet = null;
  const name = mealName(sid);
  const msg = { skipped: `${name} skipped${before?.cost ? `. ${rupee0(before.cost)} back in the week` : ""}.`, active: `${name} is back in the plan`,
    cooked: "Marked as cooked at home" }[status] || `${name} ${status}`;
  toast(msg, undo && status === "skipped" ? () => setSession(sid, "active", { undo: false }) : null);
  render();
}

/* ---- the everyday actions ---- */
async function openSheet(sid) {
  S.sheet = { sid: Number(sid), data: null, tab: "fits", place: null }; render();
  S.sheet.data = await api(`/api/session/${sid}/options`); render();
  loadPhotos(quickPicks(S.sheet.data));
}
async function choose(sid, body, msg) {
  adoptView(await api(`/api/session/${sid}/choose`, "POST", body));
  S.sheet = null; toast(msg || "Done. The rest of the week re-balanced."); render();
}
/* Epicure: "more like this" on the planned dish, ingredient swaps for cook days. */
const epicureOn = () => !!S.meta?.epicure?.available;
async function moreLikeThis(sid) {
  const r = await api(`/api/session/${sid}/more-like`, "POST", {});
  adoptView(r.plan);
  // the re-plan may have changed this very meal: show the sheet as it is now
  const data = await api(`/api/session/${sid}/options`);
  S.sheet = { ...S.sheet, data, similar: { dish: r.recorded, items: r.similar } };
  toast(`More like ${r.recorded} from now on. Your open meals were re-planned.`); render();
}
async function forgetMoreLike(name) {
  adoptView(await api(`/api/user/${S.userId}/more-like/forget`, "POST", { name }));
  toast(`Removed ${name} from “more like this”.`); render();
}
async function openSwap(token, reason) {
  S.swapPick = { token, reason, data: null }; render();
  S.swapPick.data = await api(`/api/plan/${S.planId}/swaps?token=${encodeURIComponent(token)}`); render();
}
async function setSwap(token, swapToken, reason) {
  adoptView(await api(`/api/plan/${S.planId}/grocery-swap`, "POST", { token, swap_token: swapToken, reason }));
  S.swapPick = null; toast(swapToken ? "Swap saved. Your recipe and grocery list show it." : "Back to the original ingredient."); render();
}
async function confirmMeal(sid) {
  adoptView(await api(`/api/session/${sid}/confirm`, "POST", {}));
  S.sheet = null; S.handedOff = null; toast("Logged. How was it?"); render();
}
async function rateMeal(sid, score) {
  const r = await api(`/api/session/${sid}/rate`, "POST", { score });
  adoptView(r.plan); S.saved = null;
  if (score < 0) toast("Noted. Ziggy won't plan that dish again for a while.");
  else if (r.suggest_favourite) { S.suggestFav = r.suggest_favourite; toast(`Liked. Save ${r.suggest_favourite.restaurant} to your places?`); }
  else toast("Liked. More like this.");
  render();
}
const RATE_REASONS = { late: "Late", small: "Small portion", spicy: "Too spicy", pricey: "Too pricey", great: "Great" };
async function rateReason(sid, reason) {
  const r = await api(`/api/session/${sid}/rate`, "POST", { reasons: [reason] });
  adoptView(r.plan);
  toast({ late: "Noted. That place gets planned less.", small: "Noted. That dish counts as less food.",
    spicy: "Noted. Less of that dish.", pricey: "Noted. Cheaper picks will weigh more.", great: "Great. More like that." }[reason]);
  render();
}
async function swapMeals(a, b) {
  S.moving = null;
  adoptView(await api(`/api/plan/${S.planId}/swap`, "POST", { a: Number(a), b: Number(b) }));
  toast("Swapped. Budget and nutrition re-balanced."); render();
}
async function toggleFav(rid) {
  const r = await api(`/api/user/${S.userId}/favourites/${rid}`, "POST", {});
  adoptView(r.plan);
  if (S.places) S.places = await api(`/api/restaurants?user_id=${S.userId}`);
  S.suggestFav = null;
  toast(r.favourite ? "Saved to your places" : "Removed from your places"); render();
}
const cellOf = (sid) => S.view?.grid.flatMap(d => Object.values(d.meals)).find(m => m.session_id === Number(sid));
// Order ↔ cook in one tap: the best safe option the server offers for this meal.
async function toKind(sid, kind) {
  const cell = cellOf(sid);
  if (cell && cell.kind === kind) return;
  const d = await api(`/api/session/${sid}/options`);
  if (kind === "cook") {
    const r = d.cook[0];
    if (!r) throw new Error("No recipe fits your rules for this meal. Pick a dish instead");
    return choose(sid, { recipe_key: r.recipe_key }, `Cook: ${r.name}. It's on your grocery list.`);
  }
  const dishes = [...d.usual.flatMap(g => g.dishes.map(x => ({ ...x, restaurant: g.restaurant }))), ...d.new];
  const x = dishes.find(x => x.fits) || null;
  if (!x) throw new Error("No dish nearby fits what's left of your budget for this meal");
  return choose(sid, { item_id: x.item_id }, `Order: ${x.name} from ${x.restaurant}.`);
}
// "Keep it": pin the meal so re-plans leave it alone (tap again to let Ziggy choose).
async function togglePin(sid) {
  const c = cellOf(sid);
  if (!c) return;
  if (c.pinned) return choose(sid, { action: "auto" }, "Unpinned. Re-plans can change this meal.");
  const body = c.kind === "cook" && c.recipe_key ? { recipe_key: c.recipe_key } : c.item_id != null ? { item_id: c.item_id } : null;
  if (!body) throw new Error("This meal can't be kept as it is. Pick a dish instead");
  return choose(sid, body, "Kept. Re-plans won't touch it.");
}
function toggleFilter(key) {
  S.filters[key] = !S.filters[key];
  store.set("smartplate.filters", JSON.stringify(S.filters));
  render();
}

/* ---------------------------------------------------------------- Swiggy */
const swiggyReady = () => !!(S.swiggy && S.swiggy.connected && S.swiggy.address);
const swiggySignInOpen = () => !!S.meta?.swiggy_redirect_approved;
// Swiggy menus don't list ingredients: no cart fill when anyone eating has an allergy, a
// medical rule or a vegan diet (the server checks the same: swiggy_live.cart_preview).
const ruleBound = (p) => !!(p?.allergens?.length || p?.medical?.length || p?.diet === "vegan");
const cartEligible = (c) => {
  if (ruleBound(S.view?.user)) return false;
  const on = new Set(c?.eaters || []);
  return !(S.view?.household?.people || []).some(p => !p.you && on.has(p.id) && ruleBound(p));
};
async function loadAddresses() { S.swAddrs = await api(`/api/user/${S.userId}/swiggy/addresses`); render(); }
async function openAddresses() { S.addrSheet = true; S.connectSheet = false; S.swAddrs = null; render(); await loadAddresses(); }
async function connectSwiggy() {
  const r = await api(`/api/user/${S.userId}/swiggy/connect`, "POST", {});
  rememberResume(); location.href = r.authorize_url;
}
// Everything read for one delivery address; dropped whenever the address changes.
function forgetAddressState() {
  S.swAddrs = null; S.liveResults = null; S.liveBrowseMenu = null; S.liveCart = null;
  S.liveCartEmpty = null; S.liveCartError = null; S.liveCartErrorCode = null; S.checkoutReview = null;
  S.placedOrder = null; S.liveOrderStatus = null; S.liveOrderHistory = null; S.liveOrderReview = null;
}
async function chooseAddress(addressId) {
  S.swiggy = await api(`/api/user/${S.userId}/swiggy/address`, "POST", { address_id: addressId });
  forgetAddressState();
  S.liveFavourites = await api(`/api/user/${S.userId}/swiggy/favourites`);
  await refreshLiveCart(false);
  S.addrSheet = false; S.connectSheet = false;
  toast(`Delivering to ${S.swiggy.address?.label || "this address"}`);
  S.view = await api(`/api/plan/${S.planId}`);          // its source now says what the new address needs
  S.liveSync = null; render();
  if (!(await resumePending())) await autoSyncLive();
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
async function disconnectSwiggy() {
  S.swiggy = await api(`/api/user/${S.userId}/swiggy/disconnect`, "POST", {});
  forgetAddressState(); S.carts = null; S.liveFavourites = null;
  toast("Disconnected from Swiggy"); render();
}
async function searchLivePlaces(query) {
  S.liveResults = await api(`/api/user/${S.userId}/swiggy/restaurants?query=${encodeURIComponent(query)}`);
  render();
}
async function refreshLiveCart(show = true) {
  try {
    const r = await api(`/api/user/${S.userId}/swiggy/live-cart`);
    S.liveCart = r.cart;
    S.liveCartEmpty = r.cart ? null : { address: r.address || S.swiggy?.address?.label || "this address",
                                         address_verified: r.address_verified !== false };
    S.liveCartError = null; S.liveCartErrorCode = null;
    if (show && !r.cart) toast("Your Swiggy cart is empty");
  } catch (error) { S.liveCart = null; S.liveCartEmpty = null; S.liveCartError = error.message; S.liveCartErrorCode = error.code || null; S.liveCartErrorRule = error.data?.rule || null; }
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
  S.liveFavourites = r.restaurants; toast(r.favourite ? `${name} saved. Ziggy plans from your saved places.` : `${name} removed from Saved`); render();
}
async function openLivePlace(id, name, dish) {
  S.sheet = null; S.liveLoading = name; S.liveBrowseMenu = null; S.menuCat = null; render();
  try { S.liveBrowseMenu = await api(`/api/user/${S.userId}/swiggy/live-menu?restaurant_id=${encodeURIComponent(id)}&restaurant_name=${encodeURIComponent(name)}`); }
  finally { S.liveLoading = null; }
  render();
  if (dish) await searchLiveDishes(dish);
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
  if (r.adding) return;
  r.adding = true; render();
  try {
    S.liveCart = await api(`/api/user/${S.userId}/swiggy/live-cart`, "POST", {
      restaurant_id: r.restaurant_id, restaurant_name: r.restaurant, item_id: r.item_id,
      item_name: r.item, expected_fingerprint: r.fingerprint });
    S.placedOrder = null; S.liveOrderStatus = null; S.liveCartError = null;
  } finally { S.liveOrderReview = null; }
  S.liveBrowseMenu = null; S.tab = "today";
  toast("Added to your Swiggy cart");
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
async function trackLiveOrder(orderId = S.placedOrder?.order_id) {
  if (!orderId) throw new Error("Choose a recorded order to track.");
  S.liveOrderStatus = await api(`/api/user/${S.userId}/swiggy/orders/${encodeURIComponent(orderId)}`);
  render();
}
async function planFromLiveMenus() {
  S.liveSync = "loading"; render();
  try { adoptView(await api(`/api/plan/${S.planId}/live-menus`, "POST", {})); S.liveSync = null; }
  catch (e) {
    S.liveSync = null;
    if (SWIGGY_RECONNECT.has(e.code)) throw e;          // the fix is Connect, handled by guard()
    S.liveSync = { error: e.message };
  }
  render();
}
// Connected with an address but no live menus for it yet: read them once, by itself.
async function autoSyncLive() {
  const src = S.view?.source;
  const addr = S.swiggy?.address?.id;
  if (!src || src.kind !== "none" || src.needs !== "sync" || !swiggyReady() || !addr) return;
  S.liveSyncTried = S.liveSyncTried || {};
  if (S.liveSyncTried[addr]) return;
  S.liveSyncTried[addr] = true;
  S.liveSync = "loading"; render();
  try {
    adoptView(await api(`/api/plan/${S.planId}/live-menus`, "POST", {}));
    S.liveSync = null;
  } catch (e) {
    if (SWIGGY_RECONNECT.has(e.code)) { S.liveSync = null; S.error = e.message; S.errorCode = e.code; }
    else S.liveSync = { error: e.message };
  }
  render();
}
/* One tap: Ziggy reads the exact live item, then puts exactly that in your Swiggy cart and
   shows Swiggy's own bill. The elephant rolls across the button meanwhile. You still pay
   in Swiggy; nothing is ordered here. */
async function orderMeal(sid) {
  if (S.adding) return;
  S.adding = sid; render();
  let r;
  try {
    const preview = await api(`/api/session/${sid}/swiggy-cart/preview`);
    r = await api(`/api/session/${sid}/swiggy-cart`, "POST", { expected_fingerprint: preview.fingerprint });
  } finally { S.adding = null; }
  S.carts = { ...(S.carts || {}), [sid]: r };
  // The same cart, shown once (on the meal), not as a stale "empty" anywhere else.
  S.liveCartEmpty = null; S.liveCartError = null; S.liveCartErrorCode = null;
  S.liveCart = { item: r.item, restaurant: r.restaurant, to_pay: r.to_pay, bill: r.bill, orderable: true,
    checkout_url: r.checkout_url, cancellation_note: r.cancellation_note };
  if (!store.get("ziggy.asked-reminders") && pushState() !== "on") S.ask = true;
  S.fresh = sid;
  toast("Added to your Swiggy cart");
  render();
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

/* ---------------------------------------------------------------- render */
/* Honest copy that follows what this server actually does (GET /api/meta). */
// Profiles on a temporary disk are erased on restart or redeploy: say so on every screen.
function storageBanner() {
  if (S.meta?.storage?.persistent !== false) return "";
  return `<div class="storage-warning" role="note"><strong>Trial server:</strong> profiles, plans and Swiggy links here are stored on a temporary disk and are erased whenever the server restarts or is updated. Keep a copy with Me → Account → Download my data.</div>`;
}
function welcomeLede() {
  // Restaurant dishes come only from Swiggy for the user's own address; nothing is sample data.
  return swiggySignInOpen()
    ? "Ziggy picks every meal from Swiggy places near you, inside your budget, and tells you exactly when to order."
    : "Ziggy plans every meal around your diet and budget. Real Swiggy dishes appear once Swiggy approves connecting from Ziggy.";
}
function render() {
  const app = document.getElementById("app");
  if (S.planning) { app.innerHTML = planningScreen(); wire(); return; }
  if (S.onboard) {
    const step = `ob${S.onboard.step}`, arrive = step !== S.viewKey; S.viewKey = step;
    app.innerHTML = storageBanner() + onboardingScreen().replace('<main class="onboard">', `<main class="onboard${arrive ? " anim" : ""}">`); wire(); return;
  }
  if (S.welcome || !S.view) { app.innerHTML = storageBanner() + welcomeScreen(); wire(); return; }
  const viewKey = [S.tab, S.more, S.weekView, S.savedTab].join("|");
  const arrive = viewKey !== S.viewKey; S.viewKey = viewKey;
  app.innerHTML = storageBanner() + offlineBar() + `<div class="shell">${navBar()}<div class="col">${topbar()}`
    + `<div class="body"><main class="main${arrive ? " anim" : ""}" id="main">${errbar() + tabBody()}</main>${balanceAside()}</div></div></div>`
    + (S.sheet ? sheetDialog() : "") + (S.addrSheet ? addressSheet() : "") + (S.connectSheet ? connectSheet() : "")
    + (S.liveBrowseMenu || S.liveLoading ? menuSheet() : "")
    + (S.orderReview ? orderReviewDialog() : "") + (S.liveOrderReview ? liveOrderReviewDialog() : "")
    + (S.checkoutReview ? checkoutReviewDialog() : "");
  // A sheet slides up once, when it opens; re-renders while it is open don't replay that.
  const open = [...(document.querySelectorAll?.(".sheet-bg[data-ov]") || [])];
  open.forEach(el => { if (!S.openSheets?.has(el.dataset.ov)) el.classList.add("enter"); });
  S.openSheets = new Set(open.map(el => el.dataset.ov));
  S.fresh = null;
  wire();
  queueQuick();
}
// Offline: say so, and how old the plan on screen is. Nothing is cached as if it were live.
function offlineBar() {
  if (!S.offline) return "";
  const at = S.loadedAt ? S.loadedAt.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" }) : null;
  return `<div class="offline" role="status">You're offline.${at ? ` Showing your plan as loaded at ${esc(at)}.` : ""} Changes, live menus and your Swiggy cart need a connection.</div>`;
}
function errbar(retry = true) {
  if (!S.error) return "";
  if (SWIGGY_RECONNECT.has(S.errorCode)) return `<div class="errbar connect" role="alert">${connectPrompt()}
    <button class="x" data-close-err="1" title="Dismiss" aria-label="Dismiss">${ICON.close}</button></div>`;
  return `<div class="errbar" role="alert"><div class="msg">${esc(S.error)}${ruleWhy(S.errorRule)}</div>
    ${retry ? `<button class="small" data-act="reload">Retry</button>` : ""}
    <button class="x" data-close-err="1" title="Dismiss" aria-label="Dismiss">${ICON.close}</button></div>`;
}
// The rule behind a Swiggy problem: what it is, whose rule, and what to do now.
function ruleWhy(rule) {
  if (!rule) return "";
  return `<details class="why-rule"><summary>Why? ${esc(rule.title)}</summary>
    <p>${esc(rule.plain)}</p><p><b>What to do:</b> ${esc(rule.fix)}</p>
    <p class="fine">${rule.whose === "swiggy" ? "Swiggy's rule" : "Ziggy's safety rule"} · <button class="link" data-act="rules-open">All ordering rules</button></p></details>`;
}
// A live panel's own error: the shared Connect prompt when that is the fix, else the message.
function liveError(msg, code, rule) {
  if (SWIGGY_RECONNECT.has(code)) return connectPrompt();
  return msg ? `<div class="fine" role="alert">${esc(msg)}${ruleWhy(rule)}</div>` : "";
}
// Me → Swiggy: every rule that can stop an order, marked when it stopped this profile lately.
function rulesCard() {
  const r = S.swRules;
  if (!r) return `<section class="card" id="rules"><h2 class="h2">How ordering works</h2><div class="skeleton" aria-busy="true"><i></i><i></i></div></section>`;
  const hit = r.rules.filter(x => x.hits_7d).sort((a, b) => b.hits_7d - a.hits_7d);
  return `<section class="card" id="rules" aria-labelledby="rules-h"><h2 class="h2" id="rules-h">How ordering works</h2>
    <p class="fine">The rules that can stop a cart or an order, and what to do. ${hit.length ? `Marked: what stopped you in the last 7 days.` : "Nothing has stopped you in the last 7 days."}</p>
    <ul class="rules">${[...hit, ...r.rules.filter(x => !x.hits_7d)].map(x => `<li class="${x.hits_7d ? "hit" : ""}"><details><summary><b>${esc(x.title)}</b>
      ${x.hits_7d ? `<span class="tag warn">${x.hits_7d}× this week</span>` : ""}<span class="tag">${x.whose === "swiggy" ? "Swiggy" : "Ziggy"}</span></summary>
      <p>${esc(x.plain)}</p><p class="fine"><b>What to do:</b> ${esc(x.fix)}</p></details></li>`).join("")}</ul>
    ${r.issues.length ? `<details><summary>Recent problems (${r.issues.length})</summary><ul class="issues">${r.issues.map(i => `<li><span class="fine">${esc(fmtWhen(i.at))} · ${esc(i.title)}</span><br>${esc(i.message)}</li>`).join("")}</ul></details>` : ""}
  </section>`;
}

/* ================================================================ WELCOME */
function welcomeScreen() {
  return `<main class="welcome">
    ${mascot("float")}
    <div class="wordmark" style="font-size:30px">ziggy</div>
    <h1 class="hero-h">Your week of meals, already decided.</h1>
    <p class="sub" style="font-size:17px">${welcomeLede()}</p>
    <ul class="promise">
      <li><span><b>One tap to your Swiggy cart.</b> You check the bill and pay in Swiggy.</span></li>
      <li><span><b>Your rules, every meal.</b> Diet and budget shape each pick. Check ingredients with the restaurant.</span></li>
    </ul>
    <div class="grow"></div>
    ${S.signin ? "" : errbar(false)}
    ${S.signin ? `<form id="signin" class="card signin">
        <h2 class="h2">Welcome back</h2>
        ${S.error ? `<p class="inline-err" role="alert">${esc(S.error)}</p>` : ""}
        <label>Sign-in name<input id="si-login" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" value="${esc(S.signinDraft || "")}" required></label>
        <label>Password<input id="si-pw" type="password" autocomplete="current-password" required></label>
        <button type="submit" class="primary">Sign in</button>
        <button type="button" class="ghost" data-act="signin-close">Cancel</button>
        <p class="fine">No sign-in yet? Open your profile where you made it, then Me → Account → Sign in anywhere.</p></form>`
      : `<button class="primary big" data-act="start-onboard">Get started</button>
         <button class="ghost big" data-act="signin-open">I already have an account</button>`}
    <p class="fine center">Every real order needs your OK, and you pay in Swiggy.</p>
  </main>`;
}

/* ================================================================ SHELL */
function topbar() {
  const sw = S.swiggy;
  let pill = "";
  if (sw?.connected && sw.address) pill = `<button class="addr" data-act="addr-open" aria-haspopup="dialog" aria-label="Delivering to ${esc(sw.address.label)}. Change address">${ICON.place}<span>${esc(sw.address.label)}</span>${ICON.down}</button>`;
  else if (sw?.connected) pill = `<button class="addr warn" data-act="addr-open" aria-haspopup="dialog">${ICON.place}<span>Choose delivery address</span></button>`;
  else if (sw && (swiggySignInOpen() || sw.expired)) pill = `<button class="addr warn" data-act="connect-open" aria-haspopup="dialog">${sw.expired ? "Reconnect Swiggy" : "Connect Swiggy"}</button>`;
  return `<header class="topbar">${mark()}${pill}<span class="spacer"></span>
    <button class="avatar" data-tab="more" aria-label="Me: settings, money and account" title="${esc(S.view.user?.name)}">${esc((S.view.user?.name || "?").slice(0, 1).toUpperCase())}</button></header>`;
}
const TABS = [["today", "Today", ICON.today], ["week", "Week", ICON.week], ["saved", "Saved", ICON.saved]];
function navBar() {
  return `<nav class="nav" aria-label="Main"><div class="brand-rail">${mark()}<span class="wordmark">ziggy</span></div>${TABS.map(([k, l, i]) =>
    `<button class="tab" data-tab="${k}" ${S.tab === k ? 'aria-current="page"' : ""}>${i}<span>${l}</span></button>`).join("")}</nav>`;
}
/* ================================================================ WIDE SCREENS
   Beside the meals on a computer: the week's balance (money, nutrition against the
   person's own targets, how the week is made up, a day-by-day glance, the household
   split). All read from the plan; nothing here is new data. Hidden on phones. */
const NUTRIENTS = [["Calories", "kcal", ""], ["Protein", "protein_g", " g"], ["Carbs", "carbs_g", " g"], ["Fat", "fat_g", " g"]];
function balanceAside() {
  if (!S.view?.plan || S.tab === "more") return "";
  const v = S.view, b = v.budget || {}, n = v.nutrition || {}, avg = n.daily_avg || {}, t = n.daily_target || {};
  const cap = b.budget || 0, spend = b.spend || 0, left = cap - spend, pct = cap ? Math.min(1, spend / cap) : 0;
  const C = 2 * Math.PI * 42;
  const donut = `<svg viewBox="0 0 100 100" class="donut" aria-hidden="true"><circle cx="50" cy="50" r="42" class="d-bg"/>
    <circle cx="50" cy="50" r="42" class="d-fg${left < 0 ? " over" : ""}" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${(C * (1 - pct)).toFixed(1)}" transform="rotate(-90 50 50)"/></svg>`;
  const bars = NUTRIENTS.filter(([, k]) => t[k]).map(([label, k, u]) => {
    const val = avg[k] || 0, ratio = val / t[k];
    const tone = ratio < 0.8 ? "low" : ratio > 1.15 ? "high" : "ok";
    return `<div class="nut ${tone}"><div class="nb-h"><span>${label}</span><span><b>${Math.round(val)}</b>${u} of ${Math.round(t[k])}${u}</span></div>
      <div class="nb-t" role="img" aria-label="${label}: ${Math.round(ratio * 100)}% of your daily target"><i style="width:${Math.min(100, ratio * 100).toFixed(0)}%"></i></div></div>`;
  }).join("");
  const cnt = v.counts || {}, mix = [["Order", cnt.delivery || 0, "o"], ["Cook", cnt.cook || 0, "c"], ["Skip", cnt.skip || 0, "s"]];
  const total = mix.reduce((a, [, x]) => a + x, 0) || 1;
  const today = todayIso();
  const glance = (v.grid || []).map((d, i) => `<button class="gday${d.date === today ? " now" : ""}" data-glance-day="${i}" aria-label="${esc(d.day)} ${esc(fmtDate(d.date))}: ${MEALS.map(m => {
      const c = d.meals[m]; return c ? `${m} ${c.kind === "delivery" ? "order" : c.kind}` : null; }).filter(Boolean).join(", ") || "nothing planned"}">
      <span>${esc(d.day.slice(0, 2))}</span>${MEALS.map(m => { const c = d.meals[m]; if (!c) return `<i class="none"></i>`;
        const k = ["ordered", "confirmed"].includes(c.status) ? "done" : c.kind === "delivery" ? "o" : c.kind === "cook" ? "c" : "s";
        return `<i class="${k}"></i>`; }).join("")}</button>`).join("");
  const hh = v.household;
  return `<aside class="aside" aria-label="Your week at a glance">
    <section class="card"><p class="k">This week</p>
      <div class="money-row">${donut}<div class="t"><b class="h3">${left >= 0 ? `${rupee0(left)} left` : `${rupee0(-left)} over`}</b>
        <span class="muted">${rupee0(spend)} planned of ${rupee0(cap)}</span>
        ${b.daily_cap ? `<span class="fine">Daily cap ${rupee0(b.daily_cap)}</span>` : ""}</div></div>
      <div class="mix" role="img" aria-label="${mix.map(([l, x]) => `${l} ${x}`).join(", ")}">${mix.filter(([, x]) => x).map(([, x, k]) => `<i class="${k}" style="flex:${x / total}"></i>`).join("")}</div>
      <div class="mix-key">${mix.map(([l, x, k]) => `<span><i class="${k}"></i>${l} ${x}</span>`).join("")}</div></section>
    ${bars ? `<section class="card"><p class="k">Nutrition · a day, on average</p>${bars}
      <p class="fine">From the meals in your plan${v.source?.kind === "live" ? ", estimated from dish names" : ""}. Skipped meals add nothing.</p></section>` : ""}
    <section class="card"><p class="k">Day by day</p><div class="glance">${glance}</div>
      <div class="mix-key"><span><i class="o"></i>Order</span><span><i class="c"></i>Cook</span><span><i class="done"></i>Had</span></div></section>
    ${hh?.split?.length ? `<section class="card"><p class="k">${esc(hh.name || "Household")} · split so far</p>
      <table>${hh.split.map(x => `<tr><td>${esc(x.member)}</td><td class="num">${rupee(x.share)}</td></tr>`).join("")}</table></section>` : ""}
  </aside>`;
}
function tabBody() {
  if (S.tab === "week") return weekScreen();
  if (S.tab === "saved") return savedScreen();
  if (S.tab === "more") return meScreen();
  return todayScreen();
}

/* ================================================================ TODAY */
function todayScreen() {
  const v = S.view, nu = v.next_up, b = v.budget || {};
  const left = (b.budget || 0) - (b.spend || 0);
  const first = v.user?.name && v.user.name !== "Me" ? `, ${esc(v.user.name.split(" ")[0])}` : "";
  return `<div class="rise"><h1 class="h1">${greeting()}${first}</h1>
      <p class="muted">${left >= 0 ? `${rupee0(left)} left this week` : `${rupee0(-left)} over this week's budget`} · <button class="link" data-tab="week" style="min-height:0;font-size:inherit;color:inherit">see the week</button></p></div>
    ${realDishesState()}
    ${hungryCard(nu)}
    ${nu ? nextUpCard(nu) : v.source?.kind === "none" ? "" : `<section class="empty rise2">${mascot()}<h2 class="h2">Nothing left to plan this week.</h2><button class="primary" data-act="newweek">Plan next week</button></section>`}
    ${S.ask ? askReminders() : ""}
    ${liveCartBlock()}
    ${laterList(nu)}
    ${headsUp()}`;
}
/* Where real restaurant dishes stand. Never filler: no live menus means an honest empty
   state with the one next step (connect, choose an address), a skeleton while Ziggy
   reads Swiggy for the address, or why it couldn't. */
function realDishesState() {
  const src = S.view.source || {};
  if (src.kind === "live") return "";
  const tag = src.kind === "sample" ? `<p class="fine">Sample dishes (test data)</p>` : "";
  const needs = src.needs || (src.connected ? "sync" : "connect");
  if (S.liveSync === "loading" || (src.kind === "none" && needs === "sync" && swiggyReady() && !S.liveSync)) {
    return `<section class="card hero" aria-busy="true" aria-label="Finding real dishes">
      <p class="eyebrow">Swiggy · ${esc(S.swiggy?.address?.label || "your address")}</p>
      <h2 class="h2">Finding real dishes near you…</h2><div class="skeleton" aria-hidden="true"><i></i><i></i><i></i></div></section>`;
  }
  if (S.liveSync?.error) {
    return `${tag}<section class="empty" role="status"><h2 class="h2">No live pick yet</h2>
      <p class="sub">${esc(S.liveSync.error)}</p>
      <button class="secondary" data-act="live-menus">Try again</button>
      <p class="fine">Or save a restaurant you like in Saved.</p></section>`;
  }
  if (needs === "sync") {
    return `${tag}<section class="empty" role="status"><h2 class="h2">See real dishes for your address</h2>
      <button class="primary" data-act="live-menus">Find real dishes on Swiggy</button></section>`;
  }
  if (needs === "address") {
    return `${tag}<section class="empty" role="status"><h2 class="h2">Pick a delivery address</h2>
      <p class="sub">Real dishes and prices depend on where Swiggy delivers.</p>
      <button class="primary" data-act="addr-open">Choose delivery address</button></section>`;
  }
  if (src.kind === "sample" && !swiggySignInOpen()) return tag;       // nothing to connect yet: don't contradict the sample plan
  return `${tag}<section class="empty" role="status">${mascot()}<h2 class="h2">Plan from real places near you</h2>
    ${swiggySignInOpen() ? connectPrompt() : `<p class="sub">${esc(src.note || "")}</p>`}</section>`;
}
// Why this pick, in plain words from real fields. Never a score.
function whyChips(c) {
  const u = S.view.user || {}, b = S.view.budget || {}, out = [];
  if (c.pinned) out.push(["Your pick", "ok"]);
  if (c.usual) out.push(["Your usual place", ""]);
  if (c.kind === "delivery" || c.kind === "cook") {
    if ((b.budget || 0) >= (b.spend || 0)) out.push(["Week stays in budget", "ok"]);
    else out.push(["Week is over budget", "warn"]);
  }
  const p = c.nutrition?.protein_g;
  if (p >= 15) out.push([`≈${Math.round(p)} g protein (estimate)`, ""]);
  const al = (u.allergens || []).map(a => a.replace("_", " "));
  if (al.length && c.kind === "delivery") out.push([S.view.source?.kind === "live" ? `No ${al.join(", ")} by dish name` : `${cap1(al.join(", "))} filtered out`, "ok"]);
  if (al.length && c.kind === "cook") out.push([`Recipe has no ${al.join(", ")}`, "ok"]);
  if (u.diet === "veg" || u.diet === "vegan") out.push([u.diet === "vegan" ? "Vegan plan" : "Vegetarian", ""]);
  return out.length ? `<ul class="why" aria-label="Why this pick">${out.map(([t, k]) => `<li class="${k}">${esc(t)}</li>`).join("")}</ul>` : "";
}
// "Order by 19:45": a ring that empties as the order-by time gets closer (the last 3 hours).
function orderTimer(order) {
  if (!order?.order_at) return "";
  const mins = minutesUntil(order.order_at);
  const frac = mins == null ? 1 : Math.max(0, Math.min(1, mins / 180));
  const off = (94.2 * (1 - frac)).toFixed(1);
  return `<span class="timer" aria-label="Order by ${esc(order.order_at)}${mins != null ? `, in ${mins} minutes` : ""}">
    <svg viewBox="0 0 36 36" aria-hidden="true"><circle class="ring-bg" cx="18" cy="18" r="15"/><circle class="ring" cx="18" cy="18" r="15" stroke-dasharray="94.2" stroke-dashoffset="${off}" transform="rotate(-90 18 18)"/></svg>
    <span><b>Order by ${esc(order.order_at)}</b><span class="muted" style="display:block;font-size:12px">${mins != null && mins <= 180 ? `in ${mins} min` : esc(order.why || "")}</span></span></span>`;
}
function nextUpCard(nu) {
  const c = nu.cell, when = `${nu.when} · ${cap1(nu.meal)}`;
  const isCook = c.kind === "cook";
  if (c.status === "confirmed" || c.status === "ordered") {
    return `<section class="card hero rise2" aria-label="Next meal"><div class="dish-row">${dishIcon(c.item, { cook: isCook, lg: true, image: photoOf(c) })}<div class="t"><span class="eyebrow">${esc(when)}</span>
      <h2 class="h2">${esc(c.item)}</h2><span class="muted">${c.status === "ordered" ? "Ordered" : "You had this"} · ${cellMoney(c)}</span></div></div>
      ${rateRow(c)}${learnedChips()}</section>`;
  }
  const order = c.order || {};
  const sampleDish = !isCook && S.view.source?.kind === "sample";      // test fixtures only
  const carted = (S.carts || {})[c.session_id];
  const adding = S.adding === c.session_id;
  const live = swiggyReady();
  const reasons = (c.reasons || []).filter(r => !/^Ordered |from .* \(₹/.test(r)).slice(0, 4);
  let cta = "";
  if (isCook) cta = `<button class="primary" data-confirm="${c.session_id}">I cooked it</button>`;
  else if (carted) cta = "";
  else if (live && cartEligible(c)) cta = `<button class="primary" data-cart="${c.session_id}" aria-live="polite" ${adding ? 'aria-busy="true"' : ""}>${adding ? `${roller()}<span>Adding to your cart…</span>` : (c.portions || 1) > 1 ? `Add ${c.portions} to Swiggy cart` : "Add to Swiggy cart"}</button>`;
  else if (!live && swiggySignInOpen() && cartEligible(c) && S.view.source?.kind === "live") cta = `<button class="primary" data-connect-order="${c.session_id}">Connect Swiggy &amp; add</button>`;
  else if (c.handoff_url) cta = `<a class="btn primary" href="${esc(c.handoff_url)}" target="_blank" rel="noopener" data-handoff="${c.session_id}">Order on Swiggy ${ICON.out}</a>`;
  const heart = !isCook && c.restaurant_id && S.view.source?.kind !== "live"
    ? `<button class="heart" data-fav="${c.restaurant_id}" aria-pressed="${!!c.usual}" aria-label="${c.usual ? "Remove" : "Save"} ${esc(c.restaurant)} ${c.usual ? "from" : "to"} your places">${ICON.heart}</button>` : "";
  return `<section class="card hero rise2 ${esc(c.kind)}" aria-label="Next meal">
    <div class="hero-head"><span class="eyebrow">${esc(when)}${sampleDish ? " · sample dish" : ""}</span>${!isCook && !carted ? orderTimer(order) : ""}</div>
    <div class="dish-row">${dishIcon(c.item, { cook: isCook, lg: true, image: photoOf(c) })}<div class="t">
      <h2 class="h2">${esc(c.item)}</h2>
      <span class="muted" style="font-size:14px">${isCook ? "Cook at home" : esc(c.restaurant)}${!isCook && c.rating ? ` · ${Number(c.rating).toFixed(1)}★` : ""}</span></div>${heart}</div>
    ${carted ? "" : whyChips(c)}
    ${carted ? "" : `<div class="price-line"><span class="money">${c.real_bill ? real(c.cost) : est(c.cost)}</span><span class="fine">${(c.portions || 1) > 1 ? `for ${c.portions} · ` : ""}${isCook ? esc(c.cost_basis || "grocery estimate") : c.real_bill ? "Swiggy's bill" : "Swiggy shows the exact bill"}</span></div>`}
    ${carted ? "" : whoEats(c)}
    ${reasons.length && !carted ? `<details class="why-more"><summary>Why this?</summary><ul>${reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul></details>` : ""}
    ${cta}
    ${carted ? "" : quickStrip(c)}
    ${carted ? "" : `<div class="two"><button class="secondary" data-sheet="${c.session_id}">${ICON.swap}Change</button>
      <button class="secondary" data-sess="${c.session_id}:skipped">Skip ${esc(nu.meal)}</button></div>
      ${isCook ? "" : `<button class="ghost small" data-confirm="${c.session_id}">Already had it? Mark as had</button>`}`}
    ${!isCook && live && !cartEligible(c) && !carted ? `<div class="fine">Ziggy can't verify ${ruleBound(S.view.user) ? "your" : "everyone's"} ingredient or medical rules on Swiggy's menu. Check this dish in Swiggy before ordering.${ruleWhy(INGREDIENTS_RULE)}</div>` : ""}
    ${cartNote(c)}
    ${S.handedOff === c.session_id && !carted ? `<div class="note"><span class="ic">✓</span><span>Placed it on Swiggy? Tap <b>Mark as had</b> so your budget stays right.</span></div>` : ""}
  </section>`;
}
// After the cart: Swiggy's real bill, the estimate it replaced, and paying is the next step.
function cartNote(c) {
  const r = (S.carts || {})[c.session_id];
  if (!r) return "";
  const diff = r.over_plan == null ? "" : r.over_plan > 0 ? ` · ${rupee(r.over_plan)} more` : " · within plan";
  const warn = r.budget?.over && !r.budgetOk;
  const fresh = S.fresh === c.session_id;
  return `<div class="bill-card${fresh ? " fresh" : ""}" role="status"><div class="ok-head">${mascot(fresh ? "hop" : "", "")}Added to your Swiggy cart${r.quantity > 1 ? ` · ${r.quantity} portions` : ""}</div>
    ${r.bill ? `<p class="k">Swiggy's bill</p>${billLines(r.bill)}`
      : r.to_pay != null ? `<div>To Pay ${real(r.to_pay)}</div>` : `<p class="fine">Open Swiggy to see the total.</p>`}
    ${r.planned_cost != null && r.to_pay != null ? `<p class="fine">Plan estimate ${est(r.planned_cost)}${diff}.</p>` : ""}</div>
    ${warn ? budgetWarn(c.session_id, r.budget) : checkoutLink(r, c.session_id)}
    <button class="ghost small" data-confirm="${c.session_id}">Ordered and eaten? Mark as had</button>
    <p class="fine">You pay in Swiggy. Ziggy never pays for you.</p>`;
}
// The real bill takes the week over budget: say by how much, and let the user choose.
function budgetWarn(sid, b) {
  return `<div class="overbudget" role="alert"><div><b>Over budget.</b> ${esc(b.message)}</div>
    <div class="two"><button data-over-ok="${sid}">Approve anyway</button>
    <button data-replan="${sid}">Re-plan the rest</button></div>
    <p class="fine">Both are fine: you decide. Re-plan keeps ordered, kept and carted meals and changes the rest.</p></div>`;
}
// Swiggy's bill, line by line with Swiggy's own labels and exact paise. Nothing invented.
function billLines(bill) {
  if (!bill) return `<p class="fine">Swiggy didn't return an itemised bill.</p>`;
  return `<table class="bill">${bill.lines.map(l => `<tr><td>${esc(l.label)}</td><td class="num">${real(l.amount)}</td></tr>`).join("")}
    <tr class="total"><td><b>To Pay</b></td><td class="num"><b>${real(bill.to_pay)}</b></td></tr></table>
    ${bill.rounding != null ? `<p class="fine">Swiggy rounds the total to the rupee.</p>` : ""}
    ${bill.unitemised != null ? `<p class="fine">Swiggy's total includes ${real(Math.abs(bill.unitemised))} it didn't itemise.</p>` : ""}`;
}
// The hand-off to Swiggy's checkout page, with Swiggy's own cancellation note when it sent one.
function checkoutLink(c, sid) {
  return `<a class="btn primary" href="${esc(c.checkout_url || "https://www.swiggy.com/checkout")}" target="_blank" rel="noopener"${sid ? ` data-handoff="${sid}"` : ""}>Pay in Swiggy ${ICON.out}</a>
    <p class="fine cancel-note">${esc(c.cancellation_note || "Swiggy's cancellation policy applies.")}</p>`;
}
function rateRow(c) {
  const g = c.rating_given;
  return `<p class="muted">How was it? One tap teaches Ziggy.</p>
    <div class="two" role="group" aria-label="Rate this meal">
      <button class="secondary ${g === 1 ? "on" : ""}" data-rate="${c.session_id}:1" aria-pressed="${g === 1}">Good</button>
      <button class="secondary ${g === -1 ? "on" : ""}" data-rate="${c.session_id}:-1" aria-pressed="${g === -1}">Not again</button></div>
    ${g ? `<div class="chips" role="group" aria-label="Why? (one tap)">${Object.entries(RATE_REASONS).map(([k, label]) =>
      `<button class="chip" data-reason="${c.session_id}:${k}" aria-pressed="${(c.reasons_given || []).includes(k)}">${esc(label)}</button>`).join("")}</div>` : ""}
    ${S.suggestFav ? `<div class="note"><span class="ic">♥</span><span>You liked a dish from <b>${esc(S.suggestFav.restaurant)}</b>.</span><button class="small" data-fav="${S.suggestFav.restaurant_id}">Save the place</button></div>` : ""}`;
}
function learnedChips() {
  const l = S.view.learned || [];
  if (!l.length) return "";
  return `<div class="chips" aria-label="What Ziggy learned">${l.map(x => `<span class="chip" style="cursor:default">${esc(x.text)}
    <button class="x" data-unlearn="${esc(x.key)}" title="Undo" aria-label="Undo: ${esc(x.text)}">${ICON.close}</button></span>`).join("")}</div>`;
}
function askReminders() {
  const push = pushState();
  if (push === "on" || (push === "none" && typeof Notification === "undefined")) return "";
  return `<section class="note rise" aria-label="Reminders"><span class="ic">${ICON.bell}</span><div class="stack" style="gap:6px;flex:1">
    <b>Want a nudge at order-by time?</b><span class="muted">One notification per meal, only when it's time to order.</span>
    ${push === "ios-install" ? `<span class="fine">On iPhone, tap Share → Add to Home Screen, then open Ziggy from there.</span>` : ""}
    <div class="row"><button class="small" data-act="notify-yes">Turn on</button><button class="small ghost" data-act="notify-no">Not now</button></div></div></section>`;
}
function liveCartBlock() {
  const c = S.liveCart;
  const blocks = [];
  if (S.placedOrder) blocks.push(`<section class="card"><p class="eyebrow">Swiggy order confirmed</p><b>${esc(S.placedOrder.item)} · ${rupee(S.placedOrder.to_pay)}</b>
    <p class="fine">Order ${esc(S.placedOrder.order_id)}${S.placedOrder.message ? ` · ${esc(S.placedOrder.message)}` : ""}</p>
    <button class="small" data-act="track-live-order">Check delivery status</button></section>`);
  if (S.liveOrderStatus) blocks.push(trackingCard());
  if (S.liveCartError || S.liveCartErrorCode) blocks.push(liveError(S.liveCartError, S.liveCartErrorCode, S.liveCartErrorRule));
  // Already shown, with its bill, on Today's meal card: don't repeat it.
  const onCard = c && S.view?.next_up && (S.carts || {})[S.view.next_up.cell.session_id];
  if (c && !onCard) {
    blocks.push(`<section class="card" role="status"><p class="eyebrow">In your Swiggy cart</p><b>${esc(c.item)} · ${esc(c.restaurant)}</b>
      ${c.bill ? billLines(c.bill) : c.to_pay == null ? `<p class="fine">Check the final total in Swiggy.</p>` : `<div>To Pay ${real(c.to_pay)}</div>`}
      ${S.swiggy?.order_enabled && c.orderable !== false ? `<button class="secondary" data-act="review-live-checkout">Review and place order</button>` : ""}
      ${checkoutLink(c)}
      ${c.other_address ? `<p class="fine">This cart is for ${esc(c.other_address.label)}, not ${esc(S.swiggy.address.label)}.${c.other_address.id ? ` <button class="small" data-swaddr="${esc(c.other_address.id)}">Deliver there instead</button>` : ""} Or clear the cart in Swiggy.</p>`
        : c.orderable === false ? `<p class="fine">This cart differs from the item reviewed here. Check or clear it in Swiggy before adding another.</p>` : ""}</section>`);
  } else if (!c && S.liveCartEmpty && !S.liveCartEmpty.address_verified) {
    blocks.push(`<p class="fine">Your Swiggy cart for ${esc(S.liveCartEmpty.address)} looks empty, but Swiggy didn't confirm the address: check in Swiggy before ordering.</p>`);
  }
  return blocks.join("");
}
function trackingCard() {
  const t = S.liveOrderStatus.tracking || {};
  return `<section class="card" role="status"><p class="eyebrow">Order ${esc(S.liveOrderStatus.order_id)}</p><h3 class="h3">${esc(t.title || t.status || "Check delivery in Swiggy")}</h3>
    <p class="muted">${esc(t.subtitle || t.message || "Swiggy did not return a current delivery update.")}</p>${t.eta ? `<p>${esc(t.eta)}</p>` : ""}</section>`;
}
// The rest of today, or tomorrow when today is done: one tap opens the same Change sheet.
/* Today, for people who decide when they're hungry: other dishes for the same meal one tap
   away (no sheet), and any meal whose time it is now, even one the week leaves out. */
const quickKey = (c) => `${c.session_id}:${c.kind}:${c.item_id ?? c.recipe_key ?? ""}`;
function queueQuick() {
  if (S.tab !== "today" || !S.view || S.offline) return;
  const c = S.view.next_up?.cell;
  if (!c || c.status !== "active" || !["delivery", "cook"].includes(c.kind) || (S.carts || {})[c.session_id]) return;
  const key = quickKey(c);
  if (S.quick?.key === key) return;
  S.quick = { key, sid: c.session_id, data: null };
  setTimeout(() => loadQuick(key), 0);
}
async function loadQuick(key) {
  let data = null;
  try { data = await api(`/api/session/${S.quick.sid}/options`); } catch { /* the strip just stays away */ }
  if (S.quick?.key !== key) return;
  S.quick.data = data || { failed: true };
  render();
  loadPhotos([S.view.next_up?.cell, ...quickPicks(S.quick.data)]);
}
// Up to four dishes: the best of each place first (variety), dishes that fit the budget first.
function quickPicks(d) {
  if (!d || d.failed) return [];
  const groups = d.usual || [];
  const firsts = groups.filter(g => g.dishes[0]).map(g => ({ ...g.dishes[0], restaurant: g.restaurant }));
  const rest = groups.flatMap(g => g.dishes.slice(1).map(x => ({ ...x, restaurant: g.restaurant })));
  const all = [...firsts, ...(d.new || []), ...rest].filter(x => !x.current);
  return [...all.filter(x => x.fits), ...all.filter(x => !x.fits)].slice(0, 4);
}
function quickStrip(c) {
  const q = S.quick;
  if (!q || q.sid !== c.session_id) return "";
  if (!q.data) return `<div class="quick-wrap" aria-busy="true"><p class="k">Or have instead</p><div class="quick">${'<span class="qpick ghosted"></span>'.repeat(3)}</div></div>`;
  const picks = quickPicks(q.data);
  const cook = c.kind !== "cook" ? (q.data.cook || [])[0] : null;
  if (!picks.length && !cook) return "";
  return `<div class="quick-wrap"><p class="k" id="quick-${c.session_id}">Or have instead</p>
    <div class="quick" role="group" aria-labelledby="quick-${c.session_id}">
    ${picks.map(x => `<button class="qpick${x.fits ? "" : " over"}" data-quick="${x.item_id}" data-quick-name="${esc(x.name)}"
      aria-label="${esc(`${x.name}, ${x.restaurant}, about ${rupee0(x.price)}${x.fits ? "" : ", over budget"}`)}">
      ${dishIcon(x.name, { image: photoOf(x), lg: true })}<b>${esc(x.name)}</b><span class="muted">${esc(x.restaurant)}</span>
      <span class="pr">≈${rupee0(x.price)}${x.fits ? "" : ` <i>over</i>`}</span></button>`).join("")}
    ${cook ? `<button class="qpick" data-quick-cook="${esc(cook.recipe_key)}" data-quick-name="${esc(cook.name)}" aria-label="${esc(`Cook ${cook.name} at home, about ${rupee0(cook.price)}`)}">
      ${dishIcon(cook.name, { cook: true, lg: true })}<b>${esc(cook.name)}</b><span class="muted">Cook at home</span><span class="pr">≈${rupee0(cook.price)}</span></button>` : ""}
    </div></div>`;
}
// The meal whose time it is: until 90 minutes after its planned time (10:30, 14:30, 22:00).
function mealNow() {
  const d = new Date(), m = d.getHours() * 60 + d.getMinutes();
  return m < 630 ? "breakfast" : m < 870 ? "lunch" : m < 1320 ? "dinner" : null;
}
function hungryCard(nu) {
  const meal = mealNow(), v = S.view, today = todayIso();
  if (!meal || !v.source || v.source.kind === "none" || S.offline) return "";
  if (nu && nu.date === today && nu.meal === meal) return "";
  const day = (v.grid || []).find(d => d.date === today);
  if (!day) return "";
  const cell = day.meals[meal];
  if (cell && cell.status !== "skipped" && cell.kind !== "skip") return "";     // planned, carted or had
  return `<section class="hungry rise2" aria-label="Hungry now">${mascot("", "")}<div class="t"><b>Hungry now?</b>
    <span class="muted">${cell ? `You skipped ${meal}` : `${cap1(meal)} isn't in your week`}. Get one in two taps.</span></div>
    <button class="secondary small" data-hungry="${meal}">Get ${meal}</button></section>`;
}
async function eatNow(meal) {
  const r = await api(`/api/plan/${S.planId}/eat-now`, "POST", { meal });
  adoptView(r.plan);
  const nu = S.view.next_up;
  if (!nu || nu.cell.session_id !== r.session_id) { await openSheet(r.session_id); return; }
  toast(`${cap1(meal)} is in. Add it to your cart or pick another below.`); render();
}

function laterList(nu) {
  if (!nu) return "";
  const day = S.view.grid[nu.day_index];
  const rest = MEALS.filter(m => day.meals[m] && MEALS.indexOf(m) > MEALS.indexOf(nu.meal));
  let rows = rest.map(m => mealRow(day.meals[m], m)), title = nu.when === "Today" ? "Later today" : `Later ${esc(nu.when.toLowerCase())}`;
  if (!rows.length && S.view.grid[nu.day_index + 1]) {
    const next = S.view.grid[nu.day_index + 1];
    rows = MEALS.filter(m => next.meals[m]).map(m => mealRow(next.meals[m], m));
    title = nu.when === "Today" ? "Tomorrow" : esc(next.day);
  }
  if (!rows.length) return "";
  return `<section class="card tight rise3" aria-label="${title}"><h3 class="k" style="padding:14px 0 2px">${title}</h3>${rows.join("")}</section>`;
}
function headsUp() {
  // Weather notes only when the forecast is real (Open-Meteo); the offline sample is not news.
  const hs = (S.view.heads_up || []).filter(h => h.kind !== "weather" || S.view.weather_source === "live");
  if (!hs.length) return "";
  return `<section class="stack rise3" aria-label="Heads-up">${hs.map(h => `<div class="note ${h.level === "warn" ? "warn" : ""}">
    <span class="ic" aria-hidden="true">${esc({ reconcile: "✓", budget: "₹", saving: "₹", holiday: "★", fast: "◐", weather: "☂", nutrition: "+" }[h.kind] || "•")}</span><div><b>${esc(h.title)}</b><p class="muted">${esc(h.body)}</p>
    ${h.kind === "reconcile" ? `<div class="acts"><button class="link" data-tab="week">Review past meals</button></div>` : ""}
    ${h.kind === "budget" ? `<div class="acts"><button class="link" data-go="more:settings">Adjust budget</button>${S.view.plan.mode !== "survival" && S.meta?.modes?.survival ? `<button class="link" data-mode="survival">Switch to ${esc(S.meta.modes.survival)}</button>` : ""}</div>` : ""}</div></div>`).join("")}</section>`;
}

/* Push reminders arrive at the order-by time even when Ziggy is closed
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
function remindersOn() {
  if (pushState() === "on") return true;
  return !hasPush() && typeof Notification !== "undefined" && store.get("smartplate.notify") === "1" && Notification.permission === "granted";
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
/* In-tab alerts: only while Ziggy is open; used where Web Push isn't available. */
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
  store.set("ziggy.asked-reminders", "1"); S.ask = false;
  if (hasPush()) {
    if (pushState() === "on") {
      const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription();
      if (sub) { await api(`/api/user/${S.userId}/push/unsubscribe`, "POST", { endpoint: sub.endpoint }); await sub.unsubscribe().catch(() => {}); }
      store.del("smartplate.push"); toast("Reminders off for this device"); render(); return;
    }
    if (await Notification.requestPermission() !== "granted") { toast("Notifications are blocked for this site. Add the calendar instead."); render(); return; }
    await subscribePush();
    await api(`/api/user/${S.userId}/push/test`, "POST", {}).catch(() => {});
    toast("Reminders on. You'll get a nudge at each order-by time."); render(); return;
  }
  if (store.get("smartplate.notify") === "1" && Notification.permission === "granted") {
    store.del("smartplate.notify"); alertTimers.splice(0).forEach(clearTimeout); toast("Browser alerts off"); render(); return;
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") { toast("Alerts are blocked in this browser. Add the calendar instead."); render(); return; }
  store.set("smartplate.notify", "1");
  const n = await scheduleAlerts();
  toast(n ? `${n} alert${n === 1 ? "" : "s"} set for the next 24 hours (while this tab is open)` : "Alerts on. Nothing due in the next 24 hours.");
  render();
}

/* ================================================================ WEEK */
// One meal, one row: tap it for the Change sheet. No per-row toggles.
function mealRow(m, meal) {
  const past = m.status === "past";
  const locked = ["ordered", "confirmed"].includes(m.status);
  const off = m.status === "skipped" || m.kind === "skipped" || m.kind === "skip";
  const food = ["delivery", "cook"].includes(m.kind);
  const target = S.moving && food && !locked && !past && S.moving !== m.session_id;
  const status = m.status === "confirmed" ? `<span class="tag good">had it</span>` : m.status === "ordered" ? `<span class="tag good">ordered</span>`
    : past && food ? `<span class="tag warn">did you have it?</span>` : "";
  const fee = m.kind === "delivery" && m.real_bill ? " · real Swiggy bill"
    : m.kind === "delivery" && m.delivery_fee ? ` · delivery ₹${Math.round(m.delivery_fee.amount)} ${m.delivery_fee.estimated ? "est." : "from your bill"}` : "";
  const sub = m.kind === "delivery" ? `${esc(m.restaurant)}${fee}${m.order?.order_at && !locked && !past ? ` · order by ${esc(m.order.order_at)}` : ""}`
    : m.kind === "cook" ? (m.recipe_key ? "Cook at home · grocery estimate" : "From your fridge") : esc((m.reasons || [])[0] || "Skipped");
  const name = off && !food ? "Skipped" : m.item;
  return `<button class="mrow ${off ? "off" : ""} ${target ? "target" : ""}" data-meal="${m.session_id}" aria-label="${esc(cap1(meal))}: ${esc(name)}${target ? ". Tap to swap here" : ""}">
    ${food ? dishIcon(m.item, { cook: m.kind === "cook", image: photoOf(m) }) : `<span class="dicon" style="--h:0" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M6 12h12"/></svg></span>`}
    <span class="t"><span class="lbl">${esc(cap1(meal))}</span><b>${esc(name)} ${m.pinned ? `<span class="tag" title="Kept: re-plans leave it">${ICON.pin}kept</span>` : ""}${m.usual === false && m.kind === "delivery" ? '<span class="tag hot">new</span>' : ""}${status}</b>
      <span class="s">${sub}</span></span>
    <span class="p"${m.cost_basis ? ` title="${esc(m.cost_basis)}"` : ""}>${food && m.cost ? cellMoney(m) : ""}</span></button>`;
}
// What the plan's restaurant dishes come from: live Swiggy menus, or the honest empty state.
function sourceLine(src) {
  if (!src) return "";
  if (src.kind === "live") return `<p class="fine">${esc(src.label)} · updated ${esc(fmtWhen(src.fetched))}. ${esc(src.note || "")} <button class="link" data-act="live-menus" style="min-height:0;font-size:inherit">Refresh</button></p>`;
  return realDishesState();
}
function weekScreen() {
  if (S.weekView === "groceries") return `<button class="back secondary" data-week-view="">${ICON.back}Week</button>${cookingPanel()}`;
  if (S.weekView === "orders") return `<button class="back secondary" data-week-view="">${ICON.back}Week</button>${ordersPanel()}`;
  const v = S.view, b = v.budget || {};
  const days = v.grid || [];
  const ti = days.findIndex(d => d.date === todayIso());
  const firstOpen = days.findIndex(d => Object.values(d.meals).some(m => m.status !== "past"));
  const sel = S.weekDay ?? (ti >= 0 ? ti : Math.max(0, firstOpen));
  const day = days[sel] || days[0];
  const ctx = (v.week_context || [])[sel] || {};
  const left = (b.budget || 0) - (b.spend || 0), pct = b.budget ? Math.min(100, (b.spend / b.budget) * 100) : 0;
  const cooks = (v.coach?.recipes || []).length;
  const cells = days.flatMap(d => Object.values(d.meals));
  const n = (k) => cells.filter(m => m.kind === k && m.status !== "past").length;
  const liveWx = v.weather_source === "live" && ctx.weather_source === "live" && ctx.temp_c;
  const fest = ctx.festival ? ` · ${esc(ctx.festival)}${ctx.festival_approx ? "*" : ""}` : "";
  return `<div class="row rise" style="justify-content:space-between;align-items:flex-end"><h1 class="h1">Week of ${esc(fmtDate(v.plan.week_start))}</h1>
      <button class="secondary small" data-act="reopt" title="Re-plan every meal that isn't kept, ordered or in your cart">Re-plan</button></div>
    <section class="card rise" aria-label="Budget">
      <div class="row" style="justify-content:space-between;align-items:baseline"><span><span class="money" style="font-size:26px">${est(b.spend || 0)}</span> <span class="muted">planned of ${rupee0(b.budget)}</span></span>
        <b style="color:${left < 0 ? "var(--bad)" : "var(--leaf-ink)"}">${left < 0 ? `${rupee0(-left)} over` : `${rupee0(left)} left`}</b></div>
      <div class="bar"><i class="${left < 0 ? "over" : ""}" style="width:${pct}%"></i></div>
      <span class="fine">${n("delivery")} to order · ${n("cook")} to cook${b.prorated ? ` · rest of the week only` : ""}${b.daily_cap ? ` · daily limit ${rupee0(b.daily_cap)}` : ""} · ${esc(v.plan.mode_label || "")}</span></section>
    ${sourceLine(v.source)}
    ${S.moving ? `<div class="note" role="status"><span class="ic">${ICON.swap}</span><span style="flex:1">Tap the meal to swap it with, on any day.</span><button class="small ghost" data-act="cancel-move">Cancel</button></div>` : ""}
    ${day ? `<nav class="days rise2" aria-label="Days">${days.map((d, i) => {
      const allPast = Object.values(d.meals).every(m => m.status === "past");
      const cx = (v.week_context || [])[i] || {};
      return `<button class="day ${allPast ? "past" : ""}" data-day="${i}" aria-pressed="${i === sel}" aria-label="${esc(d.day)} ${esc(fmtDate(d.date))}${cx.festival ? `, ${esc(cx.festival)}` : ""}"><span>${esc(d.day)}</span><b>${d.date ? Number(d.date.slice(8)) : i + 1}</b>${cx.festival || cx.your_fast ? "<i></i>" : ""}</button>`;
    }).join("")}</nav>
    <section class="card tight rise3" aria-label="${esc(day.day)}">
      <h3 class="k" style="padding:14px 0 2px">${esc(day.day)} ${esc(fmtDate(day.date))}${i18nToday(day)}${fest}${ctx.your_fast ? ` · ${esc(ctx.your_fast)} fast` : ""}${liveWx ? ` · ${esc(WX_ICON[ctx.weather] || "")} ${Math.round(ctx.temp_c)}°` : ""}</h3>
      ${MEALS.filter(m => day.meals[m]).map(m => mealRow(day.meals[m], m)).join("") || `<p class="fine" style="padding:12px 0">Nothing planned.</p>`}</section>` : `<p class="fine rise2">Nothing planned this week yet.</p>`}
    ${cooks ? `<button class="linkcard rise3" data-week-view="groceries"><span class="ico leaf">${ICON.bag}</span><span class="t"><b>Cooking &amp; groceries</b><span>${cooks} cook meal${cooks === 1 ? "" : "s"} · grocery list ${rupee(v.coach.basket.total)}</span></span>${ICON.right}</button>` : ""}
    ${swiggyReady() || (S.meta.swiggy_provider === "simulated" && S.meta.fixture_data) ? `<button class="linkcard rise3" data-week-view="orders"><span class="ico">${ICON.cart}</span><span class="t"><b>Order several meals</b><span>Pick meals and days, check each real cart</span></span>${ICON.right}</button>` : ""}
    <button class="ghost" data-act="newweek">Plan next week</button>`;
}
const i18nToday = (day) => day.date === todayIso() ? " · today" : "";

/* ---- the Change sheet: better fits, other places, cook, skip, keep ---- */
function pickBtn(x, place, d) {
  const left = d.limits.left_day != null ? Math.min(d.limits.left_week, d.limits.left_day) : d.limits.left_week;
  const cur = d.current?.item === x.name;
  // Every option shown fits unless it says otherwise: only a real reason earns a pill.
  const why = x.why && !/^fits( your budget)?$/i.test(x.why.trim()) ? x.why : "";
  const note = cur ? ["Current pick", ""] : !x.fits ? [`Over by ${rupee0(Math.max(0, x.price - left))}`, "warn"] : [why, "ok"];
  return `<button class="pick ${x.fits ? "" : "over"} ${cur ? "current" : ""}" data-pick="${x.item_id}" data-pick-name="${esc(x.name)}">
    ${dishIcon(x.name, { image: photoOf(x) })}<span class="t"><b>${esc(x.name)}</b><span>${esc(place || "")}${x.instructions?.length ? ` · will ask: “${esc(x.instructions.join("; "))}”` : ""}</span>
      ${note[0] ? `<span class="pillnote ${note[1]}">${esc(note[0])}</span>` : ""}</span><b class="pr">${rupee0(x.price)}</b></button>`;
}
function sheetDialog() {
  const d = S.sheet.data;
  if (!d) return `<div class="sheet-bg" data-ov="sheet" data-close-sheet="1"><section class="sheet" role="dialog" aria-modal="true" aria-label="Loading options" aria-busy="true"><span class="grab"></span><p class="fine">Loading your options…</p><div class="skeleton"><i></i><i></i><i></i></div></section></div>`;
  const s = d.session, L = d.limits;
  const left = L.left_day != null ? Math.min(L.left_week, L.left_day) : L.left_week;
  const all = [...d.usual.flatMap(g => g.dishes.map(x => ({ ...x, restaurant: g.restaurant }))), ...d.new];
  const fits = [...all].sort((a, b) => (b.fits - a.fits) || (a.current ? -1 : 0)).filter(x => !x.current).slice(0, 5);
  const places = [...new Set(all.map(x => x.restaurant))];
  const place = S.sheet.place && places.includes(S.sheet.place) ? S.sheet.place : places[0];
  const tab = S.sheet.tab || "fits";
  const liveFav = (S.liveFavourites || []).find(f => f.name === place);
  const hidden = [d.hidden.not_safe ? `${d.hidden.not_safe} not safe for your allergies or diet` : "",
    d.hidden.not_a_meal ? `${d.hidden.not_a_meal} treats hidden as standalone meals` : "",
    d.hidden.below_rating ? `${d.hidden.below_rating} below your ${Number(S.view.user.rating_floor).toFixed(1)}★ minimum` : "",
    d.hidden.not_again ? `${d.hidden.not_again} you said “not again” to` : ""].filter(Boolean).join(" · ");
  const cell = cellOf(s.id);
  const cook = d.cook[0];
  return `<div class="sheet-bg" data-ov="sheet" data-close-sheet="1"><section class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">
    <span class="grab" aria-hidden="true"></span>
    <div class="sheet-head"><div><p class="eyebrow">${esc(s.day)} ${esc(fmtDate(s.date))} · ${rupee0(left)} left${L.left_day != null ? " today" : " this week"}${(d.portions || 1) > 1 ? ` · prices for ${d.portions}` : ""}</p>
      <h2 class="h2" id="sheet-title">Change ${esc(s.meal)}</h2>${d.current ? `<p class="muted">Now: ${esc(d.current.item)}</p>` : ""}</div>
      <button class="icon-btn" data-close-sheet="1" aria-label="Close">${ICON.close}</button></div>
    ${d.timing_tip ? `<p class="fine">${esc(d.timing_tip)}</p>` : ""}
    <div class="seg" role="group" aria-label="Show"><button data-sheet-tab="fits" aria-pressed="${tab === "fits"}">Better fits</button><button data-sheet-tab="places" aria-pressed="${tab === "places"}">Other places</button>${d.cook.length ? `<button data-sheet-tab="cook" aria-pressed="${tab === "cook"}">Cook</button>` : ""}</div>
    ${tab === "fits" ? `${moreLikeBlock(d, s)}<div class="stack" style="gap:8px">${fits.map(x => pickBtn(x, x.restaurant, d)).join("") || `<p class="fine">Nothing else nearby fits your rules for this meal. Cook, or skip it.</p>`}</div>` : ""}
    ${tab === "places" ? `<div class="chips scroll" role="group" aria-label="Restaurants">${places.map(p => `<button class="chip" data-sheet-place="${esc(p)}" aria-pressed="${p === place}">${d.usual.some(g => g.restaurant === p) ? "♥ " : ""}${esc(p)}</button>`).join("")}</div>
      ${(() => { const g = d.usual.find(x => x.restaurant === place); return g ? `<p class="fine">${Number(g.rating).toFixed(1)}★ · about ${g.eta_min} min${g.more ? ` · ${g.more} more on their menu` : ""}</p>` : ""; })()}
      <div class="stack" style="gap:8px">${all.filter(x => x.restaurant === place).map(x => pickBtn(x, x.restaurant, d)).join("") || `<p class="fine">No places nearby fit your rules for this meal.</p>`}</div>
      ${liveFav && swiggyReady() ? `<button class="secondary" data-live-place="${esc(liveFav.id)}" data-live-name="${esc(liveFav.name)}">See ${esc(liveFav.name)}'s full menu</button>` : ""}` : ""}
    ${tab === "cook" ? `<div class="stack" style="gap:8px">${d.cook.map(c => `<button class="pick" data-cook="${esc(c.recipe_key)}">${dishIcon(c.name, { cook: true })}<span class="t"><b>${esc(c.name)}</b><span>Cook at home · on your grocery list</span></span><b class="pr">${rupee0(c.price)}</b></button>`).join("")}</div>` : ""}
    ${eatersBlock(s)}
    <div class="two">${cook && tab !== "cook" && cell?.kind !== "cook" ? `<button class="secondary" data-cook="${esc(cook.recipe_key)}">Cook instead · ${rupee0(cook.price)}</button>` : `<button class="secondary" data-choose-auto="${s.id}">Let Ziggy choose</button>`}
      <button class="secondary" data-sess="${s.id}:skipped">Skip this meal</button></div>
    <div class="row" style="justify-content:center;gap:4px 18px">${cell && ["delivery", "cook"].includes(cell.kind) ? `<button class="link" data-pin="${s.id}">${cell.pinned ? "Let Ziggy change it" : "Keep it, don't re-plan"}</button>` : ""}
      <button class="link" data-move="${s.id}">Swap with another meal</button></div>
    ${hidden ? `<p class="fine">Hidden: ${esc(hidden)}.</p>` : ""}
  </section></div>`;
}
function eatersBlock(s) {
  const h = S.view.household;
  if (!h) return "";
  const cell = cellOf(s.id);
  const on = new Set(cell?.eaters || []);
  return `<p class="k">Who's eating</p><div class="chips">${h.people.map(p => `<button class="chip" aria-pressed="${on.has(p.id)}" data-eater="${s.id}:${p.id}">${esc(p.name)}</button>`).join("")}</div>
    <p class="fine">One portion each: the cost, your Swiggy cart and the split follow who eats. Every meal stays safe for the whole household.</p>`;
}
// Today: who's eating this meal, one tap each. Portions, the cost and the cart follow.
function whoEats(c) {
  const h = S.view.household;
  if (!h || !c.session_id) return "";
  const on = new Set(c.eaters || []);
  return `<div class="who" role="group" aria-labelledby="who-${c.session_id}"><span class="k" id="who-${c.session_id}">Who's eating?</span>
    <div class="chips">${h.people.map(p => `<button class="chip" aria-pressed="${on.has(p.id)}" data-eater="${c.session_id}:${p.id}">${esc(p.you ? "You" : p.name)}</button>`).join("")}</div></div>`;
}
const INGREDIENTS_RULE = { id: "ingredients", whose: "ziggy", title: "Swiggy menus don't list ingredients",
  plain: "With an allergy, a medical rule or a vegan diet, Ziggy can't confirm a dish is safe from the menu alone, so it won't fill the cart.",
  fix: "Check the dish with the restaurant and order it in Swiggy." };
async function toggleEater(sid, pid) {
  const cell = cellOf(sid);
  const cur = new Set(cell?.eaters || []);
  if (cur.has(pid)) cur.delete(pid); else cur.add(pid);
  if (!cur.size) { toast("At least one person eats each meal. Skip the meal instead."); return; }
  adoptView(await api(`/api/session/${sid}/eaters`, "POST", { eaters: [...cur] }));
  if (S.carts?.[sid]) { delete S.carts[sid]; toast(`Now for ${cur.size}. Your Swiggy cart was for a different number: clear it in Swiggy, then add again.`); }
  render();
}
function moreLikeBlock(d, s) {
  if (!epicureOn() || d.current?.kind !== "delivery") return "";
  const sim = S.sheet.similar;
  if (!sim) return `<button class="secondary small" data-more-like="${s.id}">More like ${esc(d.current.item)}</button>`;
  return `<p class="k">Like ${esc(sim.dish)}</p>${sim.items.length ? sim.items.map(x => `<button class="pick" data-pick="${x.item_id}" data-pick-name="${esc(x.name)}">
      ${dishIcon(x.name)}<span class="t"><b>${esc(x.name)}</b><span>${esc(x.restaurant)}</span><span class="pillnote ok">similar flavours</span></span><b class="pr">${rupee0(x.price)}</b></button>`).join("")
    : `<p class="fine">No other dish nearby that fits your rules is close enough in flavour.</p>`}`;
}

/* ---- groceries and the order list ---- */
function cookingPanel() {
  const c = S.view.coach;
  const ing = (i) => i.swap
    ? `<span class="chip on" style="cursor:default">${esc(i.swap.name)} <small>for ${esc(i.name)}</small> <button class="x" data-unswap="${esc(i.token)}" aria-label="Use ${esc(i.name)} again">${ICON.close}</button></span>`
    : (i.swappable ? `<button class="chip" data-swap-ing="${esc(i.token)}" title="Swap ${esc(i.name)}">${esc(i.name)} ⇄</button>` : `<span class="chip" style="cursor:default">${esc(i.name)}</span>`);
  const recipes = c.recipes.map(r => `<div class="card"><p class="k">${esc(r.session)} · ${rupee(r.cost)}${r.servings > 1 ? ` a serving · ${r.servings} eating` : ""}</p>
    <h3 class="h3">${esc(r.name)}</h3>
    ${r.ingredients?.length ? `<div class="chips">${r.ingredients.map(ing).join("")}</div>` : ""}
    <ol style="margin-left:18px;display:grid;gap:4px">${r.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol></div>`).join("") || `<div class="empty">No cook days this week. Set how often you cook in Me → Food &amp; budget.</div>`;
  const unit = (b) => b.unit === "g" ? (b.need >= 1000 ? `${(b.need / 1000).toFixed(1).replace(/\.0$/, "")} kg` : `${b.need} g`) : `${b.need} ${b.unit}`;
  const label = (b) => b.swap ? `<s>${esc(b.name)}</s> → <b>${esc(b.swap.name)}</b>${b.swap.reason === "out_of_stock" ? " <small>(out of stock)</small>" : ""}`
    : (b.have ? `<s>${esc(b.name)}</s>` : esc(b.name));
  const basket = c.basket.items.map(b => `<div class="mrow" style="cursor:default"><span class="t"><b style="font-weight:600">${b.qty > 1 ? `${b.qty} × ` : ""}${label(b)}</b>
      <span class="s">${esc(b.recipe)} · needs ${esc(unit(b))} for ${b.servings} serving${b.servings === 1 ? "" : "s"}</span></span>
    <span class="stack" style="gap:2px;align-items:flex-end"><span class="p">${b.have ? "—" : rupee(b.price)}</span>
      <label class="check" style="min-height:32px"><input type="checkbox" data-have="${esc(b.name)}" ${b.have ? "checked" : ""}> Have it</label>
      ${b.have ? "" : `<a class="small btn ghost" href="${esc(instamartUrl(b.swap ? b.swap.name : b.name))}" target="_blank" rel="noopener" aria-label="Find ${esc(b.swap ? b.swap.name : b.name)} on Instamart">Instamart ${ICON.out}</a>`}
      ${b.swap ? `<button class="small ghost" data-unswap="${esc(b.token)}">Undo</button>`
        : (b.swappable && !b.have ? `<button class="small ghost" data-oos="${esc(b.token)}">Out of stock?</button>` : "")}</span></div>`).join("");
  const swapped = c.basket.items.some(b => b.swap);
  const safe = (c.safe_with_swap || []).map(f => `<li><b>${esc(f.name)}</b>: ${f.swaps.map(x => `use ${esc(x.to_name)} instead of ${esc(x.from_name)}`).join(", ")}</li>`).join("");
  return `<h1 class="h1">Cooking &amp; groceries</h1><p class="sub">${esc(c.headline)}</p>
    ${S.swapPick ? swapPicker() : ""}
    <section class="card tight"><h3 class="k" style="padding:14px 0 2px">Grocery list · ${rupee(c.basket.total)}</h3>
      ${basket || `<p class="fine" style="padding:12px 0">Nothing left to buy for this week's cooking.</p>`}
      ${c.basket.items.some(b => !b.have) ? `<div class="row" style="padding-top:10px"><button class="small secondary" data-act="copy-groceries">Copy list</button>
        <a class="small btn secondary" href="https://www.swiggy.com/instamart" target="_blank" rel="noopener">Open Instamart ${ICON.out}</a></div>` : ""}
      <p class="fine" style="padding:8px 0 14px">For the cook meals still ahead${S.view.household ? ", for everyone eating each one" : ""}, rounded up to whole packs. Tick what you already have.${swapped ? " Prices are for the original items." : ""}</p></section>
    ${recipes}
    ${safe ? `<div class="card"><p class="k">Also safe with a swap</p><ul class="fine" style="margin-left:18px">${safe}</ul>
      <p class="fine">These recipes are left out of your plan as written. With these swaps nobody's allergies or diet are broken.</p></div>` : ""}`;
}
// Instamart hand-off: Swiggy's public Instamart search for one grocery line (pack size left
// out so it matches any brand). Ziggy doesn't fill an Instamart cart.
function instamartUrl(name) {
  const q = String(name || "").replace(/\s*\(\d+\)\s*$/, "").replace(/\s*\d+(\.\d+)?\s*(g|kg|ml|l)\b/gi, "").trim();
  return `https://www.swiggy.com/instamart/search?custom_back=true&query=${encodeURIComponent(q)}`;
}
function groceryText() {
  const items = (S.view.coach?.basket?.items || []).filter(b => !b.have);
  return items.map(b => `- ${b.qty > 1 ? `${b.qty} × ` : ""}${b.swap ? b.swap.name : b.name}`).join("\n");
}
function swapPicker() {
  const p = S.swapPick, d = p.data;
  const what = d?.name || p.token.replace(/_/g, " ");
  const head = p.reason === "out_of_stock" ? `${esc(what)} is out of stock. Use instead:` : `Instead of ${esc(what)}, use:`;
  if (!d) return `<div class="card"><p class="fine">Finding swaps…</p></div>`;
  return `<div class="card swap-pick" role="group" aria-label="Swap ${esc(what)}"><p class="k">${head}</p>
    ${d.options.length ? `<div class="chips">${d.options.map(o => `<button class="chip" data-swap-to="${esc(o.token)}">${esc(o.name)}</button>`).join("")}</div>`
      : `<p class="fine">No swap that does the same job is safe for everyone eating.</p>`}
    <p class="fine">Only ingredients that fit everyone's allergies and diet are shown${d.hidden_unsafe ? ` (${d.hidden_unsafe} left out)` : ""}. Closest in flavour first.</p>
    <button class="ghost small" data-act="swap-cancel">Cancel</button></div>`;
}
/* ---- order any meal / the whole week: pick slots and days, then cart check → approve → place ---- */
function orderQueuePanel() {
  const q = S.orderQueue;
  if (!q) return `<div class="skeleton"><i></i><i></i></div>`;
  const slots = MEALS.filter(meal => q.meals.some(m => m.meal === meal));
  const pickedMeals = slots.filter(meal => q.meals.some(m => m.queued && m.meal === meal));
  const pickedDays = [...new Set(q.meals.filter(m => m.queued).map(m => m.day_index))];
  const step = m => m.state === "placed" ? `<span class="tag good">placed</span>`
    : m.state === "handed_off" ? `<span class="tag good">in Swiggy</span>`
    : m.state === "cart_ready" && S.cartBudget[m.session_id]?.over ? `<b>${rupee(m.to_pay)}</b>${budgetWarn(m.session_id, S.cartBudget[m.session_id])}`
    : m.state === "cart_ready" ? `<span class="row"><b>${rupee(m.to_pay)}</b>
        <button class="small primary" data-oq-place="${m.session_id}">${q.order_enabled ? `Approve ${rupee(m.to_pay)} and place` : "Cart ready, tap to place in Swiggy"}</button></span>`
    : m.queued ? `<button class="small" data-oq-cart="${m.session_id}">Check the real cart</button>` : "";
  const rows = q.meals.filter(m => m.queued).map(m => `<div class="card" style="gap:8px"><div><b>${DAY3[m.day_index]} ${esc(m.meal)}</b> · ${esc(m.item)}
      <p class="fine">${esc(m.restaurant || "")} · planned ${rupee0(m.planned_cost)}${m.order_at ? ` · order by ${esc(m.order_at)}` : ""}</p></div>
      ${step(m)}${m.bill && m.state === "cart_ready" ? billLines(m.bill) : ""}</div>`).join("");
  return `<h1 class="h1">Order several meals</h1>
    <p class="sub">Pick the meals and days. Each one is checked in your real Swiggy cart (with delivery, fees and GST) and needs your OK on that exact total.</p>
    <section class="card"><p class="k">Meals</p><div class="chips">${slots.map(meal => `<label class="check chip"><input type="checkbox" name="oq-meal" value="${meal}" ${pickedMeals.includes(meal) ? "checked" : ""}> ${cap1(meal)}</label>`).join("")}</div>
    <p class="k">Days</p><div class="chips">${DAY3.map((d, i) => `<label class="check chip"><input type="checkbox" name="oq-day" value="${i}" ${pickedDays.includes(i) ? "checked" : ""}> ${d}</label>`).join("")}</div>
    <button class="secondary" data-act="oq-save">Add to my order list</button></section>
    ${rows}${rows ? `<p class="fine">${q.queued} meal${q.queued === 1 ? "" : "s"} · planned ${rupee0(q.planned_total)}${q.confirmed_total ? ` · checked in Swiggy ${rupee(q.confirmed_total)}` : ""}</p>` : ""}
    <p class="fine">${esc(q.scheduling.why)}</p>`;
}
function ordersPanel() { return orderQueuePanel() + ordersSimulation(); }
function ordersSimulation() {
  if (S.meta.swiggy_provider !== "simulated" || !S.meta.fixture_data) return "";
  const head = `<h2 class="h2">Auto-ordering (simulation)</h2>
    <p class="sub">The future "Ziggy orders for me" mode. You approve a maximum total; an unavailable dish is replaced only with one that passes your filters and costs no more. No restaurant receives these orders.</p>
    <button class="secondary" data-act="exec">Review simulated orders</button>`;
  if (!S.exec?.attempted) return head + `<p class="fine">No simulated orders yet.</p>`;
  const e = S.exec;
  const rows = e.results.map(r => `<tr><td>${DAY3[r.day]} ${r.meal}</td>
    <td>${esc(r.item)}<br><span class="fine">${esc(r.idempotency_key)}</span></td>
    <td>${r.substituted ? `<span class="tag warn">substituted</span>` : (r.placed ? "simulated" : "not ordered")}</td>
    <td>${esc(r.provider_order_id || "—")}</td><td>${esc(r.state)}</td></tr>`).join("");
  return head + `<div class="stats card"><div class="stat"><span>Placed</span><b>${e.placed}/${e.attempted}</b></div>
      <div class="stat"><span>Substituted</span><b>${e.substituted}</b></div><div class="stat"><span>Failed</span><b>${e.failed}</b></div></div>
    <div class="card scroll-x"><table><tr><th>meal</th><th>item / idempotency key</th><th>outcome</th><th>order id</th><th>state</th></tr>${rows}</table></div>`;
}
/* Explicit checkout hand-off: planning is reversible, ordering is not. */
function orderReviewDialog() {
  const review = S.orderReview;
  const rows = review.items;
  return `<div class="sheet-bg" data-ov="review" data-close-review="1">
    <section class="sheet" role="dialog" aria-modal="true" aria-labelledby="checkout-title"><span class="grab"></span>
      <div class="sheet-head"><div><p class="eyebrow">Final review · ${rows.length} deliveries</p>
      <h2 class="h2" id="checkout-title">Approve before anything is ordered</h2></div><button class="icon-btn" data-close-review="1" aria-label="Close order review">${ICON.close}</button></div>
      <p class="sub">This simulation runs now. It does not schedule or send real deliveries.</p>
      <div class="card tight">${rows.map(m => `<div class="mrow" style="cursor:default"><span class="t"><b>${esc(S.view.grid[m.day]?.day || "Scheduled")} · ${esc(m.meal)}</b><span class="s">${esc(m.item)} · ${esc(m.restaurant || "Restaurant pending")}</span></span><b>${rupee(m.amount)}</b></div>`).join("")}</div>
      <div class="row" style="justify-content:space-between"><span>Maximum approved total</span><b class="money" style="font-size:22px">${rupee(review.total)}</b></div>
      <p class="fine">A replacement must pass your filters and cost no more than the reviewed price for that meal. Otherwise it is left unordered.</p>
      ${S.meta.swiggy_provider === "simulated" ? `<div class="note warn"><span class="ic">!</span><span><b>Demo mode:</b> confirming creates simulated orders only. No payment or restaurant order happens.</span></div>` : ""}
      <button class="primary" data-act="confirm-exec" autofocus>Simulate ${rows.length} orders · ${rupee(review.total)}</button>
      <button class="ghost" data-close-review="1">Keep editing</button>
    </section></div>`;
}

/* ================================================================ SAVED */
async function loadSaved() {
  const jobs = [api(`/api/user/${S.userId}/saved`).then(r => { S.saved = r; })];
  if (swiggyReady() && !S.liveFavourites) jobs.push(api(`/api/user/${S.userId}/swiggy/favourites`).then(r => { S.liveFavourites = r; }));
  if (!swiggyReady() && !S.places) jobs.push(api(`/api/restaurants?user_id=${S.userId}`).then(r => { S.places = r; }));
  await Promise.all(jobs);
}
function savedScreen() {
  const tab = S.savedTab || "places";
  const live = swiggyReady();
  return `<h1 class="h1 rise">Saved</h1>
    ${live ? `<form id="live-search" class="rise" role="search"><label class="sr" for="live-query">Search Swiggy near ${esc(S.swiggy.address.label)}</label>
      <div class="row" style="flex-wrap:nowrap"><input id="live-query" type="search" enterkeyhint="search" placeholder="Search restaurants on Swiggy" maxlength="80" required><button class="secondary" aria-label="Search">${ICON.search}</button></div></form>` : ""}
    ${S.liveResults ? liveResultsBlock() : ""}
    <div class="seg rise2" role="group" aria-label="Show"><button data-saved-tab="places" aria-pressed="${tab === "places"}">Places</button><button data-saved-tab="dishes" aria-pressed="${tab === "dishes"}">Dishes</button><button data-saved-tab="recent" aria-pressed="${tab === "recent"}">Recent</button></div>
    ${tab === "places" ? savedPlaces() : tab === "dishes" ? savedDishes() : savedRecent()}`;
}
function placeRow(p, { starred, live }) {
  const letter = esc(String(p.name || "?").replace(/^the\s+/i, "").charAt(0).toUpperCase());
  const meta = live ? [p.area, p.rating ? `${p.rating}★` : "", p.eta].filter(Boolean).map(String).map(esc).join(" · ")
    : [p.rating ? `${Number(p.rating).toFixed(1)}★` : "", p.eta_min ? `${p.eta_min} min` : "", p.dishes_fit != null ? `${p.dishes_fit} of ${p.dishes_total} fit you` : ""].filter(Boolean).join(" · ");
  const star = live ? `data-live-fav="${esc(p.id)}" data-live-name="${esc(p.name)}"` : `data-fav="${p.id}"`;
  return `<div class="mrow" style="cursor:default"><span class="ico lilac" aria-hidden="true" style="border-radius:50%;font-weight:800">${letter}</span>
    <span class="t"><b>${esc(p.name)}</b><span class="s">${meta || (live ? "Saved on Swiggy" : "")}</span></span>
    ${live ? `<button class="small secondary" data-live-place="${esc(p.id)}" data-live-name="${esc(p.name)}">Menu</button>` : ""}
    <button class="heart" ${star} aria-pressed="${starred}" aria-label="${starred ? "Remove" : "Save"} ${esc(p.name)}">${ICON.heart}</button></div>`;
}
function savedPlaces() {
  if (swiggyReady()) {
    const fav = S.liveFavourites;
    if (!fav) return `<div class="skeleton"><i></i><i></i><i></i></div>`;
    return `<section class="card tight rise3">${fav.map(p => placeRow(p, { starred: true, live: true })).join("") || `<p class="fine" style="padding:16px 0">Nothing saved yet. Search Swiggy above and tap ♥ on places you like: Ziggy plans from them.</p>`}</section>
      <p class="fine">Ziggy plans your meals from these places. ${fav.length ? `<button class="link" data-act="live-menus" style="min-height:0;font-size:inherit">Re-plan from them now</button>` : ""}</p>`;
  }
  const P = S.places;
  if (!P) return `<div class="skeleton"><i></i><i></i><i></i></div>`;
  const favs = P.filter(p => p.favourite), rest = P.filter(p => !p.favourite);
  const connect = S.swiggy && !S.swiggy.connected && swiggySignInOpen()
    ? `<div class="note"><span class="ic">${ICON.place}</span><span style="flex:1">Connect Swiggy to search and save real places near you.</span><button class="small" data-act="connect-open">Connect</button></div>` : "";
  if (!P.length) return connect + `<p class="fine">No places yet.</p>`;
  return `${connect}<section class="card tight rise3">${favs.map(p => placeRow(p, { starred: true, live: false })).join("") || `<p class="fine" style="padding:16px 0">No saved places yet. Tap ♥ on places you like below.</p>`}</section>
    ${rest.length ? `<p class="k">Nearby</p><section class="card tight">${rest.map(p => placeRow(p, { starred: false, live: false })).join("")}</section>` : ""}
    <p class="fine">Plans come mostly from saved places, plus a few new ones as your variety setting allows.</p>`;
}
// A saved dish or a past meal, with the fastest way to have it again.
function againButton(x) {
  if (x.kind !== "delivery" || !swiggyReady()) return "";
  const fav = (S.liveFavourites || []).find(f => f.name === x.restaurant);
  return fav ? `<button class="small secondary" data-live-place="${esc(fav.id)}" data-live-name="${esc(fav.name)}" data-live-dish="${esc(x.name)}">Order again</button>` : "";
}
function savedDishes() {
  const s = S.saved;
  if (!s) return `<div class="skeleton"><i></i><i></i><i></i></div>`;
  if (!s.liked.length) return `<section class="empty rise3">${mascot()}<h2 class="h2">Dishes you rate Good show up here</h2><p class="sub">After a meal, tap Good on Today. Ziggy plans more like it.</p></section>`;
  return `<section class="card tight rise3">${s.liked.map(x => `<div class="mrow" style="cursor:default">${dishIcon(x.name, { cook: x.kind === "cook" })}
      <span class="t"><b>${esc(x.name)}</b><span class="s">${x.kind === "cook" ? "Cook at home" : esc(x.restaurant || "")} · ${est(x.price)}</span></span>${againButton(x)}</div>`).join("")}</section>`;
}
function savedRecent() {
  const s = S.saved;
  if (!s) return `<div class="skeleton"><i></i><i></i><i></i></div>`;
  const history = swiggyReady() ? `<button class="ghost small" data-act="live-order-history">Recent Swiggy orders</button>${S.liveOrderHistory ? orderHistoryCard() : ""}` : "";
  if (!s.recent.length) return `<section class="empty rise3"><h2 class="h2">Nothing eaten yet</h2><p class="sub">Meals you mark as had or ordered appear here, newest first.</p></section>${history}`;
  return `<section class="card tight rise3">${s.recent.map(x => `<div class="mrow" style="cursor:default">${dishIcon(x.name, { cook: x.kind === "cook" })}
      <span class="t"><b>${esc(x.name)}</b><span class="s">${esc(DAY3[(new Date(x.date + "T00:00:00").getDay() + 6) % 7])} ${esc(x.meal)} · ${esc(fmtDate(x.date))}${x.rated === 1 ? " · rated Good" : x.rated === -1 ? " · not again" : ""}</span></span>${x.rated === -1 ? "" : againButton(x)}</div>`).join("")}</section>${history}`;
}
function orderHistoryCard() {
  const h = S.liveOrderHistory;
  return `<section class="card"><p class="k">Swiggy orders · ${esc(h.address)}</p>
    ${h.attempts.some(a => a.state === "unknown" || a.state === "started") ? `<p class="fine">A Ziggy order attempt has an uncertain result. Check Swiggy before ordering the same cart again.</p>
      <button class="small" data-act="resolve-live-attempt">I checked Swiggy: no order was placed</button>` : ""}
    ${h.provider_orders.length ? h.provider_orders.map(o => `<div><b>${esc(o.restaurant)}</b> · ${esc(o.item)} · ${esc(o.status)} · ${esc(o.total)} · ${esc(o.ordered_time)}
      <p class="fine">Order ${esc(o.order_id)}${h.attempts.some(a => a.order_id === o.order_id) ? ` <button class="small" data-live-track="${esc(o.order_id)}">Check delivery status</button>` : ""}</p></div>`).join("") : `<p class="fine">Swiggy returned no recent orders for this address.</p>`}</section>
    ${S.liveOrderStatus ? trackingCard() : ""}`;
}
// Swiggy doesn't document the unit of a bare menu price; the server reads either form and
// marks inferred ones. A menu price is an estimate until Swiggy's cart prices the order.
function livePrice(price) { return price == null ? "Price in cart" : est(price, true); }
function etaMinutes(eta) { const m = /\d+/.exec(String(eta ?? "")); return m ? Number(m[0]) : null; }
const FAST_MIN = 30;
function filterChip(key, label, extra = "") {
  return `<button class="chip" data-filter="${key}" aria-pressed="${!!S.filters[key]}" ${extra}>${label}</button>`;
}
function liveResultsBlock() {
  const results = S.liveResults.restaurants || [];
  const starred = new Set((S.liveFavourites || []).map(x => x.id));
  const fastOk = (r) => !S.filters.fast || (etaMinutes(r.eta) != null && etaMinutes(r.eta) <= FAST_MIN);
  const shown = results.filter(fastOk);
  const anyEta = results.some(r => etaMinutes(r.eta) != null);
  return `<section class="stack" aria-label="Swiggy results"><div class="row" style="justify-content:space-between"><p class="k">Swiggy results for ${esc(S.liveResults.query)}</p>
      <button class="small ghost" data-act="clear-results">Clear</button></div>
    ${anyEta ? `<div class="chips">${filterChip("fast", `Fast (≤${FAST_MIN} min)`)}</div>` : ""}
    <div class="card tight">${shown.length ? shown.map(r => placeRow(r, { starred: starred.has(r.id), live: true })).join("")
      : results.length ? `<p class="fine" style="padding:14px 0">None of these say they deliver within ${FAST_MIN} minutes. Turn off “Fast” to see all ${results.length}.</p>` : `<p class="fine" style="padding:14px 0">No live restaurants returned for this address and search.</p>`}</div></section>`;
}
// A restaurant's live menu: Swiggy's own categories, veg marks, bestsellers, real photos when
// Swiggy sends them, and allergen flags read from dish names (Swiggy menus list none).
function menuSheet() {
  if (S.liveLoading || !S.liveBrowseMenu) return `<div class="sheet-bg" data-ov="menu" data-close-menu="1"><section class="sheet" role="dialog" aria-modal="true" aria-busy="true" aria-label="Opening menu"><span class="grab"></span>
    <p class="eyebrow">Opening ${esc(S.liveLoading || "menu")}…</p><div class="skeleton"><i></i><i></i><i></i></div></section></div>`;
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
      <div class="t"><b>${i.veg === true ? '<span class="vegmark" role="img" aria-label="Veg"></span>' : i.veg === false ? '<span class="vegmark nonveg" role="img" aria-label="Non-veg"></span>' : ""}${esc(i.name)}</b>
        <span>${livePrice(i.price, i.price_estimated)}</span>
        <span class="row" style="gap:4px">${i.bestseller ? '<span class="tag hot">Bestseller</span>' : ""}${out ? '<span class="tag">unavailable</span>' : ""}${i.has_options ? '<span class="tag">options in Swiggy</span>' : ""}${fl.map(a => `<span class="tag warn">Name suggests ${esc(a.replace("_", " "))}</span>`).join("")}</span></div>
      <button class="small secondary" data-live-item="${esc(i.id)}" data-live-item-name="${esc(i.name)}" ${out ? "disabled" : ""}>Add</button></div>`;
  };
  const hiddenByFilter = menu.items.length - items.length;
  const starred = (S.liveFavourites || []).some(x => x.id === menu.restaurant.id);
  return `<div class="sheet-bg" data-ov="menu" data-close-menu="1"><section class="sheet livemenu" role="dialog" aria-modal="true" aria-labelledby="menu-title"><span class="grab"></span>
    <div class="sheet-head"><div><p class="eyebrow">Swiggy menu · ${esc(menu.address)}</p><h2 class="h2" id="menu-title">${esc(menu.restaurant.name)}</h2>
      <p class="muted">${menu.search ? `${menu.items.length} dishes matching “${esc(menu.search.query)}”` : `${menu.items.length} current dishes`}</p></div>
      <div class="row" style="flex-wrap:nowrap"><button class="heart" data-live-fav="${esc(menu.restaurant.id)}" data-live-name="${esc(menu.restaurant.name)}" aria-pressed="${starred}" aria-label="${starred ? "Remove" : "Save"} ${esc(menu.restaurant.name)}">${ICON.heart}</button>
      <button class="icon-btn" data-act="close-live-browse" aria-label="Close menu">${ICON.close}</button></div></div>
    <form id="dish-search" class="row" style="flex-wrap:nowrap" role="search"><label class="sr" for="dish-query">Search this restaurant</label><input id="dish-query" type="search" value="${esc(menu.search?.query || "")}" placeholder="Search this restaurant" required minlength="2" maxlength="80"><button class="secondary" aria-label="Find dishes">${ICON.search}</button></form>
    ${menu.search ? `<button class="ghost small" data-act="restore-live-menu">Back to the full menu</button>` : ""}
    <div class="chips scroll" role="group" aria-label="Quick filters">${vegUser ? "" : filterChip("veg", "Veg only")}${filterChip("stock", "In stock")}${left > 0 ? filterChip("budget", `Within ${rupee0(left)} left`, `title="Your plan's estimated money left this week"`) : ""}${mine.size ? filterChip("flagged", "Hide name-flagged") : ""}</div>
    ${cats.length > 1 && !menu.search ? `<div class="chips scroll" role="group" aria-label="Menu categories">
      <button class="chip" data-cat="" aria-pressed="${!cat}">All</button>${menu.items.some(i => i.bestseller) ? `<button class="chip" data-cat="Recommended" aria-pressed="${cat === "Recommended"}">Bestsellers</button>` : ""}${cats.map(c => `<button class="chip" data-cat="${esc(c)}" aria-pressed="${cat === c}">${esc(c)}</button>`).join("")}</div>` : ""}
    <div class="lmlist">${groups.length ? groups.map(g => `${groups.length > 1 || g.name !== "Dishes" ? `<p class="lmcat">${esc(g.name)}</p>` : ""}${g.items.map(row).join("")}`).join("")
      : `<p class="fine">No dishes returned for this restaurant and search${hiddenByFilter ? " with these filters" : ""}.</p>`}</div>
    ${menu.search?.has_more ? `<button class="secondary" data-act="more-live-dishes">Load more matching dishes</button>` : ""}
    <p class="fine">Checked ${esc(fmtWhen(menu.fetched))}. ${menu.truncated ? "Swiggy's menu browse returns at most 150 dishes and this restaurant has more: search to find the rest. " : ""}${menu.hidden_nonveg ? `${menu.hidden_nonveg} marked non-veg dishes hidden. ` : ""}${hiddenByFilter ? `${hiddenByFilter} hidden by your filters. ` : ""}Swiggy menus don't list allergens: flags come from the dish name only, so no flag doesn't mean safe. Final price, fees, options and availability may change.</p>
  </section></div>`;
}
function liveOrderReviewDialog() {
  const r = S.liveOrderReview;
  return `<div class="sheet-bg" data-ov="live-review" data-close-live-review="1"><section class="sheet" role="dialog" aria-modal="true" aria-labelledby="live-review-title"><span class="grab"></span>
    <div class="sheet-head"><div><p class="eyebrow">Fresh from Swiggy</p><h2 class="h2" id="live-review-title">${r.orderable === false ? "Check this dish" : "Add this to your cart?"}</h2></div>
      <button class="icon-btn" data-close-live-review="1" aria-label="Close live item review">${ICON.close}</button></div>
    <div class="dish-row">${dishIcon(r.item, { image: r.image, lg: true })}<div class="t"><b class="h3">${esc(r.item)}</b><span class="muted">${esc(r.restaurant)} · to ${esc(r.address)}</span></div>
      <b>${r.menu_price == null ? "Price in cart" : livePrice(r.menu_price)}</b></div>
    <p class="fine">Swiggy shows the exact total, with fees and taxes, once it's in your cart.</p>
    ${r.orderable === false ? `<div class="fine" role="note">${esc(r.reason)}${ruleWhy(r.rule)}</div>
      <a class="btn primary" href="${esc(r.handoff_url)}" target="_blank" rel="noopener">Open in Swiggy ${ICON.out}</a>
      <button class="ghost" data-close-live-review="1">Close</button>` : `
      <p class="fine">Ziggy cannot verify all ingredients or cross-contact. Check the details before placing an order.</p>
      <button class="primary" data-act="confirm-live-cart" autofocus ${r.adding ? 'disabled aria-busy="true"' : ""}>${r.adding ? `${roller()}<span>Adding…</span>` : "Add to Swiggy cart"}</button>
      <button class="ghost" data-close-live-review="1" ${r.adding ? "disabled" : ""}>Cancel</button>`}
  </section></div>`;
}
function checkoutReviewDialog() {
  const r = S.checkoutReview;
  return `<div class="sheet-bg" data-ov="checkout" data-close-checkout-review="1"><section class="sheet" role="dialog" aria-modal="true" aria-labelledby="checkout-review-title"><span class="grab"></span>
    <div class="sheet-head"><div><p class="eyebrow">Real Swiggy order</p><h2 class="h2" id="checkout-review-title">Place this order now?</h2></div>
      <button class="icon-btn" data-close-checkout-review="1" aria-label="Close order review">${ICON.close}</button></div>
    <div class="card tight"><div class="mrow" style="cursor:default"><span class="t"><b>${esc(r.item)}</b><span class="s">${esc(r.restaurant)} · Quantity ${esc(r.quantity)} · ${esc(r.address)}</span></span><b>${real(r.to_pay)}</b></div></div>
    <p class="fine">Payment: ${esc(r.payment_label)}. This places a real order to the address shown and you may owe the full amount. Check ingredients with the restaurant if needed. An uncertain result will not be retried automatically.</p>
    <button class="primary" data-act="place-live-order" autofocus>Confirm and place order · ${rupee(r.to_pay)}</button>
    <button class="ghost" data-close-checkout-review="1">Cancel</button>
  </section></div>`;
}

/* ---- Connect Swiggy and Deliver to: the two quick sheets ---- */
function connectSheet() {
  const sw = S.swiggy || {};
  const resume = S.pendingResume ? " Then Ziggy finishes what you started." : "";
  let body;
  if (sw.requires_private_profile) body = `<h2 class="h2">Use your own private profile</h2><p class="sub">Sample profiles are shared by every visitor. Create or sign in to your own profile to keep your Swiggy account and addresses private.</p>
    <button class="primary" data-act="start-onboard">Create my profile</button><button class="ghost" data-act="signin-open">Sign in</button>`;
  else if (!swiggySignInOpen()) body = `<h2 class="h2">Swiggy connection is coming</h2><p class="sub">Swiggy hasn't approved connecting from this server yet, so sign-in would fail here. Until then, “Order on Swiggy” opens the dish in Swiggy.</p>
    ${sw.callback_url ? `<p class="fine">For this deployment, request <code>${esc(sw.callback_url)}</code> from Swiggy Builders Club.</p>` : ""}<button class="secondary" data-close-connect="1">OK</button>`;
  else body = `<h2 class="h2">${sw.needs_reconnect || sw.expired ? "Reconnect Swiggy" : "Connect Swiggy"}</h2>
    <p class="sub">${sw.needs_reconnect ? "This server can no longer read the saved Swiggy sign-in. " : sw.expired ? "Swiggy sign-ins last about 5 days. " : ""}Sign in on Swiggy's own page with your phone and OTP, about 20 seconds.${resume}</p>
    <div class="stack" style="gap:10px"><div class="row" style="flex-wrap:nowrap"><span class="tick n">1</span><span>Sign in on Swiggy</span></div><div class="row" style="flex-wrap:nowrap"><span class="tick n">2</span><span>Tap the address you order to</span></div><div class="row" style="flex-wrap:nowrap"><span class="tick n">3</span><span>Ziggy never orders or pays without you</span></div></div>
    <button class="primary" data-act="swiggy-connect">Continue with Swiggy</button><button class="ghost" data-close-connect="1">Not now</button>`;
  return `<div class="sheet-bg" data-ov="connect" data-close-connect="1"><section class="sheet" role="dialog" aria-modal="true" aria-label="Connect Swiggy"><span class="grab" aria-hidden="true"></span>${body}</section></div>`;
}
function addressSheet() {
  const sw = S.swiggy || {}, list = S.swAddrs;
  return `<div class="sheet-bg" data-ov="addr" data-close-addr="1"><section class="sheet" role="dialog" aria-modal="true" aria-labelledby="addr-title"><span class="grab" aria-hidden="true"></span>
    <div class="sheet-head"><h2 class="h2" id="addr-title">Deliver to</h2><button class="icon-btn" data-close-addr="1" aria-label="Close">${ICON.close}</button></div>
    <p class="fine">Your saved Swiggy addresses. Ziggy uses the one you pick for menus, your cart and planning.</p>
    ${list ? `<div class="stack" style="gap:8px">${list.map(a => `<button class="pick ${sw.address?.id === a.id ? "current" : ""}" data-swaddr="${esc(a.id)}" aria-pressed="${sw.address?.id === a.id}"><span class="t"><b>${esc(a.label)}</b><span>${esc(a.text)}</span></span>${sw.address?.id === a.id ? ICON.check : ICON.right}</button>`).join("") || `<p class="fine">Swiggy returned no saved addresses.</p>`}</div>`
      : `<div class="skeleton" aria-busy="true"><i></i><i></i><i></i></div>`}
    <p class="fine">Address not listed? Add it in the Swiggy app (Account → Addresses), then <button class="link" data-act="swiggy-refresh-addresses" style="min-height:0;font-size:inherit">refresh</button>.</p>
  </section></div>`;
}

/* ================================================================ ME */
function meScreen() {
  if (S.more) {
    const body = { settings: settingsPanel, connection: connectionPanel, recap: weekMoneyPanel, receipts: weekMoneyPanel, insights,
      household: householdPanel, calendar: calendarPanel, profiles: profilesPanel }[S.more];
    return `<button class="back secondary" data-go="more:">${ICON.back}Me</button>${body ? body() : ""}`;
  }
  const v = S.view, u = v.user || {}, b = v.budget || {}, sw = S.swiggy;
  const { theme, palette } = look();
  const swState = !sw ? "" : sw.connected ? (sw.address ? `Connected · ${esc(sw.address.label)}` : "Choose an address") : sw.expired ? "Sign-in expired" : "Not connected";
  const login = S.account?.login;
  const row = (go, label, value) => `<button class="set" data-go="more:${go}"><span>${label}</span><span><span>${value}</span>${ICON.right}</span></button>`;
  const rules = [{ veg: "Vegetarian", vegan: "Vegan", nonveg: "Non-veg" }[u.diet] || "", ...(u.allergens || []).map(a => `no ${a.replace("_", " ")}`)].filter(Boolean).join(" · ");
  return `<div class="row rise" style="flex-wrap:nowrap;gap:14px"><span class="avatar" style="width:56px;height:56px;font-size:22px;display:grid;place-items:center">${esc((u.name || "?").slice(0, 1).toUpperCase())}</span>
      <span class="stack" style="gap:0"><b class="money" style="font-size:26px">${esc(u.name || "You")}</b><span class="muted">${keys.get(S.userId) ? (login ? `Signed in as ${esc(login)}` : "Private to this device") : "Shared sample profile"}</span></span></div>
    <section class="card rise2 stats" aria-label="This week's money">
      <div class="stat"><span>Planned</span><b>${rupee0(b.spend)}</b></div><div class="stat"><span>Budget</span><b>${rupee0(b.budget)}</b></div>
      <div class="stat"><span>${(b.budget || 0) - (b.spend || 0) < 0 ? "Over" : "Left"}</span><b style="color:${(b.budget || 0) - (b.spend || 0) < 0 ? "var(--bad)" : "var(--leaf-ink)"}">${rupee0(Math.abs((b.budget || 0) - (b.spend || 0)))}</b></div></section>
    <section class="card tight rise2">
      <div class="set-col"><span>Appearance</span><div class="seg" role="group" aria-label="Appearance">${THEMES.map(([k, l]) => `<button data-look="theme:${k}" aria-pressed="${theme === k}">${l}</button>`).join("")}</div></div>
      <div class="set-col"><span>Colour</span><div class="seg" role="group" aria-label="Colour">${PALETTES.map(([k, l, sw]) => `<button data-look="palette:${k}" aria-pressed="${palette === k}"><span class="swatch" style="background:${sw}"></span>${l}</button>`).join("")}</div></div></section>
    <section class="card tight rise3">
      ${row("settings", "Food &amp; budget", esc(rules || "Your rules"))}
      ${row("connection", "Swiggy", swState)}
      <div class="set"><span>Order reminders</span>${pushState() === "ios-install" ? `<span class="fine">Add to Home Screen first</span>` : `<button class="switch" role="switch" aria-checked="${remindersOn()}" aria-label="Order reminders" data-act="notify"><i></i></button>`}</div>
      <button class="set" data-act="download-ics"><span>Add reminders to my calendar</span><span>.ics${ICON.right}</span></button></section>
    <section class="card tight rise3">
      ${row("recap", "This week &amp; spending", "Bills, CSV")}
      ${row("insights", "Nutrition", "Estimates")}
      ${row("household", "Household", v.household ? esc(v.household.name) : "Just you")}
      ${row("calendar", "Coming up", "Festivals, fasts")}
      ${row("profiles", "Account &amp; devices", keys.get(S.userId) ? "Sign in anywhere" : "Profiles")}
      <a class="set" href="/credits"><span>Credits &amp; licences</span><span>${ICON.right}</span></a></section>`;
}
function calendarPanel() {
  const rows = S.calendar;
  if (!rows) return `<div class="skeleton"><i></i><i></i></div>`;
  return `<h1 class="h1">Coming up</h1><p class="sub">Holidays change delivery demand and opening hours. Fasts show only if you keep them (Food &amp; budget → Fasts).</p>
    <section class="card tight">${rows.length ? rows.map(r => `<div class="mrow" style="cursor:default"><span class="t"><b>${esc(r.name)}${r.approx ? " <small class='muted'>(date may shift a day)</small>" : ""}</b><span class="s">${esc(r.when)} · ${esc(r.note)}</span></span></div>`).join("")
      : `<p class="fine" style="padding:14px 0">Nothing in the next 60 days.</p>`}</section>`;
}
function connectionPanel() {
  const sw = S.swiggy;
  if (!sw) return `<h1 class="h1">Swiggy</h1><div class="skeleton"><i></i><i></i></div>`;
  if (sw.requires_private_profile) return `<h1 class="h1">Swiggy</h1><section class="card"><h2 class="h2">Use your own private profile</h2>
    <p class="sub">Sample profiles are shared by every visitor. Create or sign in to your own profile to keep your Swiggy account and addresses private.</p>
    <button class="primary" data-act="start-onboard">Create my profile</button><button class="ghost" data-act="signin-open">Sign in</button></section>${rulesCard()}`;
  if (!sw.connected) return `<h1 class="h1">Swiggy</h1><section class="card"><span class="tag">${sw.needs_reconnect ? "Reconnect required" : sw.expired ? "Sign-in expired" : "Not connected"}</span>
    <p class="sub">Sign in on Swiggy's own page (phone + OTP). Ziggy then plans from real places near your address, fills your cart in one tap, and you pay in Swiggy. Sign-ins last about 5 days.</p>
    <button class="primary" data-act="connect-open">${sw.expired || sw.needs_reconnect ? "Reconnect Swiggy" : "Connect Swiggy"}</button>
    ${!swiggySignInOpen() ? `<p class="fine">Swiggy needs production access and an exact-match allowlisted HTTPS redirect, and hasn't approved this server's yet, so sign-in is expected to fail here.${sw.callback_url ? ` Request <code>${esc(sw.callback_url)}</code> from Swiggy Builders Club.` : ""}</p>` : ""}</section>${rulesCard()}`;
  return `<h1 class="h1">Swiggy</h1>
    <section class="card"><span class="tag good">Connected</span>
      <div class="row" style="justify-content:space-between"><span>Delivering to <b>${esc(sw.address?.label || "no address yet")}</b></span><button class="small secondary" data-act="addr-open">${sw.address ? "Change" : "Choose address"}</button></div>
      <p class="sub">Ziggy plans from your saved places for this address, fills your cart in one tap, and you check the bill and pay in Swiggy. ${sw.order_enabled ? "Where Swiggy offers Cash on Delivery, you can confirm and place a real order here." : "Real order placement here is waiting for Swiggy's approval."}</p>
      ${sw.expires ? `<p class="fine">This sign-in lasts until ${esc(fmtWhen(sw.expires))}.</p>` : ""}
      <button class="ghost" data-act="swiggy-disconnect">Disconnect</button>
      <details><summary>Technical details</summary><p class="fine">Protocol ${esc(sw.protocol_version || "?")} · ${(sw.tools || []).length} tools (${sw.read_tools} read, ${sw.write_tools} write).</p>
        ${(sw.tools || []).map(t => `<p class="fine"><b>${esc(t.name)}</b> <span class="tag ${t.kind === "write" ? "warn" : ""}">${t.kind}</span> ${esc(t.description)}</p>`).join("")}
        <button class="small" data-act="swiggy-discover">Refresh tools</button></details></section>${rulesCard()}`;
}
function insights() {
  const v = S.view, N = v.nutrition;
  const macro = (k, unit) => {
    const cur = N.daily_avg[k] || 0, tgt = N.daily_target[k] || 1;
    const pct = Math.min(140, (cur / tgt) * 100);
    return `<div class="nbar"><span>${k.replace("_g", "")}</span><div class="tr"><i class="${pct > 115 ? "over" : ""}" style="width:${Math.min(100, pct)}%"></i></div>
      <span>${Math.round(cur)} / ${Math.round(tgt)}${unit}</span></div>`;
  };
  const working = (v.user.prefs?.targets_working || []).map(w => `<li>${esc(w)}</li>`).join("");
  return `<h1 class="h1">Nutrition</h1><section class="card"><p class="k">Planned meals · average per day</p>
      ${macro("kcal", "")}${macro("protein_g", "g")}${macro("carbs_g", "g")}${macro("fat_g", "g")}${macro("sugar_g", "g")}
      ${working ? `<ul class="fine" style="margin-left:18px">${working}</ul>` : ""}
      <p class="fine">Covers only the meals Ziggy plans. Targets are general wellness estimates, not medical advice.</p></section>
    <section class="card"><p class="k">Carbon estimate</p><b class="money" style="font-size:30px">${v.carbon.total_kg} <small class="muted" style="font-size:15px">kg CO₂e</small></b>
      <p class="fine">Rough estimate for the week (band: ${esc(v.carbon.band)}).</p></section>`;
}
function weekMoneyPanel() { return recapPanel() + receiptsPanel(); }
function recapPanel() {
  const r = S.recap;
  if (!r) return `<h1 class="h1">This week &amp; spending</h1><div class="skeleton"><i></i><i></i></div>`;
  const s = r.spend, m = r.meals, n = r.nutrition;
  const pct = (a, b) => b ? Math.round(100 * a / b) : 0;
  const nut = n.days_logged ? `<div class="nbar"><span>kcal</span><div class="tr"><i style="width:${Math.min(100, pct(n.avg.kcal, n.target.kcal))}%"></i></div><span>${Math.round(n.avg.kcal)} / ${Math.round(n.target.kcal)}</span></div>
      <div class="nbar"><span>protein</span><div class="tr"><i style="width:${Math.min(100, pct(n.avg.protein_g, n.target.protein_g))}%"></i></div><span>${Math.round(n.avg.protein_g)} / ${Math.round(n.target.protein_g)}g</span></div>
      <p class="fine">Average per day on the ${n.days_logged} day${n.days_logged === 1 ? "" : "s"} you logged meals. Protein target met on ${n.protein_days}.</p>`
    : `<p class="fine">No meals logged yet this week. Mark a meal as had and it shows here.</p>`;
  const split = r.household ? `<section class="card"><p class="k">${esc(r.household.name)} · who owes what so far</p>
      <table>${r.household.split.map(x => `<tr><td>${esc(x.member)}</td><td>${rupee(x.share)}</td></tr>`).join("")}</table>
      <p class="fine">${r.household.split_method === "by_consumption" ? "By who ate each meal you marked as had." : "Spent so far, split evenly."}</p></section>` : "";
  return `<h1 class="h1">This week &amp; spending</h1><p class="sub">From what actually happened: meals you marked as had or ordered.</p>
    <section class="card stats"><div class="stat"><span>Spent</span><b>${rupee0(s.spent)}</b></div><div class="stat"><span>Still planned</span><b>${rupee0(s.planned)}</b></div>
      <div class="stat"><span>${s.left < 0 ? "Over" : "Left"}</span><b>${rupee0(Math.abs(s.left))}</b></div></section>
    <section class="card"><p class="k">Meals</p><p>${m.had} had (${m.ordered} ordered, ${m.cooked} cooked) · ${m.ahead} still ahead · ${m.skipped} skipped</p>
      ${m.unmarked ? `<p class="fine">${m.unmarked} past meal${m.unmarked === 1 ? " isn't" : "s aren't"} marked. They count as neither spent nor skipped until you mark them.</p>` : ""}
      ${r.top_place ? `<p class="fine">Most ordered from: ${esc(r.top_place.name)} (${r.top_place.times}×)</p>` : ""}
      <p class="fine">Spent on delivery ${rupee0(s.delivery)}, on cooking ${rupee0(s.cooked)}.${s.last_week != null ? ` Last week you spent ${rupee0(s.last_week)}.` : ""}</p></section>
    <section class="card"><p class="k">Nutrition</p>${nut}<p class="fine">General wellness estimates, not medical advice.</p></section>${split}`;
}
function receiptsPanel() {
  const r = S.receipts;
  const rows = r && r.rows.length ? r.rows.map(x => `<tr><td>${esc(x.iso_date)}</td><td>${esc(x.note)}</td><td><span class="tag ${x.category === "business" ? "warn" : ""}">${esc(x.category)}</span></td>
    <td>${x.real ? `${real(x.amount)} <span class="tag good">real bill</span>` : est(x.amount, true)}</td></tr>`).join("") : "";
  return `<h2 class="h2" id="expenses">Expenses</h2>
    <p class="sub">Meals you confirmed. A real Swiggy bill is exact; anything else is the plan's estimate. Not tax invoices.</p>
    <div class="row"><button class="secondary" data-act="genrcpt">Update expenses from this week</button><button class="ghost" data-act="download-csv">Export CSV</button></div>
    ${r ? `<section class="card stats expense-stats"><div class="stat"><span>Total</span><b>${rupee(r.total)}</b></div><div class="stat"><span>Business</span><b>${rupee(r.business_total)}</b></div>
      <div class="stat"><span>Real bills</span><b>${rupee(r.rows.filter(x => x.real).reduce((t, x) => t + x.amount, 0))}</b></div></section>
      <section class="card scroll-x"><table><tr><th>date</th><th>item</th><th>category</th><th>amount</th></tr>${rows || `<tr><td colspan="4" class="fine">No expenses yet. Mark a meal as had.</td></tr>`}</table></section>` : `<div class="skeleton"><i></i><i></i></div>`}`;
}

/* ---- settings: the same answers as onboarding, editable, in one form ---- */
const RHYTHM = [["order", "I order it"], ["cook", "I cook it"], ["skip", "I skip it"]];
function rhythmOf(u) {
  const p = u.prefs || {}, meals = u.meals || p.meals || MEALS;
  return p.rhythm || Object.fromEntries(MEALS.map(m => [m, meals.includes(m) ? "order" : "skip"]));
}
function settingsPanel() {
  const u = S.view.user, n = S.view.nutrition.daily_target, p = u.prefs || {};
  const body = p.body || {}, r = rhythmOf(u), nt = u.nutrition_targets || {}, ht = u.health_targets || {};
  const share = { breakfast: 0.25, lunch: 0.4, dinner: 0.35, ...(nt.meal_share || {}) };
  const number = (key, label, value, min, max, step = 1, extra = "") => `<label>${label}<input name="${key}" type="number" inputmode="decimal" min="${min}" max="${max}" step="${step}" value="${value ?? ""}" ${extra}></label>`;
  const checks = (key, values, labels = {}) => values.map(v => `<label class="check"><input type="checkbox" name="${key}" value="${v}" ${(u[key] || []).includes(v) ? "checked" : ""}>${esc(labels[v] || cap1(v.replace("_", " ")))}</label>`).join("");
  const opt = (name, cur, pairs) => `<select name="${name}">${pairs.map(([k, v]) => `<option value="${k}" ${cur === k ? "selected" : ""}>${v}</option>`).join("")}</select>`;
  const modes = Object.entries(S.meta?.modes || {});
  return `<h1 class="h1">Food &amp; budget</h1><p class="sub">Saving re-plans the meals that haven't happened yet. Your delivery address is in the pill at the top.</p>
    <form id="preferences" class="stack" style="gap:16px">
      <section class="card"><label>Name<input name="name" maxlength="80" value="${esc(u.name)}" required></label></section>
      <section class="card"><fieldset><legend>Food rules (always applied)</legend>
        <label>Diet${opt("diet", u.diet, [["nonveg", "Non-vegetarian"], ["veg", "Vegetarian"], ["vegan", "Vegan"]])}</label></fieldset>
        <fieldset><legend>Allergies (always excluded)</legend><div class="checks">${checks("allergens", ["peanut", "dairy", "gluten", "egg", "soy", "shellfish", "fish", "sesame", "tree_nut"])}</div></fieldset>
        <details><summary>Medical filters and fasts</summary><div class="checks">${checks("medical", ["diabetes", "hypertension", "celiac"])}</div>
          <p class="fine">Simple menu-label rules. They can't guarantee a dish is medically suitable or free of cross-contact.</p>
          <div class="checks">${checks("observances", ["navratri", "ramadan", "karva_chauth"], { navratri: "Navratri", ramadan: "Ramadan", karva_chauth: "Karva Chauth" })}</div>
          <p class="fine">On fast days only dinner is planned. Ziggy never assumes this from your name or area.</p></details></section>
      <section class="card"><fieldset><legend>Money</legend><div class="grid2">
        ${number("weekly_budget", "Weekly food budget (₹)", u.weekly_budget, 100, 100000)}
        ${number("daily_cap", "Daily limit (₹, optional)", p.daily_cap || "", 0, 20000, 1, 'placeholder="none"')}</div>
        ${modes.length ? `<label>How tight is money?<select name="mode">${modes.map(([k, l]) => `<option value="${k}" ${S.view.plan.mode === k ? "selected" : ""}>${esc(l)}${S.meta.mode_outcomes?.[k] ? ` — ${esc(S.meta.mode_outcomes[k])}` : ""}</option>`).join("")}</select></label>` : ""}</fieldset></section>
      <section class="card"><fieldset><legend>What to plan</legend><p class="fine">For each meal: do you usually order it, cook it, or skip it?</p>
        <div class="grid2">${MEALS.map(m => `<label>${cap1(m)}<select name="rh_${m}">${RHYTHM.map(([k, l]) => `<option value="${k}" ${r[m] === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>`).join("")}
        <label>How often you cook${opt("cook", p.cook || (ht.max_cook_per_week ? "sometimes" : "never"), [["never", "Never"], ["sometimes", "1–2 times a week"], ["often", "3–5 times a week"], ["most", "Most days"]])}</label>
        <label>Variety${opt("variety", nt.variety || "light", [["usual", "Just my usuals"], ["light", "Mostly usual, a little new"], ["mixed", "Half new"], ["adventurous", "Surprise me often"]])}</label>
        ${number("rating_floor", "Minimum restaurant rating", u.rating_floor, 0, 5, 0.1)}
        <label>Goal${opt("goal", p.goal || "none", [["none", "No goal"], ["protein", "More protein"], ["lose", "Lose weight gently"], ["maintain", "Maintain weight"], ["gain", "Build muscle"]])}</label></div></fieldset>
        ${tasteFieldset(p)}</section>
      <section class="card"><details ${body.weight_kg ? "open" : ""}><summary>Personalise nutrition (optional)</summary><div class="grid2">
          ${number("weight_kg", "Weight (kg)", body.weight_kg, 30, 300, 0.1)}${number("height_cm", "Height (cm)", body.height_cm, 120, 230)}
          ${number("age", "Age", body.age, 18, 100)}
          <label>Sex (for the formula)${opt("sex", body.sex || "unspecified", [["unspecified", "Prefer not to say"], ["female", "Female"], ["male", "Male"]])}</label>
          <label>Activity${opt("activity", body.activity || "light", [["sedentary", "Mostly sitting"], ["light", "Light (walks)"], ["moderate", "Moderate (3–5 workouts)"], ["active", "Active (daily training)"], ["athlete", "Athlete"]])}</label></div>
          <p class="fine">Current target: ${Math.round(n.kcal)} kcal, ${Math.round(n.protein_g)} g protein a day. Mifflin–St Jeor estimate for adults. Not medical advice.</p></details>
        <details class="tuning"><summary>Calorie split &amp; targets (advanced)</summary><div class="grid2">
          ${MEALS.map(m => `<label>${cap1(m)} share of calories (%)<input name="sh_${m}" type="number" min="10" max="70" value="${Math.round(share[m] * 100)}"></label>`).join("")}
          <label>Calories a day<input name="kcal" type="number" min="1000" max="4500" value="${nt.kcal ?? n.kcal ?? ""}"></label>
          <label>Protein a day (g)<input name="protein_g" type="number" min="20" max="250" value="${nt.protein_g ?? n.protein_g ?? ""}"></label>
          <label>Same dish at most (times a week)<input name="max_repeat" type="number" min="1" max="7" value="${ht.max_item_repeat ?? 2}"></label></div>
          <p class="fine">The split must add up to 100%. Want the same lunch every day? Set "same dish" to 5 or more.</p></details></section>
      <button type="submit" class="primary">Save &amp; re-plan</button>
    </form>`;
}
function tasteFieldset(p) {
  if (!epicureOn()) return "";
  const t = p.cuisine_tilt || {};
  const cuisines = Object.entries(S.meta.epicure.cuisines || {});
  const likes = (p.more_like || []).map(m => `<span class="chip" style="cursor:default">${esc(m.name)} <button type="button" class="x" data-forget-like="${esc(m.name)}" aria-label="Stop favouring dishes like ${esc(m.name)}">${ICON.close}</button></span>`).join("");
  return `<fieldset><legend>Taste</legend><div class="grid2">
      <label>Lean toward a cuisine<select name="tilt_cuisine"><option value="">No lean</option>${cuisines.map(([k, v]) => `<option value="${k}" ${t.cuisine === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
      <label>How much<select name="tilt_strength"><option value="light" ${t.strength !== "strong" ? "selected" : ""}>A little</option><option value="strong" ${t.strength === "strong" ? "selected" : ""}>A lot</option></select></label></div>
      ${likes ? `<div class="chips">More like: ${likes}</div>` : `<p class="fine">Tap “More like …” in a meal's Change sheet to get more dishes like it.</p>`}</fieldset>`;
}
// Only what you changed is sent, so an untouched target keeps following your goal.
function readTuning(form) {
  const f = new FormData(form), n = k => Number(f.get(k));
  const changed = k => { const el = form.elements && form.elements[k]; return !!el && el.value !== el.defaultValue; };
  const out = {};
  if (MEALS.every(m => f.get(`rh_${m}`))) out.rhythm = Object.fromEntries(MEALS.map(m => [m, f.get(`rh_${m}`)]));
  const tuning = {};
  if (MEALS.some(m => changed(`sh_${m}`))) tuning.meal_share = Object.fromEntries(MEALS.map(m => [m, n(`sh_${m}`) / 100]));
  if (changed("max_repeat")) tuning.max_repeat = n("max_repeat");
  if (changed("kcal") && f.get("kcal")) tuning.kcal = n("kcal");
  if (changed("protein_g") && f.get("protein_g")) tuning.protein_g = n("protein_g");
  if (Object.keys(tuning).length) out.tuning = tuning;
  return out;
}
function readSettings(form) {
  const f = new FormData(form);
  const body = { name: f.get("name"), diet: f.get("diet"), weekly_budget: Number(f.get("weekly_budget")),
    rating_floor: Number(f.get("rating_floor")), cook: f.get("cook"), variety: f.get("variety"), goal: f.get("goal"),
    allergens: f.getAll("allergens"), medical: f.getAll("medical"), observances: f.getAll("observances") };
  const cap = Number(f.get("daily_cap"));
  body.daily_cap = cap > 0 ? cap : null;
  if (f.has("tilt_cuisine")) body.cuisine_tilt = f.get("tilt_cuisine") ? { cuisine: f.get("tilt_cuisine"), strength: f.get("tilt_strength") || "light" } : null;
  const w = f.get("weight_kg"), h = f.get("height_cm"), a = f.get("age");
  if (w && h && a) body.body = { weight_kg: Number(w), height_cm: Number(h), age: Number(a), sex: f.get("sex"), activity: f.get("activity") };
  return { ...body, ...readTuning(form) };
}
async function saveSettings(form) {
  const body = readSettings(form);
  const mode = form.elements?.mode?.value;
  adoptView(await api(`/api/user/${S.userId}/setup`, "PATCH", body));
  if (mode && mode !== S.view.plan.mode) adoptView(await api(`/api/plan/${S.planId}/optimize`, "POST", { mode }));
  if (keys.get(S.userId)) keys.put(S.userId, keys.get(S.userId), S.view.user.name);
  S.users = mergeUsers(await api("/api/users")); S.orderReview = null;
  S.tab = "today"; S.more = null; toast("Saved. Upcoming meals re-planned."); render();
}

/* ---- household: the people you cook and order for ---- */
const ALLERGY_LIST = ["peanut", "dairy", "gluten", "egg", "soy", "shellfish", "fish", "sesame", "tree_nut"];
const DIET_NAME = { veg: "Vegetarian", vegan: "Vegan", nonveg: "Non-vegetarian" };
function personForm(id, m = {}) {
  const meals = S.view.user.meals || MEALS;
  const box = (name, v, on, label) => `<label class="check"><input type="checkbox" name="${name}" value="${v}" ${on ? "checked" : ""}>${esc(label)}</label>`;
  return `<form id="${id}" class="card">
    <div class="grid2"><label>Name<input name="name" maxlength="40" value="${esc(m.name || "")}" required></label>
    <label>Diet<select name="diet">${Object.entries(DIET_NAME).map(([k, v]) => `<option value="${k}" ${(m.diet || "nonveg") === k ? "selected" : ""}>${v}</option>`).join("")}</select></label></div>
    <fieldset><legend>Allergies (always excluded from shared meals)</legend><div class="checks">${ALLERGY_LIST.map(a => box("allergens", a, (m.allergens || []).includes(a), cap1(a.replace("_", " ")))).join("")}</div></fieldset>
    <fieldset><legend>Medical filters</legend><div class="checks">${["diabetes", "hypertension", "celiac"].map(c => box("medical", c, (m.medical || []).includes(c), cap1(c))).join("")}</div></fieldset>
    <fieldset><legend>Usually eats with you</legend><div class="checks">${meals.map(x => box("meals", x, (m.meals || meals).includes(x), cap1(x))).join("")}</div></fieldset>
    <div class="row"><button type="submit" class="secondary">${m.id ? "Save" : "Add to household"}</button>
      ${m.id ? `<button type="button" class="ghost" data-act="hh-edit-cancel">Cancel</button>` : ""}</div></form>`;
}
function readPerson(form) {
  const f = new FormData(form);
  return { name: f.get("name"), diet: f.get("diet"), allergens: f.getAll("allergens"), medical: f.getAll("medical"), meals: f.getAll("meals") };
}
function householdPanel() {
  const h = S.view.household;
  if (!h) {
    if (!keys.get(S.userId)) return `<h1 class="h1">Household</h1><section class="card"><p>Sample profiles are shared by every visitor. <button class="link" data-act="start-onboard">Set up your own profile</button> to plan for a household.</p></section>`;
    return `<h1 class="h1">Household</h1><p class="sub">Cooking or ordering for more than you? Add the people you share meals with. Every shared meal meets all their allergies and diets, and you can split the cost.</p>
      <form id="hh-create" class="card"><label>Household name<input name="name" maxlength="40" value="Home" required></label>
        <button type="submit" class="secondary">Create household</button></form>`;
  }
  const r = h.rules;
  const rule = (x, label) => `<span class="tag warn">${esc(label)} · ${esc(x.who.join(", "))}</span>`;
  const rules = [...r.allergens.map(x => rule(x, `No ${x.rule.replace("_", " ")}`)), ...r.medical.map(x => rule(x, cap1(x.rule))),
    ...(r.diet.rule !== "nonveg" ? [rule(r.diet, DIET_NAME[r.diet.rule])] : [])].join(" ");
  const people = h.people.map(p => S.hhEdit === p.id ? personForm("hh-edit", p) : `<section class="card"><div class="row" style="flex-wrap:nowrap">
      <div style="flex:1"><b>${esc(p.name)}</b>${p.you ? " <small class='muted'>(you)</small>" : ""}
        <p class="fine">${DIET_NAME[p.diet] || esc(p.diet)}${p.allergens.length ? ` · no ${p.allergens.map(a => esc(a.replace("_", " "))).join(", ")}` : ""}${p.medical.length ? ` · ${p.medical.map(esc).join(", ")}` : ""} · eats ${p.meals.map(esc).join(", ") || "no planned meals"}</p></div>
      ${p.managed ? `<button class="small ghost" data-hh-edit="${p.id}">Edit</button><button class="small ghost" data-hh-remove="${p.id}" data-hh-name="${esc(p.name)}">Remove</button>`
        : (p.you ? `<button class="small ghost" data-go="more:settings">Your rules</button>` : `<span class="fine">Has their own profile</span>`)}</div></section>`).join("");
  const shares = h.split.map(x => `<tr><td>${esc(x.member)}</td><td>${rupee(x.share)}</td></tr>`).join("");
  return `<h1 class="h1">Household · ${esc(h.name)}</h1>
    <section class="card"><p class="k">Shared meals meet everyone's rules</p>
      <div class="chips">${rules || `<span class="fine">Nobody here has an allergy, medical filter or diet limit.</span>`}</div>
      <p class="fine">Dishes and recipes that break any of these are never picked for the household.</p></section>
    ${people}
    ${S.hhEdit ? "" : `<details class="card"><summary>Add someone you cook or order for</summary>${personForm("hh-add")}</details>`}
    <section class="card"><p class="k">Splitting this week's planned spend</p>
      <label>Split costs<select id="hh-split"><option value="even" ${h.split_method === "even" ? "selected" : ""}>Evenly</option>
        <option value="by_consumption" ${h.split_method === "by_consumption" ? "selected" : ""}>By who eats each meal</option></select></label>
      <table>${shares}</table>
      <p class="fine">${h.split_method === "by_consumption" ? "Each meal's cost is divided among the people eating it." : "The week's planned spend divided equally."} Planned estimate, ${rupee(S.view.budget.spend)} in total.</p></section>
    <details class="card"><summary>Stop sharing</summary><p>People without their own profile are removed. Your plan goes back to your own rules only.</p>
      <button class="ghost" data-act="hh-leave">Stop sharing this household</button></details>`;
}
async function householdCall(path, method, body, msg) {
  adoptView(await api(`/api/user/${S.userId}/household${path}`, method, body));
  S.hhEdit = null; toast(msg); render();
}

/* ---- account: profiles, sign in anywhere, recovery, your data ---- */
function profilesPanel() {
  const opts = S.users.map(u => `<button class="pick ${u.id === S.userId ? "current" : ""}" data-user="${u.id}"><span class="t"><b>${esc(u.name)}</b><span>${u.private ? "Private to this browser" : "Shared sample profile"}${u.id === S.userId ? " · open now" : ""}</span></span>${u.id === S.userId ? ICON.check : ICON.right}</button>`).join("");
  const k = keys.get(S.userId);
  const recovery = k ? `<section class="card"><p class="k">Recovery code</p>
      <p class="sub">This profile is private and its key lives only in this browser. To open it on another device, or after clearing your browser, you need this code. Keep it somewhere safe.</p>
      <div class="row" style="flex-wrap:nowrap"><code class="code">${esc(`${S.userId}.${k}`)}</code><button class="small" data-act="copy-recovery">Copy</button></div>
      <details><summary>Replace a shared or lost code</summary><p class="fine">A new code invalidates all earlier codes and signs out every other device. Change your password too if someone else knows it.</p><button data-act="rotate-recovery">Generate a new recovery code</button></details></section>` : "";
  return `<h1 class="h1">Account &amp; devices</h1>
    ${accountCard()}
    ${recovery}
    <p class="k">Profiles on this device</p><div class="stack" style="gap:8px">${opts}</div>
    <button class="secondary" data-act="start-onboard">${ICON.plus}Set up a new profile</button>
    <section class="card"><p class="k">Open a profile from another device</p>
      <form id="recover" class="row" style="flex-wrap:nowrap"><input id="rcode" type="text" placeholder="Paste recovery code" autocomplete="off" aria-label="Recovery code"><button type="submit" class="secondary">Open</button></form></section>
    ${k ? `<section class="card"><p class="k">Your data</p>
      <p class="sub">Download your saved Ziggy profile, plans, favourites and order records. Authentication secrets are excluded.</p>
      <button class="secondary" data-act="download-profile">Download my data</button>
      <details><summary>Delete my Ziggy profile</summary><p class="fine">This permanently removes your profile, sign-in, device access and saved records from Ziggy. Your Swiggy account and existing orders stay. Download your data first if you want a copy.</p>
      <form id="delete-profile" class="stack"><label>Type DELETE to confirm<input id="delete-confirmation" autocomplete="off" required pattern="DELETE"></label><button class="ghost" type="submit">Permanently delete this profile</button></form></details></section>` : ""}`;
}
/* Sign in anywhere: an optional name + password for a private profile (accounts.py). */
function accountCard() {
  if (!keys.get(S.userId)) return `<section class="card"><p class="k">Sign in anywhere</p><p class="sub">Open this profile on the device that created it to add a sign-in.</p></section>`;
  const a = S.account;
  if (!a) return `<div class="skeleton"><i></i><i></i></div>`;
  const devs = a.devices.map(d => `<div class="mrow" style="cursor:default"><span class="t"><b>${esc(d.label)}${d.this_device ? " · this device" : ""}</b><span class="s">Last used ${esc(fmtDate(d.last_seen.slice(0, 10)))}</span></span>
    <button class="small ghost" data-rm-device="${d.id}">Sign out</button></div>`).join("");
  return `<section class="card"><p class="k">Sign in anywhere</p>
    <p class="sub">${a.login ? `Sign in as <b>${esc(a.login)}</b> on any phone or computer. There's no email: if you forget the password, open the profile here or with its recovery code and set a new one.`
      : "Choose a name and password to open this profile on your other devices. No email needed."}</p>
    <form id="account" class="stack">
      <label>Sign-in name<input id="acct-login" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" value="${esc(S.acctDraft ?? a.login ?? "")}" required minlength="3" maxlength="64"></label>
      <label>${a.login ? "New password" : "Password"}<input id="acct-pw" type="password" autocomplete="new-password" required minlength="8" maxlength="200"></label>
      <button type="submit" class="secondary">${a.login ? "Change password" : "Save sign-in"}</button></form>
    ${devs ? `<p class="k">Signed-in devices</p><div>${devs}</div>` : ""}
    <button class="ghost small" data-act="sign-out">${a.devices.some(d => d.this_device) ? "Sign out of this device" : "Forget this profile on this browser"}</button></section>`;
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
  resetProfileState();
  for (const key of ["userId", "planId", "view", "onboard"]) S[key] = null;
  S.welcome = true;
}
async function deleteProfile(form) {
  const confirmation = form.querySelector("#delete-confirmation").value;
  await api(`/api/user/${S.userId}`, "DELETE", { confirmation });
  keys.drop(S.userId); store.del("smartplate.user");
  clearCurrentProfile();
  S.users = mergeUsers(await api("/api/users"));
  render(); toast("Your Ziggy profile was deleted.");
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

/* ================================================================ ONBOARDING */
const OB_STEPS = ["What you eat", "Your days", "Budget", "Save"];
const BUDGET_PRESETS = [1500, 3000, 5000];
function startOnboard() {
  S.onboard = { step: 0,
    d: { name: "", diet: "nonveg", allergens: [], medical: [], observances: [], meals: ["lunch", "dinner"], cook: "sometimes",
      rhythm: { breakfast: "skip", lunch: "order", dinner: "order" },
      favourites: [], weekly_budget: 3000, daily_cap: null, goal: "none", body: null } };
  S.welcome = false; S.sheet = null; S.connectSheet = false; S.error = null; render();
}
function chip(group, value, label, on, multi = true) {
  return `<button type="button" class="chip" data-ob="${group}" data-val="${esc(value)}" data-multi="${multi ? 1 : 0}" aria-pressed="${on}">${label}</button>`;
}
function onboardingScreen() {
  const o = S.onboard, d = o.d, st = o.step;
  const bars = OB_STEPS.map((t, i) => `<span class="${i === st ? "on" : i < st ? "done" : ""}" title="${t}"></span>`).join("");
  let body = "";
  if (st === 0) {
    body = `<h1 class="h1">What do you eat?</h1>
      <div class="stack" style="gap:10px">${[["veg", "Vegetarian", "No meat, fish or egg", ""], ["nonveg", "Non-vegetarian", "Everything is on the menu", "nonveg"], ["vegan", "Vegan", "No animal products at all", ""]].map(([k, l, s, dot]) =>
        `<button type="button" class="opt" data-ob="diet" data-val="${k}" data-multi="0" aria-pressed="${d.diet === k}"><span class="dot ${dot}"></span><span><b>${l}</b><span class="muted">${s}</span></span></button>`).join("")}</div>
      <p class="k">Anything you must avoid?</p>
      <div class="chips">${ALLERGY_LIST.map(a => chip("allergens", a, cap1(a.replace("_", " ")), d.allergens.includes(a))).join("")}</div>
      <p class="fine">Ziggy filters by dish name. Menus don't list every ingredient, so check with the restaurant when it matters.</p>
      <details><summary>Medical needs or fasts (optional)</summary>
        <div class="chips">${[["diabetes", "Diabetes (low sugar)"], ["hypertension", "Blood pressure (less salt)"], ["celiac", "Celiac"]].map(([k, l]) => chip("medical", k, l, d.medical.includes(k))).join("")}</div>
        <p class="fine">Fasts you keep. On those days only dinner is planned:</p>
        <div class="chips">${[["navratri", "Navratri"], ["ramadan", "Ramadan"], ["karva_chauth", "Karva Chauth"]].map(([k, l]) => chip("observances", k, l, d.observances.includes(k))).join("")}</div>
      </details>`;
  } else if (st === 1) {
    body = `<h1 class="h1">How do your days usually go?</h1><p class="sub">Ziggy plans only what you tell it. Cook meals get a recipe and one grocery list.</p>
      ${MEALS.map(m => `<div class="card rhythm"><b>${cap1(m)}</b>
        <div class="seg" role="group" aria-label="${cap1(m)}">${[["order", "Order"], ["cook", "Cook"], ["skip", "Skip"]].map(([k, l]) => `<button type="button" data-ob="rh_${m}" data-val="${k}" data-multi="0" aria-pressed="${d.rhythm[m] === k}">${l}</button>`).join("")}</div></div>`).join("")}
      <p class="k">On order days, swap in a cheap home-cook sometimes?</p>
      <div class="chips">${[["never", "No"], ["sometimes", "1–2× a week"], ["often", "3–5× a week"], ["most", "Most days"]].map(([k, l]) => chip("cook", k, l, d.cook === k, false)).join("")}</div>`;
  } else if (st === 2) {
    const meals = MEALS.filter(m => d.rhythm[m] !== "skip").length * 7 || 1;
    body = `<h1 class="h1">Weekly food budget</h1><p class="sub">Delivery, fees and groceries all count. Swiggy's cart shows the exact total before you pay.</p>
      <section class="card" style="align-items:center;padding:26px 18px">
        <div class="stepper"><button type="button" data-ob-budget="-250" aria-label="Lower budget by 250 rupees">${ICON.minus}</button>
          <label class="sr" for="ob-budget">Weekly budget in rupees</label><input id="ob-budget" class="money" type="number" inputmode="numeric" min="100" max="100000" value="${d.weekly_budget ?? ""}" style="font-size:44px;width:190px;text-align:center;border:0;background:transparent;min-height:60px">
          <button type="button" data-ob-budget="250" aria-label="Raise budget by 250 rupees">${ICON.plus}</button></div>
        <span class="muted">${d.weekly_budget ? `about ${rupee0(d.weekly_budget / meals)} a meal for ${meals} meals` : "Enter an amount"}</span></section>
      <div class="chips" style="justify-content:center">${BUDGET_PRESETS.map(n => chip("budgetpick", n, rupee0(n), d.weekly_budget === n, false)).join("")}</div>
      <details ${d.daily_cap ? "open" : ""}><summary>Also cap a single day (optional)</summary>
        <label>₹ per day<input id="ob-daily" type="number" inputmode="numeric" min="0" max="20000" value="${d.daily_cap ?? ""}" placeholder="no daily limit"></label>
        <p class="fine">Useful when money is tight until payday.</p></details>`;
  } else {
    const b = d.body || {};
    body = `<h1 class="h1">Save your profile</h1><p class="sub">So you can open Ziggy on any device. No email needed.</p>
      <section class="card stack">
        <label>Your name<input id="ob-name" maxlength="80" value="${esc(d.name)}" placeholder="Me" autocomplete="given-name"></label>
        <label>Sign-in name<input id="ob-login" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" minlength="3" maxlength="64" value="${esc(d.login || "")}" required></label>
        <label>Password<input id="ob-pw" type="password" autocomplete="new-password" minlength="8" maxlength="200" required></label></section>
      <p class="k">Any food goal? (optional)</p>
      <div class="chips">${[["none", "No goal"], ["protein", "More protein"], ["lose", "Lose weight gently"], ["maintain", "Maintain"], ["gain", "Build muscle"]].map(([k, l]) => chip("goal", k, l, d.goal === k, false)).join("")}</div>
      <details ${b.weight_kg ? "open" : ""}><summary>Personalise with height &amp; weight (optional)</summary>
        <div class="grid2">
          <label>Weight (kg)<input id="ob-w" type="number" inputmode="decimal" min="30" max="300" value="${b.weight_kg ?? ""}"></label>
          <label>Height (cm)<input id="ob-h" type="number" inputmode="numeric" min="120" max="230" value="${b.height_cm ?? ""}"></label>
          <label>Age<input id="ob-a" type="number" inputmode="numeric" min="18" max="100" value="${b.age ?? ""}"></label>
          <label>Sex (for the formula)<select id="ob-s"><option value="unspecified">Prefer not to say</option><option value="female" ${b.sex === "female" ? "selected" : ""}>Female</option><option value="male" ${b.sex === "male" ? "selected" : ""}>Male</option></select></label>
          <label>Activity<select id="ob-act">${[["sedentary", "Mostly sitting"], ["light", "Light"], ["moderate", "Moderate"], ["active", "Active"], ["athlete", "Athlete"]].map(([k, l]) => `<option value="${k}" ${(b.activity || "light") === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        </div><p class="fine">Adults only. General wellness estimate, not medical advice.</p></details>`;
  }
  const last = st === OB_STEPS.length - 1;
  return `<main class="onboard"><div class="ob-top"><button class="icon-btn" data-ob-nav="back" aria-label="${st ? "Back" : "Cancel"}">${ICON.back}</button>
      <div class="ob-bars" aria-label="Step ${st + 1} of ${OB_STEPS.length}">${bars}</div><span class="fine">${st + 1}/${OB_STEPS.length}</span></div>
    ${errbar(false)}<form class="stack rise" id="obform" novalidate style="gap:16px">${body}</form>
    <div class="grow"></div>
    <button class="primary big" data-ob-nav="next">${last ? "Plan my week" : "Continue"}</button></main>`;
}
function planningScreen() {
  const d = S.onboard?.d || {};
  return `<main class="planning" aria-busy="true" aria-live="polite">${mascot("bob")}
    <h1 class="h1">Planning your week</h1>
    <div class="steps"><div><span class="tick">✓</span><span>Reading your rules</span></div>
      <div><span class="tick">✓</span><span>Fitting ${d.weekly_budget ? rupee0(d.weekly_budget) : "your budget"} across the week</span></div>
      <div><span class="tick">✓</span><span>Setting order-by times</span></div></div></main>`;
}
function readOnboardInputs() {
  const o = S.onboard, d = o.d, val = (id) => document.getElementById(id)?.value;
  if (o.step === 2) {
    const b = Number(val("ob-budget")); d.weekly_budget = b > 0 ? b : null;
    const c = Number(val("ob-daily")); d.daily_cap = c > 0 ? c : null;
  }
  if (o.step === 3) {
    d.name = (val("ob-name") || "").trim();
    d.login = (val("ob-login") || "").trim();
    d.password = val("ob-pw") || d.password || "";
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
    if (d.login.length < 3) throw new Error("Choose a sign-in name (3 or more characters) so you can open this profile anywhere");
    if (d.password.length < 8) throw new Error("Use a password of at least 8 characters");
    const body = { ...d, name: d.name || "Me" };
    delete body.meals;                     // the rhythm says which meals are planned
    if (!body.body) delete body.body;
    if (!body.daily_cap) delete body.daily_cap;
    S.planning = true; render();            // the elephant plans while the server does
    let view;
    try { view = await api("/api/profiles", "POST", body); }
    finally { S.planning = false; }
    d.password = "";                                     // never kept in memory after use
    if (view.access_key) keys.put(view.user.id, view.access_key, view.user.name);
    S.users = mergeUsers(await api("/api/users"));
    S.onboard = null; S.userId = view.user.id; store.set("smartplate.user", String(S.userId));
    adoptView(view); S.exec = await api(`/api/plan/${S.planId}/orders`);
    S.swiggy = await api(`/api/user/${S.userId}/swiggy`);
    S.tab = "today"; S.more = null;
    // Quick connection: the very next step after planning, one tap away.
    S.connectSheet = swiggySignInOpen() && !S.swiggy?.connected;
    toast("Your week is planned"); render(); return;
  }
  o.step += 1; S.error = null; render();
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
  // Handlers read e.currentTarget inside closures that guard() may run again later (Retry).
  // The browser resets currentTarget to null once dispatch ends, so pin it to the element.
  const on = (sel, ev, fn) => document.querySelectorAll(sel).forEach(el => el.addEventListener(ev, (e) => {
    try { Object.defineProperty(e, "currentTarget", { value: el, configurable: true }); } catch (_) { /* keep the native one */ }
    return fn(e);
  }));
  on("[data-tab]", "click", (e) => { e.preventDefault(); guard(() => goTab(e.currentTarget.dataset.tab)); });
  on("[data-go]", "click", (e) => { const [t, sub] = e.currentTarget.dataset.go.split(":"); guard(() => goTab(t, sub || null)); });
  on("[data-week-view]", "click", (e) => { const v = e.currentTarget.dataset.weekView || null; guard(() => goWeekView(v)); });
  on("[data-day]", "click", (e) => { S.weekDay = Number(e.currentTarget.dataset.day); render(); });
  on("[data-glance-day]", "click", (e) => { S.weekDay = Number(e.currentTarget.dataset.glanceDay); S.tab = "week"; S.weekView = null; S.more = null; render(); });
  on("[data-saved-tab]", "click", (e) => { S.savedTab = e.currentTarget.dataset.savedTab; render(); });
  on("[data-sheet-tab]", "click", (e) => { S.sheet.tab = e.currentTarget.dataset.sheetTab; render(); });
  on("[data-sheet-place]", "click", (e) => { S.sheet.place = e.currentTarget.dataset.sheetPlace; render(); });
  on("[data-look]", "click", (e) => { const [k, v] = e.currentTarget.dataset.look.split(":"); setLook(k, v); });
  on("[data-pin]", "click", (e) => { e.stopPropagation(); const sid = Number(e.currentTarget.dataset.pin); guard(() => togglePin(sid), "Keep this meal"); });
  on("[data-filter]", "click", (e) => toggleFilter(e.currentTarget.dataset.filter));
  on("[data-cat]", "click", (e) => { S.menuCat = e.currentTarget.dataset.cat || null; render(); });
  on("[data-close-addr]", "click", (e) => { if (e.target === e.currentTarget || e.currentTarget.tagName === "BUTTON") { S.addrSheet = false; render(); } });
  on("[data-close-connect]", "click", (e) => { if (e.target === e.currentTarget || e.currentTarget.tagName === "BUTTON") { S.connectSheet = false; S.pendingResume = null; render(); } });
  on("[data-close-menu]", "click", (e) => { if (e.target === e.currentTarget) { S.liveBrowseMenu = null; render(); } });
  on("[data-mode]", "click", (e) => guard(() => setMode(e.currentTarget.dataset.mode)));
  on("[data-user]", "click", (e) => guard(() => switchUser(e.currentTarget.dataset.user)));
  on("[data-sheet]", "click", (e) => guard(() => openSheet(e.currentTarget.dataset.sheet)));
  on("[data-confirm]", "click", (e) => { e.stopPropagation(); guard(() => confirmMeal(e.currentTarget.dataset.confirm), "Mark as had"); });
  on("[data-reason]", "click", (e) => { const [sid, k] = e.currentTarget.dataset.reason.split(":"); guard(() => rateReason(sid, k)); });
  on("[data-unlearn]", "click", (e) => { const key = e.currentTarget.dataset.unlearn; guard(async () => {
    const r = await api(`/api/user/${S.userId}/learned/undo`, "POST", { key }); adoptView(r.plan); toast("Undone"); render(); }); });
  on("[data-rate]", "click", (e) => { const [sid, sc] = e.currentTarget.dataset.rate.split(":"); guard(() => rateMeal(sid, Number(sc))); });
  on("[data-handoff]", "click", (e) => { S.handedOff = Number(e.currentTarget.dataset.handoff); setTimeout(render, 50); });
  on("[data-fav]", "click", (e) => guard(() => toggleFav(e.currentTarget.dataset.fav), "Save this place"));
  on("[data-pick]", "click", (e) => guard(() => choose(S.sheet.sid, { item_id: Number(e.currentTarget.dataset.pick) },
    `${e.currentTarget.dataset.pickName} it is. The rest of the week re-balanced.`)));
  on("[data-quick]", "click", (e) => { const t = e.currentTarget; guard(() => choose(S.quick.sid, { item_id: Number(t.dataset.quick) },
    `${t.dataset.quickName} it is. The rest of the week re-balanced.`)); });
  on("[data-quick-cook]", "click", (e) => { const t = e.currentTarget; guard(() => choose(S.quick.sid, { recipe_key: t.dataset.quickCook },
    `Cook ${t.dataset.quickName} at home. It's on your grocery list.`)); });
  on("[data-hungry]", "click", (e) => guard(() => eatNow(e.currentTarget.dataset.hungry)));
  on("[data-cook]", "click", (e) => guard(() => choose(S.sheet.sid, { recipe_key: e.currentTarget.dataset.cook }, "Cook at home. It's on your grocery list.")));
  on("[data-choose-auto]", "click", (e) => guard(() => choose(e.currentTarget.dataset.chooseAuto, { action: "auto" }, "Ziggy will choose this one")));
  on("[data-move]", "click", (e) => { S.moving = Number(e.currentTarget.dataset.move); S.sheet = null; S.tab = "week"; S.weekView = null; render(); });
  on("[data-close-sheet]", "click", (e) => { if (e.target === e.currentTarget || e.currentTarget.tagName === "BUTTON") { S.sheet = null; render(); } });
  on("[data-meal]", "click", (e) => onMealTap(e.currentTarget.dataset.meal));
  on("[data-oq-cart]", "click", (e) => { const sid = e.currentTarget.dataset.oqCart; guard(() => liveAction("order-week", sid)); });
  on("[data-oq-place]", "click", (e) => { const sid = e.currentTarget.dataset.oqPlace; guard(() => placeQueued(sid, false)); });
  on("[data-over-ok]", "click", (e) => { const sid = Number(e.currentTarget.dataset.overOk); guard(async () => {
    if (S.carts?.[sid]) { S.carts[sid] = { ...S.carts[sid], budgetOk: true }; render(); return; }   // hand-off: open Swiggy next
    await placeQueued(sid, true); }); });
  on("[data-replan]", "click", (e) => { const sid = Number(e.currentTarget.dataset.replan); guard(() => replanRemaining(sid)); });
  on("[data-close-err]", "click", () => { S.error = null; S.errorCode = null; render(); });
  on("[data-close-review]", "click", (e) => { if (e.target === e.currentTarget || e.currentTarget.tagName === "BUTTON") { S.orderReview = null; render(); } });
  on("[data-close-live-review]", "click", (e) => { if (e.target === e.currentTarget || e.currentTarget.tagName === "BUTTON") { S.liveOrderReview = null; render(); } });
  on("[data-close-checkout-review]", "click", (e) => { if (e.target === e.currentTarget || e.currentTarget.tagName === "BUTTON") { S.checkoutReview = null; render(); } });
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
  on("[data-sess]", "click", (e) => { e.stopPropagation(); const [id, st] = e.currentTarget.dataset.sess.split(":"); guard(() => setSession(id, st), st === "skipped" ? "Skip this meal" : null); });
  on("[data-ob]", "click", (e) => { e.preventDefault(); const t = e.currentTarget; onboardChip(t.dataset.ob, t.dataset.val, t.dataset.multi === "1"); });
  on("[data-ob-budget]", "click", (e) => { e.preventDefault(); readOnboardInputs(); const d = S.onboard.d;
    d.weekly_budget = Math.min(100000, Math.max(250, (d.weekly_budget || 0) + Number(e.currentTarget.dataset.obBudget))); render(); });
  on("[data-ob-nav]", "click", (e) => { e.preventDefault(); guard(() => onboardNav(e.currentTarget.dataset.obNav)); });
  const obform = document.getElementById("obform");
  if (obform) obform.onsubmit = (e) => { e.preventDefault(); guard(() => onboardNav("next")); };
  const acts = {
    "download-profile": () => downloadPrivate(`/api/user/${S.userId}/data.json`, "ziggy-profile.json", "application/json"),
    reopt: reoptimize, exec: reviewOrders, "confirm-exec": execute,
    "confirm-live-cart": addLiveItemToCart,
    "review-live-checkout": reviewLiveCheckout, "place-live-order": placeLiveOrder,
    "track-live-order": trackLiveOrder, "live-order-history": loadLiveOrderHistory,
    "refresh-live-cart": refreshLiveCart, "resolve-live-attempt": resolveLiveAttempt,
    "more-live-dishes": () => searchLiveDishes(S.liveBrowseMenu.search.query, true),
    "restore-live-menu": () => openLivePlace(S.liveBrowseMenu.restaurant.id, S.liveBrowseMenu.restaurant.name),
    "download-ics": () => downloadPrivate(`/api/user/${S.userId}/reminders.ics`, "ziggy-reminders.ics", "text/calendar"),
    "download-csv": () => downloadPrivate(`/api/receipts/${S.userId}/export.csv`, "ziggy-expenses.csv", "text/csv"),
    genrcpt: genReceipts, reload: retryLast, newweek: newWeek,
    "start-onboard": async () => startOnboard(), notify: toggleAlerts, "notify-yes": toggleAlerts,
    "notify-no": async () => { store.set("ziggy.asked-reminders", "1"); S.ask = false; render(); },
    "oq-save": async () => {
      const vals = n => [...document.querySelectorAll(`input[name="${n}"]:checked`)].map(i => i.value);
      S.orderQueue = await api(`/api/plan/${S.planId}/order-queue`, "POST", { meals: vals("oq-meal"), days: vals("oq-day").map(Number) });
      toast(`${S.orderQueue.queued} meal${S.orderQueue.queued === 1 ? "" : "s"} on your order list`); render(); },
    "live-menus": () => liveAction("live-menus"),
    "connect-open": async () => { S.connectSheet = true; S.addrSheet = false; render(); },
    "swiggy-connect": connectSwiggy,
    "swiggy-discover": async () => { S.swiggy = await api(`/api/user/${S.userId}/swiggy/discover`, "POST", {}); toast("Tool list refreshed"); render(); },
    "addr-open": openAddresses,
    "swiggy-refresh-addresses": refreshSwiggyAddresses,
    "close-live-browse": async () => { S.liveBrowseMenu = null; render(); },
    "clear-results": async () => { S.liveResults = null; render(); },
    "swiggy-disconnect": disconnectSwiggy,
    "rules-open": async () => { await goTab("more", "connection"); document.getElementById("rules")?.scrollIntoView?.({ block: "start" }); },
    "signin-open": async () => { S.signin = true; S.connectSheet = false; S.error = null; if (S.view) { S.welcome = true; } render(); document.getElementById("si-login")?.focus(); },
    "signin-close": async () => { S.signin = false; S.error = null; if (S.view) S.welcome = false; render(); },
    "sign-out": signOut,
    "rotate-recovery": rotateRecoveryCode,
    "copy-recovery": async () => { await navigator.clipboard.writeText(`${S.userId}.${keys.get(S.userId)}`); toast("Recovery code copied"); },
    "cancel-move": async () => { S.moving = null; render(); },
    "hh-edit-cancel": async () => { S.hhEdit = null; render(); },
    "hh-leave": () => householdCall("/leave", "POST", {}, "Stopped sharing. Your plan uses your own rules again."),
    "swap-cancel": async () => { S.swapPick = null; render(); },
    "copy-groceries": async () => {
      const text = groceryText();
      try { await navigator.clipboard.writeText(text); toast("Grocery list copied. Paste it into Instamart or a note."); }
      catch { throw new Error("Couldn't copy here. Select the list and copy it instead"); }
    },
  };
  on("[data-act]", "click", (e) => { e.preventDefault(); const a = e.currentTarget.dataset.act, f = acts[a]; if (f) guard(f, ACT_LABELS[a]); });
  const deleteForm = document.getElementById("delete-profile");
  if (deleteForm) deleteForm.onsubmit = (e) => { e.preventDefault(); guard(() => deleteProfile(deleteForm)); };
  on("[data-swaddr]", "click", (e) => { const addressId = e.currentTarget.dataset.swaddr; guard(() => chooseAddress(addressId), "Choose delivery address"); });
  on("[data-live-fav]", "click", (e) => { const t = e.currentTarget; guard(() => toggleLiveFavourite(t.dataset.liveFav, t.dataset.liveName), `Save “${t.dataset.liveName}”`); });
  on("[data-live-place]", "click", (e) => { const t = e.currentTarget; guard(() => liveAction("live-place", t.dataset.livePlace, t.dataset.liveName, t.dataset.liveDish), `Open the menu for “${t.dataset.liveName}”`); });
  on("[data-live-item]", "click", (e) => { const t = e.currentTarget; guard(() => reviewLiveItem(t.dataset.liveItem, t.dataset.liveItemName), `Review “${t.dataset.liveItemName}”`); });
  on("[data-live-track]", "click", (e) => { const id = e.currentTarget.dataset.liveTrack; guard(() => trackLiveOrder(id), "Track order"); });
  const liveSearch = document.getElementById("live-search");
  if (liveSearch) liveSearch.onsubmit = (e) => { e.preventDefault(); const q = document.getElementById("live-query").value; guard(() => searchLivePlaces(q), `Search Swiggy for “${q}”`); };
  const dishSearch = document.getElementById("dish-search");
  if (dishSearch) dishSearch.onsubmit = (e) => { e.preventDefault(); const q = document.getElementById("dish-query").value; guard(() => searchLiveDishes(q), `Find dishes for “${q}”`); };
  on("[data-connect-order]", "click", (e) => { S.pendingResume = { kind: "order-meal", args: [Number(e.currentTarget.dataset.connectOrder)] }; S.connectSheet = true; render(); });
  on("[data-cart]", "click", (e) => guard(() => liveAction("order-meal", Number(e.currentTarget.dataset.cart)), "Add to Swiggy cart"));
  on("[data-rm-device]", "click", (e) => guard(() => removeDevice(e.currentTarget.dataset.rmDevice)));
  const signin = document.getElementById("signin");
  if (signin) signin.onsubmit = (e) => { e.preventDefault(); guard(() => signIn(signin)); };
  const account = document.getElementById("account");
  if (account) account.onsubmit = (e) => { e.preventDefault(); guard(saveAccount); };
  const recover = document.getElementById("recover");
  if (recover) recover.onsubmit = (e) => { e.preventDefault(); guard(() => useRecoveryCode(document.getElementById("rcode").value)); };
  const preferences = document.getElementById("preferences");
  if (preferences) preferences.onsubmit = (e) => { e.preventDefault(); guard(() => saveSettings(preferences), "Save settings"); };
  if (S.orderReview || S.liveOrderReview || S.checkoutReview) document.querySelector("[role=dialog] [autofocus]")?.focus();
}
function onMealTap(sid) {
  sid = Number(sid);
  if (S.moving) {
    if (S.moving === sid) { S.moving = null; render(); return; }
    guard(() => swapMeals(S.moving, sid)); return;
  }
  const cell = cellOf(sid);
  if (!cell || cell.status === "past" || ["ordered", "confirmed"].includes(cell.status)) {
    if (cell && ["ordered", "confirmed"].includes(cell.status)) toast("Already recorded. Rate it on Today.");
    else if (cell?.status === "past") guard(() => confirmMeal(sid), "Mark as had");
    return;
  }
  guard(() => openSheet(sid));
}
async function goWeekView(view) {
  S.weekView = view; S.tab = "week";
  if (view === "orders") {
    S.exec = await api(`/api/plan/${S.planId}/orders`);
    S.orderQueue = await api(`/api/plan/${S.planId}/order-queue`).catch(() => null);
  }
  render();
  if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
}
async function goTab(tab, sub = null) {
  tab = TAB_ALIAS[tab] || tab;
  if (tab === "more" && (sub === "orders" || sub === "cooking")) { tab = "week"; S.weekView = sub === "orders" ? "orders" : "groceries"; sub = null; }
  if (tab === "week" && S.tab !== "week") S.weekView = null;
  S.tab = tab; S.sheet = null;
  if (tab === "more") S.more = sub;
  if (tab === "saved") await loadSaved();
  if (S.tab === "week" && S.weekView === "orders") await goWeekView("orders");
  if (S.more === "receipts" || S.more === "recap") {
    [S.recap, S.receipts] = await Promise.all([api(`/api/plan/${S.planId}/recap`), api(`/api/receipts/${S.userId}`)]);
  }
  if (S.more === "calendar") S.calendar = await api(`/api/user/${S.userId}/calendar`);
  if (S.more === "connection") {
    [S.swiggy, S.swRules] = await Promise.all([api(`/api/user/${S.userId}/swiggy`),
      api(`/api/user/${S.userId}/swiggy/rules`).catch(() => ({ rules: [], issues: [] }))]);
  }
  if (S.more === "profiles" || (tab === "more" && !sub && keys.get(S.userId) && !S.account)) S.account = keys.get(S.userId) ? await api(`/api/user/${S.userId}/account`).catch(() => null) : null;
  render();
  if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
}

const anyDialog = () => S.orderReview || S.liveOrderReview || S.checkoutReview || S.sheet || S.addrSheet || S.connectSheet || S.liveBrowseMenu;
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && (anyDialog() || S.moving)) {
    if (S.liveOrderReview) S.liveOrderReview = null;
    else if (S.checkoutReview) S.checkoutReview = null;
    else { S.orderReview = null; S.sheet = null; S.moving = null; S.addrSheet = false; S.connectSheet = false; S.liveBrowseMenu = null; }
    render();
  }
  if (e.key === "Tab" && anyDialog()) {
    const dialogs = [...document.querySelectorAll("[role=dialog]")];
    const top = dialogs.at(-1);
    if (!top) return;
    const items = [...top.querySelectorAll("button:not([disabled]), a[href], input, select")];
    if (!items.length) return;
    const first = items[0], last = items.at(-1);
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
// The server is up but can't serve (database unreachable, overloaded, timed out): a clear
// screen with Try again, never an endless "Loading…" or a bare status.
function serverDown(e) {
  return ["database_unavailable", "server_timeout"].includes(e?.code) || (e?.status >= 500);
}
function bootFailed(e) {
  const offline = (typeof navigator !== "undefined" && navigator.onLine === false) || /fetch|network/i.test(e?.message || "");
  document.getElementById("app").innerHTML = offline
    ? `<main class="welcome">${mascot()}<h1 class="hero-h">You're offline.</h1>
       <p class="sub">Plans and budgets are always live, so Ziggy needs a connection. It reloads by itself when you're back online.</p>
       <button class="primary big" id="retry-boot">Try again</button></main>`
    : serverDown(e)
    ? `<main class="welcome" role="alert">${mascot()}<h1 class="hero-h">Ziggy can't start right now.</h1>
       <p class="sub">${esc(e.message)}</p>
       <button class="primary big" id="retry-boot">Try again</button></main>`
    : `<div class="boot">Failed to start: ${esc(e.message)}</div>`;
  if (offline && typeof window !== "undefined") window.addEventListener("online", () => location.reload(), { once: true });
  document.getElementById("retry-boot")?.addEventListener("click", () => location.reload());
}
if (typeof window !== "undefined" && window.addEventListener) {
  window.addEventListener("offline", () => { S.offline = true; if (S.view) render(); });
  window.addEventListener("online", () => { S.offline = false; if (S.view) render(); });
}
if (typeof matchMedia === "function") matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => applyLook());
// A Swiggy photo that fails to load becomes the dish's category icon.
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img && img.tagName === "IMG" && img.classList?.contains("dphoto")) {
    const lg = img.classList.contains("lg");
    BROKEN_PHOTOS.add(img.getAttribute("src"));
    img.outerHTML = dishIcon(img.dataset.dish || "", { lg });
  }
}, true);
if (typeof navigator !== "undefined" && "serviceWorker" in navigator)
  navigator.serviceWorker.register("/sw.js").catch(() => {});
boot().catch(bootFailed);
