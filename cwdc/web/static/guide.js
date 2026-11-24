// First-run guide and the help / glossary dialog.
import { $, esc } from "./api.js";
import { TERMS } from "./glossary.js";
import { symbolSVG } from "./symbols.js";

const KEY = "cwdc.guide.v1";

const pages = (who, when, aircraft, wing, setting) => [
  {
    title: "Welcome to the Cold War",
    body: `<p>It is ${when}. ${setting} You fly the
      ${aircraft} in the <b>${who}</b>.${wing ? ` Your wing also flies the ${wing} - their sorties are yours to fly too.` : ""}</p>
      <p>The campaign opens in a <b>crisis</b>: you fly QRA and border patrols, weapons tight, while
      tension rises - firing first has consequences. Within a few days the Warsaw Pact attacks, and the
      war begins.</p>
      <p>The campaign runs in <b>6-hour turns</b>. Each turn both air forces plan their sorties. You may fly
      one of yours in DCS; everything else is simulated, and the results shape the next turn.</p>`,
  },
  {
    title: "Reading the map",
    body: `<p class="guide-syms">${symbolSVG({ side: "blue", kind: "armor", echelon: "division", size: 40 })}
      <span><b>Blue rectangles are NATO</b> (your side), ${symbolSVG({ side: "red", kind: "armor", echelon: "division", size: 40 })}
      <b>red diamonds are the Warsaw Pact</b>.</span></p>
      <p>The <b style="color:#e0a43a">amber line</b> is the front; the dashed line is the pre-war border.
      Hatched ground has changed hands. Arrows show who is attacking where - bigger arrow, better odds.</p>
      <p>Red dashed circles are <b>SAM danger rings</b>. <b>Hover or tap anything</b> for an explanation,
      and open the <b>Legend</b> (bottom left) for every symbol.</p>`,
  },
  {
    title: "Choosing and flying a sortie",
    body: `<ol><li><b>ATO tab</b>: today's sorties. Cards marked <span class="tag amber">You can fly this</span>
      are your squadron's. Each shows the distance, the threats and why it matters.</li>
      <li>Pick a start (runway, ramp or in the air) and press <b>Fly</b>. The mission is written into DCS:
      <i>Mission › My Missions › ColdWarDynamic</i>.</li>
      <li>Fly it and <b>exit the mission normally</b>. With Auto-ingest on, the turn closes by itself and the
      <b>Debrief</b> tab opens.</li></ol>
      <p>Not flying this turn? <b>Skip turn</b> lets everything play out without you.</p>`,
  },
  {
    title: "How the war is won",
    body: `<p><b>The Warsaw Pact wins</b> if it reaches the River Main (Frankfurt, Aschaffenburg, Würzburg),
      breaks through 100 km anywhere, or wears NATO air power down below 30%.</p>
      <p><b>NATO wins</b> if, after day 7, the border is restored everywhere. Otherwise the superpowers agree a
      ceasefire on day 21 and the result depends on how much ground has been lost.</p>
      <p>Your sorties count: close air support and strikes weaken enemy divisions, interdiction starves them,
      fighters protect the bombers. The <b>War</b> tab tracks all of it.</p>`,
  },
];

export function showGuide(force = false, overview = null) {
  try { if (!force && localStorage.getItem(KEY)) return; } catch { /* private mode: show it */ }
  const dlg = $("#dlg-guide");
  let i = 0;
  const p0 = overview && overview.player;
  const when = overview ? new Date(overview.time).toLocaleString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" })
    : "August 1976";
  const PAGES = pages(p0 ? esc(`${p0.squadron} at ${p0.base}`) : "your squadron", esc(when),
    esc((p0 && p0.aircraft) || "F-4E Phantom II"), p0 && p0.wing && p0.wing.length ? esc(p0.wing.join(" and ")) : "",
    esc((overview && overview.setting) || "Warsaw Pact forces are massing on the inner German border."));
  const render = () => {
    const p = PAGES[i];
    dlg.querySelector(".guide-body").innerHTML = `<h2>${p.title}</h2>${p.body}`;
    dlg.querySelector(".guide-dots").innerHTML = PAGES.map((_, k) => `<i class="${k === i ? "on" : ""}"></i>`).join("");
    dlg.querySelector(".guide-prev").disabled = i === 0;
    dlg.querySelector(".guide-next").textContent = i === PAGES.length - 1 ? "Start" : "Next";
  };
  dlg.querySelector(".guide-prev").onclick = () => { i = Math.max(0, i - 1); render(); };
  dlg.querySelector(".guide-next").onclick = () => {
    if (i < PAGES.length - 1) { i += 1; render(); return; }
    try { localStorage.setItem(KEY, "1"); } catch { /* ignore */ }
    dlg.close();
  };
  render();
  dlg.showModal();
}

export function showHelp() {
  const dlg = $("#dlg-help");
  dlg.querySelector(".help-terms").innerHTML = Object.entries(TERMS)
    .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  dlg.showModal();
}
