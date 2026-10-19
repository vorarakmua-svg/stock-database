/**
 * Test driver for src/webapp/static/palette.js (used by tests/test_web_palette.py).
 *
 * Plays scripted keyboard scenarios against the real palette.js in a minimal
 * stand-in for the browser DOM (with a fake company-search API) and records
 * each outcome: the URL the palette navigated to, "#help" when the HELP
 * overlay opened, or null when nothing happened.
 *
 * Usage:  node palette_driver.js <path/to/palette.js>  < spec.json
 * spec:   {"scenarios": [[["open"], ["type", "AAPL GP"], ["settle"], ["enter"]], ...],
 *          "searchHits": {"<query>": [{"ticker": ..., "company_name": ...}]},
 *          "allowCollect": true|false}   // the page's data-allow-collect flag
 * Steps:  open | type <text> | settle (let the input debounce and the company
 *         search finish) | enter | escape. Every scenario ends with a settle.
 * Prints: a JSON array with one outcome per scenario.
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

async function play(source, steps, spec) {
  const elements = {
    'palette-overlay': fakeElement('div'),
    'palette-input': fakeElement('input'),
    'palette-results': fakeElement('div'),
    'help-overlay': fakeElement('div'),
  };
  const timers = new Map();
  let nextTimer = 0;
  const storage = new Map();
  let navigatedTo = null;

  const window = {
    location: {
      pathname: '/',
      get href() { return navigatedTo; },
      set href(url) { navigatedTo = url; },
    },
    setTimeout(fn) { timers.set(++nextTimer, fn); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
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

  const input = elements['palette-input'];
  async function settle() {
    do {
      for (const [id, fn] of [...timers]) { timers.delete(id); fn(); }
      await new Promise((resolve) => setImmediate(resolve)); // let company searches answer
    } while (timers.size);
  }
  const actions = {
    open: () => window.Palette.open(),
    type: (text) => { input.value = text; input.fire('input'); },
    settle,
    enter: () => input.fire('keydown', { key: 'Enter' }),
    escape: () => input.fire('keydown', { key: 'Escape' }),
  };
  for (const [name, arg] of steps) await actions[name](arg);
  await settle();
  if (navigatedTo !== null) return navigatedTo;
  return elements['help-overlay'].hidden ? null : '#help';
}

(async () => {
  const source = fs.readFileSync(process.argv[2], 'utf8');
  const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
  const outcomes = [];
  for (const steps of spec.scenarios) outcomes.push(await play(source, steps, spec));
  console.log(JSON.stringify(outcomes));
})();
