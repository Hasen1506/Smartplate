/*
 * build-merged.mjs — assemble the two SmartPlate ".dc.html" screen exports into ONE
 * self-contained, runnable wireframe: "SmartPlate Wireframes.html".
 *
 * It extracts each screen's <x-dc> template + its <script type="text/x-dc"> logic verbatim
 * (so nothing is hand-retyped), drops the design-tool scaffolding that can't run here
 * (<helmet>, the thumbnail <template>, the per-file screen-nav, the missing ./support.js),
 * and inlines wireframe-runtime.js plus a top tab-bar shell that switches between screens.
 *
 * Run:  node project/build-merged.mjs
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

const SCREENS = [
  { id: 'weekly', tab: '▦ Weekly Plan', file: 'SmartPlate Weekly Plan - Wireframes.dc.html' },
  { id: 'setup',  tab: '🔒 Setup & Rules', file: 'SmartPlate Setup and Rules - Wireframes.dc.html' },
];

function must(re, str, label) {
  const m = re.exec(str);
  if (!m) throw new Error(`build-merged: could not find ${label}`);
  return m;
}

function extract(file) {
  const raw = readFileSync(join(HERE, file), 'utf8');

  // 1) the screen markup lives inside <x-dc>…</x-dc>
  let tpl = must(/<x-dc>([\s\S]*?)<\/x-dc>/, raw, '<x-dc> block')[1];

  // 2) strip the bits that only make sense to the original design tool / standalone file
  tpl = tpl.replace(/<helmet>[\s\S]*?<\/helmet>/, '');                          // fonts/reset → hoisted to <head> once
  tpl = tpl.replace(/<template id="__bundler_thumbnail"[\s\S]*?<\/template>/, ''); // editor thumbnail
  tpl = tpl.replace(/<!--\s*screen nav\s*-->[\s\S]*?<\/div>\s*/, '');           // per-file nav pills → replaced by the shell tab bar
  tpl = tpl.trim();

  // 3) the component logic: class Component extends DCLogic {…}
  const logic = must(/<script type="text\/x-dc"[^>]*>([\s\S]*?)<\/script>/, raw, '<script type="text/x-dc"> logic')[1].trim();

  return { tpl, logic };
}

const runtime = readFileSync(join(HERE, 'wireframe-runtime.js'), 'utf8');
if (runtime.includes('</script>')) throw new Error('runtime contains </script> and cannot be inlined safely');

const parts = SCREENS.map((s) => ({ ...s, ...extract(s.file) }));

const tabButtons = parts
  .map((p, i) => `      <button class="dc-tab" data-target="screen-${p.id}" data-active="${i === 0 ? 'true' : 'false'}">${p.tab}</button>`)
  .join('\n');

const screenDivs = parts
  .map((p, i) => `  <div class="dc-screen" id="screen-${p.id}" data-template="tpl-${p.id}" data-logic="logic-${p.id}"${i === 0 ? '' : ' style="display:none;"'}></div>`)
  .join('\n');

const templates = parts
  .map((p) => `  <template id="tpl-${p.id}">\n${p.tpl}\n  </template>`)
  .join('\n');

const logicScripts = parts
  .map((p) => `<script type="text/x-dc" id="logic-${p.id}">\n${p.logic}\n</script>`)
  .join('\n');

const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SmartPlate · Wireframes</title>
<!-- fonts + reset (hoisted from each screen's <helmet>) -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Caveat:wght@500;600;700&family=Gaegu:wght@300;400;700&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: 'Gaegu', cursive; background: #efece4; color: #2a2a28; }
  /* top tab-bar shell that merges the two screens into one document */
  .dc-appbar {
    position: sticky; top: 0; z-index: 50;
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    padding: 12px 24px; background: #f3efe6; border-bottom: 2.5px solid #2a2a28;
    box-shadow: 0 3px 0 rgba(42,42,40,.08);
  }
  .dc-brand { font: 700 30px/1 'Caveat', cursive; }
  .dc-tabs { display: flex; gap: 8px; }
  .dc-tab {
    font: 700 15px 'Gaegu', cursive; padding: 8px 15px; cursor: pointer;
    border: 2px solid #2a2a28; border-radius: 11px 9px 11px 9px; background: #fff; color: #2a2a28;
    box-shadow: 2px 2px 0 rgba(42,42,40,.12);
  }
  .dc-tab[data-active="true"] { background: #2a2a28; color: #fff; }
  .dc-spacer { flex: 1; }
  .dc-notes { display: flex; align-items: center; gap: 7px; font: 700 14px 'Gaegu', cursive; cursor: pointer; user-select: none; }
  .dc-notes input { width: 16px; height: 16px; accent-color: #cf4a2f; cursor: pointer; }
  .dc-badge {
    font: 600 15px 'Caveat', cursive; border: 2px solid #2a2a28; border-radius: 9px 11px 9px 12px;
    padding: 5px 11px; transform: rotate(-2deg); background: #d8ecd5;
  }
</style>
</head>
<body>
  <div class="dc-appbar">
    <span class="dc-brand">SmartPlate</span>
    <div class="dc-tabs">
${tabButtons}
    </div>
    <span class="dc-spacer"></span>
    <label class="dc-notes"><input type="checkbox" id="dc-notes" checked> ✎ design notes</label>
    <span class="dc-badge">self-contained · offline</span>
  </div>

${screenDivs}

  <!-- ===== screen templates (interpreted by the runtime below) ===== -->
${templates}

  <!-- ===== per-screen component logic (class Component extends DCLogic) ===== -->
${logicScripts}

  <!-- ===== runtime: mini-React + template interpreter + screen bootstrap (was ./support.js) ===== -->
  <script>
${runtime}
  </script>
</body>
</html>
`;

const outPath = join(HERE, 'SmartPlate Wireframes.html');
writeFileSync(outPath, html, 'utf8');
console.log(`✓ wrote ${outPath} (${html.length.toLocaleString()} bytes)`);
console.log(`  screens: ${parts.map((p) => p.id).join(', ')}`);
