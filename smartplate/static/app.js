/* SmartPlate v1.1 — vanilla JS SPA (no build step, no framework dependency).
   Talks to the Flask JSON API and renders the weekly plan + all 17 gap features. */
"use strict";

const S = {
  meta: null, users: [], userId: 1, planId: null, view: null,
  tab: "week", exec: null, community: [], receipts: null, drawer: null,
  busy: false, error: null, hideCold: false,
};
const MEALS = ["breakfast", "lunch", "dinner"];
const rupee = (n) => "₹" + (Math.round((n || 0) * 100) / 100).toLocaleString("en-IN");
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, method = "GET", body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
}
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

/* ---- busy + error states (L1) ---- */
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
}
// Wrap every user-triggered action: show progress, surface errors instead of failing silently.
async function guard(fn) {
  setBusy(true); S.error = null;
  try { await fn(); }
  catch (e) { S.error = e.message || String(e); render(); }
  finally { setBusy(false); }
}
async function reloadPlan() { S.view = await api(`/api/plan/${S.planId}`); render(); }

/* ---------------------------------------------------------------- bootstrap */
async function boot() {
  ensureBusyBar();
  S.meta = await api("/api/meta");
  S.users = await api("/api/users");
  S.userId = S.users[0].id;
  await loadOrCreatePlan();
  render();
}
async function loadOrCreatePlan() {
  // The seed creates plan #1 for user #1; otherwise create on demand.
  if (S.userId === 1) {
    try { S.view = await api("/api/plan/1"); S.planId = 1; return; } catch (e) {}
  }
  S.view = await api("/api/plan", "POST", { user_id: S.userId });
  S.planId = S.view.plan.id;
}

/* ---------------------------------------------------------------- actions */
async function switchUser(id) {
  S.userId = Number(id); S.exec = null;
  S.view = await api("/api/plan", "POST", { user_id: S.userId });
  S.planId = S.view.plan.id; render();
}
async function setMode(mode) {
  S.view = await api(`/api/plan/${S.planId}/optimize`, "POST", { mode });
  toast(`Re-planned in ${S.view.plan.mode_label} mode`); render();
}
async function reoptimize() {
  S.view = await api(`/api/plan/${S.planId}/optimize`, "POST", {});
  toast("Re-optimised"); render();
}
async function runCommand() {
  const inp = document.getElementById("cmd");
  const text = inp.value.trim(); if (!text) return;
  const res = await api(`/api/plan/${S.planId}/command`, "POST", { text });
  S.view = res.plan; inp.value = "";
  toast(res.result.effect && res.result.effect !== "none" ? res.result.effect : "No actionable change");
  render();
}
async function execute() {
  S.exec = await api(`/api/plan/${S.planId}/execute`, "POST", {});
  S.view = await api(`/api/plan/${S.planId}`);
  S.tab = "orders";
  toast(`Placed ${S.exec.placed}/${S.exec.attempted} · ${S.exec.substituted} substituted`);
  render();
}
async function genReceipts() {
  await api(`/api/plan/${S.planId}/receipts`, "POST", {});
  S.receipts = await api(`/api/receipts/${S.userId}`);
  toast("Receipts generated"); render();
}
async function loadCommunity() { S.community = await api("/api/community"); render(); }
async function adopt(id) { const t = await api(`/api/community/${id}/adopt`, "POST", {}); toast(`Adopted “${t.title}” (${t.adopts} adopts)`); loadCommunity(); }
async function saveTemplate() {
  const title = prompt("Template title:", `${S.view.user.name}'s ${S.view.plan.mode_label} week`);
  if (!title) return;
  await api(`/api/plan/${S.planId}/save-template`, "POST", { title });
  toast("Saved to community"); if (S.tab === "community") loadCommunity();
}
async function setSession(sid, status) {
  await api(`/api/session/${sid}/status`, "POST", { status });
  S.view = await api(`/api/plan/${S.planId}/optimize`, "POST", {});
  S.drawer = null; toast(`Session ${status}`); render();
}

/* ---------------------------------------------------------------- render */
function render() {
  document.getElementById("app").innerHTML = topbar() + `<div class="wrap">${errbar() + tabs() + tabBody()}</div>` +
    (S.drawer ? drawer() : "");
  wire();
}

function errbar() {
  if (!S.error) return "";
  return `<div class="errbar"><span>⚠ ${esc(S.error)}</span>
    <button class="retry ghost" data-act="reload">Retry</button>
    <button class="x" data-close-err="1" title="Dismiss">✕</button></div>`;
}

function topbar() {
  const b = S.view.budget, m = S.meta;
  const modeOpts = Object.entries(m.modes).map(([k, v]) =>
    `<option value="${k}" ${S.view.plan.mode === k ? "selected" : ""}>${v}</option>`).join("");
  const userOpts = S.users.map(u => `<option value="${u.id}" ${u.id === S.userId ? "selected" : ""}>${esc(u.name)} · ${esc(u.city)}</option>`).join("");
  const hh = S.view.household;
  return `<div class="topbar"><div class="inner">
    <div class="brand">Smart<em>Plate</em><span class="v">v${m.version}</span></div>
    <span class="ctx"><span class="chip" title="planning area">📍 ${esc(S.view.user.city)}</span>${hh ? `<span class="chip" title="group plan">👥 ${esc(hh.name)}</span>` : ""}</span>
    <select id="userSel">${userOpts}</select>
    <select id="modeSel">${modeOpts}</select>
    <span class="spacer"></span>
    <span class="pill ok" title="${esc(m.note)}">brain: ${m.brain} · ${rupee(0)}/decision</span>
    <span class="pill accent">swiggy: ${m.swiggy_provider}</span>
    <span class="pill">${rupee(b.spend)} / ${rupee(b.budget)}</span>
  </div></div>`;
}

function tabs() {
  const T = [["week", "Week plan"], ["insights", "Insights"], ["orders", "Orders & substitution"],
    ["cooking", "Cooking coach"], ["community", "Community"], ["receipts", "Receipts"], ["features", "17 features"]];
  return `<div class="tabs">${T.map(([k, l]) =>
    `<button class="tab ${S.tab === k ? "active" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>`;
}

function tabBody() {
  switch (S.tab) {
    case "week": return coldStart() + statStrip() + controls() + weekGuide() + weekGrid() + approveBar();
    case "insights": return insights();
    case "orders": return ordersPanel();
    case "cooking": return cookingPanel();
    case "community": return communityPanel();
    case "receipts": return receiptsPanel();
    case "features": return featuresPanel();
  }
}

/* ---- week ---- */
function statStrip() {
  const v = S.view, b = v.budget, c = v.counts;
  const pct = Math.min(100, b.pct_used);
  return `<div class="stats">
    <div class="stat"><div class="label">Weekly spend</div><div class="val">${rupee(b.spend)}<small> / ${rupee(b.budget)}</small></div>
      <div class="bar"><i style="width:${pct}%;background:${b.over ? "var(--red)" : "var(--accent)"}"></i></div></div>
    <div class="stat"><div class="label">Sessions</div><div class="val">${c.delivery}<small> deliver</small> ${c.cook}<small> cook</small> ${c.skip}<small> skip</small></div></div>
    <div class="stat"><div class="label">Saved vs surge</div><div class="val">${rupee(v.surge_saved)}</div></div>
    <div class="stat"><div class="label">Carbon (week)</div><div class="val">${v.carbon.total_kg}<small> kg · ${v.carbon.band}</small></div></div>
    <div class="stat"><div class="label">Rating floor</div><div class="val">${v.user.rating_floor.toFixed(1)}<small> ★ min</small></div></div>
  </div>`;
}

function controls() {
  const u = S.view.user;
  const safety = [...u.allergens.map(a => `<span class="tag warn">no ${esc(a)}</span>`),
    ...u.medical.map(m => `<span class="tag warn">${esc(m)}-safe</span>`)].join("") || `<span class="tag">no hard exclusions</span>`;
  const reasons = S.view.summary_reasons.map(r => `<li>${esc(r)}</li>`).join("");
  return `<div class="card" style="margin-bottom:18px">
    <div class="row" style="align-items:center">
      <div style="flex:1;min-width:240px">
        <h3 class="k">This week · ${esc(S.view.plan.week_start)} · ${esc(S.view.plan.mode_label)} mode</h3>
        <div class="kv"><span class="tag">diet: ${esc(u.diet)}</span></div>
      </div>
      <input type="text" id="cmd" placeholder="Tell the agent… e.g. 'skip friday dinner'" style="flex:1;min-width:220px">
      <button data-act="cmd">Send</button>
      <button class="ghost" data-act="reopt">Re-optimise</button>
    </div>
    <div class="guarantee"><span class="lock">🔒 Hard rules locked:</span> ${safety}
      <span class="tag">≤ ${rupee(u.weekly_budget)} cap</span>
      <span style="color:var(--muted)">— the agent can't violate these, even in Survival mode.</span></div>
    <ul class="reasonlist">${reasons}</ul>
  </div>`;
}

/* sticky approve bar — the one primary action for the week view (V1/C1/T1) */
function approveBar() {
  const v = S.view, b = v.budget, c = v.counts;
  const left = b.budget - b.spend;
  const sessions = (c.delivery || 0) + (c.cook || 0) + (c.skip || 0);
  return `<div class="approvebar">
    <div class="sum">
      <div class="n">${sessions}<small> sessions</small></div>
      <div class="n">${rupee(b.spend)}<small> / ${rupee(b.budget)}</small></div>
      <div class="n" style="color:${b.over ? "var(--red)" : "var(--green)"}">${rupee(left)}<small> left</small></div>
      <span class="tag">${esc(v.plan.mode_label)} mode</span>
    </div>
    <div class="grow"></div>
    <div class="cta">
      <button class="primary" data-act="exec">Review &amp; place orders →</button>
      <span class="reassure"><span class="shield">🛡</span><span>Nothing is ordered until you approve — skip or swap any item, cancel anytime.</span></span>
    </div>
  </div>`;
}

/* cold-start honesty — don't imply learned precision before data exists (L2) */
function coldStart() {
  if (S.hideCold) return "";
  return `<div class="coldstart"><span class="i">ℹ Demo week</span>
    <span>Generated from a seeded sample Chennai catalog — not personal history yet. As you rate meals, the agent's taste model replaces these defaults.</span>
    <button class="x" data-close-cold="1" title="Dismiss">✕</button></div>`;
}

/* persistent legend + "cards are interactive" hint (V3, helps H1 discoverability) */
function weekGuide() {
  return `<div class="legend">
    <span class="grp"><span class="swatch" style="background:var(--accent)"></span>deliver</span>
    <span class="grp"><span class="swatch" style="background:var(--green)"></span>cook</span>
    <span class="grp"><span class="swatch" style="background:var(--muted-2)"></span>skip</span>
    <span style="color:var(--muted)">·</span>
    <span class="grp"><span class="b shift">⌚ shift</span>surge-dodge</span>
    <span class="grp"><span class="b sub">subbed</span>swapped ≥ floor</span>
    <span class="grp"><span class="b fridge">fridge</span>leftovers</span>
    <span class="grp"><span class="b fest">festival</span></span>
    <span class="hint">Tap any meal for the “why” &amp; swap options →</span>
  </div>`;
}

function weekGrid() {
  const ctx = S.view.week_context;
  return `<div class="week">${S.view.grid.map((day, i) => {
    const wc = ctx[i];
    const cls = wc.festival && wc.festival_effect === "feast" ? "feast"
      : (day.meals.breakfast && day.meals.breakfast.kind === "skip" && day.meals.lunch && day.meals.lunch.kind === "skip") ? "travel" : "";
    const wx = { clear: "☀", rain: "🌧", hot: "🔥", storm: "⛈" }[wc.weather] || "";
    const fest = wc.festival ? `<div class="cell-fest b ${wc.festival_effect === "fast" ? "fast" : "fest"}" style="margin-bottom:6px" title="${esc(wc.festival)}">${esc(wc.festival.slice(0, 14))}</div>` : "";
    return `<div class="daycol ${cls}">
      <div class="dhead"><span>${day.day}</span><span class="wx" title="${esc(wc.weather_note)}">${wx} ${Math.round(wc.temp_c)}°</span></div>
      ${fest}
      ${MEALS.map(meal => cellHTML(day.meals[meal], meal, i)).join("")}
    </div>`;
  }).join("")}</div>`;
}

function cellHTML(m, meal, dayIdx) {
  if (!m) return `<div class="cell skip"><div class="meal">${meal}</div><div class="item">—</div></div>`;
  const badges = [];
  if (m.time_shift) badges.push(`<span class="b shift" title="saved ${rupee(m.time_shift.saving)}">⌚ ${esc(m.time_shift.offpeak_hhmm)}</span>`);
  if (m.substituted) badges.push(`<span class="b sub">subbed</span>`);
  if (m.kind === "cook" && /Leftover/i.test(m.item)) badges.push(`<span class="b fridge">fridge</span>`);
  const cost = m.cost ? `<span class="cost">${rupee(m.cost)}</span>` : "";
  const meta = m.kind === "skip" ? "" : `<div class="meta">${esc(m.restaurant || "")} ${m.rating ? "· " + m.rating.toFixed(1) + "★" : ""} ${cost}</div>`;
  return `<div class="cell ${m.kind}" data-cell="${dayIdx}:${meal}">
    <div class="meal">${meal}</div>
    <div class="item">${esc(m.item)}</div>${meta}
    ${badges.length ? `<div class="badges">${badges.join("")}</div>` : ""}
  </div>`;
}

/* ---- drawer (decision detail + explainability) ---- */
function drawer() {
  const [di, meal] = S.drawer.split(":");
  const m = S.view.grid[Number(di)].meals[meal];
  if (!m) { S.drawer = null; return ""; }
  const reasons = (m.reasons || []).map(r => `<li>${esc(r)}</li>`).join("") || "<li>No reasons recorded.</li>";
  const n = m.nutrition || {};
  const nut = Object.keys(n).length ? `<h3 class="k" style="margin-top:18px">Nutrition</h3><div class="kv">
    ${["kcal", "protein_g", "carbs_g", "fat_g", "sugar_g"].filter(k => n[k] != null).map(k =>
      `<span class="tag">${k.replace("_g", "")}: ${n[k]}${k === "kcal" ? "" : "g"}</span>`).join("")}</div>` : "";
  const carbon = m.carbon_kg ? `<div class="kv"><span class="tag">carbon ≈ ${m.carbon_kg} kg CO₂e</span></div>` : "";
  const actions = `<div class="row" style="margin-top:20px">
      <button data-sess="${m.session_id}:skipped">Skip</button>
      <button data-sess="${m.session_id}:snoozed">Snooze</button>
      <button data-sess="${m.session_id}:cooked">I cooked this</button></div>`;
  return `<div class="drawer-bg" data-close="1"><div class="drawer">
    <button class="close ghost" data-close="1">✕</button>
    <h3 class="k">${S.view.grid[Number(di)].day} · ${meal}</h3>
    <h2 class="sec">${esc(m.item)}</h2>
    <div class="sub">${esc(m.restaurant || "")} ${m.rating ? "· " + m.rating.toFixed(1) + "★" : ""} ${m.cost ? "· " + rupee(m.cost) : ""}</div>
    <h3 class="k" style="margin-top:18px">Why the agent chose this</h3>
    <ul class="reasonlist">${reasons}</ul>
    ${nut}${carbon}${actions}
  </div></div>`;
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
  const wctx = v.week_context.map(d => `<tr><td>${d.day}</td><td>${esc(d.weather_note)}</td>
    <td>${d.festival ? esc(d.festival) + " (" + d.festival_effect + ")" : "—"}</td></tr>`).join("");
  const hh = v.household ? `<div class="card"><h3 class="k">Household · ${esc(v.household.name)}</h3>
    <div class="sub">Group meals satisfy every member's constraints (union).</div>
    <div class="kv"><span class="tag">members: ${v.household.members.map(esc).join(", ")}</span>
      <span class="tag">diet: ${esc(v.household.diet)}</span>
      ${v.household.merged_allergens.map(a => `<span class="tag warn">no ${esc(a)}</span>`).join("")}</div>
    <table style="margin-top:10px"><tr><th>member</th><th>cost share</th></tr>
      ${v.household.split.map(s => `<tr><td>${esc(s.member)}</td><td>${rupee(s.share)}</td></tr>`).join("")}</table></div>` : "";
  return `<div class="grid-cards" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
    <div class="card"><h3 class="k">Nutrition · daily average vs target</h3>
      ${macro("kcal", "")}${macro("protein_g", "g")}${macro("carbs_g", "g")}${macro("fat_g", "g")}${macro("sugar_g", "g")}
      <div class="callout">Soft constraints: the agent steers toward targets but never makes a plan infeasible over a macro.</div></div>
    <div class="card"><h3 class="k">Carbon · sustainability</h3>
      <div class="val serif" style="font-size:40px">${v.carbon.total_kg}<small style="font-size:16px;color:var(--muted)"> kg CO₂e</small></div>
      <div class="sub">Weekly estimate · band ${v.carbon.band}. Coarse priors — labelled as estimates, no greenwashing.</div>
      <div class="kv"><span class="tag">carbon preference: ${(v.user.carbon_pref * 100).toFixed(0)}%</span></div></div>
    <div class="card"><h3 class="k">Weather & festival context</h3>
      <table><tr><th>day</th><th>weather</th><th>festival</th></tr>${wctx}</table>
      <div class="sub" style="margin-top:8px">Rain → comfort bias + surge; heat → lighter meals; feast → festive picks; fast/travel → suspended.</div></div>
    ${hh}
  </div>`;
}

/* ---- orders / substitution / idempotency ---- */
function ordersPanel() {
  const head = `<h2 class="sec">Order execution</h2>
    <div class="sub">Saga: validate → cart → pay → place. Every write carries an idempotency key derived from
      (user, plan, session, trigger) — a retried network blip can never double-order. Menu-load failures (§1.2)
      trigger substitution above your rating floor, never below.</div>
    <div class="row" style="margin:12px 0"><button class="primary" data-act="exec">Place this week's orders</button>
      <button class="ghost" data-act="idem">Demo: place same order twice</button></div>`;
  if (!S.exec) return head + `<div class="card empty">No orders placed yet. Hit “Place orders”.</div>`;
  const e = S.exec;
  const rows = e.results.map(r => `<tr>
    <td>${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][r.day]} ${r.meal}</td>
    <td>${esc(r.item)}<br><span class="mono" style="font-size:11px;color:var(--muted)">${r.idempotency_key}</span></td>
    <td>${r.substituted ? `<span class="b sub">subbed</span> ${esc(r.substitution.from)} → ${esc(r.substitution.to)}` : (r.deduped ? "<span class='tag'>deduped</span>" : "ok")}</td>
    <td>${esc(r.provider_order_id || "—")}</td>
    <td>${esc(r.state)}</td></tr>`).join("");
  return head + `<div class="stats">
      <div class="stat"><div class="label">Placed</div><div class="val">${e.placed}<small>/${e.attempted}</small></div></div>
      <div class="stat"><div class="label">Substituted</div><div class="val">${e.substituted}</div></div>
      <div class="stat"><div class="label">Failed</div><div class="val">${e.failed}</div></div>
    </div>
    <div class="card"><table><tr><th>session</th><th>item / idempotency key</th><th>outcome</th><th>order id</th><th>state</th></tr>${rows}</table></div>`;
}

/* ---- cooking coach ---- */
function cookingPanel() {
  const c = S.view.coach;
  const recipes = c.recipes.map(r => `<div class="card"><h3 class="k">${esc(r.session)} · ${rupee(r.cost)}</h3>
    <div style="font-weight:600;margin-bottom:6px">${esc(r.name)}</div>
    <ol style="margin-left:18px">${r.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol></div>`).join("") || `<div class="card empty">No cook sessions this week.</div>`;
  const basket = c.basket.items.map(b => `<tr><td>${esc(b.name)}</td><td>${esc(b.recipe)}</td><td>${rupee(b.price)}</td></tr>`).join("");
  return `<h2 class="sec">Cooking coach <span class="sub">${esc(c.headline)}</span></h2>
    <div class="callout">When a week leans on cooking, the agent pulls the recipes and one aggregated Instamart-style basket.
      No first-party delivery agent will recommend cooking — that's the moat.</div>
    <div class="row"><div style="flex:2;min-width:300px"><div class="grid-cards">${recipes}</div></div>
      <div style="flex:1;min-width:240px"><div class="card"><h3 class="k">Grocery basket · ${rupee(c.basket.total)}</h3>
        <table><tr><th>item</th><th>for</th><th>₹</th></tr>${basket || `<tr><td class="empty">—</td></tr>`}</table></div></div>
    </div>`;
}

/* ---- community ---- */
function communityPanel() {
  if (!S.community.length) loadCommunity();
  const rows = S.community.map(t => `<div class="card"><div class="row" style="align-items:center">
    <div style="flex:1"><div style="font-weight:600">${esc(t.title)}</div>
      <div class="sub">by ${esc(t.author)} · ${esc(t.city)} · ${esc(t.mode)} · ${rupee(t.budget)}/wk · ${t.adopts} adopts</div></div>
    <button data-adopt="${t.id}">Adopt</button></div></div>`).join("") || `<div class="card empty">No templates yet.</div>`;
  return `<h2 class="sec">Community plan templates</h2>
    <div class="sub">Power users publish Survival/Tight-Week plans; others browse and clone. Network effect.</div>
    <div class="row" style="margin-bottom:12px"><button class="primary" data-act="savetpl">Publish my current week</button></div>
    <div class="grid-cards">${rows}</div>`;
}

/* ---- receipts ---- */
function receiptsPanel() {
  if (!S.receipts) genReceiptsLazy();
  const r = S.receipts;
  const rows = r && r.rows.length ? r.rows.map(x => `<tr><td>${esc(x.iso_date)}</td>
    <td>${esc(x.note)}</td><td><span class="tag ${x.category === "business" ? "warn" : ""}">${esc(x.category)}</span></td>
    <td>${rupee(x.amount)}</td></tr>`).join("") : "";
  return `<h2 class="sec">Receipts & expenses</h2>
    <div class="sub">Auto-tags weekday lunches as business; export CSV for claims. Sticky for the users it serves.</div>
    <div class="row" style="margin-bottom:12px"><button class="primary" data-act="genrcpt">Generate from this week's orders</button>
      <a href="/api/receipts/${S.userId}/export.csv"><button class="ghost">Export CSV ↓</button></a></div>
    ${r ? `<div class="stats"><div class="stat"><div class="label">Total</div><div class="val">${rupee(r.total)}</div></div>
      <div class="stat"><div class="label">Business</div><div class="val">${rupee(r.business_total)}</div></div></div>
      <div class="card"><table><tr><th>date</th><th>item</th><th>category</th><th>amount</th></tr>${rows || `<tr><td class="empty" colspan="4">No receipts yet — generate from orders.</td></tr>`}</table></div>` : `<div class="card empty">Loading…</div>`}`;
}
async function genReceiptsLazy() { S.receipts = await api(`/api/receipts/${S.userId}`); render(); }

/* ---- features map ---- */
function featuresPanel() {
  const F = S.meta.features;
  const groupTitles = { "5.1": "§5.1 — v1 gaps (all enforced)", "5.2": "§5.2 — v1.1 / v2 adds", "5.3": "§5.3 — bigger bets" };
  const groups = Object.keys(F).map(g => `<div class="groupttl">${groupTitles[g]}</div>
    <div class="feat">${F[g].map(f => `<div class="f"><div class="n">${f.id}</div>
      <div><div class="nm">${esc(f.name)}</div><div class="wh">${esc(f.where)}</div></div></div>`).join("")}</div>`).join("");
  return `<h2 class="sec">All 17 gap features — integrated</h2>
    <div class="callout">${esc(S.meta.note)} The "agent" is a MILP solver, not an LLM — so v1.1 runs at ≈₹0 per decision.
      Each feature below is real logic on one shared optimiser, not a stub.</div>
    ${groups}`;
}

/* ---------------------------------------------------------------- wiring */
function wire() {
  const on = (sel, ev, fn) => document.querySelectorAll(sel).forEach(el => el.addEventListener(ev, fn));
  const u = document.getElementById("userSel"); if (u) u.onchange = (e) => guard(() => switchUser(e.target.value));
  const md = document.getElementById("modeSel"); if (md) md.onchange = (e) => guard(() => setMode(e.target.value));
  on("[data-tab]", "click", (e) => { S.tab = e.currentTarget.dataset.tab; render(); });
  on("[data-cell]", "click", (e) => { S.drawer = e.currentTarget.dataset.cell; render(); });
  on("[data-close]", "click", (e) => { if (e.target.dataset.close) { S.drawer = null; render(); } });
  on("[data-close-err]", "click", () => { S.error = null; render(); });
  on("[data-close-cold]", "click", () => { S.hideCold = true; render(); });
  on("[data-adopt]", "click", (e) => guard(() => adopt(e.currentTarget.dataset.adopt)));
  on("[data-sess]", "click", (e) => { const [id, st] = e.currentTarget.dataset.sess.split(":"); guard(() => setSession(id, st)); });
  const cmd = document.getElementById("cmd"); if (cmd) cmd.addEventListener("keydown", (e) => { if (e.key === "Enter") guard(runCommand); });
  const acts = {
    cmd: runCommand, reopt: reoptimize, exec: execute, savetpl: saveTemplate,
    genrcpt: genReceipts, idem: idempotencyDemo, reload: reloadPlan,
  };
  on("[data-act]", "click", (e) => { const f = acts[e.currentTarget.dataset.act]; if (f) guard(f); });
}
async function idempotencyDemo() {
  const d = await api("/api/demo/idempotency", "POST", {});
  toast(`Same order id: ${d.same_order_id} · 2nd deduped: ${d.second_was_deduped}`);
}

boot().catch(e => { document.getElementById("app").innerHTML = `<div class="boot">Failed to start: ${esc(e.message)}</div>`; });
