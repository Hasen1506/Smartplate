"use strict";

function clearLiveCoreState() {
  S.liveWeek = null; S.liveWeekError = null; S.basketDraft = null;
  S.itemOptions = null; S.basketReview = null; S.addonReview = null;
}
async function loadLiveWeek() {
  try { S.liveWeek = (await api(`/api/user/${S.userId}/swiggy/week`)).plan; S.liveWeekError = null; }
  catch (e) { S.liveWeek = null; S.liveWeekError = e.message; }
}
function liveWeekScreen() {
  const p = S.liveWeek, meals = S.view.user.meals || ["dinner"];
  return `<h1 class="greet">Your real-menu week</h1><p>From your Swiggy favourites for ${esc(S.swiggy.address.label)}.</p>
    <p class="fine">Menu prices plus your fee reserve are planning estimates. Each order still needs a fresh item and payable-total review. Nutrition and ingredient safety are unknown.</p>
    ${S.liveWeekError ? `<p role="alert">${esc(S.liveWeekError)}</p>` : ""}
    <form id="live-week-form" class="card">
      <label>Start date<input name="start_date" type="date" required value="${esc(p?.start_date || todayIso())}"></label>
      <label>Weekly planning budget (₹)<input name="budget" type="number" min="1" max="100000" required value="${esc(p?.budget || S.view.user.weekly_budget)}"></label>
      <label>Fee reserve per meal (₹)<input name="fee_reserve" type="number" min="0" max="500" required value="${esc(p?.fee_reserve ?? 50)}"></label>
      ${meals.map(meal => `<label>${esc(cap1(meal))} reminder time<input name="time_${esc(meal)}" type="time" required value="${esc(p?.slots.find(s => s.meal === meal)?.time || {breakfast:'08:00',lunch:'13:00',dinner:'19:00'}[meal])}"></label>`).join("")}
      <p class="fine">Star up to five restaurants in Places. Rebuilding replaces this week and its pending reminders; it never changes a cart or places an order.</p>
      <button class="primary">Plan from live favourites</button></form>
    ${p ? `<section class="card"><h2>Estimated ${rupee(p.estimated_total)} of ${rupee(p.budget)}</h2><p class="fine">Menus checked ${esc(p.fetched)}. ${p.notices.map(esc).join(' · ')}</p>
      ${p.slots.map((s,i) => `<div class="checkout-row"><div><b>${esc(s.date)} · ${esc(s.meal)}</b><br>${s.item ? `${esc(s.item.name)} · ${esc(s.item.restaurant)}<br><span class="fine">${rupee(s.item.price)} menu price + ${rupee(p.fee_reserve)} fee reserve</span>` : esc(s.reason)}</div>
      ${s.item ? `<button data-live-week-slot="${i}" ${cartEligible() ? '' : 'disabled'}>Review current item</button>` : ''}</div>`).join('')}</section>` : '<p>Build your week after adding live favourites. No sample dishes are used here.</p>'}`;
}
async function openItemOptions(id, name) {
  const restaurant = S.liveBrowseMenu.restaurant;
  if (S.basketDraft && S.basketDraft.restaurant_id !== restaurant.id) throw new Error('Finish or discard the current restaurant basket first.');
  S.itemOptions = await api(`/api/user/${S.userId}/swiggy/item-options`, 'POST', {
    restaurant_id: restaurant.id, restaurant_name: restaurant.name, item_id: id, item_name: name });
  render();
}
function basketPanel() {
  const d = S.basketDraft;
  return `${d ? `<section class="card"><h3>Basket draft · ${esc(d.restaurant_name)}</h3><p class="fine">This draft is in this tab until prepared in Swiggy.</p>
    ${d.items.map((i,n) => `<p>${esc(i.quantity)} × ${esc(i.item_name)} <button data-remove-basket-line="${n}">Remove</button></p>`).join('')}
    <button class="primary" data-core-act="review-basket">Review whole basket</button><button data-core-act="discard-basket">Discard draft</button></section>` : ''}
    ${S.liveCart?.basket && S.liveCart.orderable ? '<button data-core-act="review-addons">Review available add-ons</button>' : ''}`;
}
function coreModal(title, content) {
  return `<div class="modal-bg"><section class="checkout" role="dialog" aria-modal="true" aria-label="${esc(title)}"><button class="close ghost" data-core-act="close-core-dialog" aria-label="Close basket review">✕</button><h2>${esc(title)}</h2>${content}</section></div>`;
}
function liveCoreDialogs() {
  if (S.itemOptions) {
    const p = S.itemOptions;
    return coreModal('Choose quantity and options', `<p>${esc(p.item)} · ${esc(p.restaurant)} · ${esc(p.address)}</p>
      <form id="item-options-form"><label>Quantity<input type="number" name="quantity" min="1" max="10" value="1" required></label>
        ${p.groups.map((g,i) => `<label>${esc(g.name)}<select name="variant_${i}" required><option value="">Choose an option</option>${g.choices.map(c => `<option value="${esc(c.id)}">${esc(c.name || c.id)}</option>`).join('')}</select></label>`).join('')}
        <label><input type="checkbox" name="same_options">Use the same selected options for every portion if quantity exceeds one.</label>
        <p class="fine">Add-ons are checked against the actual cart after variants are prepared. The final cart total is authoritative.</p><button class="primary">Save to basket draft</button></form>`);
  }
  if (S.basketReview) {
    const p = S.basketReview;
    return coreModal('Prepare this whole basket?', `<p>${esc(p.restaurant_name)} · ${esc(p.address)}</p>
      ${p.lines.map(l => `<p><b>${esc(l.quantity)} × ${esc(l.name)}</b> ${l.variants.map(v => esc(v.name)).join(', ')}</p>`).join('')}
      <p class="fine">${esc(p.note)} Check the resulting total before placing an order.</p><button class="primary" data-core-act="prepare-basket">Confirm and prepare basket</button>`);
  }
  if (S.addonReview) {
    const p = S.addonReview;
    return coreModal('Review variant-specific add-ons', `<form id="addon-form">${p.lines.map((line,i) => `<h3>${esc(line.name)} · ${esc(line.quantity)} portions</h3>
      ${(p.groups[line.id] || []).map((g,j) => `<fieldset><legend>${esc(g.name || 'Add-ons')} · minimum ${esc(g.minAddons || 0)}, maximum ${esc(g.maxAddons || 'available choices')}</legend>
        ${(g.choices || g.addons || []).map(c => `<label><input type="checkbox" name="addon_${i}_${j}" value="${esc(c.id)}">${esc(c.name || c.id)}</label>`).join('')}</fieldset>`).join('')}`).join('')}
      <label><input type="checkbox" name="same_options">Apply these same add-ons to all portions of each dish.</label>
      <p class="fine">Only add-ons Swiggy returned for your selected variants are offered. Review the updated total before checkout.</p><button class="primary">Confirm selected add-ons</button></form>`);
  }
  return '';
}
function wireLiveCore() {
  document.querySelectorAll('[data-core-act]').forEach(el => el.addEventListener('click', () => guard(async () => {
    const action = el.dataset.coreAct;
    if (action === 'close-core-dialog') { S.itemOptions = null; S.basketReview = null; S.addonReview = null; }
    if (action === 'discard-basket') S.basketDraft = null;
    if (action === 'review-basket') S.basketReview = await api(`/api/user/${S.userId}/swiggy/basket/preview`, 'POST', S.basketDraft);
    if (action === 'prepare-basket') {
      S.liveCart = await api(`/api/user/${S.userId}/swiggy/basket`, 'POST', {...S.basketDraft, expected_fingerprint: S.basketReview.fingerprint});
      S.basketDraft = null; S.basketReview = null; S.checkoutReview = null; S.placedOrder = null;
    }
    if (action === 'review-addons') S.addonReview = await api(`/api/user/${S.userId}/swiggy/basket/addons`);
    render();
  })));
  document.querySelectorAll('[data-item-options]').forEach(el => el.addEventListener('click', () => guard(() => openItemOptions(el.dataset.itemOptions, el.dataset.itemName))));
  document.querySelectorAll('[data-remove-basket-line]').forEach(el => el.addEventListener('click', () => {
    S.basketDraft.items.splice(Number(el.dataset.removeBasketLine), 1);
    if (!S.basketDraft.items.length) S.basketDraft = null;
    render();
  }));
  document.querySelectorAll('[data-live-week-slot]').forEach(el => el.addEventListener('click', () => guard(async () => {
    const item = S.liveWeek.slots[Number(el.dataset.liveWeekSlot)].item;
    S.tab = 'places'; await openLivePlace(item.restaurant_id, item.restaurant);
    await openItemOptions(item.id, item.name);
  })));
  const week = document.getElementById('live-week-form');
  if (week) week.onsubmit = e => { e.preventDefault(); guard(async () => {
    const f = new FormData(week), times = Object.fromEntries((S.view.user.meals || ['dinner']).map(m => [m, f.get('time_' + m)]));
    S.liveWeek = (await api(`/api/user/${S.userId}/swiggy/week`, 'POST', {start_date:f.get('start_date'), budget:Number(f.get('budget')), fee_reserve:Number(f.get('fee_reserve')), times})).plan;
    S.liveWeekError = null; render();
  }); };
  const form = document.getElementById('item-options-form');
  if (form) form.onsubmit = e => { e.preventDefault(); guard(async () => {
    const f = new FormData(form), p = S.itemOptions;
    const line = {item_id:p.item_id, item_name:p.item, quantity:Number(f.get('quantity')), same_options:f.get('same_options') === 'on',
      variants:Object.fromEntries(p.groups.map((g,i) => [g.id,f.get('variant_' + i)]))};
    S.basketDraft ||= {restaurant_id:p.restaurant_id,restaurant_name:p.restaurant,items:[]};
    S.basketDraft.items = S.basketDraft.items.filter(i => i.item_id !== p.item_id); S.basketDraft.items.push(line);
    S.itemOptions = null; render();
  }); };
  const addons = document.getElementById('addon-form');
  if (addons) addons.onsubmit = e => { e.preventDefault(); guard(async () => {
    const p = S.addonReview, f = new FormData(addons);
    const selections = Object.fromEntries(p.lines.map((line,i) => [line.id, Object.fromEntries((p.groups[line.id] || []).map((g,j) => [String(g.group_id || g.groupId), f.getAll(`addon_${i}_${j}`)]))]));
    S.liveCart = await api(`/api/user/${S.userId}/swiggy/basket/addons`, 'POST', {selections,expected_fingerprint:p.fingerprint,same_options:f.get('same_options') === 'on'});
    S.addonReview = null; S.checkoutReview = null; render();
  }); };
}

function checkoutOptionsSummary(review) {
  return (review.items || []).map(item => `<p class="fine">${esc(item.quantity)} × ${esc(item.name)}: ${[...(item.variants || []), ...(item.addons || [])].map(v => esc(v.name || v.id || v.choice_id || v.variationId || v.variation_id)).join(', ') || 'No selected options'}</p>`).join('');
}
