"use strict";
const $ = (s, el = document) => el.querySelector(s);
const MASK = "••••••••";
let CONFIG = null, SCHEMA = null, FONTS = [], MAPPINGS = [], draft = null, STATE = null;
let dirty = false, openBoards = new Set(JSON.parse(localStorageGet("openBoards") || "[]"));

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
const clone = (o) => JSON.parse(JSON.stringify(o));

async function api(path, body, method) {
  const opt = { method: method || (body !== undefined ? "POST" : "GET"), headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (isErr ? " error" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), isErr ? 6000 : 2500);
}

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "checked" || k === "value" || k === "disabled") el[k] = v;
    else if (v !== undefined && v !== null && v !== false) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

function markDirty() { dirty = true; $("#savebar").classList.remove("hidden"); }

// ---------------------------------------------------------------- preview
const preview = $("#preview"), pctx = preview.getContext("2d");
const scratch = document.createElement("canvas"), sctx = scratch.getContext("2d", { willReadFrequently: true });
let previewBusy = false;

async function pollFrame() {
  if (previewBusy || document.hidden) return;
  previewBusy = true;
  try {
    const r = await fetch("/api/frame.png", { cache: "no-store" });
    if (r.status === 200) drawLeds(await createImageBitmap(await r.blob()));
  } catch {} finally { previewBusy = false; }
}

// Pinned mini preview: appears under the header once the main preview is scrolled away.
const mini = $("#mini-live"), miniCanvas = $("#mini-preview"), mctx = miniCanvas.getContext("2d");
let mainVisible = true;

function drawLeds(bmp) {
  const W = bmp.width, H = bmp.height;
  scratch.width = W; scratch.height = H;
  sctx.drawImage(bmp, 0, 0);
  const px = sctx.getImageData(0, 0, W, H).data;
  if (mainVisible) {
    paintLeds(preview, pctx, px, W, H, preview.parentElement.clientWidth - 20, Infinity, 2);
  } else {
    // compact: at most ~13% of the screen height so it never crowds the page
    const maxH = Math.min(120, Math.max(40, window.innerHeight * 0.13));
    paintLeds(miniCanvas, mctx, px, W, H, mini.clientWidth - 24, maxH, 1);
  }
}

function paintLeds(canvas, ctx, px, W, H, availW, maxH, minScale) {
  const s = Math.max(minScale, Math.floor(Math.min(availW / W, maxH / H)));
  if (canvas.width !== W * s) { canvas.width = W * s; canvas.height = H * s; canvas.style.width = W * s + "px"; }
  ctx.fillStyle = "#050505"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  const r = s * 0.42;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const i = (y * W + x) * 4, R = px[i], G = px[i + 1], B = px[i + 2];
    ctx.fillStyle = (R | G | B) ? `rgb(${R},${G},${B})` : "#141414";
    if (s < 3) { ctx.fillRect(x * s, y * s, s, s); continue; }       // tiny: square pixels read better
    ctx.beginPath(); ctx.arc(x * s + s / 2, y * s + s / 2, r, 0, 6.2832); ctx.fill();
  }
}

function headerHeight() { return document.querySelector("header").offsetHeight; }
function placeMini() { document.documentElement.style.setProperty("--header-h", headerHeight() + "px"); }
placeMini();
window.addEventListener("resize", placeMini);

// Show the mini preview when the main one is (mostly) hidden behind the header / off screen.
let miniObserver;
function watchPreview() {
  if (miniObserver) miniObserver.disconnect();
  miniObserver = new IntersectionObserver(([entry]) => {
    mainVisible = entry.isIntersecting;
    mini.classList.toggle("show", !mainVisible);
    mini.setAttribute("aria-hidden", mainVisible ? "true" : "false");
  }, { rootMargin: `-${headerHeight()}px 0px 0px 0px`, threshold: 0.35 });
  miniObserver.observe($(".preview-wrap"));
}
watchPreview();
window.addEventListener("resize", watchPreview);
mini.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

// ---------------------------------------------------------------- state
async function pollState() {
  try { STATE = await api("/api/state"); } catch { return; }
  const st = STATE;
  $("#pill-mode").textContent = st.simulated ? "Simulator (no LED hardware)" : `${st.width}×${st.height} matrix`;
  $("#pill-mode").className = "pill " + (st.simulated ? "sim" : "hw");
  $("#pill-fps").textContent = `${st.fps} fps`;
  const cur = st.current;
  $("#now-board").textContent = st.alert ? "Alert: " + st.alert : st.overlay ? "Message" :
    cur ? (cur.board === "__combined__" ? "Combined ticker" : SCHEMA?.[cur.board]?.title || cur.board) : "—";
  $("#now-slide").textContent = !st.overlay && cur && cur.slides > 1 ? `${cur.slide} / ${cur.slides}` : "";
  $("#mini-board").textContent = $("#now-board").textContent;
  $("#mini-slide").textContent = $("#now-slide").textContent;
  $("#badge-takeover").classList.toggle("hidden", !st.takeover);
  $("#badge-quiet").classList.toggle("hidden", !st.quiet_hours);
  $("#btn-pause").textContent = st.paused ? "Resume" : "Pause";
  $("#btn-power").textContent = st.power ? "Turn off" : "Turn on";
  const br = $("#brightness");
  if (document.activeElement !== br) { br.value = CONFIG?.display.brightness ?? st.brightness; $("#bright-val").textContent = br.value; }
  for (const [name, b] of Object.entries(st.boards)) {
    const dot = document.querySelector(`[data-dot="${name}"]`);
    if (dot) dot.className = "dot " + (!b.enabled ? "idle" : b.last_error ? "err" : "ok");
    const err = document.querySelector(`[data-err="${name}"]`);
    if (err) { err.textContent = b.last_error || ""; err.classList.toggle("hidden", !b.last_error); }
  }
  renderStatus();
  renderActiveAlerts(st.alerts || []);
  $("#btn-update-badge").classList.toggle("hidden", !st.update_available);
  const sun = st.sun;
  $("#sun-status").textContent = !sun ? "" : sun.error ? "⚠ " + sun.error :
    `Sun ${sun.elevation}° ${sun.elevation >= 0 ? "above" : "below"} the horizon → brightness ${sun.brightness}`;
  const mq = st.mqtt || {};
  $("#mqtt-status").textContent = !mq.enabled ? "Off" : mq.connected ? `✅ Connected to ${mq.broker}` : `⚠ Not connected${mq.error ? " — " + mq.error : " (connecting…)"}`;
  const sa = document.querySelector("[data-spotify-status]");
  if (sa) sa.textContent = st.boards.spotify.connected ? "✅ Connected" : "Not connected";
}

function renderStatus() {
  if (!STATE) return;
  const ago = (t) => t ? `${Math.round(Date.now() / 1000 - t)}s ago` : "never";
  const rows = Object.entries(STATE.boards).map(([n, b]) => {
    let info = !b.enabled ? "disabled" : `updated ${ago(b.last_update)} · ${b.slides} slide(s)`;
    if (n === "spotify" && b.enabled) info += b.now ? ` · ${b.now.playing ? "▶" : "⏸"} ${b.now.title} — ${b.now.artist}` : " · nothing playing";
    if (n === "plex" && b.enabled) info += ` · ${b.sessions.length} stream(s)`;
    return h("div", { class: "status-row" },
      h("span", { class: "dot " + (!b.enabled ? "idle" : b.last_error ? "err" : "ok") }),
      h("span", { class: "nm" }, SCHEMA?.[n]?.title || n),
      h("span", { class: "info" }, info, b.last_error ? h("div", { class: "err-line" }, b.last_error) : null));
  });
  const pf = STATE.perf || {};
  const health = [];
  if (pf.cpu_temp_c != null) health.push(`CPU ${pf.cpu_temp_c}°C`);
  if (pf.load != null) health.push(`load ${pf.load}`);
  if (pf.cpus_used) health.push(`LEDBoard on CPUs ${pf.cpus_used.join(",")} of ${pf.cpu_count}` + (pf.isolated_cpus ? ` (isolated: ${pf.isolated_cpus})` : ""));
  const warn = [];
  if ((pf.throttled_now || []).length) warn.push("Now: " + pf.throttled_now.join(", "));
  if ((pf.throttled_since_boot || []).length) warn.push("Since boot: " + pf.throttled_since_boot.join(", "));
  const perfRow = h("div", { class: "status-row" },
    h("span", { class: "dot " + (warn.length || pf.frame_budget_pct > 60 ? "err" : "ok") }),
    h("span", { class: "nm" }, "Performance"),
    h("span", { class: "info" },
      `${STATE.fps} fps · render ${pf.render_ms ?? "?"} ms · panel push ${pf.push_ms ?? "?"} ms · ${pf.frame_budget_pct ?? "?"}% of frame time`,
      health.length ? h("div", {}, health.join(" · ")) : null,
      warn.length ? h("div", { class: "err-line" }, "Pi power/heat warning — " + warn.join(" | ") + ". Under-voltage often causes flicker: check the Pi's power supply.") : null));
  $("#status").replaceChildren(h("div", { class: "status-grid" }, perfRow, rows));
}

// ---------------------------------------------------------------- software update + restore
let updatePoll = null;
async function renderSoftware(statusIn) {
  let st = statusIn;
  if (!st) { try { st = await api("/api/update"); } catch (e) { $("#software").textContent = e.message; return; } }
  const cur = st.current || {};
  const kids = [h("div", {}, h("strong", {}, `LEDBoard ${cur.version || ""}`),
    cur.commit ? h("span", { class: "muted" }, ` · ${cur.commit} · ${cur.date || ""}`) : null)];
  if (st.error) kids.push(h("div", { class: "err-line" }, st.error));
  if (st.state === "updating" || st.state === "restarting" || (st.state === "error" && st.log?.length)) {
    kids.push(h("div", { class: "update-log" }, (st.log || []).join("\n") || "Starting…"));
  } else if (st.behind > 0) {
    kids.push(h("div", { style: "margin-top:8px" }, h("strong", {}, `Update available: ${st.behind} new change${st.behind > 1 ? "s" : ""}`)),
      h("ul", { class: "changes" }, (st.changes || []).map((c) => h("li", {}, c))));
    if ((st.local_changes || []).length) kids.push(h("div", { class: "err-line" }, "Files were edited on the Pi, so automatic update is disabled: " + st.local_changes.join(", ")));
    else kids.push(h("button", { class: "primary", onclick: startUpdate }, "Update now"));
  } else if (st.last_check) {
    kids.push(h("div", { class: "muted small", style: "margin-top:6px" }, `Up to date (checked ${new Date(st.last_check * 1000).toLocaleString()})`));
  }
  if (!["updating", "restarting"].includes(st.state)) {
    kids.push(h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "small", onclick: async (ev) => {
      ev.currentTarget.disabled = true; ev.currentTarget.textContent = "Checking…";
      try { renderSoftware(await api("/api/update/check", {})); } catch (e) { toast(e.message, true); renderSoftware(); }
    } }, "Check for updates")));
  }
  $("#software").replaceChildren(...kids.filter(Boolean));
}

async function startUpdate() {
  if (!confirm("Update LEDBoard now? The display restarts when it's done.")) return;
  try { await api("/api/update/apply", {}); } catch (e) { return toast(e.message, true); }
  clearInterval(updatePoll);
  updatePoll = setInterval(async () => {
    try {
      const st = await api("/api/update");
      renderSoftware(st);
      if (st.state === "error") clearInterval(updatePoll);
    } catch {          // server is restarting: wait for it to come back, then reload
      clearInterval(updatePoll);
      $("#software").append(h("div", { class: "muted" }, "Restarting… the page reloads when LEDBoard is back."));
      const wait = setInterval(async () => {
        try { await api("/api/state"); clearInterval(wait); location.reload(); } catch {}
      }, 2000);
    }
  }, 1500);
}

$("#restore-file").addEventListener("change", async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  let data;
  try { data = JSON.parse(await f.text()); } catch { return toast("That file isn't a LEDBoard backup", true); }
  if (!confirm(`Restore settings from ${f.name}? This replaces all current settings.`)) return;
  try {
    const j = await api("/api/config/import", data);
    toast("Settings restored"); await reloadConfig();
    if (j.restart_needed && confirm("Display hardware settings changed. Restart LEDBoard now?")) restart();
  } catch (err) { toast(err.message, true); }
});
$("#btn-update-badge").onclick = () => document.querySelector('.tabs button[data-tab="schedule"]').click();

// ---------------------------------------------------------------- alerts
let alertIcons = [];
function renderActiveAlerts(list) {
  const box = $("#active-alerts");
  const repeating = list.filter((a) => a.repeating || a.until_dismissed);
  box.classList.toggle("hidden", !repeating.length);
  if (!repeating.length) return box.replaceChildren();
  box.replaceChildren(...repeating.map((a) => h("div", { class: "alert-row" },
    h("span", { class: "badge " + (a.priority === "critical" ? "crit" : "") }, a.priority),
    h("span", { class: "grow" }, a.title + (a.message ? " — " + a.message : "")),
    h("button", { class: "small", onclick: () => api("/api/alert/dismiss", { id: a.id }).then(pollState) }, "Dismiss"))),
    ...(repeating.length > 1 ? [h("button", { class: "small", onclick: () => api("/api/alert/dismiss", { id: "all" }).then(pollState) }, "Dismiss all")] : []));
}

async function renderAlertForm() {
  try { alertIcons = (await api("/api/alerts")).icons; } catch { alertIcons = ["bell"]; }
  const a = { __scratch: true, title: "Doorbell", message: "Someone is at the front door", icon: "doorbell", color: "#3b9cff", priority: "high", seconds: 10 };
  const send = h("button", { class: "primary", onclick: async () => {
    const { __scratch, ...payload } = a;
    try { await api("/api/alert", payload); toast("Alert sent"); pollState(); } catch (e) { toast(e.message, true); }
  } }, "Send alert");
  $("#alert-form").replaceChildren(
    renderField(a, { key: "title", label: "Title", type: "text" }),
    renderField(a, { key: "message", label: "Message (optional)", type: "text" }),
    renderField(a, { key: "icon", label: "Icon", type: "select", options: alertIcons }),
    renderField(a, { key: "priority", label: "Priority", type: "select", options: ["low", "normal", "high", "critical"],
      option_labels: { low: "Low — waits for the current board", normal: "Normal — interrupts", high: "High — interrupts, flashing border", critical: "Critical — repeats until dismissed" } }),
    renderField(a, { key: "color", label: "Color", type: "color" }),
    renderField(a, { key: "seconds", label: "Seconds on screen", type: "number" }),
    renderField(a, { key: "hold", label: "Keep on screen until dismissed", type: "bool" }),
    h("div", { class: "row" }, send));
}

// ---------------------------------------------------------------- fields
function field(label, input, help, cls = "") {
  return h("div", { class: "field " + cls }, h("label", {}, label), input, help ? h("div", { class: "help" }, help) : null);
}

// Settings form with section headings; fields with show_if only appear when relevant.
function renderForm(obj, fields, rerender) {
  const deps = new Set(fields.flatMap((f) => Object.keys(f.show_if || {})));
  const out = [];
  let section = null;
  for (const f of fields) {
    if (f.show_if && !Object.entries(f.show_if).every(([k, v]) => (obj[k] ?? false) === v)) continue;
    if (f.section && f.section !== section) { section = f.section; out.push(h("h4", { class: "section" }, section)); }
    out.push(renderField(obj, f, deps.has(f.key) || f.type === "screens" ? rerender : null));
  }
  return out;
}

function renderField(obj, f, onDepChange) {
  const key = f.key, val = obj[key];
  const set = (v) => { obj[key] = v; if (!obj.__scratch) markDirty(); if (onDepChange) onDepChange(); };
  switch (f.type) {
    case "bool": {
      const sw = h("label", { class: "switch" }, h("input", { type: "checkbox", checked: !!val, onchange: (e) => set(e.target.checked) }), h("span"));
      return field(f.label, sw, f.help, "bool");
    }
    case "number":
      return field(f.label, h("input", { type: "number", value: val ?? "", step: "any", placeholder: f.placeholder || null,
        oninput: (e) => set(e.target.value === "" ? "" : Number(e.target.value)) }), f.help);
    case "password":
      return field(f.label, h("input", { type: "password", value: val || "", placeholder: "not set", autocomplete: "new-password",
        onfocus: (e) => { if (e.target.value === MASK) e.target.value = ""; },
        onblur: (e) => { if (e.target.value === "" && val === MASK) e.target.value = MASK; },
        oninput: (e) => set(e.target.value) }), f.help);
    case "color":
      return field(f.label, h("input", { type: "color", value: val || "#ffffff", oninput: (e) => set(e.target.value) }), f.help);
    case "select": {
      const sel = h("select", { onchange: (e) => set(typeof f.options[0] === "number" ? Number(e.target.value) : e.target.value) },
        f.options.map((o) => h("option", { value: o, selected: String(o) === String(val) }, (f.option_labels || {})[o] || String(o))));
      return field(f.label, sel, f.help);
    }
    case "list": {
      const input = h("input", { type: "text", value: (val || []).join(", "), list: f.datalist || null,
        oninput: (e) => set(e.target.value.split(",").map((s) => s.trim()).filter(Boolean)) });
      if (!f.datalist) return field(f.label, input, f.help);
      return field(f.label, h("div", { class: "row nowrap" }, input,
        h("button", { class: "small", onclick: () => loadHaEntities() }, "Load from HA")), f.help);
    }
    case "entities": return entitiesField(obj, f);
    case "screens": return screensField(obj, f, onDepChange);
    case "geocode": return geocodeField(obj, f);
    case "simulate": return actionsField({ ...f, action: "simulate" }, f, "Simulate a score",
      "Plays the celebration now, then this board's games, exactly as it would after a real score.");
    case "actions": return actionsField(f, f, f.label, f.help);
    case "spotify_auth": return spotifyAuthField(obj);
    default:
      return field(f.label, h("input", { type: "text", value: val ?? "", oninput: (e) => set(e.target.value) }), f.help);
  }
}

// Ordered list of screens: on/off switch + up/down arrows. Order is saved as a list of ids in obj[f.key];
// each screen's on/off is its own setting (item.toggle).
function screensField(obj, f, onChange) {
  const ids = f.items.map((it) => it.slide);
  const order = (obj[f.key] || []).filter((s) => ids.includes(s));
  ids.forEach((s) => { if (!order.includes(s)) order.push(s); });
  const byId = Object.fromEntries(f.items.map((it) => [it.slide, it]));
  const wrap = h("div", { class: "screens" });
  const draw = () => {
    wrap.replaceChildren(...order.map((sid, i) => {
      const it = byId[sid];
      const move = (d) => {
        const j = i + d;
        if (j < 0 || j >= order.length) return;
        [order[i], order[j]] = [order[j], order[i]];
        obj[f.key] = [...order]; markDirty(); draw();
      };
      const on = obj[it.toggle] ?? ["current", "daily", "hourly"].includes(sid);
      return h("div", { class: "screen-row" + (on ? "" : " off") },
        h("label", { class: "switch" }, h("input", { type: "checkbox", checked: !!on,
          onchange: (e) => { obj[it.toggle] = e.target.checked; markDirty(); if (onChange) onChange(); else draw(); } }), h("span")),
        h("span", { class: "screen-label" }, it.label),
        h("div", { class: "order" },
          h("button", { title: "Move up", disabled: i === 0, onclick: () => move(-1) }, "▲"),
          h("button", { title: "Move down", disabled: i === order.length - 1, onclick: () => move(1) }, "▼")));
    }));
  };
  draw();
  return h("div", { class: "field" }, f.label && f.label !== f.section ? h("label", {}, f.label) : null, wrap,
    f.help ? h("div", { class: "help" }, f.help) : null);
}

function entitiesField(obj, f) {
  obj.entities = obj.entities || [];
  const wrap = h("div", { class: "entities" });
  const draw = () => {
    const rows = obj.entities.map((e, i) => h("tr", {},
      h("td", {}, h("input", { class: "eid", list: "ha-entities", placeholder: "sensor.living_room_temperature", value: e.entity_id || "",
        oninput: (ev) => { e.entity_id = ev.target.value.trim(); markDirty(); } })),
      h("td", {}, h("input", { placeholder: "(friendly name)", value: e.label || "", oninput: (ev) => { e.label = ev.target.value; markDirty(); } })),
      h("td", {}, h("input", { class: "narrow", placeholder: "attribute", value: e.attribute || "", title: "Show an attribute instead of the state (e.g. current_temperature)",
        oninput: (ev) => { e.attribute = ev.target.value.trim(); markDirty(); } })),
      h("td", {}, h("input", { class: "narrow", placeholder: "#color", value: e.color || "", title: "Value color, e.g. #ffcc00 (blank = automatic)",
        oninput: (ev) => { e.color = ev.target.value.trim(); markDirty(); } })),
      h("td", {}, h("input", { class: "narrow", type: "number", placeholder: "decimals", value: e.decimals ?? "",
        oninput: (ev) => { e.decimals = ev.target.value === "" ? "" : Number(ev.target.value); markDirty(); } })),
      h("td", {}, h("button", { class: "small", title: "Up", onclick: () => { if (i) { [obj.entities[i - 1], obj.entities[i]] = [obj.entities[i], obj.entities[i - 1]]; markDirty(); draw(); } } }, "↑")),
      h("td", {}, h("button", { class: "small danger", title: "Remove", onclick: () => { obj.entities.splice(i, 1); markDirty(); draw(); } }, "✕"))));
    wrap.replaceChildren(
      h("table", {}, h("tr", {}, ["Entity ID", "Label", "Attribute", "Color", "Decimals", "", ""].map((t) => h("th", {}, t))), rows),
      h("div", { class: "row", style: "margin-top:8px" },
        h("button", { class: "small", onclick: () => { obj.entities.push({ entity_id: "", label: "" }); markDirty(); draw(); } }, "+ Add entity"),
        h("button", { class: "small", onclick: () => loadHaEntities(obj) }, "Load entity list from Home Assistant")));
  };
  draw();
  return h("div", {}, h("div", { class: "muted small", style: "margin-bottom:6px" }, f.label), wrap);
}

// A row of buttons that each send {action, board, kind} to /api/control, then jump to the board.
function actionsField(f, schemaField, label, help) {
  const board = Object.keys(SCHEMA).find((n) => SCHEMA[n].fields.includes(schemaField));
  return field(label, h("div", { class: "row" }, Object.entries(f.options).map(([kind, text]) =>
    h("button", { class: "small", onclick: async (ev) => {
      const btn = ev.currentTarget; btn.disabled = true;
      try { await api("/api/control", { action: f.action, board, kind }); toast(`${text} — watch the preview above`); pollState(); }
      catch (e) { toast(e.message, true); }
      finally { btn.disabled = false; }
    } }, "▶ " + text))), help);
}

// Location search (Open-Meteo geocoding): fills name + latitude + longitude.
function geocodeField(obj, f) {
  const input = h("input", { type: "text", value: obj[f.key] || "", placeholder: "City or ZIP, e.g. Buffalo" });
  const results = h("div", { class: "geo-results" });
  const search = async () => {
    try {
      const j = await api("/api/weather/geocode?q=" + encodeURIComponent(input.value));
      if (!j.results.length) return results.replaceChildren(h("span", { class: "muted small" }, "No places found"));
      results.replaceChildren(...j.results.map((r) => h("button", { class: "small", onclick: () => {
        obj[f.key] = r.label; obj.latitude = r.latitude; obj.longitude = r.longitude; markDirty(); renderBoards();
        toast(`Location set to ${r.label} — click Save`);
      } }, r.label)));
    } catch (e) { toast(e.message, true); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } });
  input.addEventListener("input", (e) => { obj[f.key] = e.target.value; markDirty(); });
  return field(f.label, h("div", {}, h("div", { class: "row nowrap" }, input, h("button", { class: "small", onclick: search }, "Search")), results), f.help);
}

// Fills the entity autocomplete lists from Home Assistant (unsaved URL/token from the HA form win).
async function loadHaEntities(ha) {
  ha = ha || draft.boards.homeassistant || {};
  try {
    const j = await api("/api/homeassistant/entities", { url: ha.url, token: ha.token, verify_ssl: ha.verify_ssl });
    const opt = (e) => h("option", { value: e.entity_id }, `${e.name} (${e.state})`);
    $("#ha-entities").replaceChildren(...j.entities.map(opt));
    $("#ha-media-players").replaceChildren(...j.entities.filter((e) => e.entity_id.startsWith("media_player.")).map(opt));
    toast(`Loaded ${j.entities.length} entities from Home Assistant — start typing to pick one`);
  } catch (e) { toast("Home Assistant: " + e.message, true); }
}

function spotifyAuthField(obj) {
  const box = h("input", { type: "text", placeholder: "Paste the 127.0.0.1/spotify/callback?code=… URL here", style: "width:100%" });
  return h("div", { class: "card spotify-auth", style: "background:var(--panel2)" },
    h("div", {}, h("strong", {}, "Spotify account: "), h("span", { "data-spotify-status": "" }, STATE?.boards.spotify.connected ? "✅ Connected" : "Not connected")),
    h("ol", {},
      h("li", {}, "Create an app at ", h("a", { href: "https://developer.spotify.com/dashboard", target: "_blank" }, "developer.spotify.com/dashboard"), " (choose “Web API”)."),
      h("li", {}, "Add this Redirect URI to the app: ", h("code", {}, obj.redirect_uri)),
      h("li", {}, "Paste the Client ID and Secret below and click Save."),
      h("li", {}, "Click Connect. After approving, your browser lands on a 127.0.0.1 page that won't load — that's expected. Copy that page's full URL and paste it here.")),
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: async () => {
        if (dirty) return toast("Save your changes first", true);
        try { const j = await api("/api/spotify/auth-url"); window.open(j.url, "_blank"); } catch (e) { toast(e.message, true); }
      } }, "Connect Spotify"),
      h("button", { class: "danger", onclick: async () => { await api("/api/spotify/disconnect", {}); toast("Disconnected"); pollState(); } }, "Disconnect")),
    h("div", { class: "row" }, box, h("button", { onclick: async () => {
      try { await api("/api/spotify/exchange", { code: box.value }); box.value = ""; toast("Spotify connected!"); await reloadConfig(); }
      catch (e) { toast(e.message, true); }
    } }, "Finish")));
}

// ---------------------------------------------------------------- boards tab
let showDisabled = localStorageGet("showDisabled") === "1";

// Enabled boards first (in their rotation order), then disabled ones. Disabled boards are
// never shown, so this doesn't change what appears on the display.
function partitionRotation(d) {
  const on = d.rotation.filter((n) => d.boards[n]?.enabled);
  d.rotation = on.concat(d.rotation.filter((n) => !d.boards[n]?.enabled));
}

const TRANSITIONS = {
  off: "Off (instant cut)", slide: "Slide (new board pushes in from the right)", "push-up": "Push up (like a ticker)",
  wipe: "Wipe (a bright line sweeps across)", fade: "Fade (through black)", dissolve: "Dissolve (pixels switch over randomly)",
  random: "Random (a different one each time)",
};

function renderBoards() {
  const root = $("#boards");
  const visible = (n) => SCHEMA[n] && (showDisabled || draft.boards[n].enabled || openBoards.has(n));
  const nDisabled = draft.rotation.filter((n) => SCHEMA[n] && !draft.boards[n].enabled).length;
  $("#toggle-disabled").textContent = showDisabled ? "Hide disabled boards" : `Show ${nDisabled} disabled board${nDisabled === 1 ? "" : "s"}`;
  root.replaceChildren(...draft.rotation.map((name, idx) => {
    const sch = SCHEMA[name], bc = draft.boards[name];
    if (!visible(name)) return null;
    const card = h("div", { class: "board" + (openBoards.has(name) ? " open" : "") + (bc.enabled ? "" : " disabled") });
    const toggleOpen = () => { card.classList.toggle("open"); card.classList.contains("open") ? openBoards.add(name) : openBoards.delete(name); localStorageSet("openBoards", JSON.stringify([...openBoards])); };
    const move = (d) => {
      // swap with the nearest board that is visible in that direction
      let j = idx + d;
      // only within the same group (enabled boards stay above disabled ones)
      const same = (n) => visible(n) && !!draft.boards[n].enabled === !!bc.enabled;
      while (j >= 0 && j < draft.rotation.length && !same(draft.rotation[j])) j += d;
      if (j < 0 || j >= draft.rotation.length) return;
      [draft.rotation[idx], draft.rotation[j]] = [draft.rotation[j], draft.rotation[idx]]; markDirty(); renderBoards();
    };
    const body = h("div", { class: "board-body" },
      h("div", { class: "err-line hidden", "data-err": name }),
      h("p", { class: "muted small", style: "margin-top:0" }, sch.description),
      h("div", { class: "form" }, renderForm(bc, sch.fields, renderBoards)),
      name === "plex" ? h("div", { class: "row", style: "margin-top:12px" }, h("button", { onclick: async () => {
        try { toast((await api("/api/plex/test", { url: bc.url, token: bc.token, verify_ssl: bc.verify_ssl })).message); } catch (e) { toast("Plex: " + e.message, true); }
      } }, "Test connection")) : null);
    card.append(
      h("div", { class: "board-head" },
        h("div", { class: "order" }, h("button", { title: "Move up", onclick: () => move(-1) }, "▲"), h("button", { title: "Move down", onclick: () => move(1) }, "▼")),
        h("span", { class: "dot", "data-dot": name }),
        h("div", { class: "board-title", onclick: toggleOpen }, sch.title, h("span", { class: "sub" }, summary(name, bc))),
        h("button", { class: "small", title: "Show this board now", onclick: () => api("/api/control", { action: "jump", board: name }).catch((e) => toast(e.message, true)) }, "Show"),
        h("label", { class: "switch", title: "Enabled" }, h("input", { type: "checkbox", checked: !!bc.enabled, onchange: (e) => {
          bc.enabled = e.target.checked;
          // re-enabled boards go to the end of the enabled group; disabled ones sink below it
          draft.rotation = draft.rotation.filter((n) => n !== name);
          const lastEnabled = draft.rotation.reduce((acc, n, i) => (draft.boards[n]?.enabled ? i : acc), -1);
          draft.rotation.splice(bc.enabled ? lastEnabled + 1 : draft.rotation.length, 0, name);
          markDirty(); renderBoards();
        } }), h("span")),
        h("button", { class: "small", onclick: toggleOpen }, "⚙")),
      body);
    return card;
  }).filter(Boolean));
  pollState();
}

function summary(name, bc) {
  const tags = [];
  if (SCHEMA[name].group === "Sports") {
    tags.push((bc.favorites || []).length ? "★ " + bc.favorites.join(", ") : "all teams");
    if (bc.favorites_only) tags.push("favorites only");
    if (bc.live_only) tags.push("live only");
    if (bc.takeover) tags.push("takeover");
  } else if (name === "homeassistant") tags.push(`${(bc.entities || []).length} entities`);
  else if (name === "message") tags.push(bc.text || "");
  else if (name === "spotify") {
    tags.push(bc.source === "spotify" ? "Spotify API" : "via Home Assistant");
    if (bc.takeover) tags.push("takes over while playing");
  } else if (name === "plex") tags.push(bc.takeover ? "takes over while playing" : `${bc.duration}s`);
  else tags.push(`${bc.duration}s`);
  if (bc.scroll_mode) tags.push("scroll mode");
  return tags.join(" · ");
}

// ---------------------------------------------------------------- other tabs
const DISPLAY_FIELDS = [
  { key: "rows", label: "Panel rows", type: "number" },
  { key: "cols", label: "Panel columns", type: "number" },
  { key: "chain_length", label: "Panels chained", type: "number" },
  { key: "parallel", label: "Parallel chains", type: "number" },
  { key: "hardware_mapping", label: "Hardware mapping", type: "select" },
  { key: "gpio_slowdown", label: "GPIO slowdown", type: "number", help: "Pi 4: 3–4 · Pi 3 / Zero 2 W: 2 · Pi Zero/1: 0–1. Too high = slow refresh (flicker); too low = random sparkles/garbage. Use the lowest value that looks clean." },
  { key: "pwm_bits", label: "PWM bits", type: "number", help: "11 = most colors. 8–9 roughly doubles the refresh rate (less flicker, esp. on camera) with little visible difference." },
  { key: "pwm_lsb_nanoseconds", label: "PWM LSB nanoseconds", type: "number", help: "Lower (50–100) = faster refresh; too low adds ghosting." },
  { key: "pwm_dither_bits", label: "PWM dither bits", type: "number", help: "1–2 trades a little color depth for a higher refresh rate." },
  { key: "limit_refresh_rate_hz", label: "Limit refresh rate (Hz, 0 = off)", type: "number", help: "Set a little below your typical rate (turn on 'Print refresh rate to log') to make brightness steady instead of pulsing." },
  { key: "scan_mode", label: "Scan mode", type: "select", options: [0, 1], help: "0 = progressive, 1 = interlaced" },
  { key: "row_address_type", label: "Row address type", type: "number" },
  { key: "multiplexing", label: "Multiplexing", type: "number" },
  { key: "led_rgb_sequence", label: "RGB sequence", type: "select", options: ["RGB", "RBG", "GRB", "GBR", "BRG", "BGR"] },
  { key: "pixel_mapper_config", label: "Pixel mapper", type: "text", help: "e.g. U-mapper, Rotate:180" },
  { key: "panel_type", label: "Panel type", type: "text", help: "e.g. FM6126A (leave blank unless needed)" },
  { key: "disable_hardware_pulsing", label: "Disable hardware pulsing", type: "bool" },
  { key: "inverse_colors", label: "Inverse colors", type: "bool" },
  { key: "show_refresh_rate", label: "Print refresh rate to log", type: "bool", help: "Watch it with: journalctl -u ledboard -f  (turn off afterwards, it floods the log)" },
  { key: "reserve_cpu_core", label: "Keep LEDBoard off the LED refresh CPU core", type: "bool", help: "Reduces flicker on multi-core Pis. Works best with isolcpus=3 (see README)." },
  { key: "fps", label: "Render FPS", type: "number", help: "Frames drawn per second (affects scroll smoothness and CPU)" },
];

function renderOtherTabs() {
  const dfs = DISPLAY_FIELDS.map((f) => f.key === "hardware_mapping" ? { ...f, options: MAPPINGS } : f);
  $("#display-form").replaceChildren(...dfs.map((f) => renderField(draft.display, f)));
  $("#schedule-form").replaceChildren(...[
    { key: "enabled", label: "Enable quiet hours", type: "bool" },
    { key: "start", label: "Start (HH:MM, 24h)", type: "text" },
    { key: "end", label: "End (HH:MM, 24h)", type: "text" },
    { key: "mode", label: "During quiet hours", type: "select", options: ["dim", "off", "ambient"],
      option_labels: { dim: "Dim the display", off: "Turn the display off", ambient: "Show ambient animations (dimmed)" } },
    { key: "brightness", label: "Dimmed brightness", type: "number" },
  ].map((f) => renderField(draft.schedule, f)));
  draft.combined = draft.combined || {};
  $("#combined-form").replaceChildren(...renderForm(draft.combined, [
    { key: "enabled", label: "Scroll all boards as one continuous ticker", type: "bool",
      help: "Every enabled board's slides scroll by one after another. Alerts, celebrations and takeovers still interrupt." },
    { key: "speed", label: "Speed (pixels per second)", type: "number", placeholder: "global default", show_if: { enabled: true } },
    { key: "gap", label: "Gap between slides (pixels)", type: "number", placeholder: "global default", show_if: { enabled: true } },
    { key: "pause", label: "Pause on each slide (seconds)", type: "number", show_if: { enabled: true },
      help: "Hold each slide for a moment once it's fully on screen (0 = keep moving)." },
  ], renderOtherTabs));
  draft.auto_brightness = draft.auto_brightness || {};
  $("#sun-form").replaceChildren(...renderForm(draft.auto_brightness, [
    { key: "follow_sun", label: "Follow the sun", type: "bool", help: "Uses the brightness slider during the day and eases down to the night level after sunset." },
    { key: "night", label: "Night brightness", type: "number", show_if: { follow_sun: true } },
    { key: "latitude", label: "Latitude", type: "number", placeholder: "Weather board's", show_if: { follow_sun: true } },
    { key: "longitude", label: "Longitude", type: "number", placeholder: "Weather board's", show_if: { follow_sun: true },
      help: "Leave blank to use the Weather board's location." },
  ], renderOtherTabs));
  draft.mqtt = draft.mqtt || {};
  $("#mqtt-form").replaceChildren(...renderForm(draft.mqtt, [
    { key: "enabled", label: "Connect to Home Assistant via MQTT", type: "bool" },
    { key: "host", label: "Broker host", type: "text", show_if: { enabled: true }, help: "Your Home Assistant's IP or homeassistant.local" },
    { key: "port", label: "Port", type: "number", show_if: { enabled: true } },
    { key: "username", label: "Username", type: "text", show_if: { enabled: true }, help: "An MQTT user (in HA: a person/user allowed to use MQTT)" },
    { key: "password", label: "Password", type: "password", show_if: { enabled: true } },
    { key: "device_name", label: "Device name in Home Assistant", type: "text", show_if: { enabled: true } },
    { key: "base_topic", label: "Topic prefix", type: "text", show_if: { enabled: true }, help: "Change only if you run more than one LEDBoard" },
  ], renderOtherTabs));
    draft.scroll = draft.scroll || { speed: 20, gap: 16 };
  $("#defaults-form").replaceChildren(
    renderField(draft.scroll, { key: "speed", label: "Scroll mode speed (pixels per second)", type: "number",
      help: "Used by every board in scroll mode unless that board sets its own speed." }),
    renderField(draft.scroll, { key: "gap", label: "Scroll mode gap between slides (pixels)", type: "number" }));
  draft.transitions = draft.transitions || { style: "slide", seconds: 0.6, scope: "boards" };
  $("#transitions-form").replaceChildren(
    renderField(draft.transitions, { key: "style", label: "Style", type: "select", options: Object.keys(TRANSITIONS),
      option_labels: TRANSITIONS }),
    renderField(draft.transitions, { key: "seconds", label: "Length (seconds)", type: "number",
      help: "0.4–0.8 looks smooth. Alerts and takeovers always cut in instantly." }),
    renderField(draft.transitions, { key: "scope", label: "Use between", type: "select", options: ["boards", "all"],
      option_labels: { boards: "Boards only (e.g. Clock → Weather)", all: "Every screen (also between a board's own screens)" } }));
  $("#general-form").replaceChildren(
    renderField(draft, { key: "fallback_board", label: "Show when nothing else has content", type: "select", options: Object.keys(SCHEMA) }),
    renderField(draft.web, { key: "port", label: "Web UI port (restart needed)", type: "number" }),
    renderField(draft.web, { key: "password", label: "Web UI password (blank = none)", type: "password", help: "Browser will prompt; any username works." }));
}

// ---------------------------------------------------------------- save / load
async function reloadConfig() {
  const j = await api("/api/config");
  CONFIG = j.config; SCHEMA = j.schema; FONTS = j.fonts; MAPPINGS = j.hardware_mappings;
  draft = clone(CONFIG); partitionRotation(draft); dirty = false; $("#savebar").classList.add("hidden");
  renderBoards(); renderOtherTabs(); renderAlertForm(); renderSoftware();
}

async function save() {
  const payload = clone(draft);
  delete payload.display.brightness; // controlled live by the slider
  try {
    const j = await api("/api/config", payload);
    toast("Saved");
    await reloadConfig();
    if (j.restart_needed && confirm("Display hardware settings changed. Restart LEDBoard now to apply them?")) restart();
  } catch (e) { toast("Save failed: " + e.message, true); }
}

async function restart() {
  await api("/api/restart", {});
  toast("Restarting…");
  setTimeout(() => location.reload(), 6000);
}

// ---------------------------------------------------------------- wire up
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  history.replaceState(null, "", "#" + b.dataset.tab);
}));
const initialTab = document.querySelector(`.tabs button[data-tab="${location.hash.slice(1)}"]`);
if (initialTab) initialTab.click();

$("#btn-next").onclick = () => api("/api/control", { action: "next" }).then(pollState);
$("#btn-pause").onclick = () => api("/api/control", { action: STATE?.paused ? "resume" : "pause" }).then(pollState);
$("#btn-power").onclick = () => api("/api/control", { action: "power", on: !STATE?.power }).then(pollState);
$("#btn-refresh").onclick = () => api("/api/control", { action: "refresh" }).then(() => toast("Refreshing data…"));
$("#btn-restart").onclick = () => confirm("Restart LEDBoard?") && restart();
$("#btn-save").onclick = save;
$("#toggle-disabled").onclick = () => { showDisabled = !showDisabled; localStorageSet("showDisabled", showDisabled ? "1" : "0"); renderBoards(); };
$("#btn-discard").onclick = reloadConfig;
let brTimer;
$("#brightness").oninput = (e) => {
  $("#bright-val").textContent = e.target.value;
  clearTimeout(brTimer);
  brTimer = setTimeout(() => api("/api/control", { action: "brightness", value: Number(e.target.value) }).then(() => { CONFIG.display.brightness = Number(e.target.value); }), 150);
};
$("#msg-form").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("#msg-text").value.trim();
  if (!text) return;
  try { await api("/api/message", { text, seconds: Number($("#msg-secs").value) || 10, color: $("#msg-color").value }); $("#msg-text").value = ""; toast("Message sent"); }
  catch (err) { toast(err.message, true); }
};
window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

reloadConfig().catch((e) => toast("Could not load config: " + e.message, true));
setInterval(pollFrame, 120);
setInterval(pollState, 1500);

// ---------------------------------------------------------------- installable app (PWA)
// Service workers (and so installing) need a secure origin: https://, or localhost.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((e) => console.warn("service worker:", e));
}
let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $("#btn-install").classList.remove("hidden");
});
$("#btn-install").onclick = async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  $("#btn-install").classList.add("hidden");
};
window.addEventListener("appinstalled", () => toast("LEDBoard installed — find it on your home screen"));
