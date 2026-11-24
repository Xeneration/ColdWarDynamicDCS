// War tab: how close each side is to winning, how we got here, and what each sector is doing.
import { esc, pct, sparkline } from "../api.js";
import { abbr, annotate } from "../glossary.js";

const SECTOR_COLORS = ["#e0a43a", "#c678dd", "#56b6c2", "#98c379", "#e06c75", "#61afef"];

/** A horizontal gauge: value/max filled, optional threshold mark, colour by danger to NATO. */
function gauge({ title, value, max, mark = null, markLabel = "", text, danger }) {
  const w = Math.max(0, Math.min(100, (100 * value) / max));
  const m = mark == null ? "" : `<span class="g-mark" style="left:${(100 * mark) / max}%"><em>${esc(markLabel)}</em></span>`;
  return `<div class="gauge"><div class="g-title">${title}</div>
    <div class="g-track"><span class="g-fill ${danger}" style="width:${w}%"></span>${m}</div>
    <div class="g-text">${text}</div></div>`;
}

function dangerOf(frac) { return frac > 0.66 ? "bad" : frac > 0.33 ? "warn" : "ok"; }

export function renderWar(root, o, m, hist) {
  const w = o.war;
  let html = "";
  if (o.outcome) {
    html += `<div class="outcome ${o.outcome.winner || "draw"}"><h2>${esc(o.outcome.title)}</h2>
      <p>${esc(o.outcome.reason)}</p></div><pre>${esc(o.outcome.summary.slice(2).join("\n"))}</pre>`;
  } else {
    html += `<h2>How the war is going <span class="muted">day ${w.war_day} of ${w.max_days}</span></h2>
      <p class="intro">The Warsaw Pact wins by reaching the River Main, breaking through, or grinding NATO's air
      forces down. NATO wins by restoring the border after day ${w.hold_days}. Otherwise a ceasefire on day
      ${w.max_days} is judged by the ground lost.</p>`;
  }
  const gauges = [];
  if (w.goal) {
    const done = Math.max(0, w.goal.start_km - w.goal.km);
    gauges.push(gauge({ title: `Warsaw Pact advance towards the Main (${esc(w.goal.name)})`, value: done,
      max: w.goal.start_km, text: `${w.goal.km} km still to go of ${w.goal.start_km} km`,
      danger: dangerOf(done / w.goal.start_km) }));
  }
  if (w.deepest && w.breakthrough_km)
    gauges.push(gauge({ title: "Deepest Warsaw Pact penetration", value: w.deepest.km, max: w.breakthrough_km,
      text: `${w.deepest.km.toFixed(0)} km in the ${esc(w.deepest.name)} - a breakthrough at ${w.breakthrough_km} km loses the war`,
      danger: dangerOf(w.deepest.km / w.breakthrough_km) }));
  gauges.push(gauge({ title: "NATO air strength", value: w.air.blue, max: 1, mark: w.air_exhausted,
    markLabel: "collapse", text: `${pct(w.air.blue)} of establishment (Warsaw Pact ${pct(w.air.red)}); below ${pct(w.air_exhausted)} NATO's air power is spent`,
    danger: w.air.blue < w.air_exhausted + 0.15 ? "bad" : w.air.blue < 0.6 ? "warn" : "ok" }));
  gauges.push(gauge({ title: "War clock", value: w.war_day, max: w.max_days, mark: w.hold_days,
    markLabel: `day ${w.hold_days}`, text: w.border_held
      ? (w.war_day > w.hold_days ? "The border is held - NATO wins at the end of this turn if it stays that way."
        : `The border is held - NATO wins if it still is after day ${w.hold_days}.`)
      : `Ceasefire talks on day ${w.max_days}.`, danger: "time" }));
  html += `<div class="gauges">${gauges.join("")}</div>`;

  // history
  const sectors = m.sectors;
  if (hist.length) {
    const labels = hist.map((h, i) => (i % 4 === 0 ? `T${h.turn}` : ""));
    html += `<h3>The front over time <span class="muted">(km into West Germany; below 0 = into East Germany)</span></h3>` +
      sparkline(sectors.map((s, i) => ({ name: s.name, color: SECTOR_COLORS[i % SECTOR_COLORS.length],
        values: hist.map((h) => (h.offsets[s.id] ?? null)) })), { zero: true, labels });
    html += `<h3>Air strength over time <span class="muted">(% of aircraft left)</span></h3>` +
      sparkline([{ name: "NATO", color: "#4c9be8", values: hist.map((h) => 100 * (h.air_strength.blue ?? 1)) },
                 { name: "Warsaw Pact", color: "#e5534b", values: hist.map((h) => 100 * (h.air_strength.red ?? 1)) }],
                { min: 0, max: 100, labels });
  }

  // sectors
  html += `<h3>The front, sector by sector</h3>`;
  for (const s of sectors) {
    const ov = o.sectors.find((x) => x.id === s.id) || { formations: [] };
    const tot = Math.max(1, s.red + s.blue);
    const forms = ov.formations.map((f) => `<li><span class="tag ${f.side}">${f.side === "red" ? "WP" : "NATO"}</span>
      ${esc(f.name)} <span class="muted">${Math.round(100 * f.strength / f.max)}% strength · supply ${pct(f.supply)} · ${esc(f.posture)}</span></li>`).join("");
    html += `<div class="card sector ${s.attacker || "quiet"}">
      <div class="sec-head"><b>${esc(s.name)}</b>${s.attacker ? `<span class="tag ${s.attacker}">${s.attacker === "red" ? "Warsaw Pact attacking" : "NATO attacking"}</span>` : `<span class="tag">quiet</span>`}</div>
      <div>${esc(s.text.split(": ").slice(1).join(": "))} ${esc(s.where)}</div>
      <div class="tug"><span class="r" style="width:${(100 * s.red) / tot}%">WP ${s.red}</span><span class="b" style="width:${(100 * s.blue) / tot}%">NATO ${s.blue}</span></div>
      <details><summary class="muted">Formations in this sector</summary><ul class="forms">${forms}</ul></details></div>`;
  }
  if (o.reinforcements.length)
    html += `<p class="muted">On the way for NATO: ${o.reinforcements.map((r) => `${annotate(esc(r.name))} (turn ${r.turn})`).join(", ")}.</p>`;

  // air forces and bases
  html += `<h3>Air forces</h3><div class="sq-grid">`;
  for (const q of o.squadrons) {
    const total = q.ready + q.repair + q.lost || 1;
    html += `<div class="card sq ${q.side}"><b>${esc(q.name)}</b><div class="muted">${esc(q.type)} at ${esc(q.base)}${q.player ? " · <b>your squadron</b>" : ""}</div>
      <div class="stack"><span class="ok" style="width:${100 * q.ready / total}%"></span><span class="warn" style="width:${100 * q.repair / total}%"></span><span class="bad" style="width:${100 * q.lost / total}%"></span></div>
      <div class="muted">${q.ready} ready · ${q.repair} in repair · ${q.lost} lost · ${q.pilots} pilots${q.wsos != null ? ` + ${q.wsos} ${abbr("WSO")}s` : ""}</div></div>`;
  }
  html += `</div><h3>Airfields</h3><div class="chips">` + o.airfields.map((a) =>
    `<span class="chip ${a.damage >= 0.7 ? "threat-very-high" : a.damage > 0 ? "threat-moderate" : ""}" title="${esc(a.state)}">
      ${a.side === "blue" ? "🔵" : "🔴"} ${esc(a.name)}: ${a.damage >= 0.7 ? "closed" : a.damage > 0 ? `${pct(a.damage)} damaged` : "operational"}</span>`).join("") + `</div>`;
  html += `<h3>Supply <span class="muted">(how well each side's troops are supplied - starving troops fight worse)</span></h3>`;
  for (const s of o.sectors) {
    const avg = (side) => {
      const f = s.formations.filter((x) => x.side === side);
      return f.length ? f.reduce((a, x) => a + x.supply, 0) / f.length : null;
    };
    const bar = (side) => {
      const v = avg(side);
      return v == null ? `<span class="muted">-</span>` : `<span class="sup-bar ${side}"><i style="width:${pct(v)}"></i></span> ${pct(v)}`;
    };
    html += `<div class="sup-row"><span>${esc(s.name)}</span><span>NATO ${bar("blue")}</span><span>WP ${bar("red")}</span></div>`;
  }
  html += `<details><summary class="muted">Depots and damaged rail links</summary><pre>${esc(o.supply.join("\n"))}</pre></details>`;
  root.innerHTML = html;
}
