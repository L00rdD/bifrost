// The dashboard: paints /api/status.json every 5 s (every second while something moves) and sends
// the switches, the cover's and the projector's orders as POSTs to the hub.
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const ago = (iso) => {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  return s < 45 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : s < 86400 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " d ago";
};
const BULB = `<svg viewBox="0 0 48 48" aria-hidden="true">
      <g class="rays"><line x1="24" y1="-4" x2="24" y2="-9"/><line x1="44" y1="20" x2="49" y2="20"/><line x1="4" y1="20" x2="-1" y2="20"/>
        <line x1="38" y1="5" x2="42" y2="1"/><line x1="10" y1="5" x2="6" y2="1"/><line x1="38" y1="35" x2="42" y2="39"/><line x1="10" y1="35" x2="6" y2="39"/></g>
      <path class="glass" d="M24 4 C14 4 8 11 8 19 C8 26 13 29 16 33 L32 33 C35 29 40 26 40 19 C40 11 34 4 24 4 Z"/>
      <rect class="base" x="17" y="36" width="14" height="4" rx="2"/><rect class="base" x="19" y="42" width="10" height="4" rx="2"/></svg>`;

let status = null, lastCodeKey = null;
const pending = new Map();  // outlet id -> { want, since, timer }

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 4000);
}

function card(o) {
  let c = document.querySelector(`.outlet[data-id="${CSS.escape(o.id)}"]`);
  if (!c) {
    c = el("article", "outlet panel t-panel");
    c.dataset.id = o.id;
    const bulb = el("div", "bulb");
    bulb.append(el("span", "ring"), el("span", "ring"), el("span", "ring"));
    bulb.insertAdjacentHTML("beforeend", BULB);
    const info = el("div");
    info.append(el("h2"), el("p", "when"));
    const sw = el("button", "switch");
    sw.setAttribute("role", "switch");
    sw.append(el("span", "knob"));
    sw.addEventListener("click", () => toggle(o.id));
    c.append(bulb, info, sw);
    $("#outlets").append(c);
  }
  return c;
}

function paintOutlet(o) {
  const c = card(o), p = pending.get(o.id);
  const on = p ? p.want : o.on;
  c.classList.toggle("on", on === true);
  c.classList.toggle("pending", !!p);
  $("h2", c).textContent = o.name;
  $(".when", c).textContent = p ? (p.want ? "switching on…" : "switching off…")
    : o.on === null ? "state unknown, press to switch" : `${o.on ? "On" : "Off"}${o.at ? " · " + ago(o.at) : ""}`;
  const sw = $(".switch", c);
  sw.setAttribute("aria-checked", on === null ? "mixed" : String(on));
  sw.setAttribute("aria-label", `${o.name}: ${on ? "on" : "off"}`);
}

const BEAMER = `<svg viewBox="0 0 150 96" aria-hidden="true">
      <defs><linearGradient id="cone" x1="0" x2="1"><stop offset="0" stop-color="#e6ecff" stop-opacity="0.75"/>
        <stop offset="0.5" stop-color="#6fe3ff" stop-opacity="0.35"/><stop offset="1" stop-color="#9b8cff" stop-opacity="0"/></linearGradient></defs>
      <path class="cone" d="M52 52 L150 8 L150 92 Z" fill="url(#cone)"/>
      <rect class="body" x="4" y="34" width="58" height="40" rx="12"/>
      <circle class="lens" cx="46" cy="54" r="11"/><circle class="led" cx="16" cy="64" r="3"/>
      <rect x="10" y="74" width="8" height="5" rx="2" fill="#24304f"/><rect x="48" y="74" width="8" height="5" rx="2" fill="#24304f"/></svg>`;
const REMOTE = [["🔉", "VOLUME_DOWN"], ["▲", "DPAD_UP"], ["🔊", "VOLUME_UP"], ["◀", "DPAD_LEFT"], ["OK", "DPAD_CENTER", "ok"], ["▶", "DPAD_RIGHT"],
                ["↩", "BACK"], ["▼", "DPAD_DOWN"], ["⌂", "HOME"], ["🔇", "VOLUME_MUTE"], ["⏯", "MEDIA_PLAY_PAUSE"], ["⚙", "SETTINGS"]];
const projectorPending = new Map();  // projector id -> { want, until }

function projectorCard(p) {
  let k = document.querySelector(`.projector[data-id="${CSS.escape(p.id)}"]`);
  if (!k) {
    k = el("article", "projector panel t-panel");
    k.dataset.id = p.id;
    const beamer = el("div", "beamer");
    beamer.innerHTML = BEAMER;
    const info = el("div"), top = el("div", "top"), head = el("div");
    head.append(el("h2"), el("p", "when"));
    const sw = el("button", "switch");
    sw.setAttribute("role", "switch");
    sw.append(el("span", "knob"));
    sw.addEventListener("click", () => {
      const cur = status.projectors.find((x) => x.id === p.id);
      const want = !(projectorPending.get(p.id)?.want ?? cur.on);
      projectorPending.set(p.id, { want, until: Date.now() + 20000 });
      paintProjector(cur);
      projectorSend(p.id, { on: want });
    });
    top.append(head, sw);
    const apps = el("div", "apps");
    for (const a of p.apps) {
      const b = el("button", "", a.name);
      b.dataset.package = a.package;
      b.addEventListener("click", () => projectorSend(p.id, { app: a.package }));
      apps.append(b);
    }
    const screenBtn = el("button", "screen-btn", "📺 Screen");
    screenBtn.setAttribute("aria-pressed", "false");
    screenBtn.addEventListener("click", () => toggleMirror(k, p.id));
    apps.append(screenBtn);
    info.append(top, apps);
    const remote = el("div", "remote");
    for (const [label, key, cls] of REMOTE) {
      if (!key) { remote.append(el("span")); continue; }
      const b = el("button", cls || "", label);
      b.title = key.toLowerCase().replace(/_/g, " ");
      b.addEventListener("click", () => projectorSend(p.id, { key }));
      remote.append(b);
    }
    k.append(beamer, info, remote);
    $("#projectors").append(k);
  }
  return k;
}

function paintProjector(p) {
  const k = projectorCard(p), pend = projectorPending.get(p.id);
  if (pend && (p.on === pend.want || Date.now() > pend.until)) projectorPending.delete(p.id);
  const want = projectorPending.get(p.id)?.want;
  const on = want ?? p.on;
  k.classList.toggle("on", on === true);
  k.classList.toggle("away", p.on === null);
  $("h2", k).textContent = p.name;
  $(".when", k).textContent = want !== undefined ? (want ? "turning on…" : "turning off…") : projectorState(p);
  const sw = $(".switch", k);
  sw.setAttribute("aria-checked", on === null ? "mixed" : String(on));
  sw.setAttribute("aria-label", `${p.name}: ${on ? "on" : "off"}`);
  for (const b of k.querySelectorAll(".apps button")) b.classList.toggle("front", p.on === true && b.dataset.package === p.app);
}

// What the projector does, as the hub last saw it
function projectorState(p) {
  if (p.on === null) return `unreachable${p.error ? " · " + p.error : ""}`;
  if (p.on) return `On${projectorApp(p)}${p.cover ? " · the screen follows" : ""}`;
  return p.closes_at ? `Off · the screen goes up at ${new Date(p.closes_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Off";
}

// " · " and the app in front, under its name when the dashboard knows it
function projectorApp(p) {
  const app = p.apps.find((a) => a.package === p.app) || (p.app === "screensaver" || p.app === "home" ? { name: p.app } : null);
  return app ? " · " + app.name : p.app ? " · " + p.app : "";
}

// The live screen: an MJPEG stream the hub runs only while someone watches. Hidden, closed or
// in a background tab, the <img> lets go of it and the hub stops it a little later.
function toggleMirror(k, id, open = !k.querySelector(".mirror")) {
  k.querySelector(".mirror")?.remove();
  $(".screen-btn", k).setAttribute("aria-pressed", String(open));
  if (!open) return;
  const fig = el("figure", "mirror"), img = el("img"), cap = el("figcaption", "", "connecting…");
  img.alt = "the projector's screen";
  // A stream may never fire load: look at the picture itself. All black is a protected app (DRM).
  const probe = document.createElement("canvas");
  probe.width = 16; probe.height = 9;
  const look = setInterval(() => {
    if (!fig.isConnected) return clearInterval(look);
    if (!img.naturalWidth) return;
    const ctx = probe.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, 16, 9);
    const black = ctx.getImageData(0, 0, 16, 9).data.every((v, i) => i % 4 === 3 || v < 12);
    if (black) { cap.textContent = "black screen: the app in front hides its picture (Netflix, Prime Video…)"; fig.append(cap); }
    else cap.remove();
  }, 1000);
  img.addEventListener("error", () => { clearInterval(look); cap.textContent = "no picture: the projector is off or unreachable"; fig.append(cap); });
  img.addEventListener("click", (e) => {
    // the picture is letterboxed in the 16:9 box: the tap is relative to the picture itself
    const r = img.getBoundingClientRect(), w = img.naturalWidth || 16, h = img.naturalHeight || 9;
    const scale = Math.min(r.width / w, r.height / h), pw = w * scale, ph = h * scale;
    const x = (e.clientX - r.left - (r.width - pw) / 2) / pw, y = (e.clientY - r.top - (r.height - ph) / 2) / ph;
    if (x >= 0 && x <= 1 && y >= 0 && y <= 1) projectorSend(id, { tap: [x, y] });
  });
  img.src = `/api/projectors/${encodeURIComponent(id)}/screen.mjpg?t=${Date.now()}`;
  fig.append(img, cap);
  k.append(fig);
}
document.addEventListener("visibilitychange", () => {
  for (const k of document.querySelectorAll(".projector")) {
    if (document.hidden && k.querySelector(".mirror")) { k.dataset.mirror = "1"; toggleMirror(k, k.dataset.id, false); }
    else if (!document.hidden && k.dataset.mirror) { delete k.dataset.mirror; toggleMirror(k, k.dataset.id, true); }
  }
});

async function projectorSend(id, body) {
  sendPacket(true);
  try {
    const r = await fetch(`/api/projectors/${encodeURIComponent(id)}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  } catch (e) {
    projectorPending.delete(id);
    const k = document.querySelector(`.projector[data-id="${CSS.escape(id)}"]`);
    if (k) flash(k, "failed");
    return toast(`${id}: ${e.message}`);
  }
  for (const ms of [1500, 3500, 6000]) setTimeout(load, ms);  // adb takes a moment, the state follows
}

function coverCard(c) {
  let k = document.querySelector(`.cover[data-id="${CSS.escape(c.id)}"]`);
  if (!k) {
    k = el("article", "cover panel t-panel");
    k.dataset.id = c.id;
    const screen = el("div", "screen");
    screen.append(el("div", "case"), el("div", "cloth"), el("div", "mark"));
    const info = el("div");
    const controls = el("div", "controls");
    for (const [label, body, cls, title] of [["▲ Close", { position: 0 }, "", "roll up"], ["■", { stop: true }, "stop", "stop"],
                                              ["▼ Open", { position: 100 }, "", "down to the projection height"]]) {
      const b = el("button", cls, label);
      b.title = title;
      b.addEventListener("click", () => moveCover(c.id, body));
      controls.append(b);
    }
    const manual = el("div", "controls manual");
    manual.append(el("span", "", "Manual"));
    for (const [label, body, cls, title] of [["▲", { press: "up" }, "", "up, as on the remote, whatever the drawing says: ■ stops it, the hub does after close_time at the latest"],
                                              ["▼", { press: "down" }, "", "down, as on the remote, whatever the drawing says: ■ stops it, the hub does after open_time at the latest"],
                                              ["= rolled up", { declare: 0 }, "declare", "the screen is really rolled up at the top: the hub takes 0 %, nothing moves"],
                                              ["= open", { declare: 100 }, "declare", "the screen is really open at the projection height: the hub takes 100 %, nothing moves"]]) {
      const b = el("button", cls, label);
      b.title = title;
      b.addEventListener("click", () => moveCover(c.id, body));
      manual.append(b);
    }
    const range = el("input");
    Object.assign(range, { type: "range", min: 0, max: 100, step: 5 });
    range.setAttribute("aria-label", `${c.name}: position`);
    range.addEventListener("pointerdown", () => (range.dragging = true));
    range.addEventListener("change", () => { range.dragging = false; moveCover(c.id, { position: Number(range.value) }); });
    info.append(el("h2"), el("p", "when"), controls, range, manual);
    k.append(screen, info);
    $("#covers").append(k);
  }
  return k;
}

function paintCover(c) {
  const k = coverCard(c), screen = $(".screen", k), range = $("input", k);
  k.classList.toggle("moving", !!c.moving);
  k.classList.toggle("unknown", c.position === null);
  // unknown: drawn half down, hatched; the first move goes all the way up first
  screen.style.setProperty("--p", c.position ?? 50);
  screen.style.setProperty("--t", c.target ?? 0);
  $("h2", k).textContent = c.name;
  const where = (p) => (p === 0 ? "rolled up" : p === 100 ? "open" : `${p} %`);
  $(".when", k).textContent = c.position === null ? (c.moving === "up" ? "all the way up, to start again from the top"
      : c.moving ? "moving down, position unknown" : "position unknown")
    : c.moving ? `${c.moving === "down" ? "opening" : "closing"}… ${c.position} % → ${c.target ?? (c.moving === "down" ? 100 : 0)} %`
    : where(c.position);
  if (!range.dragging && document.activeElement !== range) range.value = c.target ?? c.position ?? 0;
}

async function moveCover(id, body) {
  sendPacket(true);
  try {
    const r = await fetch(`/api/covers/${encodeURIComponent(id)}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  } catch (e) {
    const k = document.querySelector(`.cover[data-id="${CSS.escape(id)}"]`);
    if (k) flash(k, "failed");
    return toast(`${id}: ${e.message}`);
  }
  setTimeout(load, 300);
}

function flash(c, cls) {
  c.classList.remove(cls);
  void c.offsetWidth;  // restart the animation
  c.classList.add(cls);
  setTimeout(() => c.classList.remove(cls), 900);
}

// A packet of light runs along the chain: towards the air for a code sent, back for one heard
function sendPacket(outward) {
  const chain = $("#chain"), nodes = [...chain.querySelectorAll(".node")];
  const x = (n) => n.offsetLeft + n.offsetWidth / 2;
  const [from, to] = outward ? [x(nodes[1]), x(nodes[3])] : [x(nodes[3]), x(nodes[1])];
  const dot = el("span", "packet");
  chain.append(dot);
  dot.animate([{ left: from + "px", opacity: 0 }, { opacity: 1, offset: 0.15 }, { opacity: 1, offset: 0.85 }, { left: to + "px", opacity: 0 }],
              { duration: 1100, easing: "ease-in-out" }).finished.then(() => dot.remove());
}

function paint(s) {
  status = s;
  $("#updated").textContent = "433 MHz ⇄ MQTT ⇄ HomeKit · updated " + new Date(s.updated_at).toLocaleTimeString();
  paintChain(s);
  paintOutlets(s.outlets);
  paintCinema(s.covers || [], s.projectors || []);
  paintLastCode(s.last_code);
}

function paintChain(s) {
  const ups = { homebridge: s.homebridge, broker: s.broker, bridge: s.bridge.online, air: s.bridge.online };
  for (const [n, up] of Object.entries(ups)) $(`[data-n="${n}"]`).classList.toggle("up", up);
  const links = [s.homebridge && s.broker, s.broker && s.bridge.online, s.bridge.online];
  links.forEach((up, i) => $(`[data-l="${i}"]`).classList.toggle("up", up));
}

function paintOutlets(outlets) {
  if (!outlets.length) $("#outlets").replaceChildren(el("p", "empty", "No outlet yet: add one to outlets.toml."));
  for (const o of outlets) {
    const p = pending.get(o.id);
    if (p && o.on === p.want && o.at && new Date(o.at) >= p.since - 1000) {
      clearTimeout(p.timer);
      pending.delete(o.id);
      flash(card(o), "burst");
    }
    paintOutlet(o);
  }
}

function paintCinema(covers, projectors) {
  $("#covers").hidden = !covers.length;
  $("#projectors").hidden = !projectors.length;
  $("#cinema-label").hidden = !covers.length && !projectors.length;
  projectors.forEach(paintProjector);
  covers.forEach(paintCover);
}

function paintLastCode(lc) {
  const last = $("#last");
  const key = lc ? lc.code + lc.at : null;
  if (lc) last.replaceChildren("last code ", el("b", "", lc.code), ` ${lc.via === "sent" ? "sent" : "heard from a remote"} · ${ago(lc.at)}`);
  if (key && lastCodeKey !== null && key !== lastCodeKey) {
    sendPacket(lc.via === "sent");
    flash(last, "fresh");
  }
  lastCodeKey = key;
}

async function load() {
  clearTimeout(load.next);
  try {
    const r = await fetch("/api/status.json", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    paint(await r.json());
  } catch {
    $("#updated").textContent = "the hub does not answer, retrying…";
  }
  // every second while a cover moves, so its drawing follows it
  load.next = setTimeout(load, status && ((status.covers || []).some((c) => c.moving) || projectorPending.size) ? 1000 : 5000);
}

async function toggle(id) {
  if (pending.has(id) || !status) return;
  const o = status.outlets.find((x) => x.id === id);
  const want = o.on !== true;
  const p = { want, since: Date.now() };
  pending.set(id, p);
  paintOutlet(o);
  sendPacket(true);
  try {
    const r = await fetch(`/api/outlets/${encodeURIComponent(id)}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: want }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  } catch (e) {
    return fail(o, `${o.name}: ${e.message}`);
  }
  // The outlet does not answer: the state comes back from the RF bridge, which echoes every
  // code it sends. Poll quickly until it does, give up after 6 s.
  p.timer = setTimeout(() => fail(o, `${o.name}: no answer from the RF bridge`), 6000);
  for (let i = 0; i < 10 && pending.get(id) === p; i++) {
    await new Promise((r) => setTimeout(r, 600));
    await load();
  }
}

function fail(o, message) {
  const p = pending.get(o.id);
  if (p) clearTimeout(p.timer);
  pending.delete(o.id);
  paintOutlet(status.outlets.find((x) => x.id === o.id) || o);
  flash(card(o), "failed");
  toast(message);
}

load();
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
