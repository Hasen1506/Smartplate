/*
 * SmartPlate wireframe runtime  (the self-contained replacement for the missing ./support.js)
 * --------------------------------------------------------------------------------------------
 * The two ".dc.html" exports were authored against a design-tool runtime ("support.js") that
 * was never shipped with the repo. Without it the templates render as literal "{{ ... }}" text
 * with every interactive component missing. This file re-implements exactly the slice of that
 * runtime the two screens use — nothing more — so the merged wireframe renders offline, with no
 * CDN and no external dependency (which also closes design-audit finding F1, the React-CDN SPOF).
 *
 * What the screens actually use (verified against both files):
 *   - React.createElement (aliased `h`) with HOST tags only (div/span/button/svg/...), no hooks,
 *     no custom React components, no lifecycle, no ReactDOM.
 *   - this.state / this.setState (object or updater fn) on a `DCLogic` base class.
 *   - Template placeholders {{ ident }} / {{ ident.path }} / {{ true|false }}.
 *   - <sc-if value="{{ flag }}">…</sc-if>  and  <sc-for list="{{ arr }}" as="x">…</sc-for>.
 *   - onClick="{{ handler }}" bindings.
 * So this runtime is a ~minimal mini-React + a small template interpreter + a screen bootstrap.
 */
(function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var SVG_TAGS = { svg: 1, g: 1, path: 1, circle: 1, rect: 1, line: 1, polyline: 1, polygon: 1, ellipse: 1, text: 1, tspan: 1, defs: 1, stop: 1, lineargradient: 1, radialgradient: 1, clippath: 1, use: 1, symbol: 1, marker: 1, mask: 1, pattern: 1, filter: 1, title: 1 };
  // SVG attribute names that must stay camelCase (everything else is kebab-cased).
  var SVG_CAMEL_KEEP = { viewBox: 1, preserveAspectRatio: 1, gradientUnits: 1, gradientTransform: 1, patternUnits: 1, patternContentUnits: 1, spreadMethod: 1, xmlns: 1 };

  function kebab(s) { return s.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); }); }
  function svgAttr(key) { return SVG_CAMEL_KEEP[key] ? key : (/[A-Z]/.test(key) ? kebab(key) : key); }

  function styleToCss(obj) {
    var out = [];
    for (var k in obj) {
      if (!Object.prototype.hasOwnProperty.call(obj, k)) continue;
      var v = obj[k];
      if (v == null) continue;
      out.push(kebab(k) + ':' + (typeof v === 'number' ? String(v) : v));
    }
    return out.join(';');
  }

  // ---- mini-React -------------------------------------------------------------------------
  function isVNode(x) { return x != null && typeof x === 'object' && x.__v === true; }

  function createElement(tag, props) {
    var children = [];
    function add(c) {
      if (c == null || c === false || c === true) return;       // React drops these
      if (Array.isArray(c)) { c.forEach(add); return; }
      children.push(c);
    }
    for (var i = 2; i < arguments.length; i++) add(arguments[i]);
    return { __v: true, tag: tag, props: props || {}, children: children };
  }

  // Turn a vnode (or primitive) into a real DOM node.
  function mount(vnode, parentIsSvg) {
    if (vnode == null || vnode === false || vnode === true) return document.createComment('');
    if (!isVNode(vnode)) return document.createTextNode(String(vnode));

    var tag = vnode.tag, props = vnode.props, children = vnode.children;
    var isSvg = parentIsSvg || tag === 'svg' || !!SVG_TAGS[String(tag).toLowerCase()];
    var el = isSvg ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);

    for (var key in props) {
      if (!Object.prototype.hasOwnProperty.call(props, key)) continue;
      var val = props[key];
      if (key === 'key' || key === 'ref') continue;
      if (key === 'style' && val && typeof val === 'object') { el.setAttribute('style', styleToCss(val)); continue; }
      if (/^on[A-Z]/.test(key)) { if (typeof val === 'function') el.addEventListener(key.slice(2).toLowerCase(), val); continue; }
      if (key === 'className') { if (val != null) el.setAttribute('class', String(val)); continue; }
      if (val == null || val === false) continue;
      el.setAttribute(isSvg ? svgAttr(key) : key, val === true ? '' : String(val));
    }
    for (var c = 0; c < children.length; c++) el.appendChild(mount(children[c], isSvg));
    return el;
  }

  // ---- DCLogic base (what the component classes `extends`) ---------------------------------
  function DCLogic(props) { this.props = props || {}; if (!this.state) this.state = {}; }
  DCLogic.prototype.setState = function (patch) {
    var next = (typeof patch === 'function') ? patch(this.state) : patch;
    this.state = Object.assign({}, this.state, next);
    if (typeof this._render === 'function') this._render();
  };

  // ---- template-expression evaluation -----------------------------------------------------
  // Only three forms occur: a bare identifier, a dotted path (inside sc-for), or true/false.
  function stripBraces(s) {
    if (s == null) return '';
    var m = /\{\{([\s\S]*?)\}\}/.exec(s);
    return (m ? m[1] : s).trim();
  }
  function evalExpr(expr, scope) {
    expr = String(expr).trim();
    if (expr === 'true') return true;
    if (expr === 'false') return false;
    if (expr === '') return '';
    var parts = expr.split('.'), v = scope;
    for (var i = 0; i < parts.length; i++) { if (v == null) return undefined; v = v[parts[i].trim()]; }
    return v;
  }
  // String interpolation for attribute values (placeholders here are always primitives).
  function interp(str, scope) {
    return String(str).replace(/\{\{([^}]*)\}\}/g, function (_, e) {
      var v = evalExpr(e, scope);
      return v == null ? '' : String(v);
    });
  }

  // ---- template interpreter ---------------------------------------------------------------
  // Walk a parsed source node and produce output DOM node(s), substituting `scope` values.
  // Returns an array because sc-for fans out and sc-if can collapse to nothing.
  function build(src, scope, parentIsSvg) {
    if (src.nodeType === 3) return processText(src.nodeValue, scope); // text
    if (src.nodeType !== 1) return [];                                // comments / other → drop

    var tag = src.tagName.toLowerCase();

    if (tag === 'sc-if') {
      return evalExpr(stripBraces(src.getAttribute('value')), scope) ? buildChildren(src, scope, parentIsSvg) : [];
    }
    if (tag === 'sc-for') {
      var list = evalExpr(stripBraces(src.getAttribute('list')), scope) || [];
      var as = (src.getAttribute('as') || 'item').trim();
      var out = [];
      for (var i = 0; i < list.length; i++) {
        var childScope = Object.assign({}, scope);
        childScope[as] = list[i];
        childScope[as + 'Index'] = i;
        out = out.concat(buildChildren(src, childScope, parentIsSvg));
      }
      return out;
    }

    var isSvg = parentIsSvg || src.namespaceURI === SVG_NS || tag === 'svg';
    var el = isSvg ? document.createElementNS(SVG_NS, src.localName) : document.createElement(tag);

    var attrs = src.attributes;
    for (var a = 0; a < attrs.length; a++) {
      var name = attrs[a].name, value = attrs[a].value;
      var lower = name.toLowerCase();
      if (lower === 'onclick') {
        var fn = evalExpr(stripBraces(value), scope);
        if (typeof fn === 'function') el.addEventListener('click', fn);
        continue;
      }
      if (lower === 'hint-placeholder-val' || lower === 'hint-placeholder-count') continue; // design-tool hints
      el.setAttribute(name, interp(value, scope));
    }

    var kids = src.childNodes;
    for (var c = 0; c < kids.length; c++) {
      var built = build(kids[c], scope, isSvg);
      for (var b = 0; b < built.length; b++) el.appendChild(built[b]);
    }
    return [el];
  }

  function buildChildren(src, scope, parentIsSvg) {
    var kids = src.childNodes, out = [];
    for (var c = 0; c < kids.length; c++) out = out.concat(build(kids[c], scope, parentIsSvg));
    return out;
  }

  // A text node may interleave literal text with {{ }} placeholders. A placeholder can resolve
  // to a primitive (→ text) or to a vnode / array of vnodes (→ mounted React subtree).
  function processText(text, scope) {
    var parts = [], re = /\{\{([^}]*)\}\}/g, last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) parts.push(document.createTextNode(text.slice(last, m.index)));
      var v = evalExpr(m[1], scope);
      if (isVNode(v)) parts.push(mount(v));
      else if (Array.isArray(v)) v.forEach(function (x) { parts.push(isVNode(x) ? mount(x) : document.createTextNode(x == null ? '' : String(x))); });
      else if (v != null && v !== false && v !== true) parts.push(document.createTextNode(String(v)));
      last = re.lastIndex;
    }
    if (last < text.length) parts.push(document.createTextNode(text.slice(last)));
    return parts;
  }

  // ---- screen wiring ----------------------------------------------------------------------
  function loadComponentClass(scriptText) {
    // Each screen's logic is `class Component extends DCLogic {…}`; eval it in a private scope.
    return new Function('DCLogic', 'React', 'ReactDOM', scriptText + '\n;return Component;')(
      DCLogic, { createElement: createElement }, {}
    );
  }

  function makeScreen(container, templateEl, ComponentClass, getNotes) {
    var comp = new ComponentClass({ showAnnotations: getNotes() });
    var source = templateEl.content;
    comp._render = function () {
      comp.props.showAnnotations = getNotes();
      var vals = comp.renderVals();
      var frag = document.createDocumentFragment();
      var nodes = source.childNodes;
      for (var i = 0; i < nodes.length; i++) {
        var built = build(nodes[i], vals, false);
        for (var b = 0; b < built.length; b++) frag.appendChild(built[b]);
      }
      container.replaceChildren(frag);
    };
    comp._render();
    return comp;
  }

  function boot() {
    var notesToggle = document.getElementById('dc-notes');
    var getNotes = function () { return notesToggle ? !!notesToggle.checked : true; };

    var comps = [];
    var screens = document.querySelectorAll('.dc-screen');
    for (var i = 0; i < screens.length; i++) {
      var container = screens[i];
      var tpl = document.getElementById(container.getAttribute('data-template'));
      var logic = document.getElementById(container.getAttribute('data-logic'));
      if (!tpl || !logic) continue;
      try {
        comps.push(makeScreen(container, tpl, loadComponentClass(logic.textContent), getNotes));
      } catch (err) {
        container.innerHTML = '<pre style="color:#cf4a2f;font:14px monospace;white-space:pre-wrap;padding:16px;">Failed to render screen: ' + String(err && err.stack || err) + '</pre>';
        if (typeof console !== 'undefined') console.error('[dc] screen render failed', err);
      }
    }

    // top-level tab bar: switch which screen is visible
    var tabs = document.querySelectorAll('.dc-tab');
    function activate(targetId) {
      for (var s = 0; s < screens.length; s++) screens[s].style.display = (screens[s].id === targetId) ? '' : 'none';
      for (var t = 0; t < tabs.length; t++) tabs[t].setAttribute('data-active', tabs[t].getAttribute('data-target') === targetId ? 'true' : 'false');
    }
    for (var t = 0; t < tabs.length; t++) {
      (function (btn) { btn.addEventListener('click', function () { activate(btn.getAttribute('data-target')); }); })(tabs[t]);
    }
    if (tabs.length) activate(tabs[0].getAttribute('data-target'));

    // notes (red annotations) toggle: re-render every screen
    if (notesToggle) notesToggle.addEventListener('change', function () { comps.forEach(function (c) { c._render(); }); });

    // expose a tiny handle for tests / debugging
    window.__dc = { comps: comps, activate: activate };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
