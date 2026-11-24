// ColdWarDynamic web UI - wires the map, the panels and the server together.
import { $, api, esc, fmtLocal, fmtZ, guarded, PERIOD_ICON, toast } from "./api.js";
import { abbr } from "./glossary.js";
import { showGuide, showHelp } from "./guide.js";
import { openMaker } from "./maker.js";
import { CampaignMap } from "./map.js";
import { renderATO } from "./panels/ato.js";
import { renderDebrief } from "./panels/debrief.js";
import { renderMission } from "./panels/mission.js";
import { renderSquadron } from "./panels/squadron.js";
import { renderWar } from "./panels/war.js";

const state = { cid: null, version: null, o: null, a: null, m: null, selected: null, lastEvent: 0, guided: false };

function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${name}`));
}
document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

const ctx = {
  get cid() { return state.cid; },
  get selected() { return state.selected; },
  autoIngest: () => $("#auto-ingest").checked,
  refresh: (force) => refresh(force),
  showTab,
  selectFlight(id, threats) {
    state.selected = id;
    cmap.select(id, threats);
    document.querySelectorAll("#tab-ato .flt").forEach((c) => c.classList.toggle("selected", c.dataset.id === id));
  },
};
const cmap = new CampaignMap("map", (id) => {
  const f = state.a && state.a.flights.find((x) => x.id === id);
  ctx.selectFlight(id, f && f.threats);
  showTab("ato");
  const card = document.querySelector(`#tab-ato .flt[data-id="${id}"]`);
  if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
});

// ----------------------------------------------------------------------------- header and guidance
function renderHeader(o) {
  const phase = o.phase === "crisis"
    ? `<span class="crisis" title="Pre-war crisis: tension rises until the Warsaw Pact attacks. ROE weapons tight.">CRISIS</span>
       <span class="tension" title="Tension ${o.tension}/100 - war breaks out at 100 (or within a few days anyway)"><i style="width:${o.tension}%"></i></span>`
    : `war day <b>${o.war_day}</b>`;
  $("#clock").innerHTML = `Turn <b>${o.turn}</b> · <b>${fmtZ(o.time)}</b> <span class="muted">(${fmtLocal(o.time)} local)</span> ·
    ${PERIOD_ICON[o.period] || ""} ${esc(o.period)} · ${phase} ·
    <span class="wx" title="${esc(o.weather.text)}">${esc(o.weather.kind)}</span>`;
  $("#btn-skip").disabled = !!o.outcome;
  const b = $("#banner");
  if (o.outcome) {
    b.hidden = false;
    b.className = o.outcome.winner === "blue" ? "blue" : o.outcome.winner ? "" : "draw";
    b.innerHTML = `<h3>${esc(o.outcome.title.toUpperCase())}</h3>${esc(o.outcome.reason)}
      <span class="muted">The full war summary is on the War tab.</span>`;
  } else b.hidden = true;
}

function renderNext(o, a) {
  const p = o.pending;
  const r = (p && p.result) || {};
  let step = 1, title, body, action = "";
  if (o.outcome) {
    step = 3; title = `The war is over: ${esc(o.outcome.title)}`;
    body = "The War tab has the full summary. Start a new campaign to fight it again.";
  } else if (p && r.exists && r.fresh && r.final) {
    step = 3; title = "Results are in";
    body = ctx.autoIngest() ? "Closing the turn - the debrief opens in a moment." : "Press <b>Ingest now</b> on the Mission tab to close the turn.";
  } else if (p && r.exists && r.fresh) {
    step = 2; title = `DCS is flying ${esc(p.callsign)}`;
    body = "Good hunting. When you're done, exit the mission normally - the turn closes by itself.";
  } else if (p) {
    step = 2; title = `Fly ${esc(p.callsign)} in DCS`;
    body = `In DCS: <b>Mission › My Missions › ColdWarDynamic</b>, then pick <span class="mono">${esc(p.miz_path.split(/[\\/]/).pop())}</span>.`;
    action = `<button class="small ghost" data-go="mission">Mission details</button>`;
  } else {
    const flyable = a.flights.filter((f) => f.eligible).length;
    const qra = a.flights.some((f) => f.eligible && f.task === "QRA");
    title = flyable ? (qra ? "QRA: you're on alert" : "Choose a sortie to fly") : "Nothing for your squadron this period";
    body = flyable
      ? `${flyable} of today's sorties are yours - the amber cards in the ${abbr("ATO")}. Or <b>Skip turn</b> to let this period play out.`
      : "Your squadron has no sorties planned (crews resting, base damaged, or night). <b>Skip turn</b> to move on.";
    if (qra) body = `A Warsaw Pact aircraft is heading for the border. Fly the ${abbr("QRA")} sortie: intercept it and shadow it back east. ` + body;
  }
  if (!o.outcome) body += ` <div class="roe ${o.phase === "crisis" ? "tight" : ""}">${abbr("ROE")}: <b>${esc(o.roe)}</b>${o.phase === "crisis"
    ? " - do not fire unless fired upon." : ""}</div>`;
  const steps = ["Choose a sortie", "Fly it in DCS", "Debrief"].map((t, i) =>
    `<li class="${i + 1 < step ? "done" : i + 1 === step ? "now" : ""}"><span>${i + 1}</span>${t}</li>`).join("");
  $("#next").innerHTML = `<ol class="stepper">${steps}</ol><div class="next-body"><b>${title}</b><div>${body}</div>${action}</div>
    <div class="me muted">${esc(o.player.rank)} ${esc(o.player.name)} · ${esc(o.player.squadron)} · ${esc(o.player.base)}</div>`;
  const go = $("#next [data-go]");
  if (go) go.onclick = () => showTab(go.dataset.go);
}

function renderNews(o) {
  // grouped by turn, newest first; each line tagged with its category and source
  const byTurn = [];
  for (const n of o.news) {
    if (!byTurn.length || byTurn[byTurn.length - 1].turn !== n.turn) byTurn.push({ turn: n.turn, time: n.time, items: [] });
    byTurn[byTurn.length - 1].items.push(n);
  }
  $("#tab-news").innerHTML = `<h2>News from the front</h2><div class="news">${byTurn.map((g) =>
    `<div class="news-turn"><div class="mono muted">Turn ${g.turn} - ${fmtZ(g.time)}</div>${g.items.map((n) =>
      `<div class="news-item"><span class="news-cat cat-${esc(n.category || "military")}" title="${esc(n.source || "")}">${esc(n.category || "military")}</span><div>${esc(n.text)}</div></div>`).join("")}</div>`).join("")}</div>`;
}

// ----------------------------------------------------------------------------- data flow
async function refresh(force = false) {
  if (!state.cid) return;
  const v = await api(`/api/campaigns/${state.cid}/version`);
  if (!force && v.version === state.version) {
    if (state.o && state.o.pending) {                  // keep the DCS status live
      state.o.pending.result = v.result;
      renderMission($("#tab-mission"), state.o, ctx);
      renderNext(state.o, state.a);
    }
    return;
  }
  const base = `/api/campaigns/${state.cid}`;
  const [o, a, m, s, d, h] = await Promise.all([api(base), api(`${base}/ato`), api(`${base}/map`),
    api(`${base}/squadron`), api(`${base}/debriefs`), api(`${base}/history`)]);
  const turnChanged = !state.o || state.o.turn !== o.turn || state.o.id !== o.id;
  Object.assign(state, { version: o.version, o, a, m });
  if (turnChanged) {
    state.selected = null;
    cmap.selected = null;
    loadCampaigns(state.cid).catch(() => {});
  }
  renderHeader(o);
  renderNext(o, a);
  renderATO($("#tab-ato"), a, o, ctx);
  renderMission($("#tab-mission"), o, ctx);
  renderWar($("#tab-war"), o, m, h.history);
  renderSquadron($("#tab-squadron"), s);
  renderDebrief($("#tab-debrief"), d, ctx);
  renderNews(o);
  cmap.draw(m);
  if (!state.guided) { state.guided = true; showGuide(false, o); }
}

async function loadCampaigns(select) {
  const r = await api("/api/campaigns");
  const sel = $("#campaign");
  sel.innerHTML = r.campaigns.map((c) => `<option value="${esc(c.id)}">${esc(c.name)} · turn ${c.turn}${c.outcome ? " · over" : ""}</option>`).join("");
  state.cid = select || state.cid || r.current || (r.campaigns[0] && r.campaigns[0].id) || null;
  if (state.cid) sel.value = state.cid;
  if (!r.campaigns.length) {
    $("#next").innerHTML = `<div class="next-body"><b>No campaign yet</b><div>Press <b>New campaign</b> to start the war.</div></div>`;
    openMaker(created);
  }
}

async function pollEvents() {
  try {
    const r = await api(`/api/events?after=${state.lastEvent}`);
    $("#auto-ingest").checked = r.auto_ingest;
    for (const e of r.events) {
      state.lastEvent = Math.max(state.lastEvent, e.id);
      if (e.kind === "ingested" && e.campaign === state.cid) {
        toast(`<b>Mission ingested</b><br>The turn is closed - here is the debrief.`, 10000);
        await refresh(true);
        showTab("debrief");
      } else if (e.kind === "error") toast(`<b>${esc(e.text)}</b>`, 10000);
    }
  } catch { /* server restarting */ }
}

$("#campaign").onchange = (e) => { state.cid = e.target.value; state.version = null; refresh(true); };
$("#auto-ingest").onchange = (e) => api("/api/settings", { method: "POST", body: { auto_ingest: e.target.checked } })
  .then(() => state.o && (renderMission($("#tab-mission"), state.o, ctx), renderNext(state.o, state.a)));
$("#btn-skip").onclick = (e) => {
  if (!confirm("Skip this turn? Every sortie is simulated (a mission you generated but haven't flown is discarded).")) return;
  guarded(e.target, async () => {
    await api(`/api/campaigns/${state.cid}/skip`, { method: "POST" });
    await refresh(true);
    showTab("debrief");
  });
};
$("#btn-help").onclick = showHelp;
$("#btn-guide-again").onclick = () => { $("#dlg-help").close(); showGuide(true, state.o); };
async function created(id) {
  await loadCampaigns(id);
  state.version = null;
  await refresh(true);
  showTab("ato");
}
$("#btn-new").onclick = () => openMaker(created).catch((e) => toast(`<b>Error:</b> ${esc(e.message)}`, 9000));

(async function start() {
  try {
    await loadCampaigns();
    await refresh(true);
  } catch (e) { toast(`<b>Error:</b> ${esc(e.message)}`, 10000); }
  setInterval(() => refresh().catch(() => {}), 4000);
  setInterval(pollEvents, 3000);
  pollEvents();
})();
