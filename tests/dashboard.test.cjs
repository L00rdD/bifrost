// Tests of the dashboard's script (dashboard/bifrost.js) on a fake page:
//   node --test tests/
const test = require("node:test");
const assert = require("node:assert");
const { page, run, settle } = require("./dashboard-page.cjs");

const NOW = Date.parse("2026-11-04T20:00:00Z");
const ago = (s) => new Date(NOW - s * 1000).toISOString();

const status = (more = {}) => ({
  updated_at: ago(0), broker: true, homebridge: true, bridge: { online: true, last_seen: ago(3) },
  outlets: [{ id: "terrarium", name: "Terrarium", on: true, at: ago(300) },
            { id: "fan", name: "Fan", on: false, at: null },
            { id: "heater", name: "Heater", on: null, at: null }],
  covers: [], projectors: [], last_code: null, ...more,
});

const projector = (more = {}) => ({
  id: "projector", name: "Projector", cover: "screen", on: true, app: "com.netflix.ninja", error: null,
  closes_at: null, apps: [{ name: "Home", package: "home" }, { name: "Netflix", package: "com.netflix.ninja" }], ...more,
});

const cover = (more = {}) => ({ id: "screen", name: "Screen", position: 0, moving: null, target: 0, ...more });

// Each test: its own page, the script run afresh on it, the clock and timers in hand
async function open(t, s = status()) {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"], now: NOW });
  const p = page(s);
  await run();
  p.tick = async (ms) => { t.mock.timers.tick(ms); await settle(); };
  p.card = (kind, id) => p.$(`.${kind}[data-id="${id}"]`);
  return p;
}

test("the signal path, the outlets and the time of the update", async (t) => {
  const p = await open(t);
  assert.match(p.text("#updated"), /^433 MHz ⇄ MQTT ⇄ HomeKit · updated /);
  for (const n of ["homebridge", "broker", "bridge", "air"]) assert.ok(p.$(`[data-n="${n}"]`).classList.contains("up"), n);
  for (const l of [0, 1, 2]) assert.ok(p.$(`[data-l="${l}"]`).classList.contains("up"));
  const [lamp, fan, heater] = p.$("#outlets").querySelectorAll(".outlet");
  assert.deepEqual([lamp, fan, heater].map((c) => c.dataset.id), ["terrarium", "fan", "heater"]);
  assert.equal(p.text("h2", lamp), "Terrarium");
  assert.equal(p.text(".when", lamp), "On · 5 min ago");
  assert.ok(lamp.classList.contains("on"));
  assert.equal(p.$(".switch", lamp).getAttribute("aria-checked"), "true");
  assert.equal(p.$(".switch", lamp).getAttribute("aria-label"), "Terrarium: on");
  assert.match(p.$(".bulb", lamp).innerHTML, /class="glass"/);
  assert.equal(p.text(".when", fan), "Off");
  assert.equal(p.$(".switch", fan).getAttribute("aria-checked"), "false");
  assert.equal(p.text(".when", heater), "state unknown, press to switch");
  assert.equal(p.$(".switch", heater).getAttribute("aria-checked"), "mixed");
  assert.equal(p.$(".switch", heater).getAttribute("aria-label"), "Heater: off");
  assert.equal(p.text("#last"), "no RF code yet");
  assert.ok(p.$("#covers").hidden && p.$("#projectors").hidden && p.$("#cinema-label").hidden);
});

test("all down, no outlet", async (t) => {
  const p = await open(t, status({ broker: false, homebridge: false, bridge: { online: false, last_seen: null }, outlets: [],
                                   covers: undefined, projectors: undefined }));
  for (const n of ["homebridge", "broker", "bridge", "air"]) assert.ok(!p.$(`[data-n="${n}"]`).classList.contains("up"));
  for (const l of [0, 1, 2]) assert.ok(!p.$(`[data-l="${l}"]`).classList.contains("up"));
  assert.equal(p.text("#outlets"), "No outlet yet: add one to outlets.toml.");
});

test("a link is up only when both of its ends are", async (t) => {
  const p = await open(t, status({ homebridge: false, bridge: { online: false } }));
  assert.deepEqual([0, 1, 2].map((l) => p.$(`[data-l="${l}"]`).classList.contains("up")), [false, false, false]);
  p.status = status({ homebridge: true, broker: true, bridge: { online: false } });
  await p.tick(5000);
  assert.deepEqual([0, 1, 2].map((l) => p.$(`[data-l="${l}"]`).classList.contains("up")), [true, false, false]);
});

test("how long ago", async (t) => {
  const p = await open(t, status({ outlets: [10, 90, 7200, 3 * 86400, -60].map((s, i) => ({ id: `o${i}`, name: `O${i}`, on: true, at: ago(s) })) }));
  assert.deepEqual(p.$("#outlets").querySelectorAll(".when").map((w) => w.textContent),
                   ["On · just now", "On · 2 min ago", "On · 2 h ago", "On · 3 d ago", "On · just now"]);
});

test("the last code, and a packet of light along the chain when a new one comes", async (t) => {
  const p = await open(t, status({ last_code: { code: "1111", via: "sent", at: ago(0) } }));
  assert.equal(p.text("#last"), "last code 1111 sent · just now");
  assert.equal(p.$("#chain").querySelectorAll(".packet").length, 0);  // the first one is no news
  await p.tick(5000);
  assert.ok(!p.$("#last").classList.contains("fresh"));
  p.status = status({ last_code: { code: "2222", via: "remote", at: ago(0) } });
  await p.tick(5000);
  assert.equal(p.text("#last"), "last code 2222 heard from a remote · just now");
  assert.ok(p.$("#last").classList.contains("fresh"));
  assert.equal(p.$("#chain").querySelectorAll(".packet").length, 0);  // gone once its animation finished
  await p.tick(900);
  assert.ok(!p.$("#last").classList.contains("fresh"));
});

test("the packet runs from the bridge to the broker for a code heard, the other way for one sent", async (t) => {
  const p = await open(t, status({ last_code: { code: "1", via: "sent", at: ago(0) } }));
  const made = [];
  const create = p.document.createElement;
  p.document.createElement = (tag) => { const e = create(tag); made.push(e); return e; };
  p.status = status({ last_code: { code: "2", via: "remote", at: ago(0) } });
  await p.tick(5000);
  p.status = status({ last_code: { code: "3", via: "sent", at: ago(0) } });
  await p.tick(5000);
  const packets = made.filter((e) => e.classList.contains("packet"));
  assert.deepEqual(packets.map((e) => [e.animations[0].frames[0].left, e.animations[0].frames[3].left]),
                   [["320px", "120px"], ["120px", "320px"]]);
  assert.deepEqual(packets[0].animations[0].options, { duration: 1100, easing: "ease-in-out" });
});

test("polls every 5 s, every second while something moves, and says when the hub does not answer", async (t) => {
  const p = await open(t);
  assert.equal(p.polls(), 1);
  await p.tick(4999);
  assert.equal(p.polls(), 1);
  await p.tick(1);
  assert.equal(p.polls(), 2);
  p.status = status({ covers: [cover({ moving: "down", target: 100 })] });
  await p.tick(5000);
  await p.tick(1000);
  assert.equal(p.polls(), 4);
  p.status = 502;
  await p.tick(1000);
  assert.equal(p.text("#updated"), "the hub does not answer, retrying…");
  await p.tick(1000);  // the last status still says it moves
  assert.equal(p.polls(), 6);
  p.answer = () => new Error("offline");
  await p.tick(1000);
  assert.equal(p.text("#updated"), "the hub does not answer, retrying…");
});

test("the hub down from the start: every 5 s", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"], now: NOW });
  const p = page(503);
  await run();
  assert.equal(p.text("#updated"), "the hub does not answer, retrying…");
  t.mock.timers.tick(1000);
  await settle();
  assert.equal(p.polls(), 1);
  t.mock.timers.tick(4000);
  await settle();
  assert.equal(p.polls(), 2);
});

test("back to the page: a poll at once", async (t) => {
  const p = await open(t);
  p.document.hidden = true;
  await p.fire("visibilitychange");
  assert.equal(p.polls(), 1);
  p.document.hidden = false;
  await p.fire("visibilitychange");
  await settle();
  assert.equal(p.polls(), 2);
});

test("a switch sends the order, waits for the bridge's echo, and lights the bulb", async (t) => {
  const p = await open(t);
  const fan = p.card("outlet", "fan");
  p.$(".switch", fan).click();
  await settle();
  assert.deepEqual(p.posted(), [["/api/outlets/fan", { on: true }]]);
  assert.ok(fan.classList.contains("pending") && fan.classList.contains("on"));
  assert.equal(p.text(".when", fan), "switching on…");
  assert.equal(p.$("#chain").children.filter((c) => c.classList?.contains("packet")).length, 0);
  p.$(".switch", fan).click();  // pressed again while it switches: nothing more
  await settle();
  assert.equal(p.posted().length, 1);
  await p.tick(600);
  assert.ok(fan.classList.contains("pending"));  // no echo yet
  p.status.outlets[1] = { id: "fan", name: "Fan", on: true, at: new Date(Date.now()).toISOString() };
  await p.tick(600);
  assert.ok(!fan.classList.contains("pending") && fan.classList.contains("on"));
  assert.ok(fan.classList.contains("burst"));
  assert.equal(p.text(".when", fan), "On · just now");
  await p.tick(6000);
  assert.equal(p.text("#toast"), "");  // the echo came: no failure later
});

test("switching off shows it at once", async (t) => {
  const p = await open(t);
  const lamp = p.card("outlet", "terrarium");
  p.$(".switch", lamp).click();
  await settle();
  assert.deepEqual(p.posted(), [["/api/outlets/terrarium", { on: false }]]);
  assert.equal(p.text(".when", lamp), "switching off…");
  assert.ok(!lamp.classList.contains("on"));
  assert.equal(p.$(".switch", lamp).getAttribute("aria-label"), "Terrarium: off");
});

test("an unknown outlet switches on", async (t) => {
  const p = await open(t);
  p.$(".switch", p.card("outlet", "heater")).click();
  await settle();
  assert.deepEqual(p.posted(), [["/api/outlets/heater", { on: true }]]);
});

test("no echo within 6 s: the switch flips back and says so", async (t) => {
  const p = await open(t);
  const fan = p.card("outlet", "fan");
  p.$(".switch", fan).click();
  await settle();
  for (let i = 0; i < 10; i++) await p.tick(600);
  await p.tick(1);
  assert.equal(p.text("#toast"), "Fan: no answer from the RF bridge");
  assert.ok(p.$("#toast").classList.contains("show"));
  assert.ok(!fan.classList.contains("pending") && !fan.classList.contains("on"));
  assert.ok(fan.classList.contains("failed"));
  await p.tick(900);
  assert.ok(!fan.classList.contains("failed"));
  await p.tick(4000);
  assert.ok(!p.$("#toast").classList.contains("show"));
});

test("the hub refuses the switch: its error, or its status", async (t) => {
  const p = await open(t);
  p.posts["/api/outlets/fan"] = { status: 503, body: { error: "the broker is not connected" } };
  p.$(".switch", p.card("outlet", "fan")).click();
  await settle();
  assert.equal(p.text("#toast"), "Fan: the broker is not connected");
  assert.ok(!p.card("outlet", "fan").classList.contains("pending"));
  p.posts["/api/outlets/fan"] = 500;
  p.$(".switch", p.card("outlet", "fan")).click();
  await settle();
  assert.equal(p.text("#toast"), "Fan: 500");
});

test("an outlet gone from the status when the switch fails keeps its card", async (t) => {
  const p = await open(t);
  p.posts["/api/outlets/fan"] = 500;
  p.status.outlets = p.status.outlets.filter((o) => o.id !== "fan");
  p.$(".switch", p.card("outlet", "fan")).click();
  await settle();
  assert.equal(p.text("#toast"), "Fan: 500");
  assert.equal(p.text(".when", p.card("outlet", "fan")), "Off");
});

test("the projector's card: its apps, its screen button and its remote", async (t) => {
  const p = await open(t, status({ projectors: [projector()], covers: [cover()] }));
  const k = p.card("projector", "projector");
  assert.ok(!p.$("#projectors").hidden && !p.$("#covers").hidden && !p.$("#cinema-label").hidden);
  assert.match(p.$(".beamer", k).innerHTML, /class="lens"/);
  assert.deepEqual(k.querySelectorAll(".apps button").map((b) => [b.textContent, b.dataset.package, b.className]),
                   [["Home", "home", ""], ["Netflix", "com.netflix.ninja", "front"], ["📺 Screen", undefined, "screen-btn"]]);
  const remote = k.querySelectorAll(".remote button");
  assert.deepEqual(remote.map((b) => b.textContent),
                   ["🔉", "▲", "🔊", "◀", "OK", "▶", "↩", "▼", "⌂", "🔇", "⏯", "⚙"]);
  assert.equal(remote[4].className, "ok");
  assert.equal(remote[10].title, "media play pause");
  assert.equal(p.text(".when", k), "On · Netflix · the screen follows");
  assert.ok(k.classList.contains("on") && !k.classList.contains("away"));
  remote[1].click();
  k.querySelectorAll(".apps button")[0].click();
  await settle();
  assert.deepEqual(p.posted(), [["/api/projectors/projector", { key: "DPAD_UP" }], ["/api/projectors/projector", { app: "home" }]]);
  const polls = p.polls();
  await p.tick(1500);
  assert.equal(p.polls(), polls + 2);  // one for each order
});

test("the projector in every state", async (t) => {
  const closes = new Date(NOW + 300000).toISOString();
  const states = [
    [{ on: null, error: "adb: offline" }, "unreachable · adb: offline"],
    [{ on: null }, "unreachable"],
    [{ app: "home" }, "On · Home · the screen follows"],
    [{ app: "home", apps: [] }, "On · home · the screen follows"],
    [{ app: "screensaver", cover: null }, "On · screensaver"],
    [{ app: "org.other.app" }, "On · org.other.app · the screen follows"],
    [{ app: null, cover: null }, "On"],
    [{ on: false, app: null }, "Off"],
  ];
  const p = await open(t, status({ projectors: [projector()] }));
  const k = p.card("projector", "projector");
  for (const [more, said] of states) {
    p.status = status({ projectors: [projector(more)] });
    await p.tick(5000);
    assert.equal(p.text(".when", k), said);
    assert.equal(k.classList.contains("away"), more.on === null);
    assert.equal(p.$(".switch", k).getAttribute("aria-checked"), more.on === null ? "mixed" : String(more.on ?? true));
  }
  p.status = status({ projectors: [projector({ on: false, app: null, closes_at: closes })] });
  await p.tick(5000);
  assert.match(p.text(".when", k), /^Off · the screen goes up at \d/);
  assert.deepEqual(k.querySelectorAll(".apps button").map((b) => b.classList.contains("front")), [false, false, false]);
});

test("the projector's switch: turning on until it is, or for 20 s at most", async (t) => {
  const p = await open(t, status({ projectors: [projector({ on: false, app: null })] }));
  const k = p.card("projector", "projector");
  p.$(".switch", k).click();
  await settle();
  assert.deepEqual(p.posted(), [["/api/projectors/projector", { on: true }]]);
  assert.equal(p.text(".when", k), "turning on…");
  assert.ok(k.classList.contains("on"));
  p.$(".switch", k).click();  // changed my mind: it is off already, nothing to wait for
  await settle();
  assert.deepEqual(p.posted()[1], ["/api/projectors/projector", { on: false }]);
  assert.equal(p.text(".when", k), "Off");
  p.$(".switch", k).click();
  await settle();
  await p.tick(1500);  // a look once adb had the time, then every second while it is pending
  assert.equal(p.text(".when", k), "turning on…");
  p.status = status({ projectors: [projector()] });
  await p.tick(1000);
  assert.equal(p.text(".when", k), "On · Netflix · the screen follows");
  p.$(".switch", k).click();  // off, and it never turns off
  await settle();
  assert.equal(p.text(".when", k), "turning off…");
  await p.tick(19000);
  assert.equal(p.text(".when", k), "turning off…");
  await p.tick(1001);
  assert.equal(p.text(".when", k), "On · Netflix · the screen follows");
});

test("the projector refuses an order: the card says it failed", async (t) => {
  const p = await open(t, status({ projectors: [projector({ on: false, app: null })] }));
  const k = p.card("projector", "projector");
  p.posts["/api/projectors/projector"] = { status: 400, body: { error: "expected {\"on\": true}" } };
  p.$(".switch", k).click();
  await settle();
  assert.equal(p.text("#toast"), 'projector: expected {"on": true}');
  assert.ok(k.classList.contains("failed"));
  await p.tick(5000);
  assert.equal(p.text(".when", k), "Off");  // no longer pending
  p.posts["/api/projectors/projector"] = 502;
  k.querySelectorAll(".remote button")[0].click();
  await settle();
  assert.equal(p.text("#toast"), "projector: 502");
  p.status = status({ projectors: [] });  // gone meanwhile
  await p.tick(5000);
  p.root.querySelector("#projectors").replaceChildren();
  p.answer = (url, options) => (options.method === "POST" ? new Error("network down") : p.status);
  await p.tick(5000);
});

test("the live screen: opens, closes, and says why there is no picture", async (t) => {
  const p = await open(t, status({ projectors: [projector()] }));
  const k = p.card("projector", "projector"), btn = p.$(".screen-btn", k);
  btn.click();
  const fig = p.$(".mirror", k), img = p.$("img", fig);
  assert.equal(btn.getAttribute("aria-pressed"), "true");
  assert.equal(img.src, `/api/projectors/projector/screen.mjpg?t=${NOW}`);
  assert.equal(img.alt, "the projector's screen");
  assert.equal(p.text("figcaption", fig), "connecting…");
  await p.tick(1000);
  assert.equal(p.text("figcaption", fig), "connecting…");  // no picture yet
  img.naturalWidth = 1280;
  img.naturalHeight = 720;
  p.pixels = Array.from({ length: 16 * 9 * 4 }, (_, i) => (i % 4 === 3 ? 255 : 3));
  await p.tick(1000);
  assert.equal(p.text("figcaption", fig), "black screen: the app in front hides its picture (Netflix, Prime Video…)");
  p.pixels[20] = 200;
  await p.tick(1000);
  assert.equal(p.$("figcaption", fig), null);
  img.fire("error");
  assert.equal(p.text("figcaption", fig), "no picture: the projector is off or unreachable");
  btn.click();
  assert.equal(p.$(".mirror", k), null);
  assert.equal(btn.getAttribute("aria-pressed"), "false");
  await p.tick(3000);  // its probe stopped with it
});

test("the probe of a closed screen stops", async (t) => {
  const p = await open(t, status({ projectors: [projector()] }));
  const k = p.card("projector", "projector"), btn = p.$(".screen-btn", k);
  btn.click();
  const img = p.$(".mirror img", k);
  btn.click();
  img.naturalWidth = 1280;
  p.pixels = Array.from({ length: 16 * 9 * 4 }, () => 0);
  await p.tick(1000);
  assert.equal(p.$(".mirror", k), null);
});

test("a tap on the live screen lands where it was on the picture", async (t) => {
  const p = await open(t, status({ projectors: [projector()] }));
  const k = p.card("projector", "projector");
  p.$(".screen-btn", k).click();
  const img = p.$(".mirror img", k);
  img.rect = { left: 10, top: 20, width: 1600, height: 900 };
  img.click();  // no picture yet: taken as 16:9
  img.fire("click", { clientX: 10 + 800, clientY: 20 + 450 });
  img.naturalWidth = 900;
  img.naturalHeight = 900;  // a square picture letterboxed in the 16:9 box: 350 px each side
  img.fire("click", { clientX: 10 + 350, clientY: 20 + 900 });
  img.fire("click", { clientX: 10 + 349, clientY: 20 + 450 });  // on the black bar
  img.fire("click", { clientX: 10 + 1250, clientY: 20 + 901 });
  await settle();
  assert.deepEqual(p.posted().map(([, b]) => b), [{ tap: [0.5, 0.5] }, { tap: [0, 1] }]);
});

test("the live screen lets go while the page is hidden, and comes back with it", async (t) => {
  const p = await open(t, status({ projectors: [projector(), projector({ id: "other", name: "Other" })] }));
  const k = p.card("projector", "projector"), other = p.card("projector", "other");
  p.$(".screen-btn", k).click();
  p.document.hidden = true;
  await p.fire("visibilitychange");
  assert.equal(p.$(".mirror", k), null);
  assert.equal(k.dataset.mirror, "1");
  assert.equal(other.dataset.mirror, undefined);
  p.document.hidden = false;
  await p.fire("visibilitychange");
  assert.ok(p.$(".mirror", k));
  assert.equal(k.dataset.mirror, undefined);
  assert.equal(p.$(".mirror", other), null);
  assert.equal(p.$(".screen-btn", k).getAttribute("aria-pressed"), "true");
});

test("the screen's card: its buttons and its slider send the orders", async (t) => {
  const p = await open(t, status({ covers: [cover()] }));
  const k = p.card("cover", "screen");
  const buttons = k.querySelectorAll("button");
  assert.deepEqual(buttons.map((b) => [b.textContent, b.className]),
                   [["▲ Close", ""], ["■", "stop"], ["▼ Open", ""], ["▲", "hold"], ["▼", "hold"], ["= rolled up", "declare"], ["= open", "declare"]]);
  assert.equal(buttons[2].title, "down to the projection height");
  assert.equal(p.text(".manual span", k), "Manual");
  for (const b of buttons) b.click();  // a click on ▲ or ▼ is not a hold: nothing
  const [up, down] = [buttons[3], buttons[4]];
  up.fire("pointerdown", { pointerId: 1 });
  assert.equal(up.classList.contains("held"), true);
  up.fire("pointerdown", { pointerId: 1 });  // still held: no second press
  up.fire("pointerup");
  assert.equal(up.classList.contains("held"), false);
  up.fire("lostpointercapture");  // already let go: no second stop
  down.fire("keydown", { key: " " });
  down.fire("keydown", { key: " " });  // the key repeating: still the one press
  down.fire("keyup", { key: " " });
  down.fire("keydown", { key: "a" });
  down.fire("pointerdown", { pointerId: 2 });
  down.fire("pointercancel");
  const range = p.$("input", k);
  assert.deepEqual([range.type, range.min, range.max, range.step], ["range", 0, 100, 5]);
  assert.equal(range.getAttribute("aria-label"), "Screen: position");
  range.fire("pointerdown");
  assert.equal(range.dragging, true);
  range.value = "35";
  range.fire("change");
  assert.equal(range.dragging, false);
  await settle();
  assert.deepEqual(p.posted().map(([url, body]) => [url, JSON.stringify(body)]),
                   [{ position: 0 }, { stop: true }, { position: 100 }, { declare: 0 }, { declare: 100 },
                    { press: "up" }, { press: "stop" }, { press: "down" }, { press: "stop" }, { press: "down" }, { press: "stop" },
                    { position: 35 }].map((b) => ["/api/covers/screen", JSON.stringify(b)]));
  const polls = p.polls();
  await p.tick(300);
  assert.equal(p.polls(), polls + 12);  // one for each order
});

test("the screen in every state", async (t) => {
  const states = [
    [{ position: 0 }, "rolled up", "0", "0"],
    [{ position: 100, target: 100 }, "open", "100", "100"],
    [{ position: 40, target: 40 }, "40 %", "40", "40"],
    [{ position: 20, moving: "down", target: 60 }, "opening… 20 % → 60 %", "20", "60"],
    [{ position: 20, moving: "down", target: null }, "opening… 20 % → 100 %", "20", "0"],
    [{ position: 70, moving: "up", target: null }, "closing… 70 % → 0 %", "70", "0"],
    [{ position: null, moving: "up", target: 100 }, "all the way up, to start again from the top", "50", "100"],
    [{ position: null, moving: "down", target: null }, "moving down, position unknown", "50", "0"],
    [{ position: null, target: null }, "position unknown", "50", "0"],
  ];
  const p = await open(t, status({ covers: [cover()] }));
  const k = p.card("cover", "screen"), screen = p.$(".screen", k);
  for (const [more, said, pos, target] of states) {
    p.status = status({ covers: [cover(more)] });
    await p.tick(5000);
    assert.equal(p.text(".when", k), said);
    assert.deepEqual([screen.style.props["--p"], screen.style.props["--t"]], [pos, target]);
    assert.equal(k.classList.contains("moving"), !!more.moving);
    assert.equal(k.classList.contains("unknown"), more.position === null);
  }
});

test("the slider follows the screen, unless it is in someone's hands", async (t) => {
  const p = await open(t, status({ covers: [cover({ position: 30, target: 30 })] }));
  const k = p.card("cover", "screen"), range = p.$("input", k);
  assert.equal(range.value, 30);
  p.status = status({ covers: [cover({ position: 30, target: null, moving: "down" })] });
  await p.tick(5000);
  assert.equal(range.value, 30);
  p.status = status({ covers: [cover({ position: null, target: null })] });
  await p.tick(1000);
  assert.equal(range.value, 0);
  range.fire("pointerdown");
  p.status = status({ covers: [cover({ position: 80, target: 80 })] });
  await p.tick(5000);
  assert.equal(range.value, 0);
  range.dragging = false;
  p.document.activeElement = range;
  await p.tick(5000);
  assert.equal(range.value, 0);
  p.document.activeElement = null;
  await p.tick(5000);
  assert.equal(range.value, 80);
});

test("the screen refuses an order: the card says it failed", async (t) => {
  const p = await open(t, status({ covers: [cover()] }));
  const k = p.card("cover", "screen");
  p.posts["/api/covers/screen"] = { status: 503, body: { error: "the broker is not connected" } };
  k.querySelectorAll("button")[0].click();
  await settle();
  assert.equal(p.text("#toast"), "screen: the broker is not connected");
  assert.ok(k.classList.contains("failed"));
  p.status = status({ covers: [] });
  await p.tick(5000);
  p.root.querySelector("#covers").replaceChildren();
  p.posts["/api/covers/screen"] = 500;
  k.querySelectorAll("button")[0].click();
  await settle();
  assert.equal(p.text("#toast"), "screen: 500");
});

// ---------- the schedule of each outlet ----------
const ALL = [1, 2, 3, 4, 5, 6, 7];
const rule = (at, on, days = ALL) => ({ at, on, days });
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

test("the schedule under each outlet, the next rule after the state", async (t) => {
  const s = status();
  s.outlets[0].schedule = [rule("08:30", true), rule("23:30", false, [1, 2, 3, 4, 5])];
  s.outlets[0].next = { at: new Date(NOW + 3600e3).toISOString(), on: false };
  s.outlets[1].schedule = [rule("07:00", true, [6, 7]), rule("12:00", false, [1, 3])];
  s.outlets[1].next = { at: new Date(NOW + 2 * 86400e3).toISOString(), on: true };
  const p = await open(t, s);
  const lamp = p.card("outlet", "terrarium"), fan = p.card("outlet", "fan");
  assert.equal(p.text(".plan", lamp), "⏱ on 08:30 · off 23:30 weekdays");
  assert.equal(p.text(".when", lamp), `On · 5 min ago · off at ${clock(s.outlets[0].next.at)}`);
  assert.equal(p.text(".plan", fan), "⏱ on 07:00 weekends · off 12:00 Mo We");
  const day = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"][(new Date(s.outlets[1].next.at).getDay() + 6) % 7];
  assert.equal(p.text(".when", fan), `Off · on ${day} ${clock(s.outlets[1].next.at)}`);
  assert.equal(p.text(".plan", p.card("outlet", "heater")), "⏱ no schedule");
  assert.equal(p.$(".editor", lamp), null);
});

test("the editor adds a rule, sets its time, off, and weekdays, and saves it all as a PUT", async (t) => {
  const s = status();
  s.outlets[1].schedule = [rule("08:30", true)];
  const p = await open(t, s);
  const fan = p.card("outlet", "fan");
  p.$(".plan", fan).click();
  const editor = p.$(".editor", fan);
  assert.ok(editor);
  assert.equal(p.$(".plan", fan).getAttribute("aria-expanded"), "true");
  assert.equal(editor.querySelectorAll(".rule").length, 1);
  assert.equal(p.$("select", editor).value, "true");
  assert.equal(p.$("input", editor).value, "08:30");
  p.$(".add", fan).click();
  const rows = editor.querySelectorAll(".rule");
  assert.equal(rows.length, 2);
  const at = p.$("input", rows[1]), on = p.$("select", rows[1]);
  at.value = "23:30"; at.fire("change");
  on.value = "false"; on.fire("change");
  const days = rows[1].querySelectorAll(".day");
  assert.equal(days.length, 7);
  days[5].click(); days[6].click();
  assert.equal(days[5].getAttribute("aria-pressed"), "false");
  assert.equal(days[4].getAttribute("aria-pressed"), "true");
  p.$(".save", fan).click();
  await settle();
  const put = p.calls.filter((c) => c.method === "PUT");
  assert.deepEqual(put, [{ url: "/api/outlets/fan/schedule", method: "PUT", body: [rule("08:30", true), rule("23:30", false, [1, 2, 3, 4, 5])] }]);
  assert.equal(p.$(".editor", fan), null);
  assert.equal(p.$(".plan", fan).getAttribute("aria-expanded"), "false");
  assert.equal(p.text("#toast"), "");
  assert.ok(p.polls() >= 2);  // the state is read again: the hub may have applied a rule at once
});

test("a rule is removed, the editor is cancelled, and the plan line toggles it", async (t) => {
  const s = status();
  s.outlets[1].schedule = [rule("08:30", true), rule("23:30", false)];
  const p = await open(t, s);
  const fan = p.card("outlet", "fan");
  p.$(".plan", fan).click();
  const editor = p.$(".editor", fan);
  editor.querySelectorAll(".del")[0].click();
  const rows = editor.querySelectorAll(".rule");
  assert.equal(rows.length, 1);
  assert.equal(p.$("input", rows[0]).value, "23:30");
  rows[0].querySelectorAll(".del")[0].click();
  assert.equal(editor.querySelectorAll(".rule").length, 0);
  assert.match(p.text(".empty", editor), /No rule/);
  p.$(".cancel", fan).click();
  assert.equal(p.$(".editor", fan), null);
  assert.equal(p.calls.filter((c) => c.method === "PUT").length, 0);
  assert.equal(p.text(".plan", fan), "⏱ on 08:30 · off 23:30");  // untouched: the editor worked on a copy
  p.$(".plan", fan).click();
  assert.ok(p.$(".editor", fan));
  p.$(".plan", fan).click();
  assert.equal(p.$(".editor", fan), null);
});

test("a rule without a day stays on the page, and a schedule the hub refuses says why", async (t) => {
  const p = await open(t);
  const fan = p.card("outlet", "fan");
  p.$(".plan", fan).click();
  p.$(".add", fan).click();
  for (const d of p.$(".editor", fan).querySelectorAll(".day")) d.click();
  p.$(".save", fan).click();
  await settle();
  assert.equal(p.text("#toast"), "a rule needs at least one day");
  assert.equal(p.calls.filter((c) => c.method === "PUT").length, 0);
  p.$(".editor", fan).querySelectorAll(".day")[0].click();
  p.answer = (url, options) => (options.method === "PUT" ? { status: 400, body: { error: "expected a list of rules" } } : p.status);
  p.$(".save", fan).click();
  await settle();
  assert.equal(p.text("#toast"), "fan: expected a list of rules");
  assert.ok(p.$(".editor", fan));  // still open, to fix it
  assert.ok(fan.classList.contains("failed"));
});
