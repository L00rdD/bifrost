// A fake page for the tests of the dashboard's script (dashboard/bifrost.js): just enough DOM,
// fetch and canvas for the script to run in node, and for a test to read what it drew.
// Not a test itself (no ".test." in the name).
const path = require("node:path");

// DASHBOARD_JS: another copy of the script, to check the tests against an older version
const SCRIPT = process.env.DASHBOARD_JS || path.join(__dirname, "../dashboard/bifrost.js");

const camel = (name) => name.replace(/-(\w)/g, (_, c) => c.toUpperCase());

// One compound selector: tag, #id, .class and [attr="value"], in any number
function parse(compound) {
  const tag = (compound.match(/^[a-z][a-z0-9]*/) || [])[0];
  const ids = [...compound.matchAll(/#([\w-]+)/g)].map((m) => m[1]);
  const classes = [...compound.matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
  const attrs = [...compound.matchAll(/\[([\w-]+)="([^"]*)"\]/g)].map((m) => [m[1], m[2]]);
  return (e) => (!tag || e.tagName === tag.toUpperCase()) && ids.every((id) => e.id === id)
    && classes.every((c) => e.classList.contains(c)) && attrs.every(([k, v]) => e.getAttribute(k) === v);
}

// "a b": b inside an a, itself inside root
function matcher(selector) {
  const steps = selector.trim().split(/\s+/).map(parse);
  return (e, root) => {
    if (!steps[steps.length - 1](e)) return false;
    let i = steps.length - 2;
    for (let p = e.parentNode; i >= 0 && p && p !== root; p = p.parentNode) if (steps[i](p)) i--;
    return i < 0;
  };
}

class Text {
  constructor(text) { this.textContent = String(text); }
}

class Element {
  constructor(tag, page) {
    this.tagName = tag.toUpperCase();
    this.page = page;
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.classes = new Set();
    this.hidden = false;
    this.html = "";
    this.offsetLeft = 0;
    this.offsetWidth = 0;
    this.animations = [];
    const props = {};
    this.style = { props, setProperty: (k, v) => { props[k] = String(v); } };
    const classes = this.classes;
    this.classList = {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
      toggle: (c, on = !classes.has(c)) => (on ? classes.add(c) : classes.delete(c), on),
    };
  }

  get className() { return [...this.classes].join(" "); }
  set className(v) { this.classes.clear(); for (const c of String(v).split(" ").filter(Boolean)) this.classes.add(c); }
  get id() { return this.attributes.id || ""; }
  set id(v) { this.attributes.id = v; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.replaceChildren(String(v)); }
  set innerHTML(v) { this.html = v; this.children = []; }
  get innerHTML() { return this.html; }
  get isConnected() {
    let e = this;
    while (e.parentNode) e = e.parentNode;
    return e === this.page.root;
  }

  append(...nodes) {
    for (const n of nodes) {
      const node = typeof n === "object" ? n : new Text(n);
      if (node.parentNode) node.remove();
      node.parentNode = this;
      this.children.push(node);
    }
  }

  replaceChildren(...nodes) {
    for (const c of this.children) c.parentNode = null;
    this.children = [];
    if (nodes.length && nodes[0] !== "") this.append(...nodes);
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
    this.parentNode = null;
  }

  insertAdjacentHTML(where, html) { this.html += html; }
  setAttribute(k, v) {
    if (k.startsWith("data-")) this.dataset[camel(k.slice(5))] = String(v);
    else this.attributes[k] = String(v);
  }

  getAttribute(k) {
    if (k.startsWith("data-")) return this.dataset[camel(k.slice(5))] ?? null;
    if (k === "class") return this.className;
    return this.attributes[k] ?? null;
  }

  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  // what the browser does on an event: runs the listeners, returns what they return (promises)
  fire(type, event = {}) { return (this.listeners[type] || []).map((fn) => fn({ target: this, ...event })); }
  click() { return this.fire("click"); }

  descendants() { return this.children.filter((c) => c instanceof Element).flatMap((c) => [c, ...c.descendants()]); }
  querySelectorAll(selector) {
    const match = matcher(selector);
    return this.descendants().filter((e) => match(e, this));
  }

  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  animate(frames, options) {
    this.animations.push({ frames, options });
    return { finished: Promise.resolve() };
  }

  getBoundingClientRect() { return this.rect || { left: 0, top: 0, width: 0, height: 0 }; }
  // a canvas: what the probe of the live screen reads back, page.pixels
  getContext() {
    return {
      drawImage: () => {},
      getImageData: (x, y, w, h) => ({ data: this.page.pixels.slice(0, w * h * 4) }),
    };
  }
}

// The page of dashboard/index.html, without its script: the sections the script fills
function skeleton(page) {
  const make = (tag, attrs = {}, ...kids) => {
    const e = new Element(tag, page);
    for (const [k, v] of Object.entries(attrs)) k === "class" ? (e.className = v) : e.setAttribute(k, v);
    e.append(...kids);
    return e;
  };
  const chain = make("div", { class: "chain", id: "chain" },
    ...["homebridge", "broker", "bridge", "air"].flatMap((n, i) => [
      make("div", { class: "node", "data-n": n }),
      ...(i < 3 ? [make("div", { class: "link", "data-l": i })] : []),
    ]));
  chain.querySelectorAll(".node").forEach((n, i) => Object.assign(n, { offsetLeft: 100 * i, offsetWidth: 40 }));
  const label = make("div", { class: "label", id: "cinema-label" });
  label.hidden = true;
  const sections = ["projectors", "covers"].map((id) => Object.assign(make("section", { id }), { hidden: true }));
  return make("html", {}, make("body", {},
    make("main", {},
      make("div", { class: "sub", id: "updated" }, "loading…"),
      make("section", { id: "outlets" }),
      label, ...sections,
      make("section", { class: "panel" }, chain, make("p", { class: "last", id: "last" }, "no RF code yet"))),
    make("div", { class: "toast", id: "toast" })));
}

// Fake fetch: answer(url, options) returns a JSON value, a status number, or an Error to throw
// ({ status, body } for an error status with a body)
function fetcher(page) {
  return async (url, options = {}) => {
    page.calls.push({ url, method: options.method || "GET", body: options.body && JSON.parse(options.body) });
    const a = page.answer(url, options);
    if (a instanceof Error) throw a;
    if (typeof a === "number") return { ok: false, status: a, json: async () => { throw new SyntaxError("no JSON"); } };
    if (a && a.status) return { ok: false, status: a.status, json: async () => a.body };
    return { ok: true, status: 200, json: async () => structuredClone(a) };
  };
}

// Sets up the globals of the dashboard's page; status is what /api/status.json answers.
function page(status) {
  const p = { calls: [], status, posts: {}, pixels: [] };
  p.root = skeleton(p);
  p.answer = (url, options) => (options.method === "POST" ? (p.posts[url] ?? {}) : p.status);
  const listeners = {};
  p.document = {
    hidden: false,
    activeElement: null,
    createElement: (tag) => new Element(tag, p),
    querySelector: (sel) => p.root.querySelector(sel),
    querySelectorAll: (sel) => p.root.querySelectorAll(sel),
    addEventListener: (type, fn) => (listeners[type] ??= []).push(fn),
  };
  p.fire = (type) => Promise.all((listeners[type] || []).map((fn) => fn()));
  p.$ = (sel, root = p.root) => root.querySelector(sel);
  p.text = (sel, root) => p.$(sel, root).textContent;
  p.posted = () => p.calls.filter((c) => c.method === "POST").map((c) => [c.url, c.body]);
  p.polls = () => p.calls.filter((c) => c.url === "/api/status.json").length;
  globalThis.document = p.document;
  globalThis.CSS = { escape: (s) => s };
  globalThis.fetch = fetcher(p);
  return p;
}

// Runs the script afresh on the page, then lets its promises settle
async function run() {
  const file = require("node:fs").realpathSync(SCRIPT);
  delete require.cache[file];
  require(file);
  await settle();
}

async function settle() {
  for (let i = 0; i < 10; i++) await new Promise((done) => setImmediate(done));
}

module.exports = { page, run, settle, Element };
