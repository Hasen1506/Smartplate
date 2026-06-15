# SmartPlate — Wireframes

## TL;DR — open this one file

**[`SmartPlate Wireframes.html`](./SmartPlate%20Wireframes.html)** — both screens (Weekly Plan +
Setup & Rules) merged into a single, **self-contained, offline** page. Double-click it / open it
in any browser. No build step, no server, no CDN, no `support.js` needed.

## Why the original `.dc.html` files looked broken

The two source exports —

- `SmartPlate Weekly Plan - Wireframes.dc.html`
- `SmartPlate Setup and Rules - Wireframes.dc.html`

— were authored against a design-tool runtime that they load with `<script src="./support.js">`.
**That `support.js` was never shipped in this repo.** Without it, the templates can't render: the
`{{ selectorEl }}`, `{{ gridEl }}`, `{{ burndownEl }}`… placeholders stay as literal text and the
`<sc-if>` / `<sc-for>` sections never expand, so the calendar grid, the window selector, the
burn-down chart and the mobile views are all **missing**, and the leftover empty slots make the
page look **misaligned**. (The design audit logged the related React-CDN failure as finding **F1**.)

## What the merged file does

`SmartPlate Wireframes.html` fixes that by inlining a tiny replacement runtime and stitching both
screens together:

- **`wireframe-runtime.js`** — a ~minimal, dependency-free stand-in for `support.js`: a small
  React-compatible renderer (`createElement` + host-element mount, the only React surface the
  screens use) plus a template interpreter for `{{ … }}`, `<sc-if>`, `<sc-for>` and
  `onClick="{{ … }}"`. Inlining it means **no external React/CDN** — which also closes audit F1.
- A **top tab-bar shell** that merges the two screens into one document (and a "design notes"
  toggle for the red annotations).
- Each screen's `<x-dc>` template and `class Component` logic are reused **verbatim** from the
  source files (the build just extracts them), so nothing is hand-retyped.

## Rebuild / verify

```bash
# regenerate SmartPlate Wireframes.html from the two .dc.html sources
node project/build-merged.mjs

# end-to-end render test (asserts both screens render, no leftover {{ }}, tabs/toggles work)
npm install jsdom        # dev-only; not committed
node project/test-merged.mjs
```

| File | Role |
|------|------|
| `SmartPlate Wireframes.html` | **the deliverable** — merged, self-contained, runnable |
| `wireframe-runtime.js` | offline runtime (replaces the missing `support.js`) |
| `build-merged.mjs` | extracts both screens → writes the merged HTML |
| `test-merged.mjs` | jsdom end-to-end render test of the merged HTML |
| `SmartPlate Weekly Plan - Wireframes.dc.html` | original source export (Weekly Plan screen) |
| `SmartPlate Setup and Rules - Wireframes.dc.html` | original source export (Setup & Rules screen) |

The two `.dc.html` files are kept as the build's source of truth; edit them and re-run
`build-merged.mjs` to refresh the merged page.
