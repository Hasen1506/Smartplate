/*
 * test-merged.mjs — end-to-end render test for "SmartPlate Wireframes.html".
 *
 * Loads the *generated* file in a real DOM (jsdom, runScripts: dangerously) so the inlined
 * runtime boots exactly as it would in a browser, then asserts both screens render with no
 * unresolved {{ }} placeholders and that the interactive bits (tab switches, notes toggle,
 * top tab-bar) actually work. This verifies the real shipped artifact, not just the sources.
 *
 * Run:  node project/test-merged.mjs      (requires: npm install jsdom)
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const HERE = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(HERE, 'SmartPlate Wireframes.html'), 'utf8');

const fails = [];
const ok = (cond, msg) => { if (cond) console.log(`  ✓ ${msg}`); else { console.log(`  ✗ ${msg}`); fails.push(msg); } };
const textIn = (el) => (el ? el.textContent : '');
const btnByText = (root, txt) => [...root.querySelectorAll('button')].find((b) => b.textContent.includes(txt));

const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: 'https://smartplate.test/' /* real origin so localStorage works; no `resources` → no network fetch */ });
const { window } = dom;
const { document } = window;

await new Promise((res) => {
  if (document.readyState === 'complete') return res();
  window.addEventListener('load', res);
  setTimeout(res, 1500); // fallback — boot runs synchronously at end of body anyway
});

const weekly = document.getElementById('screen-weekly');
const setup = document.getElementById('screen-setup');

console.log('\n[boot] both screens mounted');
ok(weekly && weekly.querySelector('pre') === null, 'weekly screen rendered without a runtime error');
ok(setup && setup.querySelector('pre') === null, 'setup screen rendered without a runtime error');
ok(weekly && weekly.childElementCount > 0, 'weekly screen has rendered content');
ok(setup && setup.childElementCount > 0, 'setup screen has rendered content');

console.log('\n[placeholders] nothing left unresolved');
ok(!weekly.innerHTML.includes('{{'), 'weekly screen has no leftover {{ }} placeholders');
ok(!setup.innerHTML.includes('{{'), 'setup screen has no leftover {{ }} placeholders');

console.log('\n[weekly] default proposed plan is within the ₹2,000 cap (audit F2) — measured pristine');
const wc = window.__dc.comps[0];
const vDefault = wc.renderVals();
console.log(`    default: spent ₹${vDefault.spent} · left ₹${vDefault.left}`);
ok(vDefault.left >= 0, `default plan within cap (left ₹${vDefault.left})`);

console.log('\n[weekly] expected components present');
ok(textIn(weekly).includes('When should SmartPlate plan'), 'step-1 window selector heading');
ok(textIn(weekly).includes('sessions planned'), 'sessions summary chip');
ok(textIn(weekly).includes('Calendar grid'), 'calendar-grid heading');
ok(textIn(weekly).includes('MON') && textIn(weekly).includes('SUN'), 'day labels MON…SUN');
ok(textIn(weekly).includes('↻ spin'), 'interactive ↻ spin control on grid cards');
ok(weekly.querySelectorAll('button').length > 15, `many interactive buttons (${weekly.querySelectorAll('button').length})`);
ok(weekly.querySelector('svg') !== null, 'at least one SVG (header squiggle / budget ring)');

console.log('\n[setup] expected components present');
ok(textIn(setup).includes('Program the agent'), 'setup header "Program the agent"');
ok(textIn(setup).includes('Settings form'), 'default "Settings form" tab content');
ok(setup.querySelector('svg') !== null, 'at least one SVG present');
ok(!btnByText(setup, 'Guardrails'), 'redundant "Guardrails & dials" view removed from UI');
ok(!btnByText(setup, 'Plain-English'), 'redundant "Plain-English program" view removed from UI');
ok([...setup.querySelectorAll('button')].filter((b) => /Settings form|Nutrition|Taste DNA/.test(b.textContent)).length === 3, 'setup is down to 3 tabs: Settings form · Nutrition · Taste DNA');

console.log('\n[interactivity] notes toggle re-renders annotations');
const ann = 'the "when to order" selection moved up here';
ok(weekly.innerHTML.includes(ann), 'design annotation visible when notes ON');
const notes = document.getElementById('dc-notes');
notes.checked = false; notes.dispatchEvent(new window.Event('change'));
ok(!weekly.innerHTML.includes(ann), 'annotation removed when notes OFF');
notes.checked = true; notes.dispatchEvent(new window.Event('change'));
ok(weekly.innerHTML.includes(ann), 'annotation returns when notes back ON');

console.log('\n[interactivity] weekly internal tab → Command dashboard');
const dashBtn = btnByText(weekly, 'Command dashboard');
ok(!!dashBtn, 'found "Command dashboard" tab button');
ok(!textIn(weekly).includes('Your week is planned'), 'dashboard hero not shown on default (grid) tab');
dashBtn && dashBtn.click();
ok(textIn(weekly).includes('Your week is planned'), 'dashboard hero appears after click (sc-if + setState work)');
ok(textIn(weekly).includes('Budget burn-down'), 'dashboard-only burn-down section appears');

console.log('\n[interactivity] setup internal tab → Taste DNA radar');
const dnaBtn = btnByText(setup, 'Taste DNA');
ok(!!dnaBtn, 'found "Taste DNA" tab button');
dnaBtn && dnaBtn.click();
ok(setup.querySelectorAll('polygon').length > 0, 'radar <polygon> (JS-built SVG) renders after switching to DNA');

console.log('\n[interactivity] top tab-bar switches screens');
window.__dc.activate('screen-setup');
ok(weekly.style.display === 'none', 'weekly hidden when Setup tab active');
ok(setup.style.display !== 'none', 'setup visible when Setup tab active');
window.__dc.activate('screen-weekly');
ok(setup.style.display === 'none' && weekly.style.display !== 'none', 'switches back to weekly');

console.log('\n[weekly] ▢/⌂/⊘ mode toggle actually switches (bug fix)');
const resolved = (i, w) => { const o = wc.optsForCtx(wc.baseDays()[i], w, wc.activeArea(i, w)); return o[(wc.state.picks[i + '-' + w] || 0) % o.length]; };
ok(resolved(1, 'D').tag === 'DELIVER', 'TUE dinner starts as DELIVER');
wc.setMode(1, 'D', 'COOK'); ok(resolved(1, 'D').tag === 'COOK', 'tapping ⌂ switches TUE dinner to COOK');
wc.setMode(1, 'D', 'SKIP'); ok(resolved(1, 'D').tag === 'SKIP', 'tapping ⊘ switches TUE dinner to SKIP');
wc.setMode(1, 'D', 'DELIVER'); ok(resolved(1, 'D').tag === 'DELIVER', 'tapping ▢ switches it back to DELIVER');

console.log('\n[weekly] area switch is reactive where it should be');
wc.setMode(4, 'L', 'DELIVER'); // FRI lunch = Veg Biryani @ Ponnusamy (work-only)
const friBefore = resolved(4, 'L').item;
while (wc.activeArea(4, 'L') !== 'home') wc.cycleArea(4, 'L'); // force Home, where Ponnusamy doesn't serve
ok(resolved(4, 'L').item !== friBefore || resolved(4, 'L').tag !== 'DELIVER', 'switching FRI lunch to Home surfaces a different serviceable pick');

console.log('\n[weekly] meal-window times are user-editable (Step 1)');
ok(weekly.querySelectorAll('input[type="time"]').length >= 3, 'each window has an editable <input type="time">');
const winTimes = [...weekly.querySelectorAll('input[type="time"]')].map((i) => i.value);
ok(winTimes.includes('08:30') && winTimes.includes('13:00') && winTimes.includes('20:30'), `default times 08:30 / 13:00 / 20:30 (${winTimes.join(', ')})`);

console.log('\n[weekly] Step-1 selector cell cycles deliver → cook → skip (one "skip", no separate off)');
const bOpts = () => wc.optsForCtx(wc.baseDays()[0], 'B', wc.activeArea(0, 'B'));
ok(bOpts().filter((o) => o.tag === 'SKIP').length === 1, 'exactly one SKIP option per window (off merged into skip)');
const selTag = () => { const o = bOpts(); return o[(wc.state.picks['0-B'] || 0) % o.length].tag; };
const c0 = selTag(); wc.cycleWindow(0, 'B'); const c1 = selTag(); wc.cycleWindow(0, 'B'); const c2 = selTag(); wc.cycleWindow(0, 'B'); const c3 = selTag();
ok(c1 !== c0 && c2 !== c1 && c3 === c0, `selector cell steps through all modes and loops (${c0}→${c1}→${c2}→${c3})`);
wc.setState({ picks: wc.defaultSkips() }); // restore pristine default

console.log('\n[setup] BMR/TDEE calculator (Nutrition tab) — user enters details, target computes');
const sc = window.__dc.comps[1];
sc.setState({ tab: 'nutri' });
ok(/Mifflin/.test(setup.innerHTML), 'BMR panel renders (Mifflin–St Jeor)');
ok(setup.querySelectorAll('input[type=number]').length >= 3, 'has age / height / weight inputs');
ok(/target [\d,]+ kcal/.test(setup.innerHTML), 'computed target chip renders');
const t0 = sc.bmrCalc().target;
sc.setSex('M'); // male BMR is +166 kcal vs female in Mifflin–St Jeor → target rises
const t1 = sc.bmrCalc().target;
ok(t1 > t0, `target recomputes when details change (F ${t0} → M ${t1})`);

console.log('\n[cross-screen] Weekly per-day target follows the BMR calculator');
let lsTarget = null; try { lsTarget = window.localStorage.getItem('smartplate_target'); } catch (e) {}
ok(lsTarget === String(t1), `Setup persisted target to localStorage (${lsTarget})`);
window.__dc.activate('screen-weekly');
ok(weekly.innerHTML.includes('target ' + t1.toLocaleString()), `Weekly shows the shared target ${t1.toLocaleString()}`);

console.log('\n[weekly] per-meal portions (×N) + drink add-on change cost & kcal');
wc.setState({ picks: {}, qty: {}, addons: {}, spice: {}, areaOverride: {} }); // clean baseline
const tueD = () => resolved(1, 'D'); // Paneer Butter Masala (home-serviceable)
const unit = wc.num(tueD().cost), unitKcal = wc.kcalFor(tueD().item, 'DELIVER');
ok(wc.eff(tueD(), '1-D').cost === unit, `1 portion = unit price ₹${unit}`);
wc.stepQty(1, 'D', 1);
ok(wc.eff(tueD(), '1-D').cost === unit * 2, `×2 portions → ₹${unit * 2}`);
const di = wc.drinkInfo(tueD()); // per-outlet drink: price/kcal derived from the meal, not a literal 40/150
ok(di.available && di.price >= 30 && di.price <= 60, `TUE dinner outlet serves a drink, priced per-outlet ₹${di.price}`);
wc.toggleDrink(1, 'D');
ok(wc.eff(tueD(), '1-D').cost === unit * 2 + di.price, `+ drink adds the per-outlet price ₹${di.price} → ₹${unit * 2 + di.price}`);
ok(wc.eff(tueD(), '1-D').kcal === unitKcal * 2 + di.kcal, `kcal scales with portions + per-outlet drink kcal (${unitKcal * 2 + di.kcal})`);
// some outlets serve NO drink → button hidden, toggle is a no-op (Murugan Idli is a tiffin counter)
const noDrink = wc.optsForCtx(wc.baseDays()[0], 'B', wc.activeArea(0, 'B'))[0]; // MON breakfast = Murugan Idli
ok(wc.drinkInfo(noDrink).available === false, 'an outlet exists that serves no drink (Murugan Idli) — 🥤 button hidden');
const before = wc.renderVals().spent;
wc.stepQty(1, 'D', 1); // ×3
ok(wc.renderVals().spent === before + unit, 'whole-week spend tracks the extra portion');
wc.setState({ tab: 'grid' });
ok(/×\d/.test(weekly.innerHTML), 'grid cards expose a ×N portion control');

console.log('\n[setup] favourite restaurants + repeat-ordering insight (Taste DNA tab)');
sc.setState({ tab: 'dna' });
ok(/favourite restaurants/i.test(setup.innerHTML), 'Favourites panel renders');
ok(/consideration set/i.test(setup.innerHTML), 'shows the consideration-set / repeat-ordering insight');
ok(setup.innerHTML.includes('Murugan Idli'), 'default favourites listed');
const favN0 = sc.state.favs.length;
sc.removeFav('Murugan Idli');
ok(sc.state.favs.length === favN0 - 1 && !sc.state.favs.includes('Murugan Idli'), 'can remove a favourite');
sc.addFav();
ok(sc.state.favs.length === favN0, 'can add a favourite back');

console.log('\n[cross-screen] Weekly stars meals from favourite outlets');
sc.setState({ favs: ['Murugan Idli'] });
window.__dc.activate('screen-weekly');
wc.setState({ tab: 'grid', picks: {}, qty: {}, addons: {} });
ok(weekly.innerHTML.includes('⭐'), 'a ⭐ marks favourite-outlet meals on the plan');

console.log('\n[weekly] budget recommender + variety marking (new)');
ok(weekly.innerHTML.includes('Recommended') && weekly.innerHTML.includes('priced from'), 'budget recommendation present');
ok(/FLOOR/.test(weekly.innerHTML) && /VARIETY/.test(weekly.innerHTML), 'recommender shows Floor + Variety bands');
ok(weekly.innerHTML.includes('✦ new'), 'novel picks carry a subtle ✦ new mark (not V/U letters)');
ok(/✦ \d+ new/.test(weekly.innerHTML), 'week chip counts usual ⭐ vs new ✦');

console.log('\n[setup] slider cull → one mode dial + leans, grouped as Locks/Dials/Rules');
window.__dc.activate('screen-setup');
sc.setState({ tab: 'form' });
ok(/money ⇄ everything-else/.test(setup.innerHTML), 'priority sliders collapsed to the money⇄everything mode dial');
ok(/lean healthier/.test(setup.innerHTML), 'optional leans replace the slider stack');
ok(/🔒 Locks/.test(setup.innerHTML) && /🎛 Dials/.test(setup.innerHTML) && /⚡ Rules/.test(setup.innerHTML), 'Setup grouped as Locks / Dials / Rules');

console.log('\n[weekly] nested Day / Week / Month budgets');
window.__dc.activate('screen-weekly');
ok(weekly.innerHTML.includes('Budget horizon'), 'budget horizon control present');
ok(/rolls forward/.test(weekly.innerHTML), 'shows the nested / roll-forward principle');
wc.setHorizon('month');
ok(weekly.innerHTML.includes('8,600'), 'switching to Month shows the monthly cap (₹8,600)');
wc.setHorizon('week');

console.log('\n[setup] nutrition ledger framed by nutrient timescale');
sc.setState({ tab: 'nutri' });
ok(setup.innerHTML.includes("doesn't bank"), 'protein framed as DAILY (not a weekly debt)');
ok(/kcal banked/.test(setup.innerHTML), 'calories show an explicit banked credit');
ok(!/14 g this week/.test(setup.innerHTML), 'old weekly protein-debt framing removed');
ok(/Protein across the day/.test(setup.innerHTML), 'per-meal protein distribution viz present');

console.log('\n[weekly] (15/§6.2) pin & re-optimise-the-rest; off-plan extra-order hatch');
window.__dc.activate('screen-weekly');
wc.setState({ tab: 'grid', picks: wc.defaultSkips(), pins: {}, extras: [] });
wc.togglePin(1, 'D'); // pin TUE dinner (Paneer)
ok(wc.isPinned('1-D') && wc.pinnedCount() === 1, 'a slot can be 🔒 pinned (tracked in state)');
const pinnedPickBefore = wc.state.picks['1-D'] || 0;
const unpinnedPickBefore = wc.state.picks['0-B'] || 0;
wc.reoptimiseUnpinned();
ok((wc.state.picks['1-D'] || 0) === pinnedPickBefore, 're-optimise leaves the pinned slot untouched');
ok((wc.state.picks['0-B'] || 0) !== unpinnedPickBefore, 're-optimise re-rolls an unpinned, non-skip slot');
ok(weekly.innerHTML.includes('🔒'), 'pin control renders on the grid');
const spentNoExtra = wc.renderVals().spent;
wc.addExtra('Order now (one-off)', 160, 480);
ok(wc.state.extras.length === 1, 'an off-plan extra order is tracked in state');
ok(wc.renderVals().spent === spentNoExtra + 160, 'extra order adds to whole-week spend (budget burn-down truthful)');
ok(wc.extrasKcal() === 480, 'extra order adds to the kcal ledger');
ok(weekly.innerHTML.includes('＋ order now'), 'global ＋ order now affordance present');
wc.setState({ pins: {}, extras: [], picks: wc.defaultSkips() });

console.log('\n[weekly] (18/§6.3) day awareness: Today highlight + skip de-emphasis');
ok(/TODAY/.test(weekly.innerHTML), 'Today is tagged in the grid');
ok(weekly.innerHTML.includes('opacity:0.5'), 'skip cells render de-emphasised at ~0.5 opacity (not blurred)');
ok(weekly.innerHTML.includes('⊘'), 'skip cells carry a ⊘ tag');

console.log('\n[weekly] (14/§5.2) usual-first framing + infeasible three-way conflict');
ok(/kept \d+ of your usuals/.test(weekly.innerHTML), 'usual-first "kept N · swapped M" framing present');
// force over-cap to surface the conflict prompt's three-way choice
wc.setState({ picks: {}, qty: { '0-D': 9, '1-D': 9, '2-D': 9, '3-D': 9, '4-L': 9 } });
ok(wc.renderVals().left < 0, 'picks pushed over the ₹2,000 cap');
ok(/add new outlet/.test(weekly.innerHTML) && /raise budget/.test(weekly.innerHTML) && /relax target/.test(weekly.innerHTML), 'infeasible conflict offers add-outlet · raise-budget · relax-target');
wc.setState({ picks: wc.defaultSkips(), qty: {} });

console.log('\n[weekly] (19/§3.1) returning-after-a-gap reconcile banner');
ok(/returning after a gap/.test(weekly.innerHTML), 'a control to surface the welcome-back banner is present');
wc.toggleWelcomeBack();
ok(/Welcome back/.test(weekly.innerHTML) && /over-correct/.test(weekly.innerHTML), 'welcome-back banner explains roll-over nutrients won\'t over-correct');
ok(/Followed the plan/.test(weekly.innerHTML) && /Ate out/.test(weekly.innerHTML) && /Log it/.test(weekly.innerHTML), 'reconcile offers followed-the-plan · ate-out · log-it');
wc.toggleWelcomeBack();

console.log('\n[setup] (17/§4.2) ⚡ rule template gallery — templates + toggles, not syntax');
window.__dc.activate('screen-setup');
sc.setState({ tab: 'form' });
ok(/suggested rules you can toggle/.test(setup.innerHTML), 'rules reframed as suggested toggles');
ok(/auto-derived/.test(setup.innerHTML), 'note that most rules are auto-derived from calendar/locks');
ok(!setup.innerHTML.includes('Template gallery'), 'gallery is closed by default');
sc.toggleGallery();
ok(/Template gallery/.test(setup.innerHTML) && /WHEN/.test(setup.innerHTML) && /THEN/.test(setup.innerHTML), 'gallery opens to a fill-in-the-blank When→Then template');
ok(/no syntax to write/.test(setup.innerHTML), 'gallery reads as templates, not raw logic');
sc.toggleGallery();

console.log('\n[weekly] (L1/§6.3) optimising skeleton + provider-down error states, each with recovery');
window.__dc.activate('screen-weekly');
wc.setState({ tab: 'grid', picks: wc.defaultSkips() });
ok(!/Optimising your week/.test(weekly.innerHTML), 'no optimising skeleton in the default live state');
wc.setPhase('optimising');
ok(/Optimising your week/.test(weekly.innerHTML), 'optimising… skeleton state renders');
wc.setPhase('error');
ok(/reach the kitchen/.test(weekly.innerHTML) && /Retry/.test(weekly.innerHTML), 'provider-down error state carries a ↻ Retry recovery action');
wc.setPhase('live');

console.log(`\n${fails.length ? '✗ FAIL — ' + fails.length + ' assertion(s)' : '✓ ALL PASS'}\n`);
process.exit(fails.length ? 1 : 0);
