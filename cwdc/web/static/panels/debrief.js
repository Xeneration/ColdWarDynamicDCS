// Debrief tab: a visual summary per closed turn, the raw log underneath, and roll back.
import { api, esc, fmtZ, guarded, toast } from "../api.js";

function arrow(change) {
  if (Math.abs(change) < 0.5) return `<span class="muted">held</span>`;
  return change > 0 ? `<span class="red">◀ WP +${change.toFixed(0)} km</span>`
    : `<span class="blue">NATO +${(-change).toFixed(0)} km ▶</span>`;
}

function summary(st) {
  if (!st || !st.aircraft_lost) return "";
  const side = (s, name) => `<div class="db-side ${s}"><b>${name}</b>
    <div>${st.sorties[s]} sorties</div>
    <div>${st.aircraft_lost[s]} aircraft lost${st.aircrew_lost[s] ? `, ${st.aircrew_lost[s]} aircrew` : ""}</div>
    <div>${st.kills[s].air} air · ${st.kills[s].ground} ground kills</div></div>`;
  const you = st.your_flight ? `<div class="db-you ${st.your_flight.lost ? "bad" : ""}">
      <b>Your flight ${esc(st.your_flight.callsign)}</b>${st.your_flight.flown_in_dcs ? "" : " <span class=\"muted\">(simulated - not flown in DCS)</span>"}:
      ${esc(st.your_flight.summary.split(": ").slice(1).join(": "))}</div>` : "";
  const front = st.front.map((f) => `<li>${esc(f.name)}: ${arrow(f.change)}</li>`).join("");
  const fields = st.airfields_hit.length ? `<div class="muted">Airfields hit: ${st.airfields_hit.map((a) =>
    `${esc(a.name)} (${Math.round(a.damage * 100)}% damaged)`).join(", ")}</div>` : "";
  return `${you}<div class="db-sides">${side("blue", "NATO")}${side("red", "Warsaw Pact")}</div>
    <div class="db-front"><b>Front</b><ul>${front}</ul></div>${fields}`;
}

export function renderDebrief(root, d, ctx) {
  let html = `<h2>Debriefs</h2><p class="intro">What happened in each turn. The newest is first; the full log is under each summary.</p>`;
  if (!d.debriefs.length) html += `<p class="muted">No turns closed yet. Fly a mission or skip the turn.</p>`;
  d.debriefs.forEach((x, i) => {
    html += `<details class="db" ${i === 0 ? "open" : ""}><summary>Turn ${x.turn} · ${fmtZ(x.time)}
      ${x.flown_in_dcs ? '<span class="tag amber">flown in DCS</span>' : ""}</summary>
      <div class="db-body">${summary(x.stats)}
      <details><summary class="muted">Full log</summary><pre>${esc(x.lines.slice(1).join("\n"))}</pre></details></div></details>`;
  });
  if (d.snapshots.length > 1) {
    html += `<h3>Roll back</h3><p class="muted">Restores the campaign exactly as it was at the start of an earlier turn
      (handy after a crash or a bad ingest).</p><div class="row"><select id="rb-turn">${d.snapshots.slice().reverse()
      .map((t) => `<option value="${t}">start of turn ${t}</option>`).join("")}</select>
      <button id="btn-rb" class="warn">Roll back</button></div>`;
  }
  root.innerHTML = html;
  const rb = root.querySelector("#btn-rb");
  if (rb) rb.onclick = () => {
    const t = +root.querySelector("#rb-turn").value;
    if (!confirm(`Roll the campaign back to the start of turn ${t}?`)) return;
    guarded(rb, async () => {
      await api(`/api/campaigns/${ctx.cid}/rollback`, { method: "POST", body: { turn: t } });
      toast(`Rolled back to turn ${t}.`);
      await ctx.refresh(true);
    });
  };
}
