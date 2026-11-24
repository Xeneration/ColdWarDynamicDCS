// Squadron tab: your crews (fatigue in words and bars) and your aircraft.
import { esc } from "../api.js";
import { abbr } from "../glossary.js";

const STATUS = { active: ["✔", "fit to fly"], wia: ["✚", "wounded, recovering"], kia: ["✝", "killed in action"],
  mia: ["?", "missing in action"], pow: ["⛓", "prisoner of war"] };
const REST = { fresh: "rested", tired: "tired - fights a little worse", exhausted: "exhausted - grounded until rested" };

function crewCard(p) {
  const [icon, word] = STATUS[p.status] || ["", p.status];
  const away = p.status !== "active";
  return `<div class="crew ${p.is_player ? "me" : ""} ${away ? "away" : ""}">
    <div><span class="st" title="${esc(word)}">${icon}</span> <b>${esc(p.rank)} ${esc(p.name)}</b>
      ${p.is_player ? '<span class="tag amber">you</span>' : ""}</div>
    ${away ? `<div class="muted">${esc(word)}</div>` : `
    <div class="fat"><span class="bar fatigue"><i style="width:${Math.round(100 * p.fatigue)}%"></i></span>
      <span class="muted">${esc(REST[p.rest] || p.rest)}</span></div>`}
    <div class="muted">${p.sorties} sorties · ${p.air_kills} air / ${p.ground_kills} ground kills</div></div>`;
}

export function renderSquadron(root, s) {
  const counts = s.airframes.reduce((a, x) => ((a[x.status] = (a[x.status] || 0) + 1), a), {});
  root.innerHTML = `<h2>${esc(s.name)} <span class="muted">${esc(s.type)} at ${esc(s.base)}</span></h2>
    <p class="intro">Every sortie tires a crew; a turn on the ground rests them (twice as fast at night). Exhausted
      crews are grounded, so the squadron can only keep up about two sorties a day per crew.${s.wsos.length
      ? ` Two-seaters fly with a pilot and a ${abbr("WSO")}.` : ""}</p>
    <h3>Pilots</h3><div class="crew-grid">${s.pilots.map(crewCard).join("")}</div>
    ${s.wsos.length ? `<h3>${abbr("WSO", "WSOs")} (back seat)</h3><div class="crew-grid">${s.wsos.map(crewCard).join("")}</div>` : ""}
    <h3>Aircraft</h3>
    <div class="af-key"><span class="af ready"></span>ready ${counts.ready || 0}
      <span class="af damaged"></span>in repair ${counts.damaged || 0}
      <span class="af destroyed"></span>lost ${counts.destroyed || 0}</div>
    <div class="af-grid">${s.airframes.map((a) => `<div class="af ${a.status}" title="${esc(a.id)}: ${esc(a.status)}${a.status === "damaged" ? `, ${a.repair_turns} turn(s) to repair` : ""} · condition ${a.quality}% · ${a.sorties} sorties">
      <span class="mono">${esc(a.id.split(" ").pop())}</span></div>`).join("")}</div>`;
}
