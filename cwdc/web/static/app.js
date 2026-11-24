"use strict";
// ColdWarDynamic web UI. Talks to the local FastAPI server (cwdc serve).

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const fmtTime = (iso) => new Date(iso).toLocaleString("en-GB",
  { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const pct = (x) => `${Math.round(x * 100)}%`;

const state = { cid: null, version: null, overview: null, ato: null, selected: null, lastEvent: 0 };

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${r.status} ${r.statusText}`);
  return data;
}

function toast(html, ms = 6000) {
  const t = $("#toast");
  t.innerHTML = html;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}

async function guarded(btn, fn) {
  if (btn) btn.disabled = true;
  try { await fn(); } catch (e) { toast(`<b>Error:</b> ${esc(e.message)}`, 9000); }
  finally { if (btn) btn.disabled = false; }
}

// ----------------------------------------------------------------------------- tabs
function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${name}`));
}
document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

// ----------------------------------------------------------------------------- map
const map = L.map("map", { zoomControl: true, preferCanvas: true }).setView([50.6, 10.2], 7);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 12, opacity: 0.55,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);
const layers = {
  front: L.layerGroup().addTo(map),
  airbases: L.layerGroup().addTo(map),
  objectives: L.layerGroup().addTo(map),
  sams: L.layerGroup().addTo(map),
  flights: L.layerGroup().addTo(map),
  enemyFlights: L.layerGroup(),
  supply: L.layerGroup(),
  cap: L.layerGroup().addTo(map),
};
L.control.layers(null, {
  "Front line": layers.front, "Airbases": layers.airbases, "Objectives": layers.objectives,
  "SAM coverage": layers.sams, "NATO flights": layers.flights,
  "Warsaw Pact flights (intel)": layers.enemyFlights, "Supply network": layers.supply,
  "CAP stations": layers.cap,
}, { collapsed: true }).addTo(map);

const SIDE = { blue: "#4c9be8", red: "#e5534b" };
const KIND_R = { sam: 6, ewr: 6, airbase: 0, armor: 5, artillery: 5, depot: 5, rail: 5, convoy: 4, command: 5, aaa: 4 };
const flightLines = {};

function drawMap(m) {
  Object.values(layers).forEach((l) => l.clearLayers());
  for (const k in flightLines) delete flightLines[k];

  L.polyline(m.border, { color: "#9aa4ad", weight: 1.5, dashArray: "4 6", opacity: 0.8 })
    .bindTooltip("Inner German border").addTo(layers.front);
  L.polyline(m.front, { color: "#e0a43a", weight: 3.5, opacity: 0.95 }).bindTooltip("Front line").addTo(layers.front);
  for (const s of m.sectors) {
    const off = s.offset_km;
    const where = Math.abs(off) < 0.5 ? "on the border" : off > 0 ? `+${off.toFixed(0)} km` : `${off.toFixed(0)} km`;
    const att = s.attacker ? `<span class="att-${s.attacker}">${s.attacker === "red" ? "WP attack" : "NATO attack"}</span>` : "static";
    L.marker(s.at, { icon: L.divIcon({ className: "lbl sector", html: `${esc(s.name)}<br>${where} · ${att}`, iconSize: null }),
      interactive: false }).addTo(layers.front);
  }

  for (const ab of m.airbases) {
    const cls = `ab ${ab.side}${ab.damage > 0 ? " damaged" : ""}`;
    L.marker(ab.at, { icon: L.divIcon({ className: cls, html: "✈", iconSize: [22, 22], iconAnchor: [11, 11] }),
      zIndexOffset: 500 })
      .bindPopup(`<b>${esc(ab.name)}</b><br>${ab.damage > 0 ? `damage ${pct(ab.damage)}` : "operational"}` +
        (ab.squadrons.length ? `<br>${ab.squadrons.map(esc).join("<br>")}` : ""))
      .bindTooltip(esc(ab.name)).addTo(layers.airbases);
  }

  for (const o of m.objectives) {
    if (o.kind === "airbase") continue;
    const dead = o.alive === 0;
    const mk = L.circleMarker(o.at, {
      radius: KIND_R[o.kind] || 5, color: SIDE[o.side], weight: 1.5,
      fillColor: SIDE[o.side], fillOpacity: dead ? 0.05 : 0.25 + 0.6 * (o.alive / o.total), opacity: dead ? 0.4 : 1,
    }).bindPopup(`<b>${esc(o.name)}</b><br>${o.kind} · ${o.alive}/${o.total} units${dead ? " · destroyed" : ""}`)
      .bindTooltip(esc(o.name));
    mk.addTo(layers.objectives);
    if (o.kind === "sam" && !dead && o.threat_m) {
      L.circle(o.at, { radius: o.threat_m, color: SIDE[o.side], weight: 1.5, dashArray: "5 5", opacity: 0.9,
        fillColor: SIDE[o.side], fillOpacity: 0.07, interactive: false })
        .addTo(layers.sams);
    }
  }

  for (const [side, pts] of Object.entries(m.cap_stations)) {
    for (const p of pts) {
      L.circleMarker(p, { radius: 9, color: SIDE[side], weight: 1, fill: false, dashArray: "2 3" })
        .bindTooltip(`${side === "blue" ? "NATO" : "WP"} CAP station`).addTo(layers.cap);
    }
  }

  for (const l of m.supply_links) {
    const col = l.damaged > 0.05 ? `hsl(${120 - 120 * l.damaged}, 70%, 50%)` : (l.side === "blue" ? "#6fa8dc" : "#d98080");
    L.polyline([l.a, l.b], { color: col, weight: 1 + 3 * Math.min(1, l.capacity / 250), opacity: 0.75 })
      .bindTooltip(`supply ${l.flow}/${l.capacity}${l.damaged > 0.05 ? ` · ${pct(l.damaged)} damaged` : ""}`)
      .addTo(layers.supply);
  }
  for (const n of m.supply_nodes) {
    L.circleMarker(n.at, { radius: 3, color: SIDE[n.side], weight: 1, fillOpacity: 0.8 })
      .bindTooltip(`${esc(n.name)}: ${n.stock}/${n.capacity}`).addTo(layers.supply);
  }

  for (const f of m.flights) {
    const line = L.polyline([f.from, f.to], {
      color: SIDE[f.side], weight: f.player ? 4 : 2, opacity: f.side === "blue" ? 0.85 : 0.6,
      dashArray: f.side === "red" ? "6 6" : (f.task === "CAP" ? "2 5" : null),
    }).bindTooltip(`${esc(f.callsign)} · ${esc(f.task)}`);
    line.baseWeight = f.player ? 4 : 2;
    if (f.side === "blue") line.on("click", () => selectFlight(f.id));
    line.addTo(f.side === "blue" ? layers.flights : layers.enemyFlights);
    flightLines[f.id] = line;
  }
  highlightSelected();
}

function highlightSelected() {
  for (const [id, line] of Object.entries(flightLines)) {
    line.setStyle({ weight: id === state.selected ? 5 : line.baseWeight });
    if (id === state.selected) line.bringToFront();
  }
}

function selectFlight(id) {
  state.selected = id;
  highlightSelected();
  document.querySelectorAll("#tab-ato tr[data-id]").forEach((tr) => tr.classList.toggle("selected", tr.dataset.id === id));
  const line = flightLines[id];
  if (line) map.fitBounds(line.getBounds(), { padding: [60, 60], maxZoom: 9 });
}

// ----------------------------------------------------------------------------- panels
function renderHeader(o) {
  $("#clock").innerHTML = `Turn <b>${o.turn}</b> · <b>${fmtTime(o.time)}</b>Z · ${esc(o.period)} · war day <b>${o.war_day}</b>`;
  $("#btn-skip").disabled = !!o.outcome;
  const b = $("#banner");
  if (o.outcome) {
    b.hidden = false;
    b.className = o.outcome.winner === "blue" ? "blue" : o.outcome.winner ? "" : "draw";
    b.innerHTML = `<h3>${esc(o.outcome.title.toUpperCase())}</h3>${esc(o.outcome.reason)} ` +
      `<span class="muted">The full war summary is on the War tab.</span>`;
  } else b.hidden = true;
}

function renderATO(a, o) {
  const pend = o.pending;
  let html = `<h2>Air tasking order <span class="muted">turn ${a.turn}, ${fmtTime(a.time)}Z</span></h2>`;
  if (o.outcome) html += `<p class="muted">The war is over. No more sorties.</p>`;
  html += `<table><tr><th>Flight</th><th>Task</th><th>Mission</th><th></th></tr>`;
  for (const f of a.flights) {
    const cls = [f.eligible ? "eligible" : "", f.assigned ? "assigned" : "", f.id === state.selected ? "selected" : "", "clickable"].join(" ");
    const crews = f.crews.map((c) => c.map(esc).join(" / ")).join("<br>");
    const fly = f.eligible && !o.outcome ? `<div class="flycell">
        <select data-start="${f.id}" title="Start">
          <option value="runway">Runway</option><option value="ramp">Ramp</option><option value="air">Air</option>
        </select>
        <button class="small" data-fly="${f.id}">${f.assigned ? "Regenerate" : "Fly"}</button>
        <button class="small ghost" data-bot="${f.id}" title="AI-only test mission">Bot</button></div>` : "";
    html += `<tr class="${cls}" data-id="${f.id}">
      <td><b>${esc(f.callsign)}</b><div class="muted mono">${f.id} · ${f.aircraft}× ${esc(f.type)}</div>
          <div class="muted">${esc(f.squadron)}</div></td>
      <td>${esc(f.task)}${f.supports ? `<div class="muted">supports ${esc(f.supports)}</div>` : ""}
          <div class="muted mono">${esc(f.loadout)}</div></td>
      <td>${esc(f.mission)}<details><summary class="muted">crews</summary><pre>${crews}</pre></details></td>
      <td>${fly}</td></tr>`;
  }
  html += `</table><p class="muted">Intelligence expects ~${a.enemy_expected} Warsaw Pact flights this period.
    Amber rows are flights you can fly. ${pend ? `Pending: <b>${esc(pend.callsign)}</b> (see Mission).` : ""}</p>`;
  $("#tab-ato").innerHTML = html;
  $("#tab-ato").querySelectorAll("tr[data-id]").forEach((tr) =>
    tr.addEventListener("click", (e) => { if (!e.target.closest("button,select,details")) selectFlight(tr.dataset.id); }));
  $("#tab-ato").querySelectorAll("[data-fly],[data-bot]").forEach((btn) => btn.addEventListener("click", () => {
    const id = btn.dataset.fly || btn.dataset.bot;
    const start = $(`[data-start="${id}"]`).value;
    guarded(btn, async () => {
      const r = await api(`/api/campaigns/${state.cid}/fly`, { method: "POST", body: { flight_id: id, start, bot: !!btn.dataset.bot } });
      toast(`Mission written: <b>${esc(r.file)}</b><br>In DCS: Mission › My Missions › ColdWarDynamic.` +
        (r.notes.length ? `<br><span class="muted">${r.notes.map(esc).join("<br>")}</span>` : ""), 9000);
      await refresh(true);
      showTab("mission");
    });
  }));
}

function renderMission(o) {
  const p = o.pending;
  if (!p) {
    $("#tab-mission").innerHTML = `<h2>Mission</h2><p class="muted">No mission generated this turn.
      Pick a flight in the ATO and press <b>Fly</b> (or <b>Bot</b> for an AI-only test), or skip the turn.</p>`;
    return;
  }
  const r = p.result || {};
  let status, dot;
  if (r.exists && r.fresh && r.final) { dot = "ok"; status = `Result received (${r.events} events) - ready to ingest`; }
  else if (r.exists && r.fresh) { dot = "wait"; status = `Mission running in DCS (${r.events} events so far)…`; }
  else { dot = "wait"; status = "Waiting for DCS - fly the mission and exit normally"; }
  const auto = $("#auto-ingest").checked;
  $("#tab-mission").innerHTML = `
    <h2>${p.bot ? "Bot mission" : "Your mission"}: ${esc(p.callsign)}</h2>
    <div class="status"><span class="dot ${dot}"></span><span>${esc(status)}</span></div>
    <p class="muted">${auto ? "Auto-ingest is on: the turn closes by itself when you leave the mission." :
      "Auto-ingest is off: press Ingest after flying."}</p>
    <div class="row">
      <button id="btn-ingest">Ingest now</button>
      <button id="btn-ingest-log" class="ghost" title="Rebuild the result from Saved Games\\DCS\\Logs\\dcs.log">Ingest from dcs.log</button>
    </div>
    <h3>In DCS</h3>
    <p>Mission › My Missions › ColdWarDynamic › <span class="mono">${esc(p.miz_path.split(/[\\/]/).pop())}</span></p>
    <p class="muted">Your in-game pilot name comes from your DCS Logbook profile. When the mission ends,
      DCS asks about saving a track or debrief - you can just exit.</p>
    <h3>Briefing</h3><pre>${esc((p.briefing || []).join("\n"))}</pre>
    ${p.others.length ? `<h3>Also in this mission</h3><p>${p.others.map(esc).join(", ")}</p>` : ""}`;
  const ingest = (fromLog) => (e) => guarded(e.target, async () => {
    const res = await api(`/api/campaigns/${state.cid}/ingest`, { method: "POST", body: { from_log: fromLog } });
    toast(`Turn closed.${res.notes.length ? "<br>" + res.notes.map(esc).join("<br>") : ""}`);
    await refresh(true);
    showTab("debrief");
  });
  $("#btn-ingest").onclick = ingest(false);
  $("#btn-ingest-log").onclick = ingest(true);
}

function renderWar(o) {
  let html = "";
  if (o.outcome) html += `<h2>${esc(o.outcome.title)}</h2><pre>${esc(o.outcome.summary.join("\n"))}</pre>`;
  else html += `<h2>War situation</h2><pre>${esc(o.situation.map((l) => l.trim()).join("\n"))}</pre>`;
  html += `<h3>Ground war</h3><table><tr><th>Sector</th><th>Front</th><th>Power (WP / NATO)</th></tr>`;
  for (const s of o.sectors) {
    const tot = Math.max(1, s.red + s.blue);
    const off = Math.abs(s.offset_km) < 0.5 ? "border" : s.offset_km > 0 ? `+${s.offset_km.toFixed(0)} km` : `${s.offset_km.toFixed(0)} km`;
    const att = s.attacker ? `<span class="tag ${s.attacker}">${s.attacker === "red" ? "WP attack" : "NATO attack"}</span>` : `<span class="tag">static</span>`;
    const forms = s.formations.map((f) => `<tr><td class="muted">${f.side === "red" ? "WP" : "NATO"}</td>` +
      `<td>${esc(f.name)}</td><td class="mono">${f.strength}/${f.max} · sup ${pct(f.supply)} · rdy ${pct(f.readiness)}</td></tr>`).join("");
    html += `<tr><td><b>${esc(s.name)}</b><details><summary class="muted">formations</summary>
      <table>${forms}</table></details></td><td>${off}<br>${att}</td>
      <td><div class="vs"><span class="r" style="width:${100 * s.red / tot}%"></span><span class="b" style="width:${100 * s.blue / tot}%"></span></div>
      <span class="mono muted">${s.red} / ${s.blue}</span></td></tr>`;
  }
  html += `</table>`;
  if (o.reinforcements.length)
    html += `<p class="muted">Expected NATO reinforcements: ${o.reinforcements.map((r) => `${esc(r.name)} (turn ${r.turn})`).join(", ")}</p>`;
  html += `<h3>Air forces</h3><table><tr><th>Squadron</th><th>Base</th><th>Ready</th><th>Repair</th><th>Lost</th><th>Crews</th></tr>`;
  for (const q of o.squadrons)
    html += `<tr><td><span class="tag ${q.side}">${q.side === "blue" ? "NATO" : "WP"}</span> ${esc(q.name)}
      <div class="muted mono">${esc(q.type)}</div></td><td>${esc(q.base)}</td><td>${q.ready}</td><td>${q.repair}</td>
      <td>${q.lost}</td><td>${q.pilots}${q.wsos != null ? ` + ${q.wsos} WSO` : ""}</td></tr>`;
  html += `</table><h3>Airfields</h3><pre>${esc(o.airfields.map((a) => a.state).join("\n"))}</pre>`;
  html += `<h3>Supply</h3><pre>${esc(o.supply.join("\n"))}</pre>`;
  $("#tab-war").innerHTML = html;
}

function renderSquadron(s) {
  const crewTable = (rows) => `<table><tr><th>Name</th><th>Status</th><th>Fatigue</th><th>Sorties</th><th>Kills A/G</th></tr>` +
    rows.map((p) => `<tr${p.is_player ? ' class="assigned"' : ""}><td>${esc(p.rank)} ${esc(p.name)}${p.is_player ? ' <span class="tag amber">you</span>' : ""}</td>
      <td>${esc(p.status)}</td><td><div class="bar fatigue" title="${esc(p.rest)}"><i style="width:${pct(p.fatigue)}"></i></div>
      <span class="muted">${esc(p.rest)}</span></td><td>${p.sorties}</td><td>${p.air_kills} / ${p.ground_kills}</td></tr>`).join("") + `</table>`;
  const afs = s.airframes.map((a) => `<tr><td class="mono">${esc(a.id)}</td><td>${esc(a.status)}${a.status === "damaged" ? ` (${a.repair_turns} turns)` : ""}</td>
    <td><div class="bar"><i style="width:${a.quality}%"></i></div></td><td>${a.sorties}</td></tr>`).join("");
  $("#tab-squadron").innerHTML = `<h2>${esc(s.name)} <span class="muted">${esc(s.type)} @ ${esc(s.base)}</span></h2>
    <h3>Pilots</h3>${crewTable(s.pilots)}
    ${s.wsos.length ? `<h3>WSOs</h3>${crewTable(s.wsos)}` : ""}
    <h3>Aircraft</h3><table><tr><th>Tail</th><th>Status</th><th>Quality</th><th>Sorties</th></tr>${afs}</table>`;
}

function renderDebriefs(d) {
  let html = `<h2>Debriefs</h2>`;
  if (!d.debriefs.length) html += `<p class="muted">No turns closed yet.</p>`;
  d.debriefs.forEach((x, i) => {
    html += `<details${i === 0 ? " open" : ""}><summary>Turn ${x.turn} · ${fmtTime(x.time)}Z
      ${x.flown_in_dcs ? '<span class="tag amber">flown in DCS</span>' : ""}</summary><pre>${esc(x.lines.slice(1).join("\n"))}</pre></details>`;
  });
  if (d.snapshots.length > 1) {
    html += `<h3>Roll back</h3><div class="row"><select id="rb-turn">${d.snapshots.slice().reverse()
      .map((t) => `<option value="${t}">start of turn ${t}</option>`).join("")}</select>
      <button id="btn-rb" class="warn">Roll back</button></div>
      <p class="muted">Restores the campaign exactly as it was at the start of that turn. Later turns stay on disk.</p>`;
  }
  $("#tab-debrief").innerHTML = html;
  const rb = $("#btn-rb");
  if (rb) rb.onclick = () => {
    const t = +$("#rb-turn").value;
    if (!confirm(`Roll the campaign back to the start of turn ${t}?`)) return;
    guarded(rb, async () => { await api(`/api/campaigns/${state.cid}/rollback`, { method: "POST", body: { turn: t } });
      toast(`Rolled back to turn ${t}.`); await refresh(true); });
  };
}

function renderNews(o) {
  $("#tab-news").innerHTML = `<h2>News</h2><table>${o.news.map((n) =>
    `<tr><td class="mono muted" style="white-space:nowrap">${fmtTime(n.time)}</td><td>${esc(n.text)}</td></tr>`).join("")}</table>`;
}

// ----------------------------------------------------------------------------- data flow
async function refresh(force = false) {
  if (!state.cid) return;
  const v = await api(`/api/campaigns/${state.cid}/version`);
  if (!force && v.version === state.version) {
    if (state.overview && state.overview.pending) {     // keep the result indicator live
      state.overview.pending.result = v.result;
      renderMission(state.overview);
    }
    return;
  }
  const [o, a, m, s, d] = await Promise.all([
    api(`/api/campaigns/${state.cid}`), api(`/api/campaigns/${state.cid}/ato`),
    api(`/api/campaigns/${state.cid}/map`), api(`/api/campaigns/${state.cid}/squadron`),
    api(`/api/campaigns/${state.cid}/debriefs`),
  ]);
  const turnChanged = !state.overview || state.overview.turn !== o.turn || state.overview.id !== o.id;
  state.version = o.version; state.overview = o; state.ato = a;
  if (turnChanged) loadCampaigns(state.cid).catch(() => {});      // keep the selector's turn label current
  renderHeader(o); renderATO(a, o); renderMission(o); renderWar(o); renderSquadron(s); renderDebriefs(d); renderNews(o);
  drawMap(m);
}

async function loadCampaigns(select) {
  const r = await api("/api/campaigns");
  const sel = $("#campaign");
  sel.innerHTML = r.campaigns.map((c) => `<option value="${esc(c.id)}">${esc(c.name)} · turn ${c.turn}${c.outcome ? " · over" : ""}</option>`).join("");
  state.cid = select || state.cid || r.current || (r.campaigns[0] && r.campaigns[0].id) || null;
  if (state.cid) sel.value = state.cid;
  if (!r.campaigns.length) {
    $("#tab-ato").innerHTML = `<h2>No campaign yet</h2><p>Start one with <b>New campaign</b>.</p>`;
    $("#dlg-new").showModal();
  }
}

async function pollEvents() {
  try {
    const r = await api(`/api/events?after=${state.lastEvent}`);
    $("#auto-ingest").checked = r.auto_ingest;
    for (const e of r.events) {
      state.lastEvent = Math.max(state.lastEvent, e.id);
      if (e.kind === "ingested" && e.campaign === state.cid) {
        toast(`<b>${esc(e.text)}</b><br>The turn is closed - see Debrief.`, 10000);
        await refresh(true);
        showTab("debrief");
      } else if (e.kind === "error") toast(`<b>${esc(e.text)}</b>`, 10000);
    }
  } catch (e) { /* server restarting */ }
}

$("#campaign").onchange = (e) => { state.cid = e.target.value; state.version = null; state.selected = null; refresh(true); };
$("#auto-ingest").onchange = (e) => api("/api/settings", { method: "POST", body: { auto_ingest: e.target.checked } })
  .then(() => state.overview && renderMission(state.overview));
$("#btn-skip").onclick = (e) => {
  if (!confirm("Skip this turn? Every sortie is simulated (a pending mission is discarded).")) return;
  guarded(e.target, async () => { await api(`/api/campaigns/${state.cid}/skip`, { method: "POST" });
    await refresh(true); showTab("debrief"); });
};
$("#btn-new").onclick = () => { $("#new-err").textContent = ""; $("#dlg-new").showModal(); };
$("#new-go").onclick = (e) => {
  e.preventDefault();
  const f = new FormData($("#form-new"));
  guarded(e.target, async () => {
    try {
      const r = await api("/api/campaigns", { method: "POST", body: {
        name: f.get("name"), pilot: f.get("pilot"), seed: +f.get("seed") || 1976, force: f.get("force") === "on" } });
      $("#dlg-new").close();
      await loadCampaigns(r.id);
      await refresh(true);
      showTab("ato");
    } catch (err) { $("#new-err").textContent = err.message; }
  });
};

(async function start() {
  try {
    await loadCampaigns();
    await refresh(true);
  } catch (e) { toast(`<b>Error:</b> ${esc(e.message)}`, 10000); }
  setInterval(() => refresh().catch(() => {}), 4000);
  setInterval(pollEvents, 3000);
  pollEvents();
})();
