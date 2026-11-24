// Campaign maker (Phase D): pick a year on a slider laid over the service lives of every
// airframe (and the weapons each one carried), see what the war looks like, then create it.
import { $, api, esc, guarded } from "./api.js";

const m = {
  info: null, year: 1976, theatre: "germany", preset: null, airframe: null,
  nations: null, sweden: false, strict: false, mixedWing: true, filter: "all",
  expanded: new Set(), nameTouched: false, previewSeq: 0, timer: null, onCreated: null,
};
const SIDE = { west: "West", east: "East" };

const pctOf = (y) => {
  const [a, b] = m.info.years;
  return ((y - a) / (b + 1 - a)) * 100;
};

export async function openMaker(onCreated) {
  m.onCreated = onCreated;
  const dlg = $("#dlg-maker");
  $("#mk-err").textContent = "";
  if (!m.info) {
    $("#mk-chart").innerHTML = `<p class="muted">Reading your installed aircraft…</p>`;
    dlg.showModal();
    m.info = await api(`/api/maker?theatre=${m.theatre}`);
    buildStatic();
  } else dlg.showModal();
  drawChart();
  schedulePreview(0);
}

// ------------------------------------------------------------------ static parts
function buildStatic() {
  const [a, b] = m.info.years;
  const slider = $("#mk-year");
  Object.assign(slider, { min: a, max: b, value: m.year });
  slider.oninput = () => setYear(+slider.value, true);
  $("#mk-theatres").innerHTML = m.info.theatres.map((t) =>
    `<button type="button" class="mk-theatre ${t.id === m.theatre ? "on" : ""}" data-id="${t.id}"
      ${t.available ? "" : "disabled"} title="${esc(t.blurb)}">${esc(t.name)}<small>${esc(t.map)}${t.coming ? " · coming later" : ""}</small></button>`).join("");
  $("#mk-presets").innerHTML = m.info.presets.map((p) =>
    `<button type="button" class="mk-preset" data-id="${esc(p.id)}" title="${esc(p.blurb)}">${esc(p.name)}</button>`).join("");
  $("#mk-presets").querySelectorAll("button").forEach((btn) => (btn.onclick = () => applyPreset(btn.dataset.id)));
  $("#mk-filter").onchange = (e) => { m.filter = e.target.value; drawChart(); };
  $("#mk-sweden").onchange = (e) => { m.sweden = e.target.checked; m.preset = null; schedulePreview(); drawChart(); };
  $("#mk-strict").onchange = (e) => { m.strict = e.target.checked; m.preset = null; schedulePreview(); drawChart(); };
  $("#mk-mixed").onchange = (e) => { m.mixedWing = e.target.checked; m.preset = null; schedulePreview(); };
  $("#mk-aircraft").onchange = (e) => { m.airframe = e.target.value || null; m.preset = null; schedulePreview(); };
  $("#mk-name").oninput = () => (m.nameTouched = true);
  $("#mk-cancel").onclick = () => $("#dlg-maker").close();
  $("#mk-go").onclick = (e) => { e.preventDefault(); create(e.target); };
  // drag the year line across the chart
  const chart = $("#mk-chart");
  const fromPointer = (ev) => {
    const track = chart.querySelector(".mk-track");
    if (!track) return;
    const r = track.getBoundingClientRect();
    const [lo, hi] = m.info.years;
    const y = Math.floor(lo + ((ev.clientX - r.left) / r.width) * (hi + 1 - lo));
    setYear(Math.max(lo, Math.min(hi, y)), true);
  };
  chart.onpointerdown = (ev) => {
    if (ev.target.closest(".mk-label")) return;
    chart.setPointerCapture(ev.pointerId);
    fromPointer(ev);
    chart.onpointermove = fromPointer;
  };
  chart.onpointerup = () => (chart.onpointermove = null);
}

function applyPreset(id) {
  const p = m.info.presets.find((x) => x.id === id);
  m.preset = id;
  const s = p.setup || { year: 1976 };
  m.sweden = !!s.sweden; m.strict = !!s.strict; m.airframe = s.player_airframe || null; m.nations = null;
  $("#mk-sweden").checked = m.sweden; $("#mk-strict").checked = m.strict;
  if (!m.nameTouched) $("#mk-name").value = p.name;
  setYear(s.year || 1976, false);
  document.querySelectorAll(".mk-preset").forEach((b) => b.classList.toggle("on", b.dataset.id === id));
}

function setYear(y, byHand) {
  if (byHand && m.preset) {
    m.preset = null;
    document.querySelectorAll(".mk-preset").forEach((b) => b.classList.remove("on"));
  }
  m.year = y;
  $("#mk-year").value = y;
  $("#mk-year-big").textContent = y;
  if (!m.nameTouched && !m.preset) $("#mk-name").value = `Central Front ${y}`;
  moveLine();
  markNow();
  schedulePreview();
}

// ------------------------------------------------------------------ the chart
function activeNations() {
  if (m.nations) return new Set(m.nations);
  return new Set(m.info.nations.filter((n) => (n.toggle ? (n.name === "Sweden" ? m.sweden : n.default)
    : n.default_on[0] <= m.year && m.year <= n.default_on[1])).map((n) => n.name));
}

function drawChart() {
  const [a, b] = m.info.years;
  const ticks = [];
  for (let y = a; y <= b; y += 5) ticks.push(`<span style="left:${pctOf(y)}%">${y}</span>`);
  const on = activeNations();
  let html = `<div class="mk-row mk-axis"><div class="mk-label"></div><div class="mk-track">${ticks.join("")}</div></div>`;
  for (const side of ["west", "east"]) {
    html += `<div class="mk-side">${SIDE[side]}</div>`;
    for (const bar of m.info.bars.filter((x) => x.side === side)) {
      if (m.strict && bar.stand_in) continue;
      const inYear = bar.start <= m.year && m.year <= bar.end;
      if (m.filter === "mine" && !bar.owned.length) continue;
      if (m.filter === "now" && !inYear) continue;
      const off = !bar.nations.some((n) => on.has(n));
      const cls = [bar.owned.length ? "owned" : "ai", bar.stand_in ? "standin" : "", bar.available ? "" : "missing",
        off ? "off" : "", inYear ? "now" : ""].join(" ");
      const left = pctOf(bar.start), width = pctOf(bar.end + 1) - left;
      const ticksW = bar.weapons.map((w) =>
        `<i class="mk-wtick" style="left:${((pctOf(Math.max(w.year, bar.start)) - left) / width) * 100}%" title="${esc(w.family)} (${w.year})"></i>`).join("");
      const tip = `${bar.name}: ${bar.start}-${bar.end} · ${bar.nations.join(", ")}` +
        (bar.stand_in ? ` · stand-in: flown by the ${bar.dcs_type}` : "") +
        (bar.owned.length ? ` · you fly it (${bar.owned.join(", ")})` : bar.available ? " · AI only" : " · needs a mod you don't have");
      html += `<div class="mk-row ${cls}" data-id="${esc(bar.id)}">
        <div class="mk-label" title="${esc(tip)}">${bar.owned.length ? "★ " : ""}${esc(bar.name)}</div>
        <div class="mk-track"><div class="mk-bar" style="left:${left}%;width:${width}%" title="${esc(tip)}">${ticksW}</div></div></div>`;
      if (m.expanded.has(bar.id)) {
        for (const w of bar.weapons) {
          const wl = pctOf(Math.max(w.year, bar.start));
          html += `<div class="mk-row mk-sub ${w.year <= m.year && inYear ? "now" : ""}"><div class="mk-label">${esc(w.family)}</div>
            <div class="mk-track"><div class="mk-wbar" style="left:${wl}%;width:${pctOf(bar.end + 1) - wl}%" title="${esc(w.family)} from ${w.year}"></div></div></div>`;
        }
      }
    }
  }
  html += `<div class="mk-line" id="mk-line"></div>`;
  const chart = $("#mk-chart");
  chart.innerHTML = html;
  chart.querySelectorAll(".mk-row[data-id] .mk-label").forEach((el) => (el.onclick = () => {
    const id = el.parentElement.dataset.id;
    m.expanded.has(id) ? m.expanded.delete(id) : m.expanded.add(id);
    drawChart();
  }));
  moveLine();
}

function moveLine() {
  const line = $("#mk-line"), track = $("#mk-chart .mk-track");
  if (!line || !track) return;
  const chart = $("#mk-chart");
  const x = track.offsetLeft + track.offsetWidth * ((pctOf(m.year) + pctOf(m.year + 1)) / 2) / 100;
  line.style.left = `${x}px`;
  line.style.height = `${chart.scrollHeight}px`;
  line.dataset.year = m.year;
}

function markNow() {
  const on = activeNations();
  for (const row of document.querySelectorAll("#mk-chart .mk-row[data-id]")) {
    const bar = m.info.bars.find((b) => b.id === row.dataset.id);
    row.classList.toggle("now", bar.start <= m.year && m.year <= bar.end);
    row.classList.toggle("off", !bar.nations.some((n) => on.has(n)));
  }
  if (m.filter === "now") drawChart();
}

// ------------------------------------------------------------------ preview
function request() {
  return { theatre: m.theatre, year: m.year, nations: m.nations, sweden: m.sweden, strict: m.strict,
    mixed_wing: m.mixedWing, player_airframe: m.airframe, seed: +$("#mk-seed").value || 1976 };
}

function schedulePreview(ms = 180) {
  clearTimeout(m.timer);
  m.timer = setTimeout(runPreview, ms);
}

async function runPreview() {
  const seq = ++m.previewSeq;
  let p;
  try { p = await api("/api/maker/preview", { method: "POST", body: request() }); }
  catch (e) { $("#mk-err").textContent = e.message; return; }
  if (seq !== m.previewSeq) return;                      // a newer request is on its way
  renderPreview(p);
}

function nationBoxes(p) {
  const on = new Set(p.nations);
  return ["west", "east"].map((side) => `<div class="mk-nations"><b>${SIDE[side]}</b>${
    m.info.nations.filter((n) => n.side === side).map((n) =>
      `<label class="check"><input type="checkbox" data-n="${esc(n.name)}" ${on.has(n.name) ? "checked" : ""}> ${esc(n.name)}</label>`).join("")}</div>`).join("");
}

function renderPreview(p) {
  const opts = p.player_options;
  if (m.airframe && !opts.some((o) => o.id === m.airframe)) m.airframe = null;
  $("#mk-aircraft").innerHTML = `<option value="">Automatic</option>` + opts.map((o) =>
    `<option value="${esc(o.id)}" ${o.id === m.airframe ? "selected" : ""}>${esc(o.name)} · ${esc(o.nation)}${o.stand_in ? " (stand-in)" : ""}</option>`).join("");
  $("#mk-go").disabled = !p.ok;
  const out = $("#mk-preview");
  if (!p.ok) {
    out.innerHTML = `<div class="mk-warn">${esc(p.error)}</div><h4>Nations</h4>${nationBoxes(p)}`;
    wireNations();
    return;
  }
  const sq = (x) => `<li class="${x.home ? "home" : x.wing ? "wing" : ""}">${x.home ? "★ " : x.wing ? "☆ " : ""}<b>${esc(x.airframe)}</b>
      <span class="muted">${esc(x.name)}, ${esc(x.nation)}${x.stand_in ? ` · flown by the ${esc(x.type)}` : ""}</span></li>`;
  const wpn = (w) => `<div class="mk-wpn"><b>${esc(w.airframe)}</b> ${w.families.length
    ? w.families.map((f) => `<span class="chip" title="from ${f.year}">${esc(f.family)}</span>`).join("")
    : `<span class="muted">guns only</span>`}</div>`;
  const gr = (side) => p.ground[side].map((g) => `${esc(g.types.join(", "))} <span class="muted">(${esc(g.role)})</span>`).join("<br>");
  out.innerHTML = `
    <h4>Your wing <span class="muted">(★ home squadron · ☆ also yours to fly)</span></h4><ul class="mk-list">${p.wing.map(sq).join("")}</ul>
    <h4>What you can carry in ${p.year}</h4>${p.weapons.map(wpn).join("")}
    <div class="mk-cols">
      <div><h4 class="blue">NATO squadrons</h4><ul class="mk-list">${p.squadrons.west.filter((x) => !x.home && !x.wing).map(sq).join("")}</ul></div>
      <div><h4 class="red">Warsaw Pact regiments</h4><ul class="mk-list">${p.squadrons.east.map(sq).join("")}</ul></div>
    </div>
    <div class="mk-cols">
      <div><h4>NATO air defence</h4><p>${esc(p.air_defence.west.join(", ") || "none")}</p><h4>NATO ground forces</h4><p>${gr("west")}</p></div>
      <div><h4>Warsaw Pact air defence</h4><p>${esc(p.air_defence.east.join(", ") || "none")}</p><h4>Warsaw Pact ground forces</h4><p>${gr("east")}</p></div>
    </div>
    <h4>SAM sites on the map</h4><p>${esc(p.sites.join(" · "))}</p>
    ${p.notes.map((n) => `<p class="mk-note">${esc(n)}</p>`).join("")}
    <h4>Nations</h4>${nationBoxes(p)}`;
  wireNations();
}

function wireNations() {
  document.querySelectorAll("#mk-preview .mk-nations input").forEach((cb) => (cb.onchange = () => {
    m.nations = [...document.querySelectorAll("#mk-preview .mk-nations input:checked")].map((x) => x.dataset.n);
    m.sweden = m.nations.includes("Sweden");
    $("#mk-sweden").checked = m.sweden;
    m.preset = null;
    drawChart();
    schedulePreview();
  }));
}

// ------------------------------------------------------------------ create
async function create(btn) {
  const body = { name: $("#mk-name").value.trim(), pilot: $("#mk-pilot").value.trim() || "Player",
    seed: +$("#mk-seed").value || 1976, force: $("#mk-force").checked, crisis: $("#mk-crisis").checked };
  if (m.preset) body.preset = m.preset;
  else body.era = request();
  await guarded(btn, async () => {
    try {
      const r = await api("/api/campaigns", { method: "POST", body });
      $("#dlg-maker").close();
      if (m.onCreated) await m.onCreated(r.id);
    } catch (err) { $("#mk-err").textContent = err.message; }
  });
}
