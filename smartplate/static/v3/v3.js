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
  // r3 #6: snack is an optional favourites list (never planned): it can be added and picked.
  const ADD_MEALS = [...MEAL_ORDER, "snack"];
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
  const USUAL = { breakfast: "usual breakfasts", lunch: "usual lunches", dinner: "usual dinners", snack: "usual snacks" };
  const mealName = (meal) => (meal === "snack" ? "Snack (optional)" : cap1(meal));
  // Today's meals plus a picked snack (counted in money and orders, never in the rhythm).
  const pickedMeals = (d) => [...d.meals, ...(d.picks?.snack ? ["snack"] : [])];
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
    readCartOnce();
  }
  // The Swiggy cart is read once a session, and only when a pick could go in it (audit #16).
  function readCartOnce() {
    const v = st(), d = v.data;
    if (!d || v.cartRead || typeof swiggyReady !== "function" || !swiggyReady()) return;
    if (!Object.values(d.picks || {}).some(p => p.source === "swiggy" && p.kind === "order")) return;
    v.cartRead = true;
    refreshLiveCart(false).then(() => render()).catch(() => {});
  }
  async function post(path, body, msg) {
    const r = await api(`/api/user/${S.userId}/today${path}`, "POST", body || {});
    st().data = r;
    if (path === "/pick") st().impact = r.impact?.text ? r.impact : null;
    if (msg) toast(msg);
    render();
    return r;
  }

  /* -------------------------------------------------------------- pieces */
  const WX = { clear: "☀", hot: "☀", rain: "☂", storm: "⛈" };
  // ux #38: no card without a live forecast (no "sample data" tile squeezing the budget);
  // live-audit-1: say whose forecast it is and for which place.
  function weatherCard(w) {
    if (!w || !w.known || w.source !== "live") return "";
    const credit = w.attribution ? ` · <a href="${esc(w.attribution_url || "https://open-meteo.com/")}" target="_blank" rel="noopener">${esc(w.attribution)}</a>` : "";
    return `<section class="card v3-wx" aria-label="Weather today"><p class="k">Weather today</p>
      <div class="v3-wx-row"><span class="v3-wx-ico" aria-hidden="true">${WX[w.condition] || "☀"}</span>
        <b class="money">${Math.round(w.temp_c)}°</b></div>
      <span class="fine">${esc(cap1(w.condition))}${w.rain_prob != null ? ` · rain ${Math.round(w.rain_prob)}%` : ""}</span>
      <span class="fine v3-wx-src">${w.place ? `For ${esc(w.place)}` : "Forecast"}${credit}</span></section>`;
  }
  function budgetCard(d) {
    const b = d.budget;
    return `<section class="card v3-budget" aria-labelledby="v3-budget-h">
      <div class="v3-budget-top"><div><p class="k" id="v3-budget-h">Today's budget</p>
        <p><b class="money v3-big">${money(b.amount)}</b> <span class="muted">(${esc(d.label)})</span></p></div>
        <span class="stepper sm"><button type="button" data-v3-budget="-${STEP}" aria-label="Lower today's budget by ${STEP} rupees">${ICON.minus}</button>
          <button type="button" data-v3-budget="${STEP}" aria-label="Raise today's budget by ${STEP} rupees">${ICON.plus}</button></span></div>
      <div class="chips" role="group" aria-label="Budget presets">${b.presets.map(n => `<button class="chip small" data-v3-preset="${n}" aria-pressed="${b.amount === n}">${money(n)}</button>`).join("")}
        <button class="chip small" data-v3-day="${esc(b.date || todayIso())}">Other…</button></div>
      ${b.guide != null && b.amount !== b.guide ? `<p class="fine v3-share">Fair share today ${money(b.guide)}${b.source === "today" ? " · you set today's amount" : ""}</p>` : ""}${b.state === "off" ? `<p class="fine">Today is Off: nothing planned.</p>` : ""}${impactNote()}
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
    // ux #23: like with like. Spend counts the whole Mon–Sun week, so the target is the whole week's too.
    const line = wb?.kind === "target" && a ? `<p class="fine v3-week-money">This week (Mon–Sun): ${money(a.amount)} spent on Swiggy of your ${money(wb.weekly_budget ?? wb.budget)} a week${a.complete ? "" : " (so far, from the bills Ziggy has seen)"}</p>` : "";
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
  const DIET_WORD = { veg: "Vegetarian", egg: "Eggetarian", jain: "Jain / no onion-garlic", vegan: "Vegan", nonveg: "Non-veg" };
  // usab #10: rating / time Ziggy chose (or learned from the person's places), said so.
  function defaultsTag(r) {
    const src = r.source || {};
    if (src.min_rating === "you" && src.max_eta === "you") return "";
    const learned = src.min_rating === "learned" || src.max_eta === "learned";
    return `<span class="tag v3-defaults" title="${esc(r.defaults_basis || "Ziggy's defaults until you change them")}">${learned ? "Learned from your places" : "Ziggy defaults"}</span>`;
  }
  function rulesRow(d) {
    const r = d.rules, chips = [];
    chips.push(`<span class="tag ${r.diet === "nonveg" ? "" : "good"}">${DIET_WORD[r.diet] || esc(r.diet)}</span>`);
    (r.allergens || []).forEach(a => chips.push(`<span class="tag warn">No ${esc(a.replace("_", " "))}</span>`));
    chips.push(`<span class="tag">${r.min_rating ? `${r.min_rating}★ and up` : "Any rating"}</span>`);
    chips.push(`<span class="tag">${r.max_eta ? `≤ ${r.max_eta} min` : "Any delivery time"}</span>`);
    chips.push(defaultsTag(r));
    return `<div class="v3-rules" aria-label="Your rules">${chips.join("")}<button class="link small" data-v3-sheet="rules">Edit rules</button></div>`;
  }
  const entryLabel = (e) => `${e.name}${e.price != null ? ` · ₹${Math.round(e.price)}` : ""}${e.restaurant ? ` · ${e.restaurant}` : ""}`;
  function mealLine(d, meal) {
    const p = d.picks[meal], pool = d.pools[meal] || [];
    const opts = pool.map(e => `<option value="${esc(e.ref)}" ${p && p.ref === e.ref ? "selected" : ""} ${e.hard ? "disabled" : ""}>${esc(entryLabel(e))}${e.reasons.length ? ` — ${esc(e.reasons[0])}` : ""}</option>`).join("");
    const label = mealName(meal);
    const id = `v3-pick-${meal}`;
    const qty = p ? `<span class="stepper sm v3-qty"><button type="button" data-v3-pqty="${meal}:-1" aria-label="One fewer ${esc(p.name)}" ${p.qty <= 1 ? "disabled" : ""}>${ICON.minus}</button><b aria-live="polite">${p.qty}×</b><button type="button" data-v3-pqty="${meal}:1" aria-label="One more ${esc(p.name)}" ${p.qty >= 8 ? "disabled" : ""}>${ICON.plus}</button></span>` : "";
    const why = p && p.reasons?.length ? `<p class="fine v3-why">${esc(p.reasons.join(" · "))}</p>` : "";
    const how = p ? (p.how === "ziggy" ? `<span class="tag hot">Ziggy picked</span>` : p.how === "surprise" ? `<span class="tag hot">Surprise</span>` : "") : "";
    const select = pool.length || p
      ? `<label class="sr" for="${id}">${label} from your usual meals</label><select id="${id}" class="v3-select" data-v3-pick="${meal}">
          <option value="" ${p ? "" : "selected"}>${pool.length ? `Pick from your ${usual(meal)}` : `No ${usual(meal)} yet`}</option>${opts}
          ${p && !pool.some(e => e.ref === p.ref) ? `<option value="${esc(p.ref)}" selected>${esc(entryLabel(p))}</option>` : ""}
          <option value="${ADD_ANOTHER}">+ Add another ${meal}…</option></select>`
      : `<button class="secondary small" data-v3-add="${meal}">${ICON.plus}Add a usual ${meal}</button>`;
    return `<div class="v3-meal" data-v3-meal="${meal}"><span class="v3-meal-name">${label}${mealTime(d, meal)}</span>
      <div class="v3-meal-pick">${select}${qty}</div>
      <div class="v3-meal-meta">${p?.price != null ? `<span class="fine">${money(p.price * p.qty)}${p.price_basis ? ` <i>(${esc(p.price_basis)})</i>` : ""}${p.bill ? ` <span class="v3-bill" title="${esc(p.bill.label)}">· ${p.bill.estimated ? "~" : ""}${money(p.bill.total)} with delivery, fees &amp; GST${p.bill.estimated ? " (estimate)" : ""}</span>` : ""}</span>` : ""}${how}
        ${wide() ? "" : usualBtn(meal, pool.length)}<button class="link small" data-v3-swap="${meal}" aria-haspopup="dialog" aria-label="Swap ${meal}: other options">${ICON.swap}Swap</button></div>${why}${confirmNote(d, meal)}${wide() ? "" : usualDrawer(d, meal)}</div>`;
  }
  const ADD_ANOTHER = "__add__";
  const WEEK_MODES = { favourites: "From my favourites only", favourites_cook: "Favourites + cook at home",
    favourites_new: "Favourites + new places at or above my rating", surprise: "Surprise me (within my rules)" };
  // usab #14: a meal eaten at the person's own time shows it ("Dinner · 23:00").
  const mealTime = (d, meal) => d.meal_times?.[meal]?.own ? ` <span class="fine">· ${esc(d.meal_times[meal].at)}</span>` : "";
  // usab #27: a dish greyed out by a soft rule (rating, delivery time, not on the menu now) is
  // picked only after "Pick anyway", or with the rule relaxed just enough to let it in.
  function relaxFor(e, rules) {
    if (rules.max_eta && e.eta_min != null && e.eta_min > rules.max_eta) {
      const v = MAX_ETAS.map(([n]) => n).find(n => n && n >= e.eta_min) || 0;
      return { key: "max_eta", value: v, label: v ? `Relax to ≤${v} min` : "Any delivery time" };
    }
    if (rules.min_rating && e.rating != null && e.rating < rules.min_rating) {
      const v = [...MIN_RATINGS].map(([n]) => n).reverse().find(n => n <= e.rating) || 0;
      return { key: "min_rating", value: v, label: v ? `Relax to ${v}★+` : "Any rating" };
    }
    return null;
  }
  function confirmNote(d, meal) {
    const c = st().confirm;
    if (!c || c.meal !== meal) return "";
    const e = (d.pools[meal] || []).find(x => x.ref === c.ref);
    if (!e) return "";
    const relax = relaxFor(e, d.rules);
    return `<div class="note warn v3-confirm" role="alert"><span style="flex:1"><b>${esc(e.name)}</b>: ${esc(e.reasons.join(" · "))}. Pick anyway?
      <span class="acts"><button class="link" data-v3-confirm="pick">Pick anyway</button>${relax ? ` <button class="link" data-v3-confirm="relax" data-v3-relax="${relax.key}:${relax.value}">${esc(relax.label)}</button>` : ""} <button class="link" data-v3-confirm="cancel">Cancel</button></span></span></div>`;
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
  // usab #1: today's pick carries its own "when" (same arithmetic as the Week); the plan cell
  // is only the fallback.
  function earlyOrder(meal) {
    const w = st().data?.picks?.[meal]?.when;
    if (w) return w.cart_state === "early" ? { meal, sid: w.session_id, at: w.label || w.order_at, opens: w.cart_opens_label, on: !!w.reminder } : null;
    const o = todayCell(meal)?.order;
    return o && o.cart_state === "early" ? { meal, sid: todayCell(meal).session_id, at: o.label || o.order_at, opens: o.cart_opens_label } : null;
  }
  // usab #21: say when the cart opens, and remind then (per meal, #22).
  const remindBtn = (e, cls) => `<button class="${cls}" data-v3-remind="${e.meal}" aria-haspopup="dialog">${ICON.cart}<span>${e.opens ? `Cart opens ${esc(e.opens)} · ${e.on ? "reminder set" : "remind me"}` : `Remind me at ${esc(e.at)}`}</span></button>`;
  function actions(d) {
    const mode = st().mode, any = Object.keys(d.picks).length;
    const cartable = pickedMeals(d).filter(m => d.picks[m] && d.picks[m].source === "swiggy" && d.picks[m].kind === "order");
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
    // Hidden until there is a second source to compare with (audit #35): one price is no comparison.
    if (!c || !c.others || c.others === "coming") return "";
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
  // r3 #6: the snack line shows only once there is a usual snack (or one is picked).
  function snackRow(d) {
    return (d.pools.snack || []).length || d.picks.snack ? mealLine(d, "snack") : "";
  }
  // Phone: usual meals for a meal that isn't on today (say dinner on a B+L day) stay reachable.
  function otherUsual(d) {
    const rest = MEAL_ORDER.filter(m => !d.meals.includes(m));
    if (!rest.length) return "";
    return `<details class="v3-other-usual"${st().otherOpen ? " open" : ""}><summary class="fine" data-v3-other="1">Show other meals</summary>${rest.map(m => `<div class="v3-other-row"><span class="fine">${cap1(m)} (not today)</span>${usualBtn(m, (d.pools[m] || []).length)}</div>${usualDrawer(d, m)}`).join("")}</details>`;
  }
  // The Swiggy cart, its real bill, checkout and tracking: the classic block, always shown
  // here (the classic Today hides it when its meal card already shows the bill).
  function cartBlock() {
    const keep = S.carts;
    S.carts = null;
    try { return `<div class="v3-cart">${liveCartBlock()}${cartOffers()}</div>`; } finally { S.carts = keep; }
  }
  // At the cart, and only there: the coupons Swiggy lists for it (audit #9). Shown, never applied.
  function cartOffers() {
    const c = S.liveCart, v = st();
    if (!c || c.orderable === false) return "";
    const key = `${c.item}|${c.to_pay}`;
    if (v.offersKey !== key) {
      v.offersKey = key; v.offers = null;
      api(`/api/user/${S.userId}/swiggy/live-cart/offers`).then(r => { if (v.offersKey === key) { v.offers = r; render(); } }).catch(() => {});
    }
    const list = v.offers?.coupons || [];
    if (!list.length) return "";
    return `<section class="card tight v3-coupons" aria-label="Offers for this cart"><p class="k">Offers Swiggy lists for this cart</p>
      <ul class="fine">${list.map(o => `<li><b>${esc(o.code)}</b>${o.title ? ` · ${esc(o.title)}` : ""}</li>`).join("")}</ul>
      <p class="fine">Apply one in Swiggy at checkout if it fits. Ziggy counts a saving only once Swiggy's bill shows it.</p></section>`;
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

  // ux #12: the recovery code, once, on the first Today after onboarding (it was only ever in
  // Me → Account, so nobody saw it). Copy / Download reuse app.js's actions; "I've saved it" hides it.
  function recoveryCard() {
    const code = S.freshCode && S.freshCode.startsWith(`${S.userId}.`) ? S.freshCode : null;
    if (!code || S.codeAcked === S.userId) return "";
    return `<section class="card v3-recovery" aria-labelledby="v3-rec-h"><h2 class="h3" id="v3-rec-h">Save your recovery code</h2>
      <p class="fine">It opens this profile on another phone or after clearing your browser. Ziggy shows it only now; you can make a new one later in Me → Account &amp; devices.</p>
      <code class="code v3-rec-code">${esc(code)}</code>
      <div class="row"><button class="small secondary" data-act="copy-recovery">Copy code</button><button class="small secondary" data-act="download-recovery">Download</button><button class="small ghost" data-v3-act="code-saved">I've saved it</button></div>
      <p class="fine">Anyone with this code can open your profile. Don't share it.</p></section>`;
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
      ${recoveryCard()}${setupCard(d)}
      <section class="card v3-meals" aria-label="Today's meals">${d.meals.map(m => mealLine(d, m)).join("")}${snackRow(d)}
        <div class="row v3-decide"><button class="secondary small" data-v3-sheet="decide">Decide for me</button>
          <span class="fine">Only when you ask. Picks from your usual meals within your rules and ${money(d.budget.amount)}.</span></div>${decideNote()}${askTime(d)}${wide() ? "" : otherUsual(d)}</section>
      ${actions(d)}
      ${compareRow(d)}
      ${cartBlock()}
      ${recapCard(d)}
      ${saveCard(d)}</div>
      <div class="v3-side"><div class="v3-top${weatherCard(d.weather) ? "" : " solo"}">${budgetCard(d)}${weatherCard(d.weather)}</div>${rulesRow(d)}</div></div>
      ${wide() ? `<h2 class="k v3-pools-h">Your usual meals</h2>
      <div class="v3-pools">${MEAL_ORDER.map(m => poolCard(d, m)).join("")}${(d.pools.snack || []).length ? poolCard(d, "snack") : ""}</div>` : ""}
    </div>`;
  }
  // usab #14: asked once, "When do you usually eat dinner?" (a late-dinner hint from Swiggy history).
  function askTime(d) {
    const a = d.ask_meal_time;
    if (!a) return "";
    const opts = [...new Set([a.hint?.at, ...a.options].filter(Boolean))];
    return `<div class="v3-ask-time" role="group" aria-label="When do you usually eat ${a.meal}?"><p class="fine"><b>When do you usually eat ${a.meal}?</b> ${a.hint ? esc(a.hint.why) : "Order-by times and reminders follow it."}</p>
      <div class="chips">${opts.map(t => `<button class="chip small" data-v3-mealtime="${a.meal}:${t}">${t}</button>`).join("")}<button class="chip small ghost" data-v3-mealtime="dismiss">Not now</button></div></div>`;
  }
  // usab #28: why Decide couldn't fill a meal, with what to do about it.
  function decideNote() {
    const n = st().decideNote;
    if (!n) return "";
    const btn = (a) => a.act === "add" ? `<button class="link" data-v3-add="${a.meal}">${esc(a.label)}</button>` : `<button class="link" data-v3-surprise="1">${esc(a.label)}</button>`;
    return `<div class="note warn v3-decide-note" role="status"><span style="flex:1">${esc(n.note)} <span class="acts">${n.actions.map(btn).join(" ")} <button class="link" data-v3-decidenote="close">OK</button></span></span></div>`;
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
    const open = (S.view?.grid || []).some(d => Object.values(d.meals).some(m => m.open));
    const decideBtn = open ? `<div class="row v3-week-decide"><button class="secondary small" data-v3-weekdecide="1">Decide for me</button><span class="fine">Open meals stay open until you ask.</span></div>` : "";
    return `${envStrip()}${decideBtn}<div class="row v3-week-top"><label class="check"><input type="checkbox" data-v3-nutrition ${on ? "checked" : ""}> Show nutrition</label>
      <span class="fine">${on ? "A day, on average, from dish-name estimates. Unknown dishes add nothing." : "Off by default. Turn on to see the week against your daily targets."}</span></div>
      ${bars ? `<section class="card v3-nut" aria-label="Nutrition">${bars}</section>` : ""}`;
  }
  function nutritionOn() {
    const d = S.v3?.data;
    if (d) return !!d.rules.nutrition;
    return store.get(`ziggy.v3.nutrition.${S.userId}`) === "1";
  }

  /* -------------------------------------------------------------- the week's envelope (fix round 4)
     The weekly budget is an envelope; a day's share is a guide line, never a wall. The Week
     strip shows each day as a chip (₹ planned, a tick at its fair share); a tap opens one
     slider for that day (no wall of sliders). Today's budget card opens the same control. */
  const weekEnv = () => S.view?.budget?.envelope || null;
  const tt = (en, vars) => (typeof t === "function" ? t(en, vars) : en);       // app.js English / தமிழ்
  const envFor = (date) => { const e = st().env; return e && e.days?.some(x => x.date === date) ? e : weekEnv(); };
  function envStrip() {
    const e = weekEnv();
    if (!e || !Array.isArray(e.days)) return "";
    const days = e.days.filter(d => d.state !== "before");
    const top = Math.max(1, ...days.map(d => Math.max(d.planned || 0, d.target || 0, d.guide || 0, d.reserve || 0)));
    const chip = (d) => {
      const off = d.state === "off", past = d.state === "past";
      // a day the person set shows their amount (the bar still shows what's planned in it)
      const amt = past ? d.spent : off ? d.reserve : d.set != null ? d.target : d.planned;
      const h = Math.min(100, Math.round(((past ? d.spent : (d.planned || 0)) / top) * 100));
      const g = Math.min(100, Math.round((d.guide / top) * 100));
      const label = `${d.weekday}: ${off ? `Off, ${money(d.reserve)} kept aside` : `${money(amt)} ${past ? "spent" : "planned"}, share ${money(d.guide)}`}${d.set != null ? `, set to ${money(d.set)}` : ""}`;
      return `<button type="button" class="v3-daychip${off ? " off" : ""}${past ? " past" : ""}${d.is_today ? " today" : ""}${d.set != null ? " set" : ""}" data-v3-day="${esc(d.date)}" aria-label="${esc(label)}"${past ? " disabled" : ""}>
        <span class="v3-dc-d">${esc(d.weekday)}</span><span class="v3-dc-bar" aria-hidden="true"><i style="height:${h}%"></i><s style="bottom:${g}%"></s></span>
        <b>${off ? tt("Off") : money(amt)}</b></button>`;
    };
    const kept = e.kept?.text ? ` · ${esc(e.kept.text)}` : "";
    const parts = [`${money(e.spent)} spent`, `${money(e.committed)} picked`, `${money(Math.max(0, e.flexible))} for Ziggy to fill`];
    if (e.reserve > 0) parts.push(`${money(e.reserve)} kept for Off days`);
    const over = e.is_over ? `<p class="fine v3-env-over">Your own picks take this week ${money(e.over)} over ${money(e.envelope)}. They stay.</p>` : "";
    const low = e.low ? `<p class="fine v3-env-low">${esc(e.low.text)}</p>` : "";
    return `${impactNote()}<section class="card v3-env rise" aria-label="This week's money by day">
      <p class="fine">${money(e.envelope)} this week${e.override != null ? " (this week only)" : ""}: ${parts.join(" · ")}${kept}</p>
      <div class="v3-daychips" role="group" aria-label="Days. Tap one to set its amount or turn it Off">${days.map(chip).join("")}</div>${over}${low}
      <p class="fine v3-env-help">${tt("The line on each day is its fair share: a guide, not a limit.")}</p></section>`;
  }
  // Rest-of-week preview while the slider moves: the days that follow the envelope share what this day leaves.
  function restPreview(e, d, value) {
    // days whose own picks already use their whole share don't take more; the rest share what this day leaves
    const flex = e.days.filter(x => x.date !== d.date && x.state === "plan" && x.set == null && x.date >= todayIso()
      && (x.target_exact ?? x.target) > x.spent + x.committed + 0.5);
    if (!flex.length) return "No other day shares this week's money.";
    const pool = flex.reduce((a, x) => a + (x.target_exact ?? x.target), 0) + (d.target_exact ?? d.target) - value;
    if (pool < 0) return `${money(-pool)} over this week's ${money(e.envelope)}. Your choice stays; Ziggy shows it, never blocks it.`;
    return `Rest of week: ${money(pool)} over ${flex.length} day${flex.length === 1 ? "" : "s"} ≈ ${money(pool / flex.length)}/day`;
  }
  const dayName = (iso) => { try { return new Date(`${iso}T12:00:00`).toLocaleDateString("en-IN", { weekday: "long" }); } catch (_) { return iso; } };
  function daySheet() {
    const s = st().sheet, e = envFor(s.date), d = e?.days?.find(x => x.date === s.date);
    const title = s.date === todayIso() ? "Today's budget" : `${dayName(s.date)}'s budget`;
    if (!d) return sheet("day", title, `<p class="fine">Loading the week…</p>`, "day budget");
    const off = (s.state ?? d.state) === "off";
    // the slider's floor: what the day already has (spent + picked), on the slider's ₹10 grid
    const min = Math.floor(Math.max(0, d.min ?? Math.ceil(d.spent + d.committed)) / 10) * 10;
    const cur = Math.max(min, Math.round(s.value ?? d.target));
    const max = Math.max(min + 500, Math.ceil((Math.max(d.guide * 3, cur + 200)) / 50) * 50);
    const gp = Math.max(0, Math.min(100, ((d.guide - min) / Math.max(1, max - min)) * 100));
    const plan = `<div class="seg v3-dayplan" role="group" aria-label="Plan or Off">${["plan", "off"].map(k => `<button type="button" class="chip small" data-v3-dayplan="${k}" aria-pressed="${(off ? "off" : "plan") === k}">${tt(k === "plan" ? "Plan" : "Off")}</button>`).join("")}</div>`;
    const body = off
      ? `${plan}<p class="fine">Off: no meals planned, no reminders, no money assumed.${e.settings?.scope !== "planned" ? ` Its share (${money(d.guide)}) is kept aside for what you eat that day.` : ""}</p>`
      : `${plan}<div class="v3-slider"><label for="v3-day-slider" class="sr">${esc(title)} in rupees</label>
          <output id="v3-day-out" class="money v3-big" for="v3-day-slider">${money(cur)}</output>
          <div class="v3-slider-track"><input id="v3-day-slider" type="range" min="${min}" max="${max}" step="10" value="${cur}" aria-valuetext="${money(cur)}">
            <span class="v3-guide" style="left:${gp.toFixed(1)}%" aria-hidden="true"></span></div>
          <p class="fine v3-slider-scale"><span>${money(min)}${min ? " already spent or picked" : ""}</span><span>${money(max)}</span></p>
          <p class="fine">The line is ${s.date === todayIso() ? "today's" : "this day's"} fair share, ${money(d.guide)}: a guide, not a limit.</p></div>
        <p class="fine" id="v3-day-preview" role="status">${esc(restPreview(e, d, cur))}</p>
        ${(d.flags || []).map(f => `<p class="fine v3-env-low">${esc(f.text)}</p>`).join("")}
        <label class="check"><input type="checkbox" id="v3-day-hard" ${(s.mode ?? d.mode) === "hard" ? "checked" : ""}> ${tt("Never above this (a hard limit)")}</label>
        <button class="primary" data-v3-act="day-save" id="v3-day-save">Use ${money(cur)} for ${s.date === todayIso() ? "today" : esc(dayName(s.date))}</button>
        ${d.set != null ? `<button class="ghost" data-v3-act="day-follow">${tt("Follow the week again")}</button>` : ""}
        <p class="fine">Only this day. The days still ahead share the rest; your weekly budget doesn't change.</p>`;
    return sheet("day", title, body, "day budget");
  }
  async function openDay(date) {
    const sh = st().sheet;
    if (sh?.kind !== "day" || sh.date !== date) { st().sheet = { kind: "day", date }; render(); }
    st().env = await api(`/api/user/${S.userId}/envelope?week=${encodeURIComponent(date)}`);
    render();
  }
  async function afterMoney(r) {
    if (r?.envelope) st().env = r.envelope;
    if (r && r.applied === false) toast("Another device changed this day just now. Showing its amount.");
    if (S.planId) { try { S.view = await api(`/api/plan/${S.planId}`); } catch (_) { /* the Week reloads on its own */ } }
    if (S.v3?.data) await load(); else render();
  }
  async function saveDay(amount) {
    const s = st().sheet;
    const mode = document.getElementById("v3-day-hard")?.checked ? "hard" : "soft";
    const r = await api(`/api/user/${S.userId}/day-budget`, "PUT", { date: s.date, amount, mode, set_at: new Date().toISOString() });
    st().sheet = null;
    toast(amount == null ? "This day follows the week again." : "Saved. The days ahead re-balanced.");
    await afterMoney(r);
  }
  async function setDayPlan(state) {
    const s = st().sheet;
    const r = await api(`/api/user/${S.userId}/day-plan`, "PUT", { date: s.date, state, set_at: new Date().toISOString() });
    s.state = state;
    toast(state === "off" ? `${s.date === todayIso() ? "Today" : dayName(s.date)} is Off: nothing planned, no reminders.` : "Back to planning this day.");
    await afterMoney(r);
  }
  // What a pick or pin costs the week: shown after the person's own choice, never as a block.
  function impactNote() {
    const imp = S.v3?.impact;
    if (!imp?.text) return "";
    const btn = (o) => `<button class="small ${o.act === imp.default ? "primary" : "secondary"}" data-v3-impact="${esc(o.act)}"${o.amount ? ` data-v3-amount="${o.amount}"` : ""}>${esc(o.act === "take_from_week" ? tt(o.label) : o.label)}</button>`;
    const sugg = imp.showSuggest && imp.suggest?.length ? `<div class="stack v3-impact-sugg" style="gap:6px">${imp.suggest.map(x =>
      `<div class="row" style="justify-content:space-between;gap:8px"><span class="fine">${esc(dayName(x.date))} ${esc(x.meal)}: ${esc(x.name || "")} ${money(x.cost)}</span><button class="small secondary" data-v3-impact-skip="${x.session_id}">Skip it</button></div>`).join("")}<p class="fine">Or open that meal in the Week to cook it instead.</p></div>` : "";
    return `<div class="note v3-impact" role="status"><span style="flex:1"><b>${esc(imp.text)}</b>
      <span class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px">${(imp.options || []).map(btn).join("")}</span>${sugg}</span></div>`;
  }
  async function impactAct(act, amount) {
    const imp = st().impact;
    if (act === "suggest") { imp.showSuggest = true; render(); return; }
    if (act === "raise_week") {
      const d = new Date(`${imp.date}T12:00:00`); d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
      const ws = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const r = await api(`/api/user/${S.userId}/week-override`, "PUT", { week_start: ws, amount: Number(amount), set_at: new Date().toISOString() });
      st().impact = null; toast(`This week is ${money(Number(amount))}. Next week goes back to your usual budget.`);
      await afterMoney(r); return;
    }
    st().impact = null; render();
  }
  function showImpact(imp) { if (imp?.text) { st().impact = imp; render(); } }

  /* -------------------------------------------------------------- sheets */
  const sheet = (ov, title, body, label) => `<div class="sheet-bg" data-ov="v3-${ov}" data-v3-close="1"><section class="sheet v3-sheet" role="dialog" aria-modal="true" aria-labelledby="v3-${ov}-h">
    <span class="grab" aria-hidden="true"></span><div class="sheet-head"><h2 class="h2" id="v3-${ov}-h">${title}</h2>
    <button class="icon-btn" data-v3-close="1" aria-label="Close ${label || "sheet"}">${ICON.close}</button></div>${body}</section></div>`;
  function decideSheet() {
    const d = st().data || { meals: [], picks: {}, modes: WEEK_MODES }, pick = st().sheet.choice || null;
    const what = st().sheet.scope === "week" ? "the week's open meals" : d.meals.filter(m => !d.picks[m]).join(" and ") || "today";
    return sheet("decide", "Decide for me", `<p class="sub">How should Ziggy pick ${what}? Meals you already picked stay.</p>
      <div class="stack" role="radiogroup" aria-label="How to decide">${Object.entries(d.modes).map(([k, l]) => `<button type="button" class="opt" role="radio" aria-checked="${pick === k}" data-v3-choice="${k}"><span><b>${esc(l)}</b></span></button>`).join("")}</div>
      <button class="primary" data-v3-act="decide" ${pick ? "" : "disabled"}>Decide</button>
      <p class="fine">Never past your diet or allergies. Dishes that break a rule stay greyed out.</p>`, "Decide for me");
  }
  function rulesSheet() {
    const r = st().data.rules;
    return sheet("rules", "Your rules", `<p class="k">Minimum rating</p><div class="chips">${MIN_RATINGS.map(([v, l]) => `<button class="chip" data-v3-rule="min_rating:${v}" aria-pressed="${Number(r.min_rating) === v}">${l}</button>`).join("")}</div>
      <p class="k">Delivery time</p><div class="chips">${MAX_ETAS.map(([v, l]) => `<button class="chip" data-v3-rule="max_eta:${v}" aria-pressed="${Number(r.max_eta) === v}">${l}</button>`).join("")}</div>
      <p class="fine">Dishes outside these stay in your usual meals, greyed out with the reason. Rating and time are what Swiggy showed when you added the dish.</p>
      ${mealsSetting(st().data)}
      <p class="k">Diet and allergies</p><p class="fine">${esc(DIET_WORD[r.diet] || r.diet)}${r.allergens.length ? ` · avoiding ${esc(r.allergens.join(", ").replace(/_/g, " "))}` : ""}. A dish not marked vegetarian is treated as not vegetarian.</p>
      <button class="secondary" data-go="more:settings">Change diet or allergies</button>`, "rules");
  }
  // usab #32 + #14: which meals the person orders, and when they eat them.
  function mealsSetting(d) {
    const on = new Set(d.meals), times = d.meal_times || {};
    return `<p class="k">Meals I order</p><div class="chips" role="group" aria-label="Meals I order">${MEAL_ORDER.map(m => `<button class="chip" data-v3-mymeal="${m}" aria-pressed="${on.has(m)}">${cap1(m)}</button>`).join("")}</div>
      <p class="k">When I eat</p><form id="v3-times" class="v3-times">${d.meals.map(m => `<label>${cap1(m)} <input type="time" name="${m}" value="${esc(times[m]?.at || "")}" step="900"></label>`).join("")}
        <button class="secondary small" type="submit">Save times</button></form>
      <p class="fine">Order-by times, the cart window and reminders follow these.</p>`;
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
      swiggyReady() && !cell && meal !== "snack" ? `<button class="secondary small" data-hungry="${meal}">Add ${meal} to the week plan now</button>` : "",
    ].filter(Boolean).join("");
    const empty = !(d.pools[meal] || []).length
      ? (d.swiggy.connected ? `<p class="fine">No ${usual(meal)} yet. Add one from a Swiggy menu or type it in.</p>`
        : `<p class="fine">Swiggy isn't connected yet, so Swap can only offer dishes you add yourself. ${swiggySignInOpen() ? `<button class="link" data-act="connect-open">Connect Swiggy</button> to swap from real menus, or add` : "Add"} a ${meal} you usually have.</p>`)
      : `<p class="fine">None of your ${usual(meal)} passes your rules yet.</p>`;
    // Swap's "Nearby now" (audit #26): only once Swiggy is connected (menus already read for the address)
    const nb = s.nearby?.dishes || [];
    const nearby = nb.length ? `<p class="k">Nearby now</p><div class="stack" style="gap:6px">${nb.map((x, n) => `<button class="pick v3-swap-row" data-v3-nearby="${n}"><span class="t"><b>${esc(x.name)}</b><span>${esc([x.restaurant, `₹${Math.round(x.price)}`, x.rating != null ? `${x.rating}★` : null].filter(Boolean).join(" · "))}</span></span></button>`).join("")}</div>
      <p class="fine">${esc(s.nearby.note || "")}</p>` : "";
    return sheet("swap", `Swap ${meal}`, `${ok.length ? `<p class="k">From your ${usual(meal)}</p><div class="stack" style="gap:6px">${ok.map(row).join("")}</div>` : empty}
      ${nearby}
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
  // Suggested favourites from the person's own Swiggy orders (W7: read only on a tap, after this
  // consent, minimal fields, deletable). Ticked ones go into W5's Today pools. r3: the consent is
  // one line + Details (#29, "Not now" remembered); whole past orders come as usual orders with
  // quantities (#7); sides only ever ordered with a main aren't offered alone; the places save
  // in one tap (#8); "Refresh from my orders" shows when they were last read (#30).
  const suggKey = () => `ziggy.v3.sugg-later.${S.userId}`;
  const sameMeal = (i, meal) => (i.pool_meals || [i.pool_meal]).includes(meal);
  function suggRows(g, meal) {
    const pool = new Set((st().data.pools[meal] || []).map(e => `${(e.restaurant || "").toLowerCase()}|${e.name.toLowerCase()}`));
    const has = (rest, name) => pool.has(`${rest.toLowerCase()}|${name.toLowerCase()}`);
    const combos = (g.combos || []).filter(c => sameMeal(c, meal) && !c.items.every(i => has(c.restaurant_name, i.name)))
      .map(c => ({ kind: "combo", ...c }));
    const dishes = g.items.filter(i => sameMeal(i, meal) && !i.with_main_only && !has(i.restaurant_name, i.item_name))
      .map(i => ({ kind: "dish", ...i }));
    return [...combos, ...dishes];
  }
  function suggRow(i, n) {
    const title = i.kind === "combo" ? i.label : `${i.qty > 1 ? `${i.qty}× ` : ""}${i.item_name}`;
    const meta = [i.restaurant_name, `${i.times}× ordered`, i.rating != null ? `${i.rating}★` : null, i.side ? "side" : null];
    return `<label class="v3-check v3-sugg-row"><input type="checkbox" data-v3-sugg="${n}"><span class="t"><span><b>${esc(title)}</b>${i.kind === "combo" ? ` <span class="tag">whole order</span>` : ""}</span>
      <span class="fine">${esc(meta.filter(Boolean).join(" · "))}</span></span></label>`;
  }
  function suggBlock(s, meal) {
    const g = s.sugg;
    if (!g) return "";
    if (!g.imported) {
      if (store.get(suggKey()) === "1" && !s.suggOpen) return `<p class="fine v3-sugg-later"><button class="link small" data-v3-act="sugg-open">Suggest from my Swiggy orders</button></p>`;
      return `<section class="v3-sugg stack" aria-label="Suggestions from your Swiggy orders"><p class="k">Suggest from my Swiggy orders</p>
      <p class="fine">${esc(g.consent_short || g.consent)}</p><details class="v3-consent-more"><summary class="fine">Details</summary><p class="fine">${esc(g.consent)}</p></details>
      <div class="row"><button class="secondary small" data-v3-act="sugg-import">Suggest from my Swiggy orders</button><button class="ghost small" data-v3-act="sugg-later">Not now</button></div></section>`;
    }
    const items = suggRows(g, meal);
    s.suggShown = items;
    const unsaved = (g.restaurants || []).filter(r => !r.saved);
    const places = unsaved.length ? `<div class="row v3-sugg-places"><span class="fine" style="flex:1">${esc(unsaved.slice(0, 5).map(r => r.restaurant_name).join(", "))}${unsaved.length > 5 ? "…" : ""}</span>
      <button class="secondary small" data-v3-act="sugg-places">Save ${unsaved.length === 1 ? "this place" : `these ${unsaved.length} places`}</button></div>` : "";
    const when = g.imported_at ? String(g.imported_at).slice(0, 10).split("-").reverse().join("/") : null;
    return `<section class="v3-sugg stack" aria-label="Suggestions from your Swiggy orders"><p class="k">From your Swiggy orders</p>
      ${items.length ? `<form id="v3-sugg" class="stack" style="gap:6px">${items.map(suggRow).join("")}
        <button class="secondary small" type="submit">Add ticked to your ${usual(meal)}</button></form>`
        : `<p class="fine">Nothing new for ${meal} in your recent Swiggy orders.</p>`}
      ${places}
      <p class="fine">${esc(g.note)}${g.attribution ? ` ${esc(g.attribution)}.` : ""}</p>
      <p class="fine v3-sugg-foot">${when ? `Last read ${esc(when)} · ` : ""}<button class="link small" data-v3-act="sugg-refresh">Refresh from my orders</button> · <button class="link small" data-v3-act="sugg-delete">Delete these suggestions</button></p></section>`;
  }
  // r3 #8: places from past orders open their menu in one tap (the id comes with them).
  function histChips(s) {
    const g = s.sugg;
    const list = g?.imported ? g.restaurants.map(r => ({ id: r.restaurant_id, name: r.restaurant_name, rating: r.rating }))
      : (s.history || []).map(h => (typeof h === "string" ? { id: null, name: h } : h));
    if (!list.length) return "";
    return `<p class="k">From your Swiggy orders</p><div class="chips">${list.slice(0, 8).map(h => h.id
      ? `<button class="chip small" data-v3-place="${esc(h.id)}" data-v3-pname="${esc(h.name)}" data-v3-prating="${h.rating ?? ""}" data-v3-peta="">${esc(h.name)}</button>`
      : `<button class="chip small" data-v3-hist="${esc(h.name)}">${esc(h.name)}</button>`).join("")}</div>`;
  }
  /* ---- a restaurant's menu in the Add sheet (audit #17, #9, #19, #34) ---- */
  const MENU_SHOWN = 60;
  // "30" → "30 min"; a range or text Swiggy sends ("25-30 mins") is shown as it is.
  const etaText = (e) => e == null || e === "" ? null : /^\d+(\.\d+)?$/.test(String(e).trim()) ? `${String(e).trim()} min` : String(e);
  const offerChip = (o) => o?.label ? `<span class="tag good v3-offer" title="${esc(o.text || o.label)}. Swiggy applies it at checkout if your order qualifies.">${esc(o.label)}</span>` : "";
  function menuRow(i) {
    const price = i.price != null ? `${i.list_price ? `<s class="muted">₹${Math.round(i.list_price)}</s> ` : ""}₹${Math.round(i.price)}` : "Price in cart";
    const badges = [i.best_value ? `<span class="tag hot v3-best">Best value</span>` : "", i.bestseller ? `<span class="tag v3-bestseller">Bestseller</span>` : ""].join("");
    const notes = [i.has_options ? "sizes or add-ons: chosen in Swiggy" : null, i.in_stock === false ? "out of stock" : null, i.safety?.note || null].filter(Boolean);
    return `<button class="pick v3-swap-row v3-menu-row" data-v3-additem="${esc(i.id)}" data-name="${esc(i.name.toLowerCase())}" data-veg="${i.veg ? 1 : 0}" ${i.in_stock === false ? "disabled" : ""}>
      ${i.image ? `<img class="v3-thumb" src="${esc(i.image)}" alt="" loading="lazy" width="44" height="44">` : ""}
      <span class="vegdot ${i.veg ? "veg" : "nonveg"}" aria-label="${i.veg ? "Vegetarian" : "Not marked vegetarian"}"></span>
      <span class="t"><b>${esc(i.name)}</b><span>${price}${badges ? ` ${badges}` : ""}${notes.length ? ` · ${esc(notes.join(" · "))}` : ""}</span></span>${ICON.plus}</button>`;
  }
  function menuBody(s, menu) {
    const veg = !!s.vegOnly;
    const items = menu.items.filter(i => !veg || i.veg);
    const shown = items.slice(0, MENU_SHOWN);
    // Sections in Swiggy's order; a dish shows once, under its first section.
    const groups = [];
    const at = {};
    for (const i of shown) {
      const sec = (i.categories && i.categories[0]) || "Menu";
      if (!(sec in at)) { at[sec] = groups.length; groups.push([sec, []]); }
      groups[at[sec]][1].push(i);
    }
    const total = menu.total_items && menu.total_items > menu.items.length ? menu.total_items : items.length;
    const best = menu.value ? `<p class="fine v3-value-note">${esc(menu.value.why)}</p>` : "";
    const status = menu.refreshing ? `<p class="fine v3-menu-status" role="status">${menu.partial ? "More dishes loading…" : "Checking for changes…"}</p>`
      : menu.source === "cache" && menu.age_min ? `<p class="fine v3-menu-status">Menu checked ${menu.age_min} min ago.</p>` : "";
    const sections = groups.map(([sec, rows], n) => `<details class="v3-sec" ${n < 4 || groups.length <= 6 ? "open" : ""}><summary class="k">${esc(sec)} <span class="fine v3-sec-n">· ${rows.length}</span></summary>
      <div class="stack" style="gap:6px">${rows.map(menuRow).join("")}</div></details>`).join("");
    const more = shown.length < total ? `<p class="fine v3-menu-count">Showing ${shown.length} of ${total}. Search to find the rest.</p>` : "";
    const found = s.menuSearch ? `<div class="stack v3-menu-found" style="gap:6px"><p class="k">On Swiggy for “${esc(s.menuSearch.query)}”</p>${s.menuSearch.items.length ? s.menuSearch.items.map(menuRow).join("") : `<p class="fine">No dish called that here.</p>`}</div>` : "";
    return `<button class="link small" data-v3-act="add-back">${ICON.back}All places</button>
      <p class="k v3-menu-head">${esc(menu.restaurant.name)}${s.place?.rating ? ` · ${s.place.rating}★` : ""}${etaText(s.place?.eta) ? ` · ${esc(etaText(s.place.eta))}` : ""} ${offerChip(menu.restaurant.offer || s.place?.offer)}</p>
      <form id="v3-menu-search" class="row v3-menu-tools"><label class="sr" for="v3-mq">Search this menu</label><input id="v3-mq" type="search" placeholder="Search this menu" value="${esc(s.menuQ || "")}" maxlength="80" style="flex:1;min-width:0">
        ${["veg", "vegan"].includes(st().data?.rules?.diet) ? "" : `<label class="chip small"><input type="checkbox" data-v3-vegonly ${veg ? "checked" : ""}> Veg only</label>`}</form>
      ${status}${best}${found}
      <div class="stack v3-menu" style="gap:6px">${sections || `<p class="fine">No dishes to show.</p>`}</div>${more}
      ${hiddenList(menu)}`;
  }
  // Typing filters the dishes already shown (no request); Enter asks Swiggy for the rest.
  function filterMenu(q) {
    const want = (q || "").trim().toLowerCase();
    document.querySelectorAll?.(".v3-menu [data-v3-additem]")?.forEach(b => { b.hidden = !!want && !String(b.dataset.name || "").includes(want); });
    document.querySelectorAll?.(".v3-menu details.v3-sec")?.forEach(d => {
      const any = [...d.querySelectorAll("[data-v3-additem]")].some(b => !b.hidden);
      d.hidden = !any; if (want && any) d.open = true;
    });
  }
  function addSheet() {
    const s = st().sheet, d = st().data, meal = s.meal;
    const tab = s.tab || (d.swiggy.connected && d.swiggy.address ? "swiggy" : "type");
    const tabs = `<div class="seg" role="group" aria-label="How to add">${d.swiggy.connected && d.swiggy.address ? `<button data-v3-addtab="swiggy" aria-pressed="${tab === "swiggy"}">Search Swiggy</button>` : ""}<button data-v3-addtab="type" aria-pressed="${tab === "type"}">Type it in</button></div>`;
    const meals = `<div class="chips" role="group" aria-label="Which meal">${ADD_MEALS.map(m => `<button class="chip small" data-v3-addmeal="${m}" aria-pressed="${m === meal}">${cap1(m)}</button>`).join("")}</div>`;
    let body;
    if (tab === "swiggy") {
      const res = s.results, menu = s.menu;
      if (menu) {
        body = menuBody(s, menu);
      } else if (s.opening) {
        body = `<button class="link small" data-v3-act="add-back">${ICON.back}All places</button>
          <p class="k">${esc(s.opening.name)}</p><div class="skeleton" aria-busy="true"><i></i><i></i><i></i></div>`;
      } else {
        body = `<form id="v3-search" class="row"><label class="sr" for="v3-q">Search Swiggy</label><input id="v3-q" type="search" placeholder="A restaurant or a dish" value="${esc(s.q || "")}" minlength="2" maxlength="80" style="flex:1;min-width:0">
          <button class="secondary small" type="submit">${ICON.search}<span>Search</span></button></form>
          ${histChips(s)}
          ${res ? (res.restaurants.length ? `<div class="stack" style="gap:6px">${res.restaurants.map(r => `<button class="pick v3-swap-row" data-v3-place="${esc(r.id)}" data-v3-pname="${esc(r.name)}" data-v3-prating="${r.rating ?? ""}" data-v3-peta="${esc(r.eta ?? "")}" data-v3-poffer="${esc(r.offer?.label || "")}">
              <span class="t"><b>${esc(r.name)}${r.sponsored ? ` <span class="tag">Ad</span>` : ""}</b><span>${esc([r.rating != null ? `${r.rating}★` : null, etaText(r.eta), r.area].filter(Boolean).join(" · "))} ${offerChip(r.offer)}</span></span>${ICON.right}</button>`).join("")}</div>`
            : `<p class="fine">Nothing found for “${esc(s.q)}”.</p>`) : ""}`;
      }
    } else {
      body = `<form id="v3-typed" class="stack"><label>Dish<input id="v3-t-name" required minlength="2" maxlength="80" placeholder="Cappuccino, burrito, idli…" value="${esc(s.draft?.name || "")}"></label>
        <div class="grid2"><label>Place (optional)<input id="v3-t-place" maxlength="80" placeholder="Where from" value="${esc(s.draft?.place || "")}"></label>
        <label>Price you pay (₹)<input id="v3-t-price" type="number" inputmode="numeric" min="1" max="20000" value="${esc(s.draft?.price || "")}"></label></div>
        <fieldset class="v3-fs"><legend class="k">Vegetarian?</legend><div class="chips">${[["1", "Yes"], ["0", "No"], ["", "Not sure"]].map(([v, l]) => `<label class="chip small"><input type="radio" name="v3-t-veg" value="${v}" ${v === (s.draft?.veg ?? "") ? "checked" : ""}> ${l}</label>`).join("")}</div></fieldset>
        <fieldset class="v3-fs"><legend class="k">I usually</legend><div class="chips">${[["order", "Order it"], ["cook", "Cook it"]].map(([v, l]) => `<label class="chip small"><input type="radio" name="v3-t-kind" value="${v}" ${v === (s.draft?.kind || "order") ? "checked" : ""}> ${l}</label>`).join("")}</div></fieldset>
        <button class="primary" type="submit">Add to your ${usual(meal)}</button>
        <p class="fine">Typed dishes are yours: Ziggy can share or cook them, but can only put Swiggy menu dishes in a cart.</p></form>`;
    }
    // A place is open (audit #18): the menu takes the sheet; suggestions and tabs come back on "All places".
    const placeOpen = tab === "swiggy" && (s.menu || s.opening);
    return sheet("add", placeOpen ? `Add to your ${usual(meal)}` : "New item / restaurant",
      placeOpen ? body : `${meals}${d.swiggy.connected ? suggBlock(s, meal) : ""}${tabs}${body}`, "add a dish");
  }
  function orderSheet() {
    const d = st().data;
    const picks = pickedMeals(d).filter(m => d.picks[m]).map(m => [m, d.picks[m]]);
    const rows = picks.map(([m, p]) => {
      const cartable = p.source === "swiggy" && p.kind === "order";
      return `<div class="mrow" style="cursor:default"><span class="t"><b>${cap1(m)}: ${p.qty}× ${esc(p.name)}</b><span class="s">${esc(p.restaurant || (p.kind === "cook" ? "Cook at home" : ""))}</span></span>
        ${cartable ? (earlyOrder(m) ? `<span class="stack" style="gap:2px;align-items:flex-end">${remindBtn(earlyOrder(m), "secondary small")}<span class="fine">Order by ${esc(earlyOrder(m).at)}</span></span>`
          : `<button class="secondary small" data-v3-cart="${m}">Add to Swiggy cart</button>`) : `<span class="fine">${p.kind === "cook" ? "Cook it" : "Typed by you: order it in your app"}</span>`}</div>`;
    }).join("");
    return sheet("order", "Place order", `<p class="fine">Swiggy carts hold one restaurant at a time, so each meal goes in on its own. You check the bill and pay in Swiggy.</p>
      <div class="card tight">${rows}</div>`, "place order");
  }
  // usab #22: a reminder for this one meal: a push nudge when its cart opens, or the meal's
  // own calendar file where push isn't available (iPhone outside the installed app).
  function remindSheet() {
    const d = st().data, meal = st().sheet.meal, e = earlyOrder(meal) || {};
    const ps = typeof pushState === "function" ? pushState() : "none";
    const ics = `/api/user/${S.userId}/reminders.ics?meal=${meal}&date=${d.date}`;
    const push = ps === "ready" || ps === "on" || (ps === "none" && typeof Notification !== "undefined")
      ? `<button class="primary" data-v3-act="remind-push">${e.on ? "Reminder set on this phone" : "Notify me on this phone"}</button>` : "";
    const ios = ps === "ios-install" ? `<p class="fine">On iPhone, notifications need Ziggy on your Home Screen (Share → Add to Home Screen). The calendar works everywhere.</p>` : "";
    return sheet("remind", `Remind me: ${meal}`, `<p class="sub">${e.opens ? `Ziggy nudges you at ${esc(e.opens)}, when the Swiggy cart opens. Order by ${esc(e.at)}.` : `Order by ${esc(e.at || "its time")}.`}</p>
      ${push}${ios}<button class="secondary" data-v3-act="remind-ics" data-v3-ics="${esc(ics)}">Add to my calendar</button>
      <p class="fine">Only this meal. For every meal, turn on Order reminders in Me.</p>`, `remind ${meal}`);
  }
  async function remindPush() {
    const meal = st().sheet.meal;
    await api(`/api/user/${S.userId}/today/remind`, "POST", { meal });
    const ps = typeof pushState === "function" ? pushState() : "none";
    if (ps === "ready") {
      if (await Notification.requestPermission() !== "granted") { toast("Notifications are blocked for this site. Add it to your calendar instead."); return; }
      await subscribePush();
    } else if (ps === "none" && typeof Notification !== "undefined" && Notification.permission !== "granted") {
      if (await Notification.requestPermission() !== "granted") { toast("Notifications are blocked here. Add it to your calendar instead."); return; }
    }
    st().sheet = null; await load();
    toast(`Reminder set for ${meal}`);
  }
  async function remindIcs(path) {
    await api(`/api/user/${S.userId}/today/remind`, "POST", { meal: st().sheet.meal }).catch(() => {});
    if (typeof downloadPrivate === "function") await downloadPrivate(path, "ziggy-reminder.ics", "text/calendar");
    else window.open(path, "_blank", "noopener");
    st().sheet = null; await load();
  }
  function overlays() {
    if (!ON) return "";
    if (S.v3?.sheet?.kind === "day") return daySheet();
    if (!S.v3?.sheet || !S.v3.data) return "";
    const k = S.v3.sheet.kind;
    return k === "decide" ? decideSheet() : k === "rules" ? rulesSheet() : k === "budget" ? budgetSheet()
      : k === "swap" ? swapSheet() : k === "add" ? addSheet() : k === "order" ? orderSheet() : k === "remind" ? remindSheet() : "";
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
  const menuUrl = (id, name) => `/api/user/${S.userId}/swiggy/live-menu?restaurant_id=${encodeURIComponent(id)}&restaurant_name=${encodeURIComponent(name)}`;
  const sheetTop = () => { const el = typeof document !== "undefined" ? document.querySelector(".v3-sheet") : null; if (el && el.scrollTo) el.scrollTo(0, 0); else if (el) el.scrollTop = 0; };
  const MENU_POLL_MS = 1500, MENU_POLLS = 12;
  async function openPlace(id, name, rating, eta, offer) {
    const s = st().sheet;
    s.place = { id, name, rating: rating ? Number(rating) : null, eta, offer: offer ? { label: offer } : null };
    s.menu = null; s.menuQ = ""; s.menuSearch = null; s.opening = { id, name };
    render(); sheetTop();
    try { s.menu = await api(menuUrl(id, name)); } finally { s.opening = null; }
    if (st().sheet !== s) return;
    render(); sheetTop();
    if (s.menu?.refreshing) pollMenu(s, id, name, 0);
  }
  // A kept menu is shown at once; when Swiggy is being re-read (or more pages are coming) look again.
  async function pollMenu(s, id, name, n) {
    if (n >= MENU_POLLS) return;
    await new Promise(r => setTimeout(r, MENU_POLL_MS));
    if (st().sheet !== s || s.menu?.restaurant?.id !== id) return;
    let m;
    try { m = await api(menuUrl(id, name)); } catch (_) { return; }
    if (st().sheet !== s || s.menu?.restaurant?.id !== id) return;
    const changed = m.items.length !== s.menu.items.length || m.partial !== s.menu.partial || m.source !== s.menu.source;
    s.menu = m;
    if (changed || !m.refreshing) { const q = document.getElementById?.("v3-mq")?.value; if (q != null) s.menuQ = q; render(); if (s.menuQ) filterMenu(s.menuQ); }
    if (m.refreshing) pollMenu(s, id, name, n + 1);
  }
  async function searchMenu(q) {
    const s = st().sheet, m = s.menu;
    if (!m || (q || "").trim().length < 2) { s.menuSearch = null; render(); return; }
    s.menuQ = q;
    const r = await api(`/api/user/${S.userId}/swiggy/dishes?restaurant_id=${encodeURIComponent(m.restaurant.id)}&restaurant_name=${encodeURIComponent(m.restaurant.name)}&query=${encodeURIComponent(q.trim())}`);
    const have = new Set(m.items.map(i => i.id));
    s.menuSearch = { query: q.trim(), items: (r.items || []).filter(i => !have.has(i.id)) };
    for (const i of s.menuSearch.items) m.items.push(i);           // so a found dish can be added
    render(); filterMenu(q);
  }
  // Opening the Add sheet reads the menus of the person's own places in the background (audit #3).
  function prefetchMenus() {
    api(`/api/user/${S.userId}/swiggy/menus/prefetch`, "POST", {}).catch(() => {});
  }
  // A background read re-renders the sheet: keep what the person is typing in the search box.
  const keepTyped = () => { const el = typeof document !== "undefined" ? document.getElementById("v3-q") : null;
    if (el && st().sheet && typeof el.value === "string") st().sheet.q = el.value; };
  async function loadHistory() {
    const s = st().sheet;
    if (!s) return;
    try {
      const h = await api(`/api/user/${S.userId}/swiggy/order-history`);
      const seen = new Set();
      s.history = (h.provider_orders || []).filter(o => o.restaurant && !seen.has(o.restaurant_id || o.restaurant) && seen.add(o.restaurant_id || o.restaurant))
        .map(o => ({ id: o.restaurant_id || null, name: o.restaurant })).slice(0, 8);
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
    store.del(suggKey());
    render();
  }
  async function refreshSugg() {
    await importSugg();
    toast("Read your latest Swiggy orders");
  }
  async function savePlaces() {
    const s = st().sheet;
    const r = await api(`/api/user/${S.userId}/favourites/suggested/places`, "POST", {});
    s.sugg = r;
    const n = (r.places_saved || []).length;
    toast(n ? `Saved ${n} place${n === 1 ? "" : "s"}. Ziggy plans from your saved places.` : "Already in your saved places");
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
      .filter(Boolean).map(i => (i.kind === "combo" ? { restaurant_id: i.restaurant_id, combo: i.label, meal: s.meal }
        : { restaurant_id: i.restaurant_id, item_name: i.item_name, meal: s.meal }));
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
  // Swap's "Nearby now" (audit #26): from menus already read for the address, no Swiggy call.
  async function loadNearby(meal) {
    const s = st().sheet;
    if (!s || !st().data?.swiggy?.connected) return;
    try { s.nearby = await api(`/api/user/${S.userId}/swiggy/nearby?meal=${encodeURIComponent(meal)}`); } catch (_) { s.nearby = null; }
    if (st().sheet === s) render();
  }
  async function pickNearby(n) {
    const s = st().sheet, x = s.nearby?.dishes?.[n];
    if (!x) return;
    await post("/pool", { source: "swiggy", meal: s.meal, restaurant_id: x.restaurant_id, restaurant_name: x.restaurant,
      item_id: x.item_id, rating: x.rating });
    await post("/pick", { meal: s.meal, ref: `sw:${x.restaurant_id}:${x.item_id}` }, `${x.name} for ${s.meal}`);
    T(null);
  }
  async function decide(mode) {
    if (st().sheet?.scope === "week") return decideWeek();
    const r = await post("/decide", { mode: mode || st().sheet.choice });
    st().sheet = null;
    const dd = r.decided;
    st().decideNote = dd.note && dd.actions?.length ? { note: dd.note, actions: dd.actions } : null;
    if (!st().decideNote) toast(dd.note || `Picked ${dd.filled.join(" and ")}: ${dd.label}`);
    render();
  }
  // usab #24: the Week stays open until the person asks; this fills its open meals (pins).
  const WEEK_MODE = { favourites: "favourites_only", favourites_cook: "favourites_plus_cook", favourites_new: "favourites_plus_new", surprise: "surprise_me" };
  async function decideWeek() {
    const r = await api(`/api/plan/${S.planId}/decide`, "POST", { mode: WEEK_MODE[st().sheet.choice], apply: true });
    st().sheet = null;
    if (r.view) adoptView(r.view);
    if (S.v3) S.v3.data = null;
    toast(r.note || `Filled ${r.picks.filter(p => p.kind !== "skip").length} open meals`);
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
    S.v3ob = { step: 0, diet: null, allergens: [], budget: null, meals: [], consent: false, per: "day", eaters: 1 };
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
        <div class="stack" style="gap:10px" role="radiogroup" aria-label="Diet">${DIET_CHOICES.map(([k, l, s, dot]) =>
          `<button type="button" class="opt" role="radio" data-v3-ob="diet:${k}" aria-checked="${o.diet === k}"><span class="dot ${dot}"></span><span><b>${l}</b><span class="muted">${s}</span></span></button>`).join("")}</div>
        <p class="k">Anything you must avoid? (optional)</p>
        <div class="chips">${ALLERGY_LIST.map(a => `<button type="button" class="chip" data-v3-ob="allergen:${a}" aria-pressed="${o.allergens.includes(a)}">${cap1(a.replace("_", " "))}</button>`).join("")}</div>
        <p class="fine">Ziggy filters by dish name and Swiggy's veg mark. Menus don't list every ingredient, so check with the restaurant when it matters.</p>`;
    } else {
      ready = obBudgetOk(o) && o.meals.length > 0 && o.consent;
      const wk = o.per === "week", lim = obLimits(o);
      body = `<h1 class="h1">Your food budget</h1><p class="sub">${wk ? "For the whole week, everyone included." : "For a day of meals, everyone included."} Change it any time.</p>
        <p class="k">How many eat?</p>
        <div class="chips" role="group" aria-label="How many eat">${[1, 2, 3, 4].map(n => `<button type="button" class="chip" data-v3-ob="eaters:${n}" aria-pressed="${o.eaters === n}">${n === 4 ? "4+" : n}</button>`).join("")}</div>
        <div class="chips" role="group" aria-label="Budget per" style="justify-content:center">${[["day", "A day"], ["week", "A week"]].map(([k, l]) => `<button type="button" class="chip" data-v3-ob="per:${k}" aria-pressed="${o.per === k}">${l}</button>`).join("")}</div>
        <section class="card" style="align-items:center;padding:22px 18px">
          <div class="stepper"><button type="button" data-v3-ob="budget:-${wk ? STEP * 5 : STEP}" aria-label="Lower budget by ${wk ? STEP * 5 : STEP} rupees">${ICON.minus}</button>
            <label class="sr" for="v3-ob-budget">Budget ${wk ? "a week" : "a day"} in rupees</label><input id="v3-ob-budget" class="money" type="number" inputmode="numeric" min="${lim[0]}" max="${lim[1]}" value="${o.budget ?? ""}" placeholder="₹" style="font-size:40px;width:170px;text-align:center;border:0;background:transparent;min-height:56px">
            <button type="button" data-v3-ob="budget:${wk ? STEP * 5 : STEP}" aria-label="Raise budget by ${wk ? STEP * 5 : STEP} rupees">${ICON.plus}</button></div>
          ${o.budget ? `<p class="fine" id="v3-ob-equiv">${wk ? `About ${money(Math.round(o.budget / 7))} a day` : `About ${money(o.budget * 7)} a week`}</p>` : ""}</section>
        <div class="chips" style="justify-content:center">${obPresets(o).map(n => `<button type="button" class="chip" data-v3-ob="preset:${n}" aria-pressed="${o.budget === n}">${money(n)}</button>`).join("")}</div>
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
  // ux #15: a day or a week, and presets that scale with how many eat (never fixed amounts).
  const obLimits = (o) => o.per === "week" ? [100, 100000] : [50, 14000];
  const obBudgetOk = (o) => { const [lo, hi] = obLimits(o); return o.budget >= lo && o.budget <= hi; };
  function obPresets(o) {
    const n = o.eaters || 1, day = [250, 400, 600].map(x => x * n);
    return o.per === "week" ? day.map(x => Math.round((x * 7) / 500) * 500) : day;
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
    else if (k === "budget") { const [lo, hi] = obLimits(o); o.budget = Math.min(hi, Math.max(lo, (o.budget || 0) + Number(v))); }
    else if (k === "per" && v !== o.per) { o.per = v; if (o.budget) o.budget = v === "week" ? o.budget * 7 : Math.round(o.budget / 7); }
    else if (k === "eaters") o.eaters = Number(v);
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
    if (!obBudgetOk(o)) { const [lo, hi] = obLimits(o); throw new Error(`Your budget ${o.per === "week" ? "a week" : "a day"} must be between ${money(lo)} and ${money(hi)}`); }
    const weekly = o.per === "week" ? o.budget : o.budget * 7;
    if (!o.meals.length) throw new Error("Pick at least one meal for today");
    if (!o.consent) throw new Error("Please read the privacy notice and tick “I'm 18 or older and I agree”");
    const rhythm = Object.fromEntries(MEAL_ORDER.map(m => [m, o.meals.includes(m) ? "order" : "skip"]));
    S.planning = true; render();
    let view;
    try {
      view = await api("/api/profiles", "POST", { name: "Me", diet: o.diet, allergens: o.allergens, medical: [],
        weekly_budget: weekly, eaters: o.eaters || 1, rhythm, cook: "sometimes", consent: { account: true } });
    } finally { S.planning = false; }
    // W8: the browser keeps no key (cookie session); the recovery code is shown once in Me → Account.
    if (view.access_key) { keys.put(view.user.id, null, view.user.name); S.freshCode = view.recovery_code; }
    S.users = mergeUsers(await api("/api/users"));
    S.v3ob = null; S.userId = view.user.id; store.set("smartplate.user", String(S.userId));
    adoptView(view);
    S.swiggy = await api(`/api/user/${S.userId}/swiggy`).catch(() => null);
    // One budget model: today is the weekly budget ÷ 7 unless the person changes today alone.
    st().data = await api(`/api/user/${S.userId}/today`);
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
    const obReady = () => { readOb(); const b = document.querySelector('[data-v3-ob="nav:next"]'); if (b) b.disabled = !(obBudgetOk(S.v3ob) && S.v3ob.meals.length && S.v3ob.consent); };
    if (obIn) obIn.oninput = obReady;
    const obC = document.getElementById("v3-ob-consent");
    if (obC) obC.onchange = obReady;                 // W8: the notice must be agreed to before a profile exists
    if (!S.userId) return;
    on("[data-v3-mode]", "click", (e) => { v().mode = e.currentTarget.dataset.v3Mode; render(); });
    on("[data-v3-day]", "click", (e) => { const d = e.currentTarget.dataset.v3Day; guard(() => openDay(d), "Open the day"); });
    on("[data-v3-dayplan]", "click", (e) => { const k = e.currentTarget.dataset.v3Dayplan; guard(() => setDayPlan(k), "Plan or Off"); });
    on("[data-v3-impact]", "click", (e) => { const t = e.currentTarget.dataset; guard(() => impactAct(t.v3Impact, t.v3Amount), "This week's money"); });
    on("[data-v3-impact-skip]", "click", (e) => { const sid = e.currentTarget.dataset.v3ImpactSkip;
      guard(async () => { const view = await api(`/api/session/${sid}/choose`, "POST", { action: "skip" }); adoptView(view);
        const imp = v().impact; if (imp) imp.suggest = (imp.suggest || []).filter(x => String(x.session_id) !== String(sid));
        toast("Skipped. The week re-balanced."); render(); }, "Skip a meal"); });
    const slider = document.getElementById("v3-day-slider");
    if (slider) slider.oninput = () => { const sh = v().sheet, e = envFor(sh.date), d = e?.days?.find(x => x.date === sh.date);
      const n = Number(slider.value); sh.value = n; if (!d) return;
      const out = document.getElementById("v3-day-out"), pv = document.getElementById("v3-day-preview"), sv = document.getElementById("v3-day-save");
      if (out) out.textContent = money(n);
      slider.setAttribute("aria-valuetext", money(n));
      if (pv) pv.textContent = restPreview(e, d, n);
      if (sv) sv.textContent = `Use ${money(n)} for ${sh.date === todayIso() ? "today" : dayName(sh.date)}`; };
    on("[data-v3-budget]", "click", (e) => { const n = Number(e.currentTarget.dataset.v3Budget);
      guard(() => post("/budget", { amount: Math.max(50, (v().data?.budget.amount || 0) + n) }), "Change today's budget"); });
    on("[data-v3-preset]", "click", (e) => { const n = Number(e.currentTarget.dataset.v3Preset); guard(() => post("/budget", { amount: n }), "Change today's budget"); });
    on("[data-v3-sheet]", "click", (e) => T({ kind: e.currentTarget.dataset.v3Sheet }));
    on("[data-v3-close]", "click", (e) => { if (e.target === e.currentTarget || e.currentTarget.tagName === "BUTTON") T(null); });
    on("[data-v3-choice]", "click", (e) => { v().sheet.choice = e.currentTarget.dataset.v3Choice; render(); });
    on("[data-v3-rule]", "click", (e) => { const [k, n] = e.currentTarget.dataset.v3Rule.split(":"); guard(() => post("/rules", { [k]: Number(n) }), "Change your rules"); });
    on("[data-v3-pick]", "change", (e) => { const meal = e.currentTarget.dataset.v3Pick, ref = e.currentTarget.value || null;
      if (ref === ADD_ANOTHER) { T({ kind: "add", meal }); if (v().data?.swiggy.connected) { loadHistory(); loadSugg(); } return; }
      const entry = (v().data?.pools[meal] || []).find(x => x.ref === ref);
      if (entry && entry.reasons.length && !entry.hard) { v().confirm = { meal, ref }; render(); return; }
      v().confirm = null;
      guard(() => post("/pick", { meal, ref }), `Pick ${meal}`); });
    on("[data-v3-confirm]", "click", (e) => { const c = v().confirm, what = e.currentTarget.dataset.v3Confirm, relax = e.currentTarget.dataset.v3Relax;
      v().confirm = null;
      if (!c || what === "cancel") { render(); return; }
      guard(async () => { if (what === "relax" && relax) { const [k, n] = relax.split(":"); await post("/rules", { [k]: Number(n) }); }
        await post("/pick", { meal: c.meal, ref: c.ref }); }, `Pick ${c.meal}`); });
    on("[data-v3-remind]", "click", (e) => T({ kind: "remind", meal: e.currentTarget.dataset.v3Remind }));
    on("[data-v3-mealtime]", "click", (e) => { const a = e.currentTarget.dataset.v3Mealtime;
      const body = a === "dismiss" ? { dismiss: true } : { [a.split(":")[0]]: a.slice(a.indexOf(":") + 1) };
      guard(() => post("/meal-times", body, a === "dismiss" ? null : "Meal time saved. Order-by and reminders follow it."), "Meal time"); });
    on("[data-v3-mymeal]", "click", (e) => { const m = e.currentTarget.dataset.v3Mymeal, cur = new Set(v().data.meals);
      if (cur.has(m)) cur.delete(m); else cur.add(m);
      if (!cur.size) { toast("Keep at least one meal"); return; }
      guard(async () => { const meals = MEAL_ORDER.filter(x => cur.has(x));
        const view = await api(`/api/user/${S.userId}/setup`, "PATCH", { meals });
        if (view?.plan) adoptView(view);
        await load(); }, "Meals I order"); });
    on("[data-v3-other]", "click", () => { v().otherOpen = !v().otherOpen; });
    on("[data-v3-surprise]", "click", () => { v().decideNote = null; guard(() => decide("surprise"), "Surprise me"); });
    on("[data-v3-decidenote]", "click", () => { v().decideNote = null; render(); });
    on("[data-v3-weekdecide]", "click", () => guard(async () => { if (!v().data) await load(); T({ kind: "decide", scope: "week" }); }, "Decide for me"));
    on("[data-v3-pqty]", "click", (e) => { const [meal, dn] = e.currentTarget.dataset.v3Pqty.split(":"), p = v().data.picks[meal];
      guard(() => post("/pick", { meal, ref: p.ref, qty: Math.max(1, Math.min(8, p.qty + Number(dn))) }), "Change quantity"); });
    on("[data-v3-qty]", "click", (e) => { const [meal, ref, dn] = e.currentTarget.dataset.v3Qty.split("|"), cur = v().data.pools[meal].find(x => x.ref === ref);
      guard(() => post("/pool/qty", { meal, ref, qty: Math.max(1, Math.min(8, cur.qty + Number(dn))) }), "Change quantity"); });
    on("[data-v3-remove]", "click", (e) => { const [meal, ref] = e.currentTarget.dataset.v3Remove.split("|");
      guard(() => post("/pool/remove", { meal, ref }, `Removed from your ${usual(meal)}`), "Remove a dish"); });
    on("[data-v3-usual]", "click", (e) => { const m = e.currentTarget.dataset.v3Usual, v0 = v();
      v0.usualOpen = { ...(v0.usualOpen || {}), [m]: !v0.usualOpen?.[m] }; render();
      document.querySelector?.(`[data-v3-usual="${m}"]`)?.focus?.(); });
    on("[data-v3-swap]", "click", (e) => { const meal = e.currentTarget.dataset.v3Swap; T({ kind: "swap", meal }); loadNearby(meal); });
    on("[data-v3-swapto]", "click", (e) => { const [meal, ref] = e.currentTarget.dataset.v3Swapto.split("|");
      guard(async () => { await post("/pick", { meal, ref: ref || null }); T(null); }, `Swap ${meal}`); });
    on("[data-v3-add]", "click", (e) => { const meal = e.currentTarget.dataset.v3Add; T({ kind: "add", meal });
      // background reads (each catches its own error): not through guard(), which drops a call while
      // another runs and would block the person's first tap in the sheet
      if (v().data?.swiggy.connected) { loadHistory(); loadSugg(); if (v().data.swiggy.address) prefetchMenus(); } });
    on("[data-v3-addtab]", "click", (e) => { v().sheet.tab = e.currentTarget.dataset.v3Addtab; render(); });
    on("[data-v3-addmeal]", "click", (e) => { v().sheet.meal = e.currentTarget.dataset.v3Addmeal; render(); });
    on("[data-v3-hist]", "click", (e) => { const q = e.currentTarget.dataset.v3Hist; guard(() => searchSwiggy(q), `Search Swiggy for “${q}”`); });
    on("[data-v3-place]", "click", (e) => { const d = e.currentTarget.dataset; guard(() => openPlace(d.v3Place, d.v3Pname, d.v3Prating, d.v3Peta, d.v3Poffer), `Open “${d.v3Pname}”`); });
    on("[data-v3-vegonly]", "change", (e) => { v().sheet.vegOnly = e.currentTarget.checked; const q = document.getElementById("v3-mq")?.value; v().sheet.menuQ = q || ""; render(); if (q) filterMenu(q); });
    on("[data-v3-nearby]", "click", (e) => { const n = Number(e.currentTarget.dataset.v3Nearby); guard(() => pickNearby(n), "Pick a nearby dish"); });
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
        "add-back": async () => { const sh = v().sheet; sh.menu = null; sh.opening = null; sh.menuSearch = null; render(); },
        "sugg-import": importSugg, "sugg-delete": deleteSugg, "sugg-refresh": refreshSugg, "sugg-places": savePlaces,
        "sugg-later": async () => { store.set(suggKey(), "1"); render(); },
        "sugg-open": async () => { v().sheet.suggOpen = true; render(); },
        "remind-push": remindPush, "remind-ics": () => remindIcs(e.currentTarget.dataset.v3Ics),
        "save-later": async () => { store.set(`ziggy.v3.saved-later.${S.userId}`, "1"); render(); },
        "code-saved": async () => { S.codeAcked = S.userId; render(); },
        olist: async () => { T(null); await openOrderList(); },
        "day-save": () => saveDay(Number(document.getElementById("v3-day-slider")?.value ?? v().sheet?.value)),
        "day-follow": () => saveDay(null) }[a];
      if (f) guard(f);
    });
    const mq = document.getElementById("v3-mq");
    if (mq) mq.oninput = () => { v().sheet.menuQ = mq.value; filterMenu(mq.value); };
    const msearch = document.getElementById("v3-menu-search");
    if (msearch) msearch.onsubmit = (e) => { e.preventDefault(); const q = document.getElementById("v3-mq").value; guard(() => searchMenu(q), `Search the menu for “${q}”`); };
    const search = document.getElementById("v3-search");
    // What's typed survives any re-render (the background menu job reloads Today while the Add sheet is open)
    const qIn = document.getElementById("v3-q");
    if (qIn) qIn.oninput = () => { if (v().sheet) v().sheet.q = qIn.value; };
    if (search) search.onsubmit = (e) => { e.preventDefault(); const q = document.getElementById("v3-q").value; guard(() => searchSwiggy(q), `Search Swiggy for “${q}”`); };
    const sugg = document.getElementById("v3-sugg");
    if (sugg) sugg.onsubmit = (e) => { e.preventDefault(); guard(addSugg, "Add suggestions to your usual meals"); };
    const typed = document.getElementById("v3-typed");
    if (typed) typed.onsubmit = (e) => { e.preventDefault(); guard(addTyped, "Add to your usual meals"); };
    // The typed dish survives a re-render (a background menu job or suggestions load redraws the sheet)
    if (typed) typed.oninput = typed.onchange = () => { const sh = v().sheet; if (!sh) return;
      const f = (id) => document.getElementById(id)?.value || "", r = (n) => typed.querySelector(`input[name="${n}"]:checked`)?.value;
      sh.draft = { name: f("v3-t-name"), place: f("v3-t-place"), price: f("v3-t-price"), veg: r("v3-t-veg") ?? "", kind: r("v3-t-kind") || "order" }; };
    const times = document.getElementById("v3-times");
    if (times) times.onsubmit = (e) => { e.preventDefault(); const f = new FormData(times), body = {};
      for (const [k, val] of f.entries()) body[k] = val || null;
      guard(() => post("/meal-times", body, "Meal times saved"), "Meal times"); };
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
    impact(view) { if (ON && view) showImpact(view.impact); },
    nutritionOn() { return ON ? nutritionOn() : true; },
    startOnboard,
    // A tap on a meal in the Week opens Swap (for today's meals) — one way to change a meal.
    onMealTap(sid) {
      if (!ON) return false;
      const day = (S.view?.grid || []).find(x => x.date === todayIso());
      const meal = day && MEAL_ORDER.find(m => day.meals[m]?.session_id === Number(sid));
      if (!meal || !st().data) return false;
      T({ kind: "swap", meal }); loadNearby(meal);
      return true;
    },
    // After any plan change, re-read Today.
    invalidate() { if (S.v3) S.v3.data = null; },
    _test: { load, post, decide, share, startOnboard, finishOb, obTap, today, cookScreen, overlays, hiddenList, suggBlock, addSugg, histChips, savePlaces, snackRow, recapCard, actions, orderSheet,
      rulesRow, mealLine, swapSheet, remindSheet, relaxFor, earlyOrder, askTime, decideNote, weekTop,
      menuBody, menuRow, etaText, openPlace, addSheet, compareRow, cartOffers, readCartOnce,
      envStrip, daySheet, restPreview, openDay, saveDay, setDayPlan, impactNote, impactAct, showImpact },
  };
})();
