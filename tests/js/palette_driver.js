/**
 * Test driver for src/webapp/static/palette.js (used by tests/test_web_palette.py).
 *
 * Runs the real palette.js against a minimal stand-in for the browser DOM,
 * types each query into the palette, lets the company search settle, presses
 * Enter, and records the outcome: the URL the palette navigates to, "#help"
 * when Enter opened the HELP overlay, or null when Enter did nothing.
 *
 * Usage:  node palette_driver.js <path/to/palette.js>  < spec.json
 * spec:   {"queries": ["AAPL GP", ...],
 *          "searchHits": {"<query>": [{"ticker": ..., "company_name": ...}]},
 *          "allowCollect": true|false}   // the page's data-allow-collect flag
 * Prints: a JSON array with one outcome per query.
 */
'use strict';

const fs = require('fs');
const vm = require('vm');

function fakeElement(tagName) {
  const listeners = {};
  return {
    tagName: tagName.toUpperCase(),
    hidden: true,
    value: '',
    placeholder: '',
    className: '',
    textContent: '',
    dataset: {},
    appendChild(child) { return child; },
    focus() {},
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    fire(type, props) {
      const evt = Object.assign({ target: this, preventDefault() {} }, props);
      (listeners[type] || []).forEach((fn) => fn(evt));
    },
  };
}

async function outcomeFor(source, query, spec) {
  const elements = {
    'palette-overlay': fakeElement('div'),
    'palette-input': fakeElement('input'),
    'palette-results': fakeElement('div'),
    'help-overlay': fakeElement('div'),
  };
  const timers = [];
  const storage = new Map();
  let navigatedTo = null;

  const window = {
    location: {
      pathname: '/',
      get href() { return navigatedTo; },
      set href(url) { navigatedTo = url; },
    },
    setTimeout(fn) { timers.push(fn); return timers.length; },
    clearTimeout() {},
  };
  vm.runInNewContext(source, {
    window,
    document: {
      body: { getAttribute: (name) => (name === 'data-allow-collect' && spec.allowCollect ? '1' : null) },
      getElementById: (id) => elements[id] || null,
      createElement: (tag) => fakeElement(tag),
      addEventListener() {},
    },
    localStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => { storage.set(key, String(value)); },
    },
    fetch: (url) => {
      const q = new URL(url, 'http://localhost').searchParams.get('q');
      const hits = (spec.searchHits || {})[q] || [];
      return Promise.resolve({ ok: true, json: () => Promise.resolve(hits) });
    },
  });

  window.Palette.open();
  const input = elements['palette-input'];
  input.value = query;
  input.fire('input');
  while (timers.length) timers.shift()(); // flush the input debounce
  await new Promise((resolve) => setImmediate(resolve)); // let the company search settle
  input.fire('keydown', { key: 'Enter' });
  if (navigatedTo !== null) return navigatedTo;
  return elements['help-overlay'].hidden ? null : '#help';
}

(async () => {
  const source = fs.readFileSync(process.argv[2], 'utf8');
  const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
  const outcomes = [];
  for (const query of spec.queries) outcomes.push(await outcomeFor(source, query, spec));
  console.log(JSON.stringify(outcomes));
})();
