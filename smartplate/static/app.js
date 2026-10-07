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
  sheet: null, moving: null, onboard: null, places: null, calendar: null, welcome: false, signin: false, account: null,
};
const MEALS = ["breakfast", "lunch", "dinner"];
const MEAL_ICON = { breakfast: "☀", lunch: "◐", dinner: "☾" };
const WX_ICON = { clear: "☀", rain: "🌧", hot: "🔥", storm: "⛈" };
const rupee = (n) => "₹" + (Math.round((n || 0) * 100) / 100).toLocaleString("en-IN");
const rupee0 = (n) => "₹" + Math.round(n || 0).toLocaleString("en-IN");
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cap1 = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);
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
    const err = new Error(data.message || data.error || r.statusText);
    err.code = data.code;
    throw err;
  }
  return r.json();
}
function toast(msg) {
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
async function guard(fn) {
  if (S.busy) return;
  setBusy(true); S.error = null; S.errorCode = null;
  try { await fn(); }
  catch (e) { S.error = e.message || String(e); S.errorCode = e.code || null; render(); }
  finally { setBusy(false); }
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
    if (!/private|not found/i.test(e.message)) throw e;
    keys.drop(S.userId); store.del("smartplate.user"); S.userId = null; S.view = null;
    S.users = mergeUsers(await api("/api/users")); S.welcome = true; render(); return;
  }
  const params = typeof location !== "undefined" ? new URLSearchParams(location.search) : new Map();
  const wanted = params.get("tab");
  if (["today", "week", "places", "more"].includes(wanted)) S.tab = wanted;
  if (params.get("swiggy") || params.get("swiggy_error")) {             // back from Swiggy sign-in
    S.tab = "more"; S.more = "connection"; S.swiggy = await api(`/api/user/${S.userId}/swiggy`);
    // Fixed messages only: the URL carries a code, never text to display (a crafted link can't fake warnings).
    const SWIGGY_ERRORS = { denied: "Swiggy sign-in was cancelled.",
      expired: "That Swiggy sign-in link expired or was already used. Tap Connect Swiggy again.",
      other_browser: "That Swiggy sign-in was started in a different browser. Tap Connect Swiggy again on this device.",
      failed: "Swiggy sign-in didn't complete. Tap Connect Swiggy to try again." };
    if (params.get("swiggy_error")) S.error = SWIGGY_ERRORS[params.get("swiggy_error")] || SWIGGY_ERRORS.failed;
    else toast("Connected to Swiggy");
    if (typeof history !== "undefined") history.replaceState(null, "", "/");
  }
  render();
  scheduleAlerts().catch(() => {});
  syncPush().catch(() => {});
}
async function loadOrCreatePlan() {
  S.view = await api(`/api/user/${S.userId}/plan`);
  S.planId = S.view.plan.id;
  S.exec = await api(`/api/plan/${S.planId}/orders`);
  S.swiggy = await api(`/api/user/${S.userId}/swiggy`).catch(() => null);
  S.liveFavourites = swiggyReady() ? await api(`/api/user/${S.userId}/swiggy/favourites`).catch(() => null) : null;
  if (swiggyReady()) await refreshLiveCart(false);
}
function adoptView(view) { S.view = view; S.planId = view.plan.id; scheduleAlerts().catch(() => {}); }

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
    if (!/private|not found/i.test(e.message)) throw e;
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
  S.tab = "more"; S.more = "orders";
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

/* ---------------------------------------------------------------- render */
function render() {
  const app = document.getElementById("app");
  if (S.onboard) { app.innerHTML = onboardingScreen(); wire(); return; }
  if (S.welcome || !S.view) { app.innerHTML = welcomeScreen(); wire(); return; }
  app.innerHTML = topbar() + `<main class="wrap ${S.tab === "more" ? "wide" : ""}" id="main">${errbar() + tabBody()}</main>`
    + navBar() + (S.sheet ? sheetDialog() : "") + (S.drawer ? drawer() : "")
    + (S.orderReview ? orderReviewDialog() : "") + (S.cartReview ? cartReviewDialog() : "")
    + (S.liveOrderReview ? liveOrderReviewDialog() : "")
    + (S.checkoutReview ? checkoutReviewDialog() : "");
  wire();
}

function welcomeScreen() {
  const samples = (S.users || []).filter(u => u.setup_done);
  return `<main class="welcome">
    <div class="brand big">Smart<em>Plate</em></div>
    <h1 class="hero">Meals from the places you like.</h1>
    <p class="lede">Create your private profile and connect Swiggy to find real restaurants and dishes for your saved delivery address.</p>
    <ul class="promise">
      <li><b>Remember your favourites.</b> Search local restaurants and browse their current Swiggy menus after connecting.</li>
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
  return `<div class="errbar" role="alert"><span>⚠ ${esc(S.error)}</span>
    ${SWIGGY_RECONNECT.has(S.errorCode) ? `<button class="retry" data-act="swiggy-connect">Connect Swiggy</button>`
      : retry ? `<button class="retry ghost" data-act="reload">Retry</button>` : ""}
    <button class="x" data-close-err="1" title="Dismiss" aria-label="Dismiss">✕</button></div>`;
}

function topbar() {
  const b = S.view.budget || {};
  const left = (b.budget || 0) - (b.spend || 0);
  return `<header class="topbar"><div class="inner">
    <div class="brand">Smart<em>Plate</em></div>
    <span class="spacer"></span>
    ${swiggyReady() && ["today", "places"].includes(S.tab) ? `<span class="pill accent">Live Swiggy menu</span>` : `<span class="pill ${left < 0 ? "bad" : "ok"}" title="Sample weekly plan estimate">Plan estimate · ${rupee0(Math.abs(left))} ${left < 0 ? "over" : "left"}</span>`}
    <button class="avatar" data-go="more:profiles" title="Profile: ${esc(S.view.user?.name)}" aria-label="Profile and settings">${esc((S.view.user?.name || "?").slice(0, 1))}</button>
  </div></header>`;
}

function navBar() {
  const T = [["today", "Today", "◉"], ["week", "Week", "▦"], ["places", "Places", "★"], ["more", "More", "⋯"]];
  return `<nav class="nav" aria-label="Main">${T.map(([k, l, i]) =>
    `<button class="navbtn ${S.tab === k ? "on" : ""}" data-tab="${k}" ${S.tab === k ? 'aria-current="page"' : ""}><span aria-hidden="true">${i}</span>${l}</button>`).join("")}</nav>`;
}

function tabBody() {
  if (S.tab === "today") return todayScreen();
  if (S.tab === "week") return weekScreen();
  if (S.tab === "places") return placesScreen();
  return moreScreen();
}

/* ================================================================ TODAY */
function todayScreen() {
  const v = S.view, nu = v.next_up;
  const hello = greeting();
  if (swiggyReady()) return `<p class="fine">Signed in to Swiggy · ${esc(S.swiggy.address.label)}</p>${livePlacesScreen()}
    <details><summary>Open the sample weekly planner</summary><p class="fine">This planner currently uses demonstration Chennai dishes and prices. It is separate from the live Swiggy ordering flow.</p>
      ${budgetCard()}${nu ? nextUpCard(nu) : ""}${todayRest(nu)}</details>`;
  const sample = v.user?.prefs?.sample ? `<div class="coldstart"><span class="i">Sample</span><span>This profile uses demonstration preferences and sample menu data. <a href="#" data-act="start-onboard">Set up your own</a>.</span></div>`
    : `<div class="coldstart"><span class="i">Demo plan</span><span>These weekly restaurant dishes and prices are sample data. <a href="#" data-go="more:connection">Connect Swiggy</a> to order from real restaurants near your saved address.</span></div>`;
  return `${sample}<h1 class="greet">${hello}${v.user?.name && v.user.name !== "Me" && !v.user?.prefs?.sample ? ", " + esc(v.user.name.split(" ")[0]) : ""}</h1>
    ${budgetCard()}
    ${nu ? nextUpCard(nu) : `<div class="card empty">Nothing left to plan this week. <button data-act="newweek">Plan next week</button></div>`}
    ${todayRest(nu)}
    ${reminderRow()}
    ${headsUp()}
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
    <div class="brow"><div><div class="lbl">Sample plan estimate</div>
      <div class="big ${left < 0 ? "neg" : ""}">${rupee0(Math.abs(left))} <small>${left < 0 ? "over budget" : "left of " + rupee0(cap)}</small></div></div>
      <div class="seg" role="group" aria-label="How tight is money this week?">${Object.entries(modes).map(([k, l]) =>
        `<button class="${S.view.plan.mode === k ? "on" : ""}" data-mode="${k}" title="${esc(S.meta.mode_outcomes?.[k] || "")}">${esc(l)}</button>`).join("")}</div></div>
    <div class="bar"><i style="width:${pct}%;background:${left < 0 ? "var(--red)" : "var(--accent)"}"></i></div>
    <div class="fine">${b.prorated ? `Covers the rest of this week (${rupee0(cap)} of your ${rupee0(b.weekly_budget)} weekly budget). ` : ""}${b.daily_cap ? `Daily planning limit ${rupee0(b.daily_cap)}. ` : ""}Sample prices include estimated fees. Live prices and actual purchases are reviewed separately.</div>
  </section>`;
}
function nextUpCard(nu) {
  const c = nu.cell, when = `${nu.when} · ${cap1(nu.meal)}`;
  if (c.status === "confirmed" || c.status === "ordered") {
    return `<section class="hero-card done"><div class="eyebrow">${esc(when)}</div>
      <h2>${esc(c.item)}</h2><p class="sub">${c.status === "ordered" ? "Ordered (simulation)" : "You had this"} · ${rupee0(c.cost)}</p>
      ${rateRow(c)}</section>`;
  }
  const isCook = c.kind === "cook";
  const order = c.order || {};
  return `<section class="hero-card ${esc(c.kind)}" aria-label="Next meal">
    <div class="eyebrow">${esc(when)}${c.pinned ? " · your pick" : ""}${c.usual ? " · usual place" : ""}</div>
    <h2>${esc(c.item)}</h2>
    <p class="sub">${isCook ? "Home kitchen" : esc(c.restaurant)}${c.rating && !isCook ? " · " + Number(c.rating).toFixed(1) + "★" : ""} · <b>${rupee0(c.cost)}</b></p>
    ${!isCook && order.order_at ? `<div class="when"><span class="clock">⏱</span><div><b>Order by ${esc(order.order_at)}</b><span>${esc(order.why)}</span></div></div>` : ""}
    ${(c.reasons || []).length ? `<p class="why">${esc(c.reasons.find(r => !/^Ordered |from .* \(₹/.test(r)) || c.reasons[0])}</p>` : ""}
    <div class="actions">
      ${!isCook && swiggyReady() && cartEligible() ? `<button class="primary" data-cart="${c.session_id}">Review live Swiggy item</button>`
        : !isCook && c.handoff_url ? `<a class="btn primary" href="${esc(c.handoff_url)}" target="_blank" rel="noopener" data-handoff="${c.session_id}">Order on Swiggy ↗</a>` : ""}
      <button data-sheet="${c.session_id}">Change</button>
      <button class="ghost" data-confirm="${c.session_id}">${isCook ? "I cooked it ✓" : "I had it ✓"}</button>
    </div>
    ${!isCook && swiggyReady() && !cartEligible() ? `<p class="fine">SmartPlate cannot verify your ingredient or medical rules on Swiggy's menu. Check this dish directly in Swiggy before ordering.</p>` : ""}
    ${cartNote(c)}
    ${S.handedOff === c.session_id ? `<div class="consent">Placed it on Swiggy? Tap <b>I had it</b> so your budget stays accurate.</div>` : ""}
  </section>`;
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
  if (push === "on") btn = `<button class="ghost small on" data-act="notify">🔔 Reminders on for this device</button>`;
  else if (push === "ready") btn = `<button class="ghost small" data-act="notify">🔔 Remind me at order time</button>`;
  else if (push === "ios-install") btn = `<span class="fine">On iPhone, tap Share → Add to Home Screen, then open SmartPlate from there to get reminders.</span>`;
  else if (typeof Notification !== "undefined") {
    const on = store.get("smartplate.notify") === "1" && Notification.permission === "granted";
    btn = `<button class="ghost small" data-act="notify">${on ? "🔔 Browser alerts on" : "🔔 Alert me in this browser"}</button>`;
  }
  return `<section class="remind" aria-label="Reminders"><span class="fine">Never miss an order-by time:</span>
    ${btn}
    <button class="btn ghost small" data-act="download-ics">📅 Add to my calendar</button></section>`;
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
  const hs = S.view.heads_up || [];
  if (!hs.length) return "";
  return `<section class="heads"><h3 class="k">Heads-up</h3>${hs.map(h => `<div class="hu ${esc(h.level)}">
    <span class="ic" aria-hidden="true">${esc(h.icon)}</span><div><b>${esc(h.title)}</b><p>${esc(h.body)}</p>
    ${h.kind === "reconcile" ? `<button class="link" data-tab="week">Review past meals →</button>` : ""}
    ${h.kind === "budget" ? `<button class="link" data-go="more:settings">Adjust budget →</button>` : ""}</div></div>`).join("")}</section>`;
}
function learningLine() {
  const l = S.view.learning || {};
  const src = S.view.weather_source === "live" ? "Live weather: Open-Meteo (CC BY 4.0)." : "Sample weather (offline).";
  const n = (k, one, many) => `${k || 0} ${k === 1 ? one : many}`;
  return `<p class="fine center">Learning from ${n(l.favourites, "usual place", "usual places")} · ${n(l.ratings, "rating", "ratings")} · ${n(l.orders, "meal had", "meals had")}. ${src}</p>`;
}
function rateRow(c) {
  const g = c.rating_given;
  return `<div class="rate" role="group" aria-label="Rate this meal"><span>How was it?</span>
    <button class="${g === 1 ? "on" : ""}" data-rate="${c.session_id}:1" aria-pressed="${g === 1}">👍 Good</button>
    <button class="${g === -1 ? "on" : ""}" data-rate="${c.session_id}:-1" aria-pressed="${g === -1}">👎 Not again</button></div>`;
}

/* ================================================================ WEEK */
function weekScreen() {
  const v = S.view;
  const days = v.grid.map((d, i) => ({ d, i, ctx: (v.week_context || [])[i] || {} }));
  const gone = days.filter(({ d }) => Object.values(d.meals).every(m => m.status === "past" && m.kind === "past"));
  const shown = days.filter(x => !gone.includes(x));
  const moving = S.moving ? `<div class="moving" role="status">Moving a meal: tap the meal to swap it with. <button class="ghost" data-act="cancel-move">Cancel</button></div>` : "";
  return `<div class="whead"><h1 class="greet">Week of ${esc(fmtDate(v.plan.week_start))}</h1>
      <button class="ghost small" data-act="reopt" title="Recompute with your current rules">↻ Re-plan</button></div>
    ${moving}
    <p class="fine">Sample weekly planner: dishes, nutrition and prices come from the demo catalogue. Open Places for real restaurants and current Swiggy menus after connecting.</p>
    <p class="fine">Tap a meal to change it. Drag it onto another meal (or use <b>Move</b>) to swap them. The rest of the week re-balances each time.</p>
    ${gone.length ? `<p class="fine gone">${esc(gone.map(x => x.d.day).join(", "))}: before this plan started.</p>` : ""}
    <div class="days">${shown.map(({ d, i, ctx }) => dayRow(d, i, ctx)).join("")}</div>
    <div class="row gap"><button data-act="newweek">Plan next week</button>
      ${S.meta.swiggy_provider === "simulated" ? `<button class="ghost" data-act="exec" title="Try SmartPlate placing every delivery for you (simulation)">Simulate auto-ordering…</button>` : ""}</div>`;
}
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
function dayRow(day, i, ctx) {
  const wx = WX_ICON[ctx.weather] || "";
  const fest = ctx.festival ? `<span class="tag fest" title="${esc(ctx.festival_note || "")}">${esc(ctx.festival)}${ctx.festival_approx ? "*" : ""}</span>` : "";
  const fast = ctx.your_fast ? `<span class="tag fast">${esc(ctx.your_fast)} fast</span>` : "";
  const spend = Object.values(day.meals).reduce((s, m) => s + (["delivery", "cook"].includes(m.kind) && m.status !== "past" ? m.cost : 0), 0);
  const isToday = day.date && day.date === todayIso();
  return `<section class="day ${isToday ? "today" : ""}" aria-label="${esc(day.day)}">
    <header><b>${esc(day.day)}</b><span class="date">${esc(fmtDate(day.date))}</span>
      <span class="wx" title="${esc(ctx.weather_note || "")}${ctx.rain_prob ? " · rain " + Math.round(ctx.rain_prob) + "%" : ""}">${wx} ${ctx.temp_c ? Math.round(ctx.temp_c) + "°" : ""}</span>
      ${fest}${fast}<span class="spacer"></span><span class="dspend">${spend ? rupee0(spend) : ""}</span></header>
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
  const sub = m.kind === "delivery" ? `${esc(m.restaurant)}${m.order?.order_at && !locked && !past ? ` · order by ${esc(m.order.order_at)}` : ""}`
    : m.kind === "cook" ? (m.recipe_key ? "Home kitchen" : "From your fridge") : esc((m.reasons || [])[0] || "");
  return `<div class="meal ${esc(m.kind)} ${past ? "is-past" : ""} ${target ? "target" : ""} ${S.moving === m.session_id ? "lifted" : ""}"
      data-meal="${m.session_id}" ${movable ? 'draggable="true"' : ""} role="button" tabindex="0"
      aria-label="${esc(cap1(meal))}: ${esc(m.item)}${target ? ". Tap to swap here" : ""}">
    <span class="mi" aria-hidden="true">${MEAL_ICON[meal]}</span>
    <div class="mt"><div class="mname">${esc(m.item)} ${m.pinned ? '<span class="pin" title="Your pick">•</span>' : ""}${m.usual === false && m.kind === "delivery" ? '<span class="tag new">new</span>' : ""}${kindLabel ? `<span class="tag">${kindLabel}</span>` : ""}${status}</div>
      <div class="msub">${sub}</div></div>
    <span class="mcost">${["delivery", "cook"].includes(m.kind) && m.cost ? rupee0(m.cost) : ""}</span>
    ${past && ["delivery", "cook"].includes(m.kind) ? `<span class="pastbtns"><button class="small" data-confirm="${m.session_id}">I had it</button><button class="small ghost" data-sess="${m.session_id}:skipped">Skipped</button></span>` : ""}
  </div>`;
}

/* ---- the choose sheet: a capped shortlist instead of an endless menu ---- */
function sheetDialog() {
  const d = S.sheet.data;
  if (!d) return `<div class="modal-bg" data-close-sheet="1"><section class="sheet" role="dialog" aria-modal="true" aria-label="Loading options"><p class="fine">Loading your options…</p></section></div>`;
  const s = d.session, L = d.limits;
  const left = L.left_day != null ? Math.min(L.left_week, L.left_day) : L.left_week;
  const dishBtn = (x, place) => `<button class="dish ${x.fits ? "" : "over"} ${x.current ? "current" : ""}" data-pick="${x.item_id}" data-pick-name="${esc(x.name)}">
      <span class="dn">${esc(x.name)}${place ? `<small>${esc(place)}</small>` : ""}</span>
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
    <h3 class="k">${d.has_favourites ? "Your usual places" : "Good picks nearby"}</h3>
    ${usual || `<p class="fine">None of your usual places has a dish that fits. Try something new, or cook.</p>`}
    ${d.usual_more ? `<p class="fine">+${d.usual_more} more usual places. <a href="#" data-tab="places">Edit your list</a></p>` : ""}
    ${d.new.length ? `<h3 class="k">Something new</h3>${d.new.map(x => dishBtn(x, x.restaurant)).join("")}` : ""}
    ${d.cook.length ? `<h3 class="k">Cook at home</h3><div class="chips">${d.cook.map(c => `<button data-cook="${esc(c.recipe_key)}">${esc(c.name)} · ${rupee0(c.price)}</button>`).join("")}</div>` : ""}
    <div class="sheet-foot">
      <button class="ghost" data-choose-auto="${s.id}">Let SmartPlate choose</button>
      <button class="ghost" data-sess="${s.id}:skipped">Skip this meal</button>
      <button class="ghost" data-move="${s.id}">Move to another day…</button>
    </div>
    ${hidden ? `<p class="fine">Hidden: ${esc(hidden)}.</p>` : ""}
  </section></div>`;
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
  return `<h1 class="greet">Your usual places</h1>
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
  return `${estimated ? "≈" : ""}${rupee(price)}`;
}
function livePlacesScreen() {
  const fav = S.liveFavourites || [];
  const results = S.liveResults?.restaurants || [];
  const starred = new Set(fav.map(x => x.id));
  const place = (r) => `<div class="pcard"><button class="star" data-live-fav="${esc(r.id)}" data-live-name="${esc(r.name)}"
      aria-label="${starred.has(r.id) ? "Remove" : "Add"} ${esc(r.name)} ${starred.has(r.id) ? "from" : "to"} favourites">${starred.has(r.id) ? "★" : "☆"}</button>
      <div><b>${esc(r.name)}</b><div class="fine">${r.area ? esc(String(r.area)) + " · " : ""}${r.rating ? esc(String(r.rating)) + "★ · " : ""}${r.eta ? esc(String(r.eta)) : ""}</div>
      ${r.availability ? `<div class="fine">${esc(r.availability)}</div>` : ""}
      <button class="link" data-live-place="${esc(r.id)}" data-live-name="${esc(r.name)}">See current menu →</button></div></div>`;
  const menu = S.liveBrowseMenu;
  const rows = menu ? menu.items.map(i => `<div class="lmrow"><span>${i.veg === true ? "🟢 " : i.veg === false ? "🔴 " : ""}${esc(i.name)}
    ${i.in_stock === false || i.in_stock === 0 ? " · unavailable" : ""}${i.has_options ? " · options in Swiggy" : ""}</span>
    <span><b>${livePrice(i.price, i.price_estimated)}</b> <button class="small" data-live-item="${esc(i.id)}"
      data-live-item-name="${esc(i.name)}" ${i.in_stock === false || i.in_stock === 0 ? "disabled" : ""}>Review</button></span></div>`).join("") : "";
  return `<h1 class="greet">Order from your area</h1><p class="sub">Live Swiggy restaurants and menus for ${esc(S.swiggy.address.label)}. Choose a real item and review the cart before checkout.</p>
    <form id="live-search" class="row" style="gap:8px;margin:14px 0"><input id="live-query" aria-label="Restaurant or cuisine" placeholder="Restaurant or cuisine" maxlength="80" required style="flex:1;min-width:150px"><button class="primary">Search Swiggy</button></form>
    <div class="row"><button class="ghost small" data-act="live-order-history">Check recent Swiggy orders</button><button class="ghost small" data-act="refresh-live-cart">Refresh Swiggy cart</button></div>
    ${S.liveCartError ? `<p class="fine" role="alert">${esc(S.liveCartError)}</p>` : ""}
    ${!S.liveCart && S.liveCartEmpty ? `<p class="fine">Your Swiggy cart for ${esc(S.liveCartEmpty.address)} is empty${S.liveCartEmpty.address_verified ? "" : " (Swiggy didn't confirm the address; check in Swiggy before ordering)"}.</p>` : ""}
    ${S.liveOrderHistory ? `<section class="card"><h3>Recent Swiggy orders</h3><p class="fine">Orders at ${esc(S.liveOrderHistory.address)}. If a placement failed or timed out, check here and in Swiggy before trying again.</p>
      ${S.liveOrderHistory.attempts.some(a => a.state === "unknown" || a.state === "started") ? `<p class="fine">A SmartPlate order attempt has an uncertain result. Check Swiggy or contact support before ordering the same cart again.</p>
      <button class="small" data-act="resolve-live-attempt">I checked Swiggy: no order was placed</button>` : ""}
      ${S.liveOrderHistory.provider_orders.length ? S.liveOrderHistory.provider_orders.map(o => `<p><b>${esc(o.restaurant)}</b> · ${esc(o.item)} · ${esc(o.status)} · ${esc(o.total)} · ${esc(o.ordered_time)}<br><span class="fine">Order ${esc(o.order_id)}</span>${S.liveOrderHistory.attempts.some(a => a.order_id === o.order_id) ? ` <button class="small" data-live-track="${esc(o.order_id)}">Check delivery status</button>` : ""}</p>`).join("") : `<p class="fine">Swiggy returned no recent orders for this address.</p>`}</section>` : ""}
    ${S.placedOrder ? `<div class="consent cartnote"><b>Swiggy order confirmed:</b> ${esc(S.placedOrder.order_id)} · ${esc(S.placedOrder.item)} · ${rupee(S.placedOrder.to_pay)}.
      ${S.placedOrder.message ? `<p class="fine">${esc(S.placedOrder.message)}</p>` : ""}
      <button class="small" data-act="track-live-order">Check delivery status</button></div>` : ""}
    ${S.liveOrderStatus ? liveTrackingCard() : ""}
    ${S.liveCart ? `<div class="consent cartnote"><b>In your Swiggy cart:</b> ${esc(S.liveCart.item)} · ${esc(S.liveCart.restaurant)}.
      ${S.liveCart.to_pay == null ? "Check the final total in Swiggy." : `Current total ${rupee(S.liveCart.to_pay)}.`}
      ${S.swiggy.order_enabled && S.liveCart.orderable !== false ? `<button class="small primary" data-act="review-live-checkout">Review and place order</button>` : ""}
      ${S.liveCart.orderable === false ? `<p class="fine">This cart differs from the item reviewed here. Check or clear it in Swiggy before selecting another item.</p>` : ""}
      <a class="btn small" href="${esc(S.liveCart.checkout_url)}" target="_blank" rel="noopener">Open Swiggy checkout ↗</a></div>` : ""}
    <h3 class="k">Your live favourites</h3><div class="plist">${fav.length ? fav.map(place).join("") : `<p class="fine">Search and star real restaurants for this address.</p>`}</div>
    ${S.liveResults ? `<h3 class="k">Swiggy results for ${esc(S.liveResults.query)}</h3><div class="plist">${results.length ? results.map(place).join("") : `<p class="fine">No live restaurants returned for this address and search.</p>`}</div>` : ""}
    ${menu ? `<section class="card livemenu"><button class="close ghost" data-act="close-live-browse" aria-label="Close menu">✕</button>
      <p class="eyebrow">Swiggy menu · ${esc(menu.restaurant.name)} · ${esc(menu.address)}</p><h3>${menu.items.length} current dishes</h3>
      <form id="dish-search" class="row"><label>Search this restaurant<input id="dish-query" value="${esc(menu.search?.query || "")}" placeholder="Dish name" required minlength="2" maxlength="80"></label><button type="submit">Find dishes</button>${menu.search ? `<button type="button" class="ghost" data-act="restore-live-menu">Browse menu</button>` : ""}</form>
      <p class="fine">Checked ${esc(menu.fetched)}. ${menu.truncated ? "Swiggy shortened this browse list; search in Swiggy for more dishes. " : ""}
      ${menu.hidden_nonveg ? `${menu.hidden_nonveg} marked non-veg dishes hidden. ` : ""}Ingredient and allergy safety cannot be established from this menu. Final price, fees, options and availability may change.</p>
      <div class="lmlist">${rows || `<p class="fine">No dishes returned for this restaurant and search.</p>`}</div>${menu.search?.has_more ? `<button data-act="more-live-dishes">Load more matching dishes</button>` : ""}</section>` : ""}
    <details><summary>Sample planner (demo data)</summary><p class="fine">The weekly plan and sample Chennai list currently use seeded data. They do not determine which live Swiggy items are orderable.</p></details>`;
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
    S.liveCartError = null;
    if (show && !r.cart) toast("Your Swiggy cart is empty");
  } catch (error) { S.liveCart = null; S.liveCartEmpty = null; S.liveCartError = error.message; }
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
  S.liveBrowseMenu = await api(`/api/user/${S.userId}/swiggy/live-menu?restaurant_id=${encodeURIComponent(id)}&restaurant_name=${encodeURIComponent(name)}`);
  render();
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
    <div class="checkout-list"><div class="checkout-row"><div><strong>${esc(r.item)}</strong><span>${esc(r.restaurant)} · Delivering to ${esc(r.address)}</span></div>
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
  S.carts = { ...(S.carts || {}), [sid]: r }; render();
}
function cartReviewDialog() {
  const r = S.cartReview;
  return `<div class="modal-bg" data-close-cart-review="1"><section class="checkout" role="dialog" aria-modal="true" aria-labelledby="cart-review-title">
    <button class="close ghost" data-close-cart-review="1" aria-label="Close live item review">✕</button>
    <p class="eyebrow">Fresh Swiggy item · review before cart</p>
    <h2 class="sec" id="cart-review-title">Add this exact item?</h2>
    <div class="checkout-list"><div class="checkout-row"><div><strong>${esc(r.item)}</strong><span>${esc(r.restaurant)} · ${esc(r.address)}</span></div>
      <b>${r.menu_price == null ? "Price to verify" : rupee(r.menu_price)}</b></div></div>
    <p class="fine">Your plan estimated ${rupee(r.planned_cost)}. Swiggy's final payable total, including fees and taxes, appears after the cart is updated. Check ingredients and the final total in Swiggy before paying.</p>
    <div class="checkout-actions"><button class="ghost" data-close-cart-review="1">Cancel</button><button class="primary" data-act="confirm-cart" autofocus>Add to Swiggy cart</button></div>
  </section></div>`;
}
function cartNote(c) {
  const r = (S.carts || {})[c.session_id];
  if (!r) return "";
  const diff = r.over_plan == null ? "" : r.over_plan > 0 ? ` · ${rupee0(r.over_plan)} more than planned` : " · within plan";
  return `<div class="consent cartnote"><b>In your Swiggy cart:</b> ${esc(r.item)} · ${esc(r.restaurant)}.
    ${r.to_pay != null ? `To pay <b>${rupee0(r.to_pay)}</b>${diff}.` : "Open Swiggy to see the total."}
    <a class="btn small primary" href="${esc(r.checkout_url)}" target="_blank" rel="noopener" data-handoff="${c.session_id}">Open Swiggy to review and pay ↗</a></div>`;
}

/* ================================================================ MORE */
function moreScreen() {
  const items = [["settings", "Settings", "Budget, meals, diet, allergies, goal"], ["calendar", "Coming up", "Holidays, festivals, your fasts"],
    ["insights", "Nutrition & insights", "Daily averages vs targets, weather"], ["orders", "Auto-ordering (simulation)", "Try SmartPlate placing orders with a spend limit"],
    ["cooking", "Cooking & groceries", "Recipes and one grocery list for cook days"], ["receipts", "Expenses", "What you spent; CSV export"],
    ["community", "Community weeks", "Plans others shared"], ["connection", "Swiggy connection", "What's live and what's not"],
    ["profiles", "Profiles", "Switch or add a profile"]];
  if (!S.more) {
    return `<h1 class="greet">More</h1><div class="mlist">${items.map(([k, t, d]) =>
      `<button class="mitem" data-go="more:${k}"><b>${t}</b><span>${d}</span></button>`).join("")}</div>`;
  }
  const back = `<button class="ghost small back" data-go="more:">← More</button>`;
  const body = { settings: settingsPanel, calendar: calendarPanel, insights, orders: ordersPanel, cooking: cookingPanel,
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

/* ---- legacy detail drawer (reasons + skip/snooze), reachable from insights ---- */
function drawer() {
  const [di, meal] = S.drawer.split(":");
  const m = S.view.grid[Number(di)]?.meals[meal];
  if (!m) { S.drawer = null; return ""; }
  const reasons = (m.reasons || []).map(r => `<li>${esc(r)}</li>`).join("") || "<li>No reasons recorded.</li>";
  return `<div class="drawer-bg" data-close="1"><div class="drawer">
    <button class="close ghost" data-close="1" aria-label="Close">✕</button>
    <h3 class="k">${S.view.grid[Number(di)].day} · ${meal}</h3>
    <h2 class="sec">${esc(m.item)}</h2>
    <h3 class="k" style="margin-top:18px">Why SmartPlate chose this</h3>
    <ul class="reasonlist">${reasons}</ul></div></div>`;
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
      ${v.household.merged_allergens.map(a => `<span class="tag warn">no ${esc(a)}</span>`).join("")}</div></div>` : "";
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
function ordersPanel() {
  if (S.meta.swiggy_provider !== "simulated") return `<h2 class="sec">Real Swiggy orders</h2>
    <p class="sub">The sample weekly planner cannot schedule or place real orders. Choose a restaurant and exact item for your saved address in Places, then review its live cart.</p>
    <button class="primary" data-go="places">Open live Places</button>`;
  const head = `<h2 class="sec">Auto-ordering (simulation)</h2>
    <div class="sub">This shows the future "SmartPlate orders for me" mode. You approve a maximum total. If a dish is unavailable, it is replaced only with one that passes your filters and costs no more. No restaurant receives these orders.</div>
    <div class="row" style="margin:12px 0"><button class="primary" data-act="exec">Review simulated orders</button>
      <button class="ghost" data-act="idem">Check: same order twice</button></div>`;
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
  const recipes = c.recipes.map(r => `<div class="card"><h3 class="k">${esc(r.session)} · ${rupee(r.cost)}</h3>
    <div style="font-weight:600;margin-bottom:6px">${esc(r.name)}</div>
    <ol style="margin-left:18px">${r.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol></div>`).join("") || `<div class="card empty">No cook days this week. Set how often you cook in Settings.</div>`;
  const basket = c.basket.items.map(b => `<tr><td>${esc(b.name)}</td><td>${esc(b.recipe)}</td><td>${rupee(b.price)}</td></tr>`).join("");
  return `<h2 class="sec">Cooking & groceries</h2><p class="sub">${esc(c.headline)}</p>
    <div class="row"><div style="flex:2;min-width:280px"><div class="grid-cards">${recipes}</div></div>
      <div style="flex:1;min-width:240px"><div class="card"><h3 class="k">Grocery list · ${rupee(c.basket.total)}</h3>
        <table><tr><th>item</th><th>for</th><th>₹</th></tr>${basket || `<tr><td class="empty">—</td></tr>`}</table></div></div>
    </div>`;
}

/* ---- community ---- */
function communityPanel() {
  const rows = S.community.map(t => `<div class="card"><div class="row" style="align-items:center">
    <div style="flex:1"><div style="font-weight:600">${esc(t.title)}</div>
      <div class="sub">by ${esc(t.author)} · ${esc(t.city)} · ${esc(t.mode)} · ${rupee(t.budget)}/wk · ${t.adopts} adopts</div></div>
    <button data-adopt="${t.id}">Adopt</button></div></div>`).join("") || `<div class="card empty">No templates yet.</div>`;
  return `<h2 class="sec">Community weeks</h2>
    <div class="sub">Sample weeks saved in this trial. “Adopt” records interest. It does not replace your plan.</div>
    <div class="row" style="margin-bottom:12px"><button class="primary" data-act="savetpl">Share my current week</button></div>
    <div class="grid-cards">${rows}</div>`;
}

/* ---- receipts ---- */
function receiptsPanel() {
  const r = S.receipts;
  const rows = r && r.rows.length ? r.rows.map(x => `<tr><td>${esc(x.iso_date)}</td>
    <td>${esc(x.note)}</td><td><span class="tag ${x.category === "business" ? "warn" : ""}">${esc(x.category)}</span></td>
    <td>${rupee(x.amount)}</td></tr>`).join("") : "";
  return `<h2 class="sec">Expenses</h2>
    <div class="sub">Meals you confirmed and simulated orders. These are not tax invoices. Check the business/personal suggestions before you use them.</div>
    <div class="row" style="margin-bottom:12px"><button class="primary" data-act="genrcpt">Update from this week</button>
      <button class="btn ghost" data-act="download-csv">Export CSV ↓</button></div>
    ${r ? `<div class="stats"><div class="stat"><div class="label">Total</div><div class="val">${rupee(r.total)}</div></div>
      <div class="stat"><div class="label">Business</div><div class="val">${rupee(r.business_total)}</div></div></div>
      <div class="card scroll-x"><table><tr><th>date</th><th>item</th><th>category</th><th>amount</th></tr>${rows || `<tr><td class="empty" colspan="4">No expenses yet. Confirm a meal with “I had it”.</td></tr>`}</table></div>` : `<div class="card empty">Loading…</div>`}`;
}

/* ---- settings: the same five answers as onboarding, editable ---- */
function settingsPanel() {
  const u = S.view.user, n = S.view.nutrition.daily_target, p = u.prefs || {};
  const meals = u.meals || MEALS;
  const body = p.body || {};
  const number = (key, label, value, min, max, step = 1, extra = "") => `<label>${label}<input name="${key}" type="number" inputmode="decimal" min="${min}" max="${max}" step="${step}" value="${value ?? ""}" ${extra}></label>`;
  const checks = (key, values, labels = {}) => values.map(v => `<label class="check"><input type="checkbox" name="${key}" value="${v}" ${(u[key] || []).includes(v) ? 'checked' : ''}>${esc(labels[v] || v.replace('_', ' '))}</label>`).join('');
  const mealChecks = MEALS.map(m => `<label class="check"><input type="checkbox" name="meals" value="${m}" ${meals.includes(m) ? "checked" : ""}>${cap1(m)}</label>`).join("");
  const opt = (name, cur, pairs) => `<select name="${name}">${pairs.map(([k, v]) => `<option value="${k}" ${cur === k ? "selected" : ""}>${v}</option>`).join("")}</select>`;
  return `<h2 class="sec">Settings</h2><p class="sub">Saving re-plans the meals that haven't happened yet. Meals you've ordered or had stay as they are.</p>
    <form id="preferences" class="card preferences">
      <div class="settings-grid"><label>Name<input name="name" maxlength="80" value="${esc(u.name)}" required></label>
      <label>Diet${opt("diet", u.diet, [['nonveg', 'Non-vegetarian'], ['veg', 'Vegetarian'], ['vegan', 'Vegan']])}</label>
      ${number('weekly_budget', 'Weekly food budget (₹)', u.weekly_budget, 100, 100000)}
      ${number('daily_cap', 'Daily limit (₹, optional)', p.daily_cap || "", 0, 20000, 1, 'placeholder="none"')}
      ${number('rating_floor', 'Minimum restaurant rating', u.rating_floor, 0, 5, 0.1)}
      <label>How often you cook${opt("cook", p.cook || (u.health_targets?.max_cook_per_week ? "sometimes" : "never"), [["never", "Never"], ["sometimes", "1–2 times a week"], ["often", "3–5 times a week"], ["most", "Most days"]])}</label>
      <label>Variety${opt("variety", u.nutrition_targets?.variety || "light", [["usual", "Just my usuals"], ["light", "Mostly usual, a little new"], ["mixed", "Half new"], ["adventurous", "Surprise me often"]])}</label>
      <label>Goal${opt("goal", p.goal || "none", [["none", "No goal"], ["protein", "More protein"], ["lose", "Lose weight gently"], ["maintain", "Maintain weight"], ["gain", "Build muscle"]])}</label></div>
      <fieldset><legend>Meals to plan</legend><div class="checks">${mealChecks}</div></fieldset>
      <fieldset><legend>Allergies (always excluded)</legend><div class="checks">${checks('allergens', ['peanut', 'dairy', 'gluten', 'egg', 'soy', 'shellfish', 'fish', 'sesame', 'tree_nut'])}</div></fieldset>
      <fieldset><legend>Medical filters</legend><div class="checks">${checks('medical', ['diabetes', 'hypertension', 'celiac'])}</div><p class="sub">Simple menu-label rules. They can't guarantee a restaurant dish is medically suitable or free of cross-contact.</p></fieldset>
      <fieldset><legend>Fasts you keep (optional)</legend><div class="checks">${checks('observances', ['navratri', 'ramadan', 'karva_chauth'], { navratri: "Navratri", ramadan: "Ramadan", karva_chauth: "Karva Chauth" })}</div><p class="sub">On those days only dinner is planned. We never assume this from your name or area.</p></fieldset>
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
function readSettings(form) {
  const f = new FormData(form);
  const body = { name: f.get("name"), diet: f.get("diet"), weekly_budget: Number(f.get("weekly_budget")),
    rating_floor: Number(f.get("rating_floor")), cook: f.get("cook"), variety: f.get("variety"), goal: f.get("goal"),
    meals: f.getAll("meals"), allergens: f.getAll("allergens"), medical: f.getAll("medical"), observances: f.getAll("observances") };
  const cap = Number(f.get("daily_cap"));
  body.daily_cap = cap > 0 ? cap : null;
  const w = f.get("weight_kg"), h = f.get("height_cm"), a = f.get("age");
  if (w && h && a) body.body = { weight_kg: Number(w), height_cm: Number(h), age: Number(a), sex: f.get("sex"), activity: f.get("activity") };
  return body;
}

function connectionPanel() {
  const sw = S.swiggy;
  const addr = sw && sw.connected ? (S.swAddrs ? `<div class="mlist">${S.swAddrs.map(a => `<button class="mitem ${sw.address?.id === a.id ? "on" : ""}" data-swaddr="${esc(a.id)}"><b>${esc(a.label)}</b><span>${esc(a.text)}</span></button>`).join("")}</div>`
      : sw.address ? `<p>Delivering to <b>${esc(sw.address.label)}</b> <button class="small ghost" data-act="swiggy-addresses">Change</button></p>`
      : `<p><button class="primary" data-act="swiggy-addresses">Choose delivery address</button></p><p class="fine">Needed for live menus and your cart.</p>`) : "";
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
      <p class="fine">Swiggy requires production access and an exact-match allowlisted HTTPS redirect. ${sw.callback_url ? `For this deployment, request <code>${esc(sw.callback_url)}</code> from Swiggy Builders Club. ` : ""}A Render URL is an HTTPS redirect; it still needs Swiggy approval. This connection has only been tested against a fake server.</p></div>`;
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
    body = `<h1>Which meals should we plan?</h1><p class="fine">Most people start with just dinner, or lunch and dinner.</p>
      <div class="chips">${MEALS.map(m => chip("meals", m, `${MEAL_ICON[m]} ${cap1(m)}`, d.meals.includes(m))).join("")}</div>
      <h3 class="k">How often do you cook?</h3>
      <div class="chips">${[["never", "Never"], ["sometimes", "1–2× a week"], ["often", "3–5× a week"], ["most", "Most days"]].map(([k, l]) => chip("cook", k, l, d.cook === k, false)).join("")}</div>
      <p class="fine">Cook days are cheaper. We'll suggest simple recipes and one grocery list.</p>`;
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
  if (o.step === 1 && !d.meals.length) throw new Error("Pick at least one meal to plan");
  if (o.step === 2) {
    if (!d.weekly_budget) throw new Error("Enter a weekly budget greater than ₹0");
    if (d.weekly_budget < 100 || d.weekly_budget > 100000) throw new Error("Weekly budget must be between ₹100 and ₹1,00,000");
    if (d.daily_cap && (d.daily_cap < 50 || d.daily_cap > 20000)) throw new Error("Daily limit must be between ₹50 and ₹20,000");
    if (d.daily_cap && d.daily_cap > d.weekly_budget) throw new Error("Daily limit can't be more than your weekly budget");
  }
  if (o.step === OB_STEPS.length - 1) {
    const body = { ...d, name: d.name || "Me" };
    if (!body.body) delete body.body;
    if (!body.daily_cap) delete body.daily_cap;
    const view = await api("/api/profiles", "POST", body);
    if (view.access_key) keys.put(view.user.id, view.access_key, view.user.name);
    S.users = mergeUsers(await api("/api/users"));
    S.onboard = null; S.userId = view.user.id; store.set("smartplate.user", String(S.userId));
    adoptView(view); S.exec = await api(`/api/plan/${S.planId}/orders`);
    S.swiggy = await api(`/api/user/${S.userId}/swiggy`);
    S.tab = "more"; S.more = "connection"; toast("Profile saved. Connect Swiggy to use real restaurants."); render(); return;
  }
  o.step += 1; S.error = null; render();
  render();
}
function onboardChip(group, value, multi) {
  const d = S.onboard.d;
  readOnboardInputs();
  if (group === "budgetpick") { d.weekly_budget = Number(value); render(); return; }
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
  on("[data-go]", "click", (e) => { const [t, sub] = e.currentTarget.dataset.go.split(":"); guard(() => goTab(t, sub || null)); });
  on("[data-mode]", "click", (e) => guard(() => setMode(e.currentTarget.dataset.mode)));
  on("[data-user]", "click", (e) => guard(() => switchUser(e.currentTarget.dataset.user)));
  on("[data-sheet]", "click", (e) => guard(() => openSheet(e.currentTarget.dataset.sheet)));
  on("[data-confirm]", "click", (e) => { e.stopPropagation(); guard(() => confirmMeal(e.currentTarget.dataset.confirm)); });
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
  on("[data-close]", "click", (e) => { if (e.target.dataset.close) { S.drawer = null; render(); } });
  on("[data-close-err]", "click", () => { S.error = null; S.errorCode = null; render(); });
  on("[data-close-review]", "click", (e) => { if (e.target.dataset.closeReview) { S.orderReview = null; render(); } });
  on("[data-close-cart-review]", "click", (e) => { if (e.target.dataset.closeCartReview) { S.cartReview = null; render(); } });
  on("[data-close-live-review]", "click", (e) => { if (e.target.dataset.closeLiveReview) { S.liveOrderReview = null; render(); } });
  on("[data-close-checkout-review]", "click", (e) => { if (e.target.dataset.closeCheckoutReview) { S.checkoutReview = null; render(); } });
  on("[data-cmd]", "click", (e) => guard(() => quickCmd(e.currentTarget.dataset.cmd)));
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
    genrcpt: genReceipts, idem: idempotencyDemo, reload: reloadPlan, newweek: newWeek,
    "start-onboard": async () => startOnboard(), notify: toggleAlerts,
    "swiggy-connect": async () => { const r = await api(`/api/user/${S.userId}/swiggy/connect`, "POST", {}); location.href = r.authorize_url; },
    "swiggy-discover": async () => { S.swiggy = await api(`/api/user/${S.userId}/swiggy/discover`, "POST", {}); toast("Tool list refreshed"); render(); },
    "swiggy-addresses": async () => { S.swAddrs = await api(`/api/user/${S.userId}/swiggy/addresses`); render(); },
    "close-live-menu": async () => { S.liveMenu = null; render(); },
    "close-live-browse": async () => { S.liveBrowseMenu = null; render(); },
    "swiggy-disconnect": async () => { S.swiggy = await api(`/api/user/${S.userId}/swiggy/disconnect`, "POST", {}); S.liveMenu = null; S.carts = null; S.swAddrs = null; S.liveResults = null; S.liveFavourites = null; S.liveBrowseMenu = null; S.liveCart = null; S.liveCartEmpty = null; S.checkoutReview = null; S.placedOrder = null; S.liveOrderStatus = null; S.liveOrderHistory = null; S.liveCartError = null; toast("Disconnected from Swiggy"); render(); },
    "signin-open": async () => { S.signin = true; S.error = null; render(); document.getElementById("si-login")?.focus(); },
    "signin-close": async () => { S.signin = false; S.error = null; render(); },
    "sign-out": signOut,
    "rotate-recovery": rotateRecoveryCode,
    "copy-recovery": async () => { await navigator.clipboard.writeText(`${S.userId}.${keys.get(S.userId)}`); toast("Recovery code copied"); }, "cancel-move": async () => { S.moving = null; render(); },
  };
  on("[data-act]", "click", (e) => { e.preventDefault(); const f = acts[e.currentTarget.dataset.act]; if (f) guard(f); });
  const deleteForm = document.getElementById("delete-profile");
  if (deleteForm) deleteForm.onsubmit = (e) => { e.preventDefault(); guard(() => deleteProfile(deleteForm)); };
  if (S.orderReview || S.cartReview || S.liveOrderReview || S.checkoutReview) document.querySelector(".checkout [autofocus]")?.focus();
  if (S.sheet?.data) document.querySelector(".sheet .close")?.focus();
  on("[data-swaddr]", "click", (e) => guard(async () => {
    S.swiggy = await api(`/api/user/${S.userId}/swiggy/address`, "POST", { address_id: e.currentTarget.dataset.swaddr });
    S.swAddrs = null; S.liveMenu = null; S.liveResults = null; S.liveBrowseMenu = null; S.liveCart = null; S.liveCartError = null; S.checkoutReview = null; S.placedOrder = null; S.liveOrderStatus = null; S.liveOrderHistory = null;
    S.liveFavourites = await api(`/api/user/${S.userId}/swiggy/favourites`);
    await refreshLiveCart(false);
    toast("Delivery address saved"); render(); }));
  on("[data-live-menu]", "click", (e) => guard(() => openLiveMenu(e.currentTarget.dataset.liveMenu)));
  on("[data-live-fav]", "click", (e) => guard(() => toggleLiveFavourite(e.currentTarget.dataset.liveFav, e.currentTarget.dataset.liveName)));
  on("[data-live-place]", "click", (e) => guard(() => openLivePlace(e.currentTarget.dataset.livePlace, e.currentTarget.dataset.liveName)));
  on("[data-live-item]", "click", (e) => guard(() => reviewLiveItem(e.currentTarget.dataset.liveItem, e.currentTarget.dataset.liveItemName)));
  on("[data-live-track]", "click", (e) => guard(() => trackLiveOrder(e.currentTarget.dataset.liveTrack)));
  const liveSearch = document.getElementById("live-search");
  if (liveSearch) liveSearch.onsubmit = (e) => { e.preventDefault(); guard(() => searchLivePlaces(document.getElementById("live-query").value)); };
  const dishSearch = document.getElementById("dish-search");
  if (dishSearch) dishSearch.onsubmit = (e) => { e.preventDefault(); guard(() => searchLiveDishes(document.getElementById("dish-query").value)); };
  on("[data-cart]", "click", (e) => guard(() => reviewCart(Number(e.currentTarget.dataset.cart))));
  on("[data-rm-device]", "click", (e) => guard(() => removeDevice(e.currentTarget.dataset.rmDevice)));
  const signin = document.getElementById("signin");
  if (signin) signin.onsubmit = (e) => { e.preventDefault(); guard(() => signIn(signin)); };
  const account = document.getElementById("account");
  if (account) account.onsubmit = (e) => { e.preventDefault(); guard(saveAccount); };
  const recover = document.getElementById("recover");
  if (recover) recover.onsubmit = (e) => { e.preventDefault(); guard(() => useRecoveryCode(document.getElementById("rcode").value)); };
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
async function goTab(tab, sub = null) {
  S.tab = tab; S.sheet = null;
  if (tab === "more") S.more = sub;
  if (tab === "places" && swiggyReady() && !S.liveFavourites)
    S.liveFavourites = await api(`/api/user/${S.userId}/swiggy/favourites`);
  if (tab === "places" && swiggyReady()) await refreshLiveCart(false);
  if (tab === "places" && !swiggyReady() && !S.places)
    S.places = await api(`/api/restaurants?user_id=${S.userId}`);
  if (S.more === "community") S.community = await api("/api/community");
  if (S.more === "receipts") S.receipts = await api(`/api/receipts/${S.userId}`);
  if (S.more === "orders") S.exec = await api(`/api/plan/${S.planId}/orders`);
  if (S.more === "calendar") S.calendar = await api(`/api/user/${S.userId}/calendar`);
  if (S.more === "connection") S.swiggy = await api(`/api/user/${S.userId}/swiggy`);
  if (S.more === "profiles") S.account = keys.get(S.userId) ? await api(`/api/user/${S.userId}/account`) : null;
  render();
  if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && (S.drawer || S.orderReview || S.cartReview || S.liveOrderReview || S.checkoutReview || S.sheet || S.moving)) {
    S.drawer = null; S.orderReview = null; S.cartReview = null; S.liveOrderReview = null; S.checkoutReview = null; S.sheet = null; S.moving = null; render();
  }
  if (e.key === 'Tab' && (S.orderReview || S.cartReview || S.liveOrderReview || S.checkoutReview || S.sheet)) {
    const items = [...document.querySelectorAll('[role=dialog] button:not([disabled]), [role=dialog] a[href]')];
    if (!items.length) return;
    const first = items[0], last = items.at(-1);
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
async function idempotencyDemo() {
  const d = await api("/api/demo/idempotency", "POST", {});
  toast(d.same_order_id && d.second_was_deduped ? "Safe: the repeat returned the same order, no duplicate charge" : "Duplicate detected: check order history");
}

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
if (typeof navigator !== "undefined" && "serviceWorker" in navigator)
  navigator.serviceWorker.register("/sw.js").catch(() => {});
boot().catch(bootFailed);
