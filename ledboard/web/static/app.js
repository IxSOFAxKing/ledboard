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

function drawLeds(bmp) {
  const W = bmp.width, H = bmp.height;
  scratch.width = W; scratch.height = H;
  sctx.drawImage(bmp, 0, 0);
  const px = sctx.getImageData(0, 0, W, H).data;
  const avail = preview.parentElement.clientWidth - 20;
  const s = Math.max(2, Math.floor(avail / W));
  if (preview.width !== W * s) { preview.width = W * s; preview.height = H * s; preview.style.width = W * s + "px"; }
  pctx.fillStyle = "#050505"; pctx.fillRect(0, 0, preview.width, preview.height);
  const r = s * 0.42;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const i = (y * W + x) * 4, R = px[i], G = px[i + 1], B = px[i + 2];
    pctx.fillStyle = (R | G | B) ? `rgb(${R},${G},${B})` : "#141414";
    pctx.beginPath(); pctx.arc(x * s + s / 2, y * s + s / 2, r, 0, 6.2832); pctx.fill();
  }
}

// ---------------------------------------------------------------- state
async function pollState() {
  try { STATE = await api("/api/state"); } catch { return; }
  const st = STATE;
  $("#pill-mode").textContent = st.simulated ? "Simulator (no LED hardware)" : `${st.width}×${st.height} matrix`;
  $("#pill-mode").className = "pill " + (st.simulated ? "sim" : "hw");
  $("#pill-fps").textContent = `${st.fps} fps`;
  const cur = st.current;
  $("#now-board").textContent = st.overlay ? "Message" : cur ? (SCHEMA?.[cur.board]?.title || cur.board) : "—";
  $("#now-slide").textContent = !st.overlay && cur && cur.slides > 1 ? `${cur.slide} / ${cur.slides}` : "";
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
    out.push(renderField(obj, f, deps.has(f.key) ? rerender : null));
  }
  return out;
}

function renderField(obj, f, onDepChange) {
  const key = f.key, val = obj[key];
  const set = (v) => { obj[key] = v; markDirty(); if (onDepChange) onDepChange(); };
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
    case "geocode": return geocodeField(obj, f);
    case "simulate": return simulateField(f);
    case "spotify_auth": return spotifyAuthField(obj);
    default:
      return field(f.label, h("input", { type: "text", value: val ?? "", oninput: (e) => set(e.target.value) }), f.help);
  }
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

// Simulate buttons: queue a celebration on this board and jump to it.
function simulateField(f) {
  const board = Object.keys(SCHEMA).find((n) => SCHEMA[n].fields.includes(f));
  return field("Simulate a score", h("div", { class: "row" }, Object.entries(f.options).map(([kind, label]) =>
    h("button", { class: "small", onclick: async () => {
      try { await api("/api/control", { action: "simulate", board, kind }); toast(`Simulating a ${label.toLowerCase()} — watch the preview`); pollState(); }
      catch (e) { toast(e.message, true); }
    } }, "▶ " + label))), "Plays the celebration now, then this board's games, exactly as it would after a real score.");
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
    { key: "mode", label: "During quiet hours", type: "select", options: ["dim", "off"] },
    { key: "brightness", label: "Dimmed brightness", type: "number" },
  ].map((f) => renderField(draft.schedule, f)));
  draft.scroll = draft.scroll || { speed: 20, gap: 16 };
  $("#defaults-form").replaceChildren(
    renderField(draft.scroll, { key: "speed", label: "Scroll mode speed (pixels per second)", type: "number",
      help: "Used by every board in scroll mode unless that board sets its own speed." }),
    renderField(draft.scroll, { key: "gap", label: "Scroll mode gap between slides (pixels)", type: "number" }));
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
  renderBoards(); renderOtherTabs();
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
