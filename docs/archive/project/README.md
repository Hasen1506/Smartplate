# SmartPlate — Wireframe

## Open this file

**[`SmartPlate Wireframes.html`](./SmartPlate%20Wireframes.html)** — both screens (Weekly Plan +
Setup & Rules) in one **self-contained, offline** page. Double-click it / open it in any browser.
No build step, no server, no CDN, no external runtime.

This single file is now the **source of truth** — edit it directly. (It started life as two
separate design-tool exports, `… Weekly Plan …` and `… Setup and Rules …`, which loaded a
`support.js` runtime that was never shipped, so they rendered as broken `{{ … }}` text. Those two
files have been merged here and removed.)

## What's inside

- A top **tab-bar shell** that switches between the two screens (plus a "design notes" toggle).
- Each screen's markup `<template>` and its `class Component extends DCLogic { … }` logic.
- An inlined **runtime** (mini-React `createElement` + a small template interpreter for
  `{{ … }}`, `<sc-if>`, `<sc-for>`, `onClick`) that renders it all with **no external dependency**.

## Verify

```bash
npm install jsdom            # dev-only, not committed
node project/test-merged.mjs # end-to-end render test of SmartPlate Wireframes.html
```

The test loads the file in a real DOM (jsdom) and asserts both screens render with no leftover
`{{ }}` placeholders, the default plan is within the ₹2,000 cap, the ▢/⌂/⊘ order-cook-skip toggle
and area switch react, internal tabs (grid↔dashboard, settings↔Taste-DNA), the notes toggle, and
the top tab-bar all work.
