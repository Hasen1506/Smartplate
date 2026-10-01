"use strict";

function clearLiveCoreState() {
  S.liveWeek = null; S.liveWeekError = null; S.basketDraft = null;
  S.itemOptions = null; S.basketReview = null; S.addonReview = null;
  S.foodMemory = null; S.weekStatus = null; S.agentConnections = null; S.agentToken = null; S.agentReview = null; S.memorySlot = null;
}
async function loadLiveWeek() {
  try { S.weekStatus = await api(`/api/user/${S.userId}/swiggy/week/status`); S.liveWeek = S.weekStatus.plan;
    S.foodMemory = (await api(`/api/user/${S.userId}/food-memory`)).items; S.liveWeekError = null; }
  catch (e) { S.liveWeek = null; S.liveWeekError = e.message; }
}
function liveWeekScreen() {
  const p = S.liveWeek, meals = S.view.user.meals || ["dinner"];
  return `<h1 class="greet">Your real-menu week</h1><p>From your Swiggy favourites for ${esc(S.swiggy.address.label)}.</p>
    <p class="fine">Menu prices plus your fee reserve are planning estimates. Each order still needs a fresh item and payable-total review. Nutrition and ingredient safety are unknown.</p>
    ${S.liveWeekError ? `<p role="alert">${esc(S.liveWeekError)}</p>` : ""}
    <form id="live-week-form" class="card">
      <label>Start date<input name="start_date" type="date" required value="${esc(p?.start_date || nextWeekStart())}"></label>
      <label>Weekly planning budget (₹)<input name="budget" type="number" min="1" max="100000" required value="${esc(p?.budget || S.view.user.weekly_budget)}"></label>
      <label>Fee reserve per meal (₹)<input name="fee_reserve" type="number" min="0" max="500" required value="${esc(p?.fee_reserve ?? 50)}"></label>
      ${meals.map(meal => `<label>${esc(cap1(meal))} reminder time<input name="time_${esc(meal)}" type="time" required value="${esc(p?.slots.find(s => s.meal === meal)?.time || {breakfast:'08:00',lunch:'13:00',dinner:'19:00'}[meal])}"></label>`).join("")}
      <p class="fine">Star up to five restaurants in Places. Your chosen meals are the coverage scope. Updating this week preserves recorded spending and revises future choices.</p>
      <button class="primary">Plan from live favourites</button></form>
    ${p ? `<section class="card"><h2>Estimated ${rupee(p.estimated_total)} of ${rupee(p.budget)}</h2><p class="fine">Menus checked ${esc(p.fetched)}. ${p.notices.map(esc).join(' · ')}</p>
      <p><b>${esc(p.coverage?.covered ?? 0)} of ${esc(p.coverage?.required ?? 0)} future meals covered.</b> Recorded/committed spending: ${rupee(S.weekStatus?.actual_spend ?? p.actual_spend ?? 0)}. Remaining budget: ${rupee(S.weekStatus?.remaining_budget ?? p.remaining_budget ?? p.budget)}.</p>
      ${p.shortfall ? `<p role="alert">At least ${rupee(p.shortfall)} more is needed for all remaining eligible meals at current menu estimates. Fees may change.</p>` : ''}
      ${p.unrecorded_past ? `<p role="alert">${esc(p.unrecorded_past)} past meal slots are unrecorded. Add actual meals and spending before relying on the remaining budget.</p>` : ''}
      <p class="fine">Coverage is a menu selection estimate. Confirm portions and meal suitability; a low-priced item alone may not be a complete meal.</p>
      ${S.weekStatus?.status === "revision_required" ? '<p role="alert">Your meals, spending or preferences changed. Revise the remaining week to update these estimates.</p>' : ""}
      <button data-core-act="revise-week">Get me through the rest of the week</button>
      ${p.slots.map((s,i) => `<div class="checkout-row"><div><b>${esc(s.date)} · ${esc(s.meal)}</b><br>${s.item ? `${esc(s.item.name)} · ${esc(s.item.restaurant)}<br><span class="fine">${rupee(s.item.price)} menu price + ${rupee(p.fee_reserve)} fee reserve</span>` : esc(s.reason)}</div>
      ${s.item ? `<button data-live-week-slot="${i}" ${cartEligible() ? '' : 'disabled'}>Review current item</button><button data-memory-slot="${i}">Food preference</button>` : ''}</div>`).join('')}</section>${foodLedgerForm()}` : '<p>Build your week after adding live favourites. No sample dishes are used here.</p>'}`;
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
    if (action === 'revise-week') { S.liveWeek = (await api(`/api/user/${S.userId}/swiggy/week/revise`, 'POST', {expected_version:S.liveWeek.version})).plan; await loadLiveWeek(); }
    if (action === 'hide-agent-token') S.agentToken = null;
    if (action === 'approve-agent-order') { S.placedOrder = await api(`/api/user/${S.userId}/agent-review/${S.agentReview.review_id}`, 'POST', {confirmation:'PLACE ORDER'}); S.agentReview = null; await refreshLiveCart(false); toast('Swiggy confirmed the order'); }
    if (action === 'close-agent-review') S.agentReview = null;
    if (action === 'close-core-dialog') { S.memorySlot = null; S.agentReview = null; S.itemOptions = null; S.basketReview = null; S.addonReview = null; }
    if (action === 'discard-basket') S.basketDraft = null;
    if (action === 'review-basket') S.basketReview = await api(`/api/user/${S.userId}/swiggy/basket/preview`, 'POST', S.basketDraft);
    if (action === 'prepare-basket') {
      S.liveCart = await api(`/api/user/${S.userId}/swiggy/basket`, 'POST', {...S.basketDraft, expected_fingerprint: S.basketReview.fingerprint});
      S.basketDraft = null; S.basketReview = null; S.checkoutReview = null; S.placedOrder = null;
    }
    if (action === 'review-addons') S.addonReview = await api(`/api/user/${S.userId}/swiggy/basket/addons`);
    render();
  })));
  wirePersonalAgent();
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
    const revision = S.liveWeek && S.liveWeek.start_date === f.get('start_date');
    S.liveWeek = (await api(`/api/user/${S.userId}/swiggy/week${revision ? '/revise' : ''}`, 'POST', {start_date:f.get('start_date'), budget:Number(f.get('budget')), fee_reserve:Number(f.get('fee_reserve')), times, ...(revision ? {expected_version:S.liveWeek.version} : {})})).plan;
    await loadLiveWeek();
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


function foodLedgerForm() {
  return `<section class="card"><h2>What did you actually eat?</h2><form id="food-event-form">
    <label>Date<input name="date" type="date" max="${esc(todayIso())}" value="${esc(todayIso())}" required></label>
    <label>Meal<select name="meal">${['breakfast','lunch','dinner'].map(m=>`<option>${m}</option>`).join('')}</select></label>
    <label>Actual amount paid (₹)<input name="amount" type="number" min="0" max="100000" step="0.01" required></label>
    <label>What you ate<input name="note" maxlength="500"></label>
    <p class="fine">Home meals can cost ₹0 if you are tracking only additional spending. A confirmed SmartPlate order is already counted; do not record it twice.</p>
    <button class="primary">Record meal and revise remaining week</button></form>
    <h3>Saved food memory</h3>${Object.entries(S.foodMemory || {}).map(([key,m])=>`<p>${esc(m.restaurant_id)} / ${esc(m.item_id)} · ${esc(m.preference)} · ${esc((m.suitable_meals || []).join(', ') || 'Meal suitability unknown')}<br>${esc(m.note || '')} <button data-forget-memory="${esc(key)}">Forget</button></p>`).join('') || '<p>No saved preferences.</p>'}
    ${(S.weekStatus?.events || []).map(e=>`<p>${esc(e.meal_date)} · ${esc(e.meal)} · ${rupee(e.amount_paise / 100)} · ${esc(e.source)} ${e.source === 'swiggy_confirmed_order' && e.meal === 'unassigned' ? `<select data-order-meal="${esc(e.payload.order_id)}" aria-label="Meal covered by this order"><option value="">Allocate this order to a meal</option>${['breakfast','lunch','dinner'].map(m=>`<option>${m}</option>`).join('')}</select>` : ''} ${e.source === 'user_reported' ? `<button data-remove-food-event="${e.id}">Remove incorrect entry</button>` : ''}</p>`).join('')}</section>`;
}
function personalAgentPanel() {
  const a = S.agentConnections;
  if (!keys.get(S.userId)) return '<h2>Personal agent connection</h2><p>Create your own private profile first.</p>';
  if (!a) return '<p>Loading agent connections…</p>';
  return `<h2 class="sec">Connect your personal agent</h2><p>Your agent can render its own dashboard and call SmartPlate for real menus, plans and food memory. It needs a connector configured by you.</p>
    <p class="fine">Streamable HTTP MCP endpoint: <code>${esc(a.endpoint)}</code>. Use a client supporting custom Bearer headers, or the documented stdio bridge. Automatic OAuth sign-in is not implemented.</p>
    <form id="agent-connection-form" class="card"><label>Connection name<input name="label" maxlength="80" required placeholder="My personal agent"></label>
    <label>Expires in days<input name="days" type="number" min="1" max="30" value="7" required></label>
    ${['read','plan','memory','cart'].map(s=>`<label><input name="scopes" value="${s}" type="checkbox" ${s==='read'?'checked':''}>${s === 'cart' ? 'Prepare cart and request human checkout review' : cap1(s)}</label>`).join('')}
    <p class="fine">Grant only needed permissions. Tokens cannot approve purchases or access your recovery code.</p><button class="primary">Create scoped connection</button></form>
    ${S.agentToken ? `<section class="card"><h3>Save this token now</h3><p class="fine">Shown once. Store it in your agent’s connector secrets.</p><textarea readonly rows="3" aria-label="Agent access token">${esc(S.agentToken.token)}</textarea><button data-core-act="hide-agent-token">Hide token</button></section>`:''}
    <section class="card">${a.connections.map(c=>`<p><b>${esc(c.label)}</b> · ${esc(c.scopes.join(', '))}<br>Expires ${esc(c.expires_ts)} ${c.revoked ? '· Revoked' : `<button data-revoke-agent="${esc(c.id)}">Revoke</button>`}</p>`).join('') || '<p>No agent connections yet.</p>'}</section>`;
}
function agentReviewDialog() {
  if (!S.agentReview) return '';
  const r = S.agentReview, q = r.quote;
  return coreModal('Review your agent’s order', `<p>${esc(q.restaurant)} · ${esc(q.address)}</p>
    ${(q.items || []).map(i=>`<p><b>${esc(i.quantity)} × ${esc(i.name)}</b><br>${[...(i.variants || []), ...(i.addons || [])].map(v=>esc(v.name || v.id)).join(', ')}</p>`).join('')}
    <p><b>Pay ${rupee(q.to_pay)}</b> · ${esc(q.payment_label)}</p>
    <p class="fine">Expires ${esc(r.expires_ts)}. SmartPlate refreshes and compares the exact cart before placing it. A changed price or address requires a new review.</p>
    ${r.state === 'pending' ? '<button class="primary" data-core-act="approve-agent-order">Confirm and place this order</button>' : `<p role="status">Review ${esc(r.state)}</p>`}
    <button data-core-act="close-agent-review">Close</button>`);
}
function wirePersonalAgent() {
  const form = document.getElementById('agent-connection-form');
  if (form) form.onsubmit=e=>{e.preventDefault();guard(async()=>{const f=new FormData(form);S.agentToken=await api(`/api/user/${S.userId}/agents`,'POST',{label:f.get('label'),days:Number(f.get('days')),scopes:f.getAll('scopes')});S.agentConnections=await api(`/api/user/${S.userId}/agents`);render();});};
  document.querySelectorAll('[data-revoke-agent]').forEach(el=>el.onclick=()=>guard(async()=>{await api(`/api/user/${S.userId}/agents/${el.dataset.revokeAgent}/revoke`,'POST',{});S.agentToken=null;S.agentConnections=await api(`/api/user/${S.userId}/agents`);render();}));
  document.querySelectorAll('[data-memory-slot]').forEach(el=>el.onclick=()=>{S.memorySlot=Number(el.dataset.memorySlot);render();});
  const memory=document.getElementById('food-memory-form');
  if(memory) memory.onsubmit=e=>{e.preventDefault();guard(async()=>{const f=new FormData(memory),item=S.liveWeek.slots[S.memorySlot].item;await api(`/api/user/${S.userId}/food-memory`,'POST',{restaurant_id:item.restaurant_id,item_id:item.id,preference:f.get('preference'),suitable_meals:f.getAll('meals'),note:f.get('note')});S.memorySlot=null;await loadLiveWeek();render();});};
  document.querySelectorAll('[data-forget-memory]').forEach(el=>el.onclick=()=>guard(async()=>{const m=S.foodMemory[el.dataset.forgetMemory];await api(`/api/user/${S.userId}/food-memory/forget`,'POST',{restaurant_id:m.restaurant_id,item_id:m.item_id});await loadLiveWeek();render();}));
  document.querySelectorAll('[data-order-meal]').forEach(el=>el.onchange=()=>{if(el.value)guard(async()=>{await api(`/api/user/${S.userId}/food-events/assign-order`,'POST',{order_id:el.dataset.orderMeal,meal:el.value});await api(`/api/user/${S.userId}/swiggy/week/revise`,'POST',{expected_version:S.liveWeek.version});await loadLiveWeek();render();});});
  const event=document.getElementById('food-event-form');
  if(event) event.onsubmit=e=>{e.preventDefault();guard(async()=>{const f=new FormData(event);if(!event.dataset.eventKey)event.dataset.eventKey=crypto.randomUUID();await api(`/api/user/${S.userId}/food-events`,'POST',{date:f.get('date'),meal:f.get('meal'),amount:Number(f.get('amount')),note:f.get('note'),event_key:event.dataset.eventKey});await api(`/api/user/${S.userId}/swiggy/week/revise`,'POST',{expected_version:S.liveWeek.version});await loadLiveWeek();render();});};
  document.querySelectorAll('[data-remove-food-event]').forEach(el=>el.onclick=()=>guard(async()=>{await api(`/api/user/${S.userId}/food-events/${el.dataset.removeFoodEvent}/remove`,'POST',{});await loadLiveWeek();render();}));
}
function foodMemoryDialog() {
  if(S.memorySlot == null || !S.liveWeek?.slots[S.memorySlot]?.item) return '';
  const i=S.liveWeek.slots[S.memorySlot].item,p=S.foodMemory?.[i.restaurant_id+':'+i.id] || {};
  return coreModal('Food memory',`<p>${esc(i.name)} · ${esc(i.restaurant)}</p><form id="food-memory-form">
    <label>Preference<select name="preference" aria-label="Preference">${['neutral','like','avoid'].map(v=>`<option ${p.preference===v?'selected':''}>${v}</option>`).join('')}</select></label>
    <p>Meals you consider this dish suitable for:</p>${['breakfast','lunch','dinner'].map(m=>`<label><input type="checkbox" name="meals" value="${m}" ${(p.suitable_meals||[]).includes(m)?'checked':''}>${cap1(m)}</label>`).join('')}
    <label>Note<textarea name="note" maxlength="500" aria-label="Note">${esc(p.note || '')}</textarea></label>
    <p class="fine">Avoid excludes this dish from future planning. Portions, ingredients and nutrition still need your review. No checked meal means suitability remains unknown.</p><button class="primary">Save preference</button></form>`);
}

function nextWeekStart() {
  const date = new Date(todayIso() + 'T12:00:00');
  date.setDate(date.getDate() + 1);
  return [date.getFullYear(), String(date.getMonth()+1).padStart(2,'0'), String(date.getDate()).padStart(2,'0')].join('-');
}
