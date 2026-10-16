/* Ziggy v3 — the founder's sketch (13 Oct 2026), on top of the dark redesign.
   Navigation is written down once, in docs/IA.md. In short:
     Today — today's budget (± and presets), weather, your rules as chips, one line per meal
             ("Breakfast: 1× Cappuccino ▾"), Place order / Send details / Cook it, and your usual
             meals (founder, Oct 2026: "usual meals", not "pools"). On a phone each meal line has
             its own "Usual ▾" drawer (pick, add, remove); from 1024 px the three usual-meals
             panels sit side by side below. Nothing is decided
             for you: "Decide for me" runs only when you tap it and pick how.
     Week  — the week plan, with nutrition as an opt-in.
     Cook  — type any dish: ingredients scaled, Instamart links, cook along.
     Saved — places and dishes you liked.
   One "Swap" sheet replaces the five ways to change a meal (Hungry now, Or have instead,
   Change sheet, Any place near you, Order several); each old function is still reachable
   from it. The classic Today stays behind a flag: ?ui=classic (or localStorage ziggy.ui).
   Loaded before app.js; app.js calls these hooks by name and works without them. */
"use strict";
(function () {
  const g = globalThis;
  const MEAL_ORDER = ["breakfast", "lunch", "dinner"];
  const MIN_RATINGS = [[0, "Any"], [3.5, "3.5★+"], [4, "4★+"], [4.5, "4.5★+"]];
  const MAX_ETAS = [[0, "Any"], [20, "≤20 min"], [30, "≤30 min"], [45, "≤45 min"]];
  const STEP = 50;
  const flag = () => {
    try {
      const q = typeof location !== "undefined" ? new URLSearchParams(location.search).get("ui") : null;
      if (q === "classic" || q === "v3") localStorage.setItem("ziggy.ui", q);
      return localStorage.getItem("ziggy.ui") !== "classic";
    } catch (_) { return true; }
  };
  const ON = flag();
  const st = () => {
    if (!S.v3 || S.v3.uid !== S.userId) S.v3 = { uid: S.userId, data: null, loading: false, mode: "order", sheet: null };
    return S.v3;
  };
  const money = (n) => rupee0(n);
  // "Usual meals" (the founder's word; the API and storage still say pool).
  const USUAL = { breakfast: "usual breakfasts", lunch: "usual lunches", dinner: "usual dinners" };
  const usual = (meal) => USUAL[meal] || `usual ${meal}s`;
  // Phones get a drawer per meal line; computers (≥ 1024 px) the three panels side by side.
  const WIDE_Q = "(min-width:1024px)";
  const wide = () => (typeof matchMedia === "function" ? matchMedia(WIDE_Q).matches : true);
  let watching = false;
  function watchWidth() {
    if (watching || typeof matchMedia !== "function") return;
    watching = true;
    const mq = matchMedia(WIDE_Q), again = () => { if (S.v3?.data && (S.tab || "today") === "today") render(); };
    if (mq.addEventListener) mq.addEventListener("change", again); else if (mq.addListener) mq.addListener(again);
  }
  const T = (sheet) => { st().sheet = sheet; render(); };

  /* -------------------------------------------------------------- data */
  async function load() {
    const v = st();
    if (v.loading) return;
    v.loading = true;
    try { v.data = await api(`/api/user/${S.userId}/today`); v.error = null; }
    catch (e) { v.error = e.message; }
    finally { v.loading = false; }
    render();
  }
  async function post(path, body, msg) {
    const r = await api(`/api/user/${S.userId}/today${path}`, "POST", body || {});
    st().data = r;
    if (msg) toast(msg);
    render();
    return r;
  }

  /* -------------------------------------------------------------- pieces */
  const WX = { clear: "☀", hot: "☀", rain: "☂", storm: "⛈" };
  function weatherCard(w) {
    if (!w || !w.known) return `<section class="card v3-wx" aria-label="Weather today"><p class="k">Weather today</p>
      <p class="fine">No live forecast for your area yet.</p></section>`;
    return `<section class="card v3-wx" aria-label="Weather today"><p class="k">Weather today</p>
      <div class="v3-wx-row"><span class="v3-wx-ico" aria-hidden="true">${WX[w.condition] || "☀"}</span>
        <b class="money">${Math.round(w.temp_c)}°</b></div>
      <span class="fine">${esc(cap1(w.condition))}${w.rain_prob != null ? ` · rain ${Math.round(w.rain_prob)}%` : ""}${w.source === "sample" ? " · sample data" : ""}</span></section>`;
  }
  function budgetCard(d) {
    const b = d.budget;
    return `<section class="card v3-budget" aria-labelledby="v3-budget-h">
      <div class="v3-budget-top"><div><p class="k" id="v3-budget-h">Today's budget</p>
        <p><b class="money v3-big">${money(b.amount)}</b> <span class="muted">(${esc(d.label)})</span></p></div>
        <span class="stepper sm"><button type="button" data-v3-budget="-${STEP}" aria-label="Lower today's budget by ${STEP} rupees">${ICON.minus}</button>
          <button type="button" data-v3-budget="${STEP}" aria-label="Raise today's budget by ${STEP} rupees">${ICON.plus}</button></span></div>
      <div class="chips" role="group" aria-label="Budget presets">${b.presets.map(n => `<button class="chip small" data-v3-preset="${n}" aria-pressed="${b.amount === n}">${money(n)}</button>`).join("")}
        <button class="chip small" data-v3-sheet="budget">Other…</button></div>
      ${b.picked ? `<p class="v3-left-line">${leftLine(b)}</p>` : ""}<p class="fine">${b.picked ? `${money(b.picked_dishes ?? b.picked)} picked` : "Nothing picked yet"}${b.unpriced?.length ? ` · ${b.unpriced.length} without a price` : ""}${b.source === "week" && b.basis ? ` · ${esc(b.basis)}` : ""}</p>${b.picked_bill != null ? `<p class="fine v3-bill-total">${b.bill_estimated ? "About " : ""}${money(b.picked_bill)} with Swiggy delivery, fees &amp; GST${b.bill_estimated ? " (estimate until Swiggy shows the bill)" : ""}</p>` : ""}${weekMoney()}</section>`;
  }
  // What's left of today's budget, net of Swiggy fees. The server's figure (budget.left_after_fees,
  // budget.left_estimated) wins; without it, today's budget minus the whole bill (picked_bill);
  // with no Swiggy pick there are no fees, so the plain figure is already net.
  function leftLine(b) {
    const net = b.left_after_fees != null ? Number(b.left_after_fees) : b.picked_bill != null ? b.amount - b.picked_bill : null;
    if (net == null) return `<b class="v3-left">${b.left >= 0 ? `${money(b.left)} left` : `${money(-b.left)} over`}</b>`;
    const est = b.left_estimated != null ? !!b.left_estimated : !!b.bill_estimated;
    const n = Math.round(net * 100) / 100;
    return `<b class="v3-left">${n >= 0 ? `${money(n)} left after fees` : `${money(-n)} over after fees`}</b> <span class="tag ${est ? "" : "good"} v3-left-tag">${est ? "estimate" : "real bill"}</span>`;
  }
  // W4: the week's target against real Swiggy bills (plan view budget.kind/actual), the
  // low-budget heads-up, and (usab #17) the plan's "₹N over this week's target" heads-up when
  // the person's own picks take the week over: said on Today, not only as a red number in Week.
  // Read from S.view; nothing shown when the plan doesn't carry them.
  function weekMoney() {
    const wb = S.view?.budget, a = wb?.actual, hs = S.view?.heads_up || [];
    const line = wb?.kind === "target" && a ? `<p class="fine v3-week-money">This week: ${money(a.amount)} spent on Swiggy of a ${money(wb.budget)} target${a.complete ? "" : " (so far, from the bills Ziggy has seen)"}</p>` : "";
    const low = hs.find(h => h.kind === "low_budget");
    const over = wb?.over ? hs.find(h => h.kind === "budget" && (h.actions || []).some(x => x.act === "raise-budget")) : null;
    return line + (over ? `<div class="note warn v3-week-over" role="alert"><span style="flex:1"><b>${esc(over.title)}</b> ${esc(over.body || "")}
        <span class="acts"><button class="link" data-act="budget">Change the weekly target</button> <button class="link" data-tab="week">See the week</button></span></span></div>` : "")
      + (low ? `<div class="note warn" role="note"><span style="flex:1"><b>${esc(low.title)}</b> ${esc(low.body || "")}</span></div>` : "");
  }
  // W4 Sunday recap: real Swiggy bills only; "kept" only when the order history was read.
  function recapCard(d) {
    const r = d.recap;
    if (!r) return "";
    const day = (x) => x ? `${esc(x.day)} (${money(x.spent)})` : null;
    const lines = [`<p><b class="money">${money(r.actual)}</b> <span class="muted">spent on Swiggy of your ${money(r.target)} target · ${r.orders} order${r.orders === 1 ? "" : "s"}</span></p>`];
    if (r.kept != null) lines.push(`<p class="v3-kept"><b>${money(r.kept)} ${r.finished ? "kept" : "under so far"}</b></p>`);
    if (r.best_day && r.worst_day && r.best_day.date !== r.worst_day.date) lines.push(`<p class="fine">Lightest ordering day ${day(r.best_day)} · heaviest ${day(r.worst_day)}</p>`);
    if (r.note) lines.push(`<p class="fine">${esc(r.note)}</p>`);
    const share = r.share_text ? `<button class="secondary small" data-v3-recap-share="${esc(r.share_text)}">Share</button>` : "";
    return `<section class="card v3-recap" aria-labelledby="v3-recap-h"><h2 class="h3" id="v3-recap-h">Your week</h2>${lines.join("")}
      <p class="fine">${esc(r.basis)}</p>${share}</section>`;
  }
  function rulesRow(d) {
    const r = d.rules, chips = [];
    chips.push(`<span class="tag ${r.diet === "nonveg" ? "" : "good"}">${{ veg: "Vegetarian", vegan: "Vegan", nonveg: "Non-veg" }[r.diet] || esc(r.diet)}</span>`);
    (r.allergens || []).forEach(a => chips.push(`<span class="tag warn">No ${esc(a.replace("_", " "))}</span>`));
    chips.push(`<span class="tag">${r.min_rating ? `${r.min_rating}★ and up` : "Any rating"}</span>`);
    chips.push(`<span class="tag">${r.max_eta ? `≤ ${r.max_eta} min` : "Any delivery time"}</span>`);
    return `<div class="v3-rules" aria-label="Your rules">${chips.join("")}<button class="link small" data-v3-sheet="rules">Edit rules</button></div>`;
  }
  const entryLabel = (e) => `${e.name}${e.price != null ? ` · ₹${Math.round(e.price)}` : ""}${e.restaurant ? ` · ${e.restaurant}` : ""}`;
  function mealLine(d, meal) {
    const p = d.picks[meal], pool = d.pools[meal] || [];
    const opts = pool.map(e => `<option value="${esc(e.ref)}" ${p && p.ref === e.ref ? "selected" : ""} ${e.hard ? "disabled" : ""}>${esc(entryLabel(e))}${e.reasons.length ? ` — ${esc(e.reasons[0])}` : ""}</option>`).join("");
    const label = cap1(meal);
    const id = `v3-pick-${meal}`;
    const qty = p ? `<span class="stepper sm v3-qty"><button type="button" data-v3-pqty="${meal}:-1" aria-label="One fewer ${esc(p.name)}" ${p.qty <= 1 ? "disabled" : ""}>${ICON.minus}</button><b aria-live="polite">${p.qty}×</b><button type="button" data-v3-pqty="${meal}:1" aria-label="One more ${esc(p.name)}" ${p.qty >= 8 ? "disabled" : ""}>${ICON.plus}</button></span>` : "";
    const why = p && p.reasons?.length ? `<p class="fine v3-why">${esc(p.reasons.join(" · "))}</p>` : "";
    const how = p ? (p.how === "ziggy" ? `<span class="tag hot">Ziggy picked</span>` : p.how === "surprise" ? `<span class="tag hot">Surprise</span>` : "") : "";
    const select = pool.length || p
      ? `<label class="sr" for="${id}">${label} from your usual meals</label><select id="${id}" class="v3-select" data-v3-pick="${meal}">
          <option value="" ${p ? "" : "selected"}>${pool.length ? `Pick from your ${usual(meal)}` : `No ${usual(meal)} yet`}</option>${opts}
          ${p && !pool.some(e => e.ref === p.ref) ? `<option value="${esc(p.ref)}" selected>${esc(entryLabel(p))}</option>` : ""}</select>`
      : `<button class="secondary small" data-v3-add="${meal}">${ICON.plus}Add a usual ${meal}</button>`;
    return `<div class="v3-meal" data-v3-meal="${meal}"><span class="v3-meal-name">${label}</span>
      <div class="v3-meal-pick">${select}${qty}</div>
      <div class="v3-meal-meta">${p?.price != null ? `<span class="fine">${money(p.price * p.qty)}${p.price_basis ? ` <i>(${esc(p.price_basis)})</i>` : ""}${p.bill ? ` <span class="v3-bill" title="${esc(p.bill.label)}">· ${p.bill.estimated ? "~" : ""}${money(p.bill.total)} with delivery, fees &amp; GST${p.bill.estimated ? " (estimate)" : ""}</span>` : ""}</span>` : ""}${how}
        ${wide() ? "" : usualBtn(meal, pool.length)}<button class="link small" data-v3-swap="${meal}" aria-haspopup="dialog" aria-label="Swap ${meal}: other options">${ICON.swap}Swap</button></div>${why}${wide() ? "" : usualDrawer(d, meal)}</div>`;
  }
  // Phone: the meal's usual meals open under its line (a dropdown drawer), not as a separate block.
  function usualBtn(meal, n) {
    const open = !!st().usualOpen?.[meal];
    return `<button class="link small v3-usual-btn" data-v3-usual="${meal}" aria-expanded="${open}" aria-controls="v3-usual-${meal}">Usual${n ? ` (${n})` : ""} <span aria-hidden="true">${open ? "▴" : "▾"}</span><span class="sr"> ${usual(meal)}</span></button>`;
  }
  function usualDrawer(d, meal) {
    if (!st().usualOpen?.[meal]) return "";
    return `<div class="v3-usual" id="v3-usual-${meal}" role="region" aria-label="Your ${usual(meal)}">${usualList(d, meal)}</div>`;
  }
  // W2: a meal's Swiggy cart opens a while before its order-by time; paying earlier would
  // deliver now. The plan's cell for today's meal carries order.cart_state / label.
  function earlyOrder(meal) {
    const o = todayCell(meal)?.order;
    return o && o.cart_state === "early" ? { sid: todayCell(meal).session_id, at: o.label || o.order_at, opens: o.cart_opens_label } : null;
  }
  const remindBtn = (e, cls) => `<button class="${cls}" data-remind="${e.sid}" data-remind-at="${esc(e.at)}">${ICON.cart}<span>Remind me at ${esc(e.at)}</span></button>`;
  function actions(d) {
    const mode = st().mode, any = Object.keys(d.picks).length;
    const cartable = d.meals.filter(m => d.picks[m] && d.picks[m].source === "swiggy" && d.picks[m].kind === "order");
    const early = cartable.map(earlyOrder);
    const allEarly = cartable.length && early.every(Boolean) ? early.reduce((a, b) => (a.at <= b.at ? a : b)) : null;
    const place = allEarly ? remindBtn(allEarly, mode === "order" ? "primary" : "secondary")
      : `<button class="${mode === "order" ? "primary" : "secondary"}" data-v3-act="order" ${any ? "" : "disabled"}>${ICON.cart}<span>Place order</span></button>`;
    const send = `<button class="secondary" data-v3-act="share" ${any ? "" : "disabled"}>Send details</button>`;
    const cook = `<button class="${mode === "cook" ? "primary" : "secondary"}" data-v3-act="cook" ${any ? "" : "disabled"}>${ICON.cook}<span>Cook it</span></button>`;
    return `<div class="v3-actions">${mode === "order" ? place + send + cook : cook + send + place}</div>`;
  }
  function compareRow(d) {
    const c = d.price_compare;
    if (!c) return "";                     // no data, no row: never a made-up comparison
    return `<div class="v3-compare" role="note" aria-label="Price compare"><span><b>Swiggy</b> ${money(c.swiggy)}</span>
      <span class="muted">Other apps · coming</span><span class="fine">${esc(c.basis)}</span></div>`;
  }
  function usualList(d, meal) {
    const pool = d.pools[meal] || [];
    const rows = pool.map(e => {
      const off = e.reasons.length > 0;
      return `<li class="v3-pool-item${off ? " off" : ""}"><div class="t"><b>${e.qty}× ${esc(e.name)}</b>
          <span class="fine">${[e.restaurant, e.price != null ? `₹${Math.round(e.price)}` : "price not known", e.rating != null ? `${e.rating}★` : null,
            e.eta_min != null ? `${e.eta_min} min` : null, e.source === "typed" ? "typed by you" : null, e.kind === "cook" ? "cook" : null].filter(Boolean).map(esc).join(" · ")}</span>
          ${off ? `<span class="v3-reason">${esc(e.reasons.join(" · "))}</span>` : ""}</div>
        <div class="v3-pool-btns"><span class="stepper sm"><button type="button" data-v3-qty="${meal}|${esc(e.ref)}|-1" aria-label="One fewer ${esc(e.name)}" ${e.qty <= 1 ? "disabled" : ""}>${ICON.minus}</button><button type="button" data-v3-qty="${meal}|${esc(e.ref)}|1" aria-label="One more ${esc(e.name)}" ${e.qty >= 8 ? "disabled" : ""}>${ICON.plus}</button></span>
          <button class="icon-btn" data-v3-remove="${meal}|${esc(e.ref)}" aria-label="Remove ${esc(e.name)} from your ${usual(meal)}">${ICON.close}</button></div></li>`;
    }).join("");
    return `${rows ? `<ul class="v3-pool-list">${rows}</ul>` : `<p class="fine">No dishes yet. Add the ones you'd actually have for ${meal}.</p>`}
      <button class="secondary small" data-v3-add="${meal}">${ICON.plus}New item / restaurant</button>`;
  }
  function poolCard(d, meal) {
    return `<section class="card v3-pool" aria-labelledby="v3-pool-${meal}"><h2 class="h3" id="v3-pool-${meal}">${cap1(usual(meal))}</h2>
      ${usualList(d, meal)}</section>`;
  }
  // Phone: usual meals for a meal that isn't on today (say dinner on a B+L day) stay reachable.
  function otherUsual(d) {
    const rest = MEAL_ORDER.filter(m => !d.meals.includes(m));
    if (!rest.length) return "";
    return `<div class="v3-other-usual">${rest.map(m => `<div class="v3-other-row"><span class="fine">${cap1(m)} (not today)</span>${usualBtn(m, (d.pools[m] || []).length)}</div>${usualDrawer(d, m)}`).join("")}</div>`;
  }
  // The Swiggy cart, its real bill, checkout and tracking: the classic block, always shown
  // here (the classic Today hides it when its meal card already shows the bill).
  function cartBlock() {
    const keep = S.carts;
    S.carts = null;
    try { return `<div class="v3-cart">${liveCartBlock()}</div>`; } finally { S.carts = keep; }
  }
  function setupCard(d) {
    const fav = MEAL_ORDER.some(m => (d.pools[m] || []).length);
    if (d.swiggy.connected && d.swiggy.address && fav) return "";
    const open = swiggySignInOpen();
    const step1 = d.swiggy.connected
      ? (d.swiggy.address ? `<li class="done">Delivering to ${esc(d.swiggy.address)}</li>` : `<li><button class="link" data-act="addr-open">Choose your delivery address</button></li>`)
      : open ? `<li><button class="link" data-act="connect-open">Connect Swiggy</button> for your address, menus and past orders</li>`
        : `<li>Swiggy sign-in is opening soon. Meanwhile, type in the dishes you usually get.</li>`;
    const step2 = fav ? `<li class="done">Favourites added</li>`
      : `<li><button class="link" data-v3-add="${d.meals[0] || "lunch"}">Add your favourite dishes</button>${d.swiggy.connected ? " (from your Swiggy orders or any menu)" : ""}</li>`;
    return `<section class="card v3-setup" aria-labelledby="v3-setup-h"><h2 class="h3" id="v3-setup-h">Get set up</h2><ol>${step1}${step2}</ol></section>`;
  }
  function saveCard(d) {
    if (d.account_saved || !Object.keys(d.picks).length || !keys.has(S.userId) || store.get(`ziggy.v3.saved-later.${S.userId}`)) return "";
    return `<section class="card v3-save" aria-labelledby="v3-save-h"><h2 class="h3" id="v3-save-h">Keep this on every device?</h2>
      <p class="fine">Save a sign-in name and password. No email needed.</p>
      <form id="v3-save" class="stack"><label>Sign-in name<input id="v3-login" autocomplete="username" autocapitalize="none" spellcheck="false" minlength="3" maxlength="64" required></label>
        <label>Password<input id="v3-pw" type="password" autocomplete="new-password" minlength="8" required></label>
        <div class="row"><button class="secondary small" type="submit">Save</button><button class="ghost small" type="button" data-v3-act="save-later">Later</button></div></form></section>`;
  }

  /* -------------------------------------------------------------- screens */
  function today() {
    const v = st();
    watchWidth();
    if (!v.data && !v.loading && !v.error) setTimeout(() => guard(load, "Load today"), 0);
    if (!v.data) return `<div class="v3-today">${v.error ? `<div class="errbar" role="alert"><div class="msg">${esc(v.error)}</div><button class="small" data-v3-act="reload">Retry</button></div>`
      : `<div class="skeleton" aria-busy="true"><i></i><i></i><i></i></div>`}</div>`;
    const d = v.data;
    const first = S.view?.user?.name && S.view.user.name !== "Me" ? `, ${esc(S.view.user.name.split(" ")[0])}` : "";
    return `<div class="v3-today"><div class="v3-cols"><div class="v3-left">
      <div class="v3-head"><h1 class="h1">${greeting()}${first}</h1><p class="muted">${esc(d.weekday)} · ${esc(d.date.split("-").reverse().join("/"))}</p></div>
      <div class="seg v3-mode" role="group" aria-label="Order or cook">
        <button data-v3-mode="order" aria-pressed="${v.mode === "order"}">Order</button><button data-v3-mode="cook" aria-pressed="${v.mode === "cook"}">Cook</button></div>
      ${setupCard(d)}
      <section class="card v3-meals" aria-label="Today's meals">${d.meals.map(m => mealLine(d, m)).join("")}
        <div class="row v3-decide"><button class="secondary small" data-v3-sheet="decide">Decide for me</button>
          <span class="fine">Only when you ask. Picks from your usual meals within your rules and ${money(d.budget.amount)}.</span></div>${wide() ? "" : otherUsual(d)}</section>
      ${actions(d)}
      ${compareRow(d)}
      ${cartBlock()}
      ${recapCard(d)}
      ${saveCard(d)}</div>
      <div class="v3-side"><div class="v3-top">${budgetCard(d)}${weatherCard(d.weather)}</div>${rulesRow(d)}</div></div>
      ${wide() ? `<h2 class="k v3-pools-h">Your usual meals</h2>
      <div class="v3-pools">${MEAL_ORDER.map(m => poolCard(d, m)).join("")}</div>` : ""}
    </div>`;
  }
  function cookScreen() {
    return `<div class="v3-cook"><h1 class="h1">Cook</h1>
      <p class="sub">Type any dish. Ziggy shows its ingredients for how many are eating, with Instamart links, and cooks along with you.</p>
      <section class="card">${cookFinder({})}</section>
      <button class="linkcard" data-go="week:groceries">${ICON.cook}<span class="t"><b>This week's grocery list</b><span>For the cook meals in your week</span></span>${ICON.right}</button></div>`;
  }
  function weekTop() {
    const on = nutritionOn(), n = S.view?.nutrition || {}, avg = n.daily_avg || {}, t = n.daily_target || {};
    const bars = on ? NUTRIENTS.filter(([, k]) => t[k]).map(([label, k, u]) => {
      const val = avg[k] || 0, ratio = val / t[k];
      return `<div class="nut ${ratio < 0.8 ? "low" : ratio > 1.15 ? "high" : "ok"}"><div class="nb-h"><span>${label}</span><span><b>${Math.round(val)}</b>${u} of ${Math.round(t[k])}${u}</span></div>
        <div class="nb-t" role="img" aria-label="${label}: ${Math.round(ratio * 100)}% of your daily target"><i style="width:${Math.min(100, ratio * 100).toFixed(0)}%"></i></div></div>`;
    }).join("") : "";
    return `<div class="row v3-week-top"><label class="check"><input type="checkbox" data-v3-nutrition ${on ? "checked" : ""}> Show nutrition</label>
      <span class="fine">${on ? "A day, on average, from dish-name estimates. Unknown dishes add nothing." : "Off by default. Turn on to see the week against your daily targets."}</span></div>
      ${bars ? `<section class="card v3-nut" aria-label="Nutrition">${bars}</section>` : ""}`;
  }
  function nutritionOn() {
    const d = S.v3?.data;
    if (d) return !!d.rules.nutrition;
    return store.get(`ziggy.v3.nutrition.${S.userId}`) === "1";
  }

  /* -------------------------------------------------------------- sheets */
  const sheet = (ov, title, body, label) => `<div class="sheet-bg" data-ov="v3-${ov}" data-v3-close="1"><section class="sheet v3-sheet" role="dialog" aria-modal="true" aria-labelledby="v3-${ov}-h">
    <span class="grab" aria-hidden="true"></span><div class="sheet-head"><h2 class="h2" id="v3-${ov}-h">${title}</h2>
    <button class="icon-btn" data-v3-close="1" aria-label="Close ${label || "sheet"}">${ICON.close}</button></div>${body}</section></div>`;
  function decideSheet() {
    const d = st().data, pick = st().sheet.choice || null;
    return sheet("decide", "Decide for me", `<p class="sub">How should Ziggy pick ${d.meals.filter(m => !d.picks[m]).join(" and ") || "today"}? Meals you already picked stay.</p>
      <div class="stack" role="radiogroup" aria-label="How to decide">${Object.entries(d.modes).map(([k, l]) => `<button type="button" class="opt" role="radio" aria-checked="${pick === k}" data-v3-choice="${k}"><span><b>${esc(l)}</b></span></button>`).join("")}</div>
      <button class="primary" data-v3-act="decide" ${pick ? "" : "disabled"}>Decide</button>
      <p class="fine">Never past your diet or allergies. Dishes that break a rule stay greyed out.</p>`, "Decide for me");
  }
  function rulesSheet() {
    const r = st().data.rules;
    return sheet("rules", "Your rules", `<p class="k">Minimum rating</p><div class="chips">${MIN_RATINGS.map(([v, l]) => `<button class="chip" data-v3-rule="min_rating:${v}" aria-pressed="${Number(r.min_rating) === v}">${l}</button>`).join("")}</div>
      <p class="k">Delivery time</p><div class="chips">${MAX_ETAS.map(([v, l]) => `<button class="chip" data-v3-rule="max_eta:${v}" aria-pressed="${Number(r.max_eta) === v}">${l}</button>`).join("")}</div>
      <p class="fine">Dishes outside these stay in your usual meals, greyed out with the reason. Rating and time are what Swiggy showed when you added the dish.</p>
      <p class="k">Diet and allergies</p><p class="fine">${esc({ veg: "Vegetarian", vegan: "Vegan", nonveg: "Non-vegetarian" }[r.diet] || r.diet)}${r.allergens.length ? ` · avoiding ${esc(r.allergens.join(", ").replace(/_/g, " "))}` : ""}. A dish not marked vegetarian is treated as not vegetarian.</p>
      <button class="secondary" data-go="more:settings">Change diet or allergies</button>`, "rules");
  }
  function budgetSheet() {
    const b = st().data.budget;
    return sheet("budget", "Today's budget", `<label>₹ for today<input id="v3-budget-in" class="money" type="number" inputmode="numeric" min="50" max="20000" value="${Math.round(b.amount)}"></label>
      <button class="primary" data-v3-act="budget-save">Use this budget</button><p class="fine">Only today. Your weekly budget doesn't change.</p>`, "budget");
  }
  function swapSheet() {
    const s = st().sheet, d = st().data, meal = s.meal;
    const ok = (d.pools[meal] || []).filter(e => !e.reasons.length).sort((a, b) => (b.rating || 0) - (a.rating || 0) || (a.price || 0) - (b.price || 0));
    const off = (d.pools[meal] || []).filter(e => e.reasons.length);
    const cell = todayCell(meal);
    const row = (e) => `<button class="pick v3-swap-row" data-v3-swapto="${meal}|${esc(e.ref)}"><span class="t"><b>${e.qty}× ${esc(e.name)}</b><span>${esc([e.restaurant, e.price != null ? `₹${Math.round(e.price)}` : null, e.rating != null ? `${e.rating}★` : null].filter(Boolean).join(" · "))}</span></span></button>`;
    const classic = [
      `<button class="secondary small" data-v3-add="${meal}">${ICON.plus}New item / restaurant</button>`,
      swiggyReady() ? `<button class="secondary small" data-v3-act="olist">Order several meals</button>` : "",
      cell ? `<button class="secondary small" data-sheet="${cell.session_id}">Change in the week plan (cook, keep, skip)</button>` : "",
      swiggyReady() && !cell ? `<button class="secondary small" data-hungry="${meal}">Add ${meal} to the week plan now</button>` : "",
    ].filter(Boolean).join("");
    return sheet("swap", `Swap ${meal}`, `${ok.length ? `<p class="k">From your ${usual(meal)}</p><div class="stack" style="gap:6px">${ok.map(row).join("")}</div>` : `<p class="fine">None of your ${usual(meal)} passes your rules yet.</p>`}
      ${off.length ? `<details><summary>${off.length} greyed out by your rules</summary><ul class="fine v3-off-list">${off.map(e => `<li>${esc(e.name)} — ${esc(e.reasons.join(" · "))}</li>`).join("")}</ul></details>` : ""}
      ${d.picks[meal] ? `<button class="ghost small" data-v3-swapto="${meal}|">Clear ${meal}</button>` : ""}
      <p class="k">More ways</p><div class="row">${classic}</div>`, `swap ${meal}`);
  }
  // W1: dishes left out for this person, each with its reason (never silently dropped).
  function hiddenList(menu) {
    const h = menu?.hidden_items || [];
    if (!h.length) return "";
    return `<details class="v3-hidden"><summary class="fine">${h.length} dish${h.length === 1 ? "" : "es"} hidden for you</summary>
      <ul class="fine">${h.slice(0, 60).map(i => `<li><b>${esc(i.name)}</b> — ${esc(i.reason || "Not checked")}</li>`).join("")}</ul></details>`;
  }
  // Suggested favourites from the person's own Swiggy orders (W7: read once, only after this
  // consent, minimal fields, deletable). Ticked ones go into W5's Today pools.
  function suggBlock(s, meal) {
    const g = s.sugg;
    if (!g) return "";
    if (!g.imported) return `<section class="v3-sugg stack" aria-label="Suggestions from your Swiggy orders"><p class="k">Suggest from my Swiggy orders</p>
      <p class="fine">${esc(g.consent)}</p><button class="secondary small" data-v3-act="sugg-import">Suggest from my Swiggy orders</button></section>`;
    const pool = new Set((st().data.pools[meal] || []).map(e => `${(e.restaurant || "").toLowerCase()}|${e.name.toLowerCase()}`));
    const items = g.items.filter(i => i.pool_meal === meal && !pool.has(`${i.restaurant_name.toLowerCase()}|${i.item_name.toLowerCase()}`));
    s.suggShown = items;
    return `<section class="v3-sugg stack" aria-label="Suggestions from your Swiggy orders"><p class="k">From your Swiggy orders</p>
      ${items.length ? `<form id="v3-sugg" class="stack" style="gap:6px">${items.map((i, n) => `<label class="tick v3-sugg-row"><input type="checkbox" data-v3-sugg="${n}"> <span><b>${esc(i.item_name)}</b> <span class="fine">${esc([i.restaurant_name, `${i.times}× ordered`, i.rating != null ? `${i.rating}★` : null].filter(Boolean).join(" · "))}</span></span></label>`).join("")}
        <button class="secondary small" type="submit">Add ticked to your ${usual(meal)}</button></form>`
        : `<p class="fine">Nothing new for ${meal} in your recent Swiggy orders.</p>`}
      <p class="fine">${esc(g.note)}${g.attribution ? ` ${esc(g.attribution)}.` : ""} <button class="link small" data-v3-act="sugg-delete">Delete these suggestions</button></p></section>`;
  }
  function addSheet() {
    const s = st().sheet, d = st().data, meal = s.meal;
    const tab = s.tab || (d.swiggy.connected && d.swiggy.address ? "swiggy" : "type");
    const tabs = `<div class="seg" role="group" aria-label="How to add">${d.swiggy.connected && d.swiggy.address ? `<button data-v3-addtab="swiggy" aria-pressed="${tab === "swiggy"}">Search Swiggy</button>` : ""}<button data-v3-addtab="type" aria-pressed="${tab === "type"}">Type it in</button></div>`;
    const meals = `<div class="chips" role="group" aria-label="Which meal">${MEAL_ORDER.map(m => `<button class="chip small" data-v3-addmeal="${m}" aria-pressed="${m === meal}">${cap1(m)}</button>`).join("")}</div>`;
    let body;
    if (tab === "swiggy") {
      const res = s.results, menu = s.menu;
      if (menu) {
        body = `<button class="link small" data-v3-act="add-back">${ICON.back}All places</button>
          <p class="k">${esc(menu.restaurant.name)}${s.place?.rating ? ` · ${s.place.rating}★` : ""}${s.place?.eta ? ` · ${esc(String(s.place.eta))}` : ""}</p>
          <div class="stack v3-menu" style="gap:6px">${menu.items.slice(0, 60).map(i => `<button class="pick v3-swap-row" data-v3-additem="${esc(i.id)}" ${i.has_options || i.in_stock === false ? "disabled" : ""}>
            <span class="vegdot ${i.veg ? "veg" : "nonveg"}" aria-label="${i.veg ? "Vegetarian" : "Not marked vegetarian"}"></span>
            <span class="t"><b>${esc(i.name)}</b><span>${i.price != null ? `₹${Math.round(i.price)}` : "Price in cart"}${i.has_options ? " · has options, order in Swiggy" : ""}${i.in_stock === false ? " · out of stock" : ""}${i.safety?.note ? ` · ${esc(i.safety.note)}` : ""}</span></span>${ICON.plus}</button>`).join("")}</div>
          ${hiddenList(menu)}`;
      } else {
        body = `<form id="v3-search" class="row"><label class="sr" for="v3-q">Search Swiggy</label><input id="v3-q" type="search" placeholder="A restaurant or a dish" value="${esc(s.q || "")}" minlength="2" maxlength="80" style="flex:1;min-width:0">
          <button class="secondary small" type="submit">${ICON.search}<span>Search</span></button></form>
          ${s.history?.length ? `<p class="k">From your Swiggy orders</p><div class="chips">${s.history.map(h => `<button class="chip small" data-v3-hist="${esc(h)}">${esc(h)}</button>`).join("")}</div>` : ""}
          ${res ? (res.restaurants.length ? `<div class="stack" style="gap:6px">${res.restaurants.map(r => `<button class="pick v3-swap-row" data-v3-place="${esc(r.id)}" data-v3-pname="${esc(r.name)}" data-v3-prating="${r.rating ?? ""}" data-v3-peta="${esc(r.eta ?? "")}">
              <span class="t"><b>${esc(r.name)}${r.sponsored ? ` <span class="tag">Ad</span>` : ""}</b><span>${esc([r.rating != null ? `${r.rating}★` : null, r.eta, r.area].filter(Boolean).join(" · "))}</span></span>${ICON.right}</button>`).join("")}</div>`
            : `<p class="fine">Nothing found for “${esc(s.q)}”.</p>`) : ""}`;
      }
    } else {
      body = `<form id="v3-typed" class="stack"><label>Dish<input id="v3-t-name" required minlength="2" maxlength="80" placeholder="Cappuccino, burrito, idli…"></label>
        <div class="grid2"><label>Place (optional)<input id="v3-t-place" maxlength="80" placeholder="Where from"></label>
        <label>Price you pay (₹)<input id="v3-t-price" type="number" inputmode="numeric" min="1" max="20000"></label></div>
        <fieldset class="v3-fs"><legend class="k">Vegetarian?</legend><div class="chips">${[["1", "Yes"], ["0", "No"], ["", "Not sure"]].map(([v, l]) => `<label class="chip small"><input type="radio" name="v3-t-veg" value="${v}" ${v === "" ? "checked" : ""}> ${l}</label>`).join("")}</div></fieldset>
        <fieldset class="v3-fs"><legend class="k">I usually</legend><div class="chips">${[["order", "Order it"], ["cook", "Cook it"]].map(([v, l]) => `<label class="chip small"><input type="radio" name="v3-t-kind" value="${v}" ${v === "order" ? "checked" : ""}> ${l}</label>`).join("")}</div></fieldset>
        <button class="primary" type="submit">Add to your ${usual(meal)}</button>
        <p class="fine">Typed dishes are yours: Ziggy can share or cook them, but can only put Swiggy menu dishes in a cart.</p></form>`;
    }
    return sheet("add", "New item / restaurant", `${meals}${d.swiggy.connected ? suggBlock(s, meal) : ""}${tabs}${body}`, "add a dish");
  }
  function orderSheet() {
    const d = st().data;
    const picks = d.meals.filter(m => d.picks[m]).map(m => [m, d.picks[m]]);
    const rows = picks.map(([m, p]) => {
      const cartable = p.source === "swiggy" && p.kind === "order";
      return `<div class="mrow" style="cursor:default"><span class="t"><b>${cap1(m)}: ${p.qty}× ${esc(p.name)}</b><span class="s">${esc(p.restaurant || (p.kind === "cook" ? "Cook at home" : ""))}</span></span>
        ${cartable ? (earlyOrder(m) ? `<span class="stack" style="gap:2px;align-items:flex-end">${remindBtn(earlyOrder(m), "secondary small")}<span class="fine">Cart opens ${esc(earlyOrder(m).opens || "later")}</span></span>`
          : `<button class="secondary small" data-v3-cart="${m}">Add to Swiggy cart</button>`) : `<span class="fine">${p.kind === "cook" ? "Cook it" : "Typed by you: order it in your app"}</span>`}</div>`;
    }).join("");
    return sheet("order", "Place order", `<p class="fine">Swiggy carts hold one restaurant at a time, so each meal goes in on its own. You check the bill and pay in Swiggy.</p>
      <div class="card tight">${rows}</div>`, "place order");
  }
  function overlays() {
    if (!ON || !S.v3?.sheet || !S.v3.data) return "";
    const k = S.v3.sheet.kind;
    return k === "decide" ? decideSheet() : k === "rules" ? rulesSheet() : k === "budget" ? budgetSheet()
      : k === "swap" ? swapSheet() : k === "add" ? addSheet() : k === "order" ? orderSheet() : "";
  }
  function todayCell(meal) {
    const day = (S.view?.grid || []).find(x => x.date === todayIso());
    const c = day?.meals?.[meal];
    return c && c.status !== "past" && !["ordered", "confirmed"].includes(c.status) ? c : null;
  }

  /* -------------------------------------------------------------- actions */
  async function share() {
    const text = st().data.share_text;
    if (typeof navigator !== "undefined" && navigator.share) {
      try { await navigator.share({ text }); return; } catch (e) { if (e?.name === "AbortError") return; }
    }
    window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank", "noopener");
  }
  async function cookIt() {
    const d = st().data, meal = d.meals.find(m => d.picks[m]);
    const p = d.picks[meal];
    const r = await api(`/api/user/${S.userId}/dishes?q=${encodeURIComponent(p.name.slice(0, 60))}`);
    const hit = r.dishes?.[0];
    if (hit) { S.cook = { sid: null, q: p.name, results: r }; await openDish(hit.key, p.qty || 1); return; }
    S.cook = { sid: null, q: p.name, results: r }; S.tab = "cook"; render();
    toast(`No recipe for “${p.name}” yet. Try another name.`);
  }
  async function cart(meal) {
    const p = st().data.picks[meal];
    if (!swiggyReady()) { st().sheet = null; S.connectSheet = true; render(); return; }
    st().sheet = null;
    S.liveOrderReview = await api(`/api/user/${S.userId}/swiggy/live-cart/preview`, "POST",
      { restaurant_id: p.restaurant_id, restaurant_name: p.restaurant, item_id: p.item_id, item_name: p.name, quantity: p.qty || 1 });
    render();
  }
  async function searchSwiggy(q) {
    const s = st().sheet;
    s.q = q; s.menu = null;
    s.results = await api(`/api/user/${S.userId}/swiggy/restaurants?query=${encodeURIComponent(q)}`);
    render();
  }
  async function openPlace(id, name, rating, eta) {
    const s = st().sheet;
    s.place = { id, name, rating: rating ? Number(rating) : null, eta };
    s.menu = await api(`/api/user/${S.userId}/swiggy/live-menu?restaurant_id=${encodeURIComponent(id)}&restaurant_name=${encodeURIComponent(name)}`);
    render();
  }
  // A background read re-renders the sheet: keep what the person is typing in the search box.
  const keepTyped = () => { const el = typeof document !== "undefined" ? document.getElementById("v3-q") : null;
    if (el && st().sheet && typeof el.value === "string") st().sheet.q = el.value; };
  async function loadHistory() {
    const s = st().sheet;
    if (!s) return;
    try {
      const h = await api(`/api/user/${S.userId}/swiggy/order-history`);
      s.history = [...new Set((h.provider_orders || []).map(o => o.restaurant).filter(Boolean))].slice(0, 8);
    } catch (_) { s.history = []; }
    if (st().sheet !== s) return;
    keepTyped(); render();
  }
  async function loadSugg() {
    const s = st().sheet;
    if (!s) return;
    try { s.sugg = await api(`/api/user/${S.userId}/favourites/suggested`); } catch (_) { s.sugg = null; }
    if (st().sheet !== s) return;
    keepTyped(); render();
  }
  async function importSugg() {
    const s = st().sheet;
    s.sugg = await api(`/api/user/${S.userId}/favourites/suggested`, "POST", { consent: true });
    render();
  }
  async function deleteSugg() {
    const s = st().sheet;
    s.sugg = await api(`/api/user/${S.userId}/favourites/suggested`, "DELETE", {});
    toast("Suggestions deleted"); render();
  }
  async function addSugg() {
    const s = st().sheet, shown = s.suggShown || [];
    const items = [...document.querySelectorAll("[data-v3-sugg]")].filter(b => b.checked).map(b => shown[Number(b.dataset.v3Sugg)])
      .filter(Boolean).map(i => ({ restaurant_id: i.restaurant_id, item_name: i.item_name, meal: s.meal }));
    if (!items.length) throw new Error("Tick at least one suggestion");
    const r = await post("/pool/suggested", { meal: s.meal, items });
    const a = r.suggested_added || { swiggy: [], typed: [], refused: [] };
    const n = a.swiggy.length + a.typed.length;
    toast([n ? `${n} added to your ${usual(s.meal)}` : null, a.typed.length ? `${a.typed.join(", ")}: typed in (not on the menu now)` : null,
      a.refused.length ? `Not added: ${a.refused.join("; ")}` : null].filter(Boolean).join(". "));
  }
  async function addItem(itemId) {
    const s = st().sheet, item = s.menu.items.find(i => i.id === itemId);
    const eta = Number(/\d+/.exec(String(s.place.eta || ""))?.[0]) || null;
    await post("/pool", { source: "swiggy", meal: s.meal, restaurant_id: s.menu.restaurant.id, restaurant_name: s.menu.restaurant.name,
      item_id: itemId, rating: s.place.rating, eta_min: eta }, `${item.name} added to your ${usual(s.meal)}`);
  }
  async function addTyped() {
    const s = st().sheet, val = (id) => document.getElementById(id)?.value;
    const radio = (n) => document.querySelector(`input[name="${n}"]:checked`)?.value;
    const vegRaw = radio("v3-t-veg");
    const name = (val("v3-t-name") || "").trim();
    await post("/pool", { source: "typed", meal: s.meal, name, restaurant_name: val("v3-t-place") || "",
      price: val("v3-t-price") ? Number(val("v3-t-price")) : null, veg: vegRaw === "" || vegRaw == null ? null : vegRaw === "1",
      kind: radio("v3-t-kind") || "order" }, `${name} added to your ${usual(s.meal)}`);
    st().sheet = null; render();
  }
  async function decide() {
    const r = await post("/decide", { mode: st().sheet.choice });
    st().sheet = null;
    const dd = r.decided;
    toast(dd.note || `Picked ${dd.filled.join(" and ")}: ${dd.label}`);
    render();
  }
  async function saveAccount(form) {
    const login = form.querySelector("#v3-login").value, password = form.querySelector("#v3-pw").value;
    await api(`/api/user/${S.userId}/account`, "POST", { login, password });
    st().data.account_saved = true;
    toast("Saved. Sign in with it on your other devices."); render();
  }

  /* -------------------------------------------------------------- onboarding (≤ 2 questions before value) */
  function startOnboard() {
    if (!ON) return false;
    S.v3ob = { step: 0, diet: null, allergens: [], budget: null, meals: [], consent: false };
    S.welcome = false; S.sheet = null; S.connectSheet = false; S.error = null; S.onboard = null; render();
    return true;
  }
  function onboardScreen() {
    const o = S.v3ob;
    if (!o) return null;
    const bars = [0, 1].map(i => `<span class="${i === o.step ? "on" : i < o.step ? "done" : ""}"></span>`).join("");
    let body, ready;
    if (o.step === 0) {
      ready = !!o.diet;
      body = `<h1 class="h1">What do you eat?</h1>
        <div class="stack" style="gap:10px" role="radiogroup" aria-label="Diet">${[["veg", "Vegetarian", "No meat, fish or egg", ""], ["nonveg", "Non-vegetarian", "Everything is on the menu", "nonveg"], ["vegan", "Vegan", "No animal products at all", ""]].map(([k, l, s, dot]) =>
          `<button type="button" class="opt" role="radio" data-v3-ob="diet:${k}" aria-checked="${o.diet === k}"><span class="dot ${dot}"></span><span><b>${l}</b><span class="muted">${s}</span></span></button>`).join("")}</div>
        <p class="k">Anything you must avoid? (optional)</p>
        <div class="chips">${ALLERGY_LIST.map(a => `<button type="button" class="chip" data-v3-ob="allergen:${a}" aria-pressed="${o.allergens.includes(a)}">${cap1(a.replace("_", " "))}</button>`).join("")}</div>
        <p class="fine">Ziggy filters by dish name and Swiggy's veg mark. Menus don't list every ingredient, so check with the restaurant when it matters.</p>`;
    } else {
      ready = o.budget >= 50 && o.meals.length > 0 && o.consent;
      body = `<h1 class="h1">Today's food budget</h1><p class="sub">For the meals you'll order or cook today. Change it any day.</p>
        <section class="card" style="align-items:center;padding:22px 18px">
          <div class="stepper"><button type="button" data-v3-ob="budget:-${STEP}" aria-label="Lower budget by ${STEP} rupees">${ICON.minus}</button>
            <label class="sr" for="v3-ob-budget">Today's budget in rupees</label><input id="v3-ob-budget" class="money" type="number" inputmode="numeric" min="50" max="20000" value="${o.budget ?? ""}" placeholder="₹" style="font-size:40px;width:170px;text-align:center;border:0;background:transparent;min-height:56px">
            <button type="button" data-v3-ob="budget:${STEP}" aria-label="Raise budget by ${STEP} rupees">${ICON.plus}</button></div></section>
        <div class="chips" style="justify-content:center">${[300, 600, 1000].map(n => `<button type="button" class="chip" data-v3-ob="preset:${n}" aria-pressed="${o.budget === n}">${money(n)}</button>`).join("")}</div>
        <p class="k">Which meals today?</p>
        <div class="chips">${MEAL_ORDER.map(m => `<button type="button" class="chip" data-v3-ob="meal:${m}" aria-pressed="${o.meals.includes(m)}">${cap1(m)}</button>`).join("")}</div>
        <section class="card stack consent" aria-label="Your data">
          <p class="fine">Ziggy uses your answers only to plan and order your meals. It doesn't sell them, show ads or send them to any AI service. <a href="/privacy" target="_blank" rel="noopener">Privacy notice</a>.</p>
          <label class="tick"><input type="checkbox" id="v3-ob-consent" data-v3-obc="1" ${o.consent ? "checked" : ""}> I'm 18 or older and I agree to the <a href="/privacy" target="_blank" rel="noopener">privacy notice</a> and <a href="/terms" target="_blank" rel="noopener">terms</a>.</label></section>`;
    }
    return `<main class="onboard v3-ob"><div class="ob-top"><button class="icon-btn" data-v3-ob="nav:back" aria-label="${o.step ? "Back" : "Cancel"}">${ICON.back}</button>
        <div class="ob-bars" aria-label="Step ${o.step + 1} of 2">${bars}</div><span class="fine">${o.step + 1}/2</span></div>
      ${errbar(false)}<div class="stack rise" style="gap:16px">${body}</div><div class="grow"></div>
      <button class="primary big" data-v3-ob="nav:next" ${ready ? "" : "disabled"}>${o.step ? "Show my day" : "Continue"}</button>
      <p class="fine center">Next: your address and favourite dishes. Saving an account comes later.</p></main>`;
  }
  function readOb() {
    const o = S.v3ob, el = document.getElementById("v3-ob-budget");
    if (el) { const n = Math.round(Number(el.value)); o.budget = n > 0 ? n : null; }
    const c = document.getElementById("v3-ob-consent");
    if (c) o.consent = !!c.checked;
  }
  async function obTap(arg) {
    const o = S.v3ob, [k, v] = arg.split(":");
    readOb();
    if (k === "diet") o.diet = v;
    else if (k === "allergen") o.allergens = o.allergens.includes(v) ? o.allergens.filter(a => a !== v) : [...o.allergens, v];
    else if (k === "budget") o.budget = Math.min(20000, Math.max(50, (o.budget || 0) + Number(v)));
    else if (k === "preset") o.budget = Number(v);
    else if (k === "meal") o.meals = o.meals.includes(v) ? o.meals.filter(m => m !== v) : [...o.meals, v];
    else if (k === "nav" && v === "back") { if (o.step) o.step -= 1; else { S.v3ob = null; S.welcome = !S.view; } }
    else if (k === "nav" && v === "next") {
      if (o.step === 0) { if (!o.diet) throw new Error("Choose what you eat"); o.step = 1; }
      else return finishOb();
    }
    S.error = null; render();
  }
  async function finishOb() {
    const o = S.v3ob;
    if (!(o.budget >= 50 && o.budget <= 20000)) throw new Error("Today's budget must be between ₹50 and ₹20,000");
    if (!o.meals.length) throw new Error("Pick at least one meal for today");
    if (!o.consent) throw new Error("Please read the privacy notice and tick “I'm 18 or older and I agree”");
    const rhythm = Object.fromEntries(MEAL_ORDER.map(m => [m, o.meals.includes(m) ? "order" : "skip"]));
    S.planning = true; render();
    let view;
    try {
      view = await api("/api/profiles", "POST", { name: "Me", diet: o.diet, allergens: o.allergens, medical: [],
        weekly_budget: o.budget * 7, rhythm, cook: "sometimes", consent: { account: true } });
    } finally { S.planning = false; }
    // W8: the browser keeps no key (cookie session); the recovery code is shown once in Me → Account.
    if (view.access_key) { keys.put(view.user.id, null, view.user.name); S.freshCode = view.recovery_code; }
    S.users = mergeUsers(await api("/api/users"));
    S.v3ob = null; S.userId = view.user.id; store.set("smartplate.user", String(S.userId));
    adoptView(view);
    S.swiggy = await api(`/api/user/${S.userId}/swiggy`).catch(() => null);
    st().data = await api(`/api/user/${S.userId}/today/budget`, "POST", { amount: o.budget });
    S.tab = "today"; S.more = null;
    toast("Here's your day. Add your favourite dishes next."); render();
  }

  /* -------------------------------------------------------------- wiring */
  function wire(on, acts) {
    if (!ON) return;
    acts["v3-noop"] = async () => {};
    const v = () => st();
    on("[data-v3-ob]", "click", (e) => { e.preventDefault(); const a = e.currentTarget.dataset.v3Ob; guard(() => obTap(a)); });
    const obIn = document.getElementById("v3-ob-budget");
    const obReady = () => { readOb(); const b = document.querySelector('[data-v3-ob="nav:next"]'); if (b) b.disabled = !(S.v3ob.budget >= 50 && S.v3ob.meals.length && S.v3ob.consent); };
    if (obIn) obIn.oninput = obReady;
    const obC = document.getElementById("v3-ob-consent");
    if (obC) obC.onchange = obReady;                 // W8: the notice must be agreed to before a profile exists
    if (!S.userId) return;
    on("[data-v3-mode]", "click", (e) => { v().mode = e.currentTarget.dataset.v3Mode; render(); });
    on("[data-v3-budget]", "click", (e) => { const n = Number(e.currentTarget.dataset.v3Budget);
      guard(() => post("/budget", { amount: Math.max(50, (v().data?.budget.amount || 0) + n) }), "Change today's budget"); });
    on("[data-v3-preset]", "click", (e) => { const n = Number(e.currentTarget.dataset.v3Preset); guard(() => post("/budget", { amount: n }), "Change today's budget"); });
    on("[data-v3-sheet]", "click", (e) => T({ kind: e.currentTarget.dataset.v3Sheet }));
    on("[data-v3-close]", "click", (e) => { if (e.target === e.currentTarget || e.currentTarget.tagName === "BUTTON") T(null); });
    on("[data-v3-choice]", "click", (e) => { v().sheet.choice = e.currentTarget.dataset.v3Choice; render(); });
    on("[data-v3-rule]", "click", (e) => { const [k, n] = e.currentTarget.dataset.v3Rule.split(":"); guard(() => post("/rules", { [k]: Number(n) }), "Change your rules"); });
    on("[data-v3-pick]", "change", (e) => { const meal = e.currentTarget.dataset.v3Pick, ref = e.currentTarget.value || null;
      guard(() => post("/pick", { meal, ref }), `Pick ${meal}`); });
    on("[data-v3-pqty]", "click", (e) => { const [meal, dn] = e.currentTarget.dataset.v3Pqty.split(":"), p = v().data.picks[meal];
      guard(() => post("/pick", { meal, ref: p.ref, qty: Math.max(1, Math.min(8, p.qty + Number(dn))) }), "Change quantity"); });
    on("[data-v3-qty]", "click", (e) => { const [meal, ref, dn] = e.currentTarget.dataset.v3Qty.split("|"), cur = v().data.pools[meal].find(x => x.ref === ref);
      guard(() => post("/pool/qty", { meal, ref, qty: Math.max(1, Math.min(8, cur.qty + Number(dn))) }), "Change quantity"); });
    on("[data-v3-remove]", "click", (e) => { const [meal, ref] = e.currentTarget.dataset.v3Remove.split("|");
      guard(() => post("/pool/remove", { meal, ref }, `Removed from your ${usual(meal)}`), "Remove a dish"); });
    on("[data-v3-usual]", "click", (e) => { const m = e.currentTarget.dataset.v3Usual, v0 = v();
      v0.usualOpen = { ...(v0.usualOpen || {}), [m]: !v0.usualOpen?.[m] }; render();
      document.querySelector?.(`[data-v3-usual="${m}"]`)?.focus?.(); });
    on("[data-v3-swap]", "click", (e) => T({ kind: "swap", meal: e.currentTarget.dataset.v3Swap }));
    on("[data-v3-swapto]", "click", (e) => { const [meal, ref] = e.currentTarget.dataset.v3Swapto.split("|");
      guard(async () => { await post("/pick", { meal, ref: ref || null }); T(null); }, `Swap ${meal}`); });
    on("[data-v3-add]", "click", (e) => { const meal = e.currentTarget.dataset.v3Add; T({ kind: "add", meal });
      // background reads (each catches its own error): not through guard(), which drops a call while
      // another runs and would block the person's first tap in the sheet
      if (v().data?.swiggy.connected) { loadHistory(); loadSugg(); } });
    on("[data-v3-addtab]", "click", (e) => { v().sheet.tab = e.currentTarget.dataset.v3Addtab; render(); });
    on("[data-v3-addmeal]", "click", (e) => { v().sheet.meal = e.currentTarget.dataset.v3Addmeal; render(); });
    on("[data-v3-hist]", "click", (e) => { const q = e.currentTarget.dataset.v3Hist; guard(() => searchSwiggy(q), `Search Swiggy for “${q}”`); });
    on("[data-v3-place]", "click", (e) => { const d = e.currentTarget.dataset; guard(() => openPlace(d.v3Place, d.v3Pname, d.v3Prating, d.v3Peta), `Open “${d.v3Pname}”`); });
    on("[data-v3-additem]", "click", (e) => { const id = e.currentTarget.dataset.v3Additem; guard(() => addItem(id), "Add to your usual meals"); });
    on("[data-v3-recap-share]", "click", (e) => { const text = e.currentTarget.dataset.v3RecapShare;
      guard(async () => { if (typeof navigator !== "undefined" && navigator.share) { try { await navigator.share({ text }); return; } catch (_) { return; } }
        window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank", "noopener"); }, "Share"); });
    on("[data-v3-cart]", "click", (e) => { const m = e.currentTarget.dataset.v3Cart; guard(() => cart(m), "Add to Swiggy cart"); });
    on("[data-v3-nutrition]", "change", (e) => { const n = e.currentTarget.checked; store.set(`ziggy.v3.nutrition.${S.userId}`, n ? "1" : "0");
      guard(() => post("/rules", { nutrition: n }), "Nutrition"); });
    on("[data-v3-act]", "click", (e) => {
      e.preventDefault();
      const a = e.currentTarget.dataset.v3Act;
      const f = { order: async () => T({ kind: "order" }), share, cook: cookIt, decide, reload: load,
        "budget-save": () => post("/budget", { amount: Number(document.getElementById("v3-budget-in").value) }).then(() => T(null)),
        "add-back": async () => { v().sheet.menu = null; render(); },
        "sugg-import": importSugg, "sugg-delete": deleteSugg,
        "save-later": async () => { store.set(`ziggy.v3.saved-later.${S.userId}`, "1"); render(); },
        olist: async () => { T(null); await openOrderList(); } }[a];
      if (f) guard(f);
    });
    const search = document.getElementById("v3-search");
    if (search) search.onsubmit = (e) => { e.preventDefault(); const q = document.getElementById("v3-q").value; guard(() => searchSwiggy(q), `Search Swiggy for “${q}”`); };
    const sugg = document.getElementById("v3-sugg");
    if (sugg) sugg.onsubmit = (e) => { e.preventDefault(); guard(addSugg, "Add suggestions to your usual meals"); };
    const typed = document.getElementById("v3-typed");
    if (typed) typed.onsubmit = (e) => { e.preventDefault(); guard(addTyped, "Add to your usual meals"); };
    const save = document.getElementById("v3-save");
    if (save) save.onsubmit = (e) => { e.preventDefault(); guard(() => saveAccount(save), "Save sign-in"); };
  }

  /* -------------------------------------------------------------- hooks called by app.js */
  g.ZiggyV3 = {
    on: () => ON,
    tabs(ICON) {
      if (!ON) return null;
      return [["today", "Today", ICON.today], ["week", "Week", ICON.week], ["cook", "Cook", ICON.cook], ["saved", "Saved", ICON.saved]];
    },
    body(tab) {
      if (!ON) return null;
      const v = S.userId ? st() : null, back = v && v.seenTab !== tab;
      if (v) v.seenTab = tab;
      // Coming back to Today re-reads it (pools, cart or the address may have changed elsewhere).
      if (tab === "today" && back && v.data && !v.loading) setTimeout(() => guard(load, "Load today"), 0);
      if (tab === "today") return today();
      if (tab === "cook") return cookScreen();
      if (tab === "saved") {
        // The drag-and-drop pool board is classic-only: pools live on Today now.
        const t = S.savedTab || (swiggyReady() ? "meals" : "places");
        if (t !== "meals" || !swiggyReady()) return null;
        return savedScreen().replace(poolsBoard(), `<section class="card rise3"><h2 class="h3">Your usual meals are on Today</h2>
          <p class="fine">Usual breakfasts, lunches and dinners: tap + New item / restaurant to add a dish. No dragging.</p>
          <button class="secondary small" data-tab="today">Open Today</button></section>`);
      }
      return null;
    },
    screen() { return ON ? onboardScreen() : null; },
    overlays,
    wire,
    weekTop() { return ON ? weekTop() : ""; },
    nutritionOn() { return ON ? nutritionOn() : true; },
    startOnboard,
    // A tap on a meal in the Week opens Swap (for today's meals) — one way to change a meal.
    onMealTap(sid) {
      if (!ON) return false;
      const day = (S.view?.grid || []).find(x => x.date === todayIso());
      const meal = day && MEAL_ORDER.find(m => day.meals[m]?.session_id === Number(sid));
      if (!meal || !st().data) return false;
      T({ kind: "swap", meal });
      return true;
    },
    // After any plan change, re-read Today.
    invalidate() { if (S.v3) S.v3.data = null; },
    _test: { load, post, decide, share, startOnboard, finishOb, obTap, today, cookScreen, overlays, hiddenList, suggBlock, addSugg, recapCard, actions, orderSheet },
  };
})();
