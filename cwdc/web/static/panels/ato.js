// ATO tab: today's NATO sorties as package cards, flyable ones first.
import { api, esc, fmtZ, guarded, PERIOD_ICON, toast } from "../api.js";
import { abbr, annotate } from "../glossary.js";

const START = {
  runway: ["Runway", "engines running at the runway, ready to roll"],
  ramp: ["Ramp", "parked, about 10 minutes of taxi first"],
  air: ["In the air", "start ~50 nm out, skip most of the transit"],
};
const THREAT_HELP = "Threat estimate from intelligence: SAM sites covering the route, guns at the target and enemy fighter patrols nearby.";

function threatDetail(t) {
  const parts = [];
  if (t.sams.length) parts.push(`${abbr("SAM", "SAM")} sites on the route: ${t.sams.map(esc).join(", ")}`);
  if (t.aaa) parts.push(`${t.aaa} ${abbr("AAA", "anti-aircraft gun")}${t.aaa > 1 ? "s" : ""} at the target`);
  if (t.fighters) parts.push(`${t.fighters} enemy fighter patrol${t.fighters > 1 ? "s" : ""} near the route`);
  return parts.length ? parts.join(" · ") : "No known threats on the route.";
}

function flightCard(f, over, selected) {
  const cls = ["flt", f.eligible ? "eligible" : "", f.assigned ? "assigned" : "", f.id === selected ? "selected" : ""].join(" ");
  const tags = [
    f.assigned ? `<span class="tag amber">Your mission</span>` : f.eligible ? `<span class="tag amber">You can fly this</span>` : "",
    f.supports_callsign ? `<span class="tag">protects ${esc(f.supports_callsign)}</span>` : "",
  ].join(" ");
  const crews = f.crews.map((c, i) => `<li>${esc(f.callsign)}-${i + 1}: ${c.map(esc).join(" / ")}${c.length > 1 ? ` <span class="muted">(pilot / ${abbr("WSO")})</span>` : ""}</li>`).join("");
  const fly = f.eligible && !over ? `
    <div class="fly-box">
      <div class="start-opts">${Object.entries(START).map(([k, [name, help]], i) => `
        <label><input type="radio" name="start-${f.id}" value="${k}" ${i === 0 ? "checked" : ""}>
          <b>${name}</b> <span class="muted">${help}</span></label>`).join("")}</div>
      <div class="row">
        <button class="ghost" data-preview="${f.id}">Preview</button>
        <button data-fly="${f.id}">${f.assigned ? "Regenerate mission" : "Fly this mission"}</button>
        <button class="ghost" data-bot="${f.id}" title="An AI-only copy for testing: you watch instead of fly">Bot test</button>
      </div>
      <div class="preview" id="pv-${f.id}"></div>
    </div>` : "";
  return `<div class="${cls}" data-id="${f.id}">
    <div class="flt-top"><b class="callsign">${esc(f.callsign)}</b>
      <span class="muted">${f.aircraft}× ${esc(f.type)} · ${esc(f.squadron)} · from ${esc(f.base)}</span> ${tags}</div>
    <div class="flt-plain">${annotate(esc(f.task_plain))}</div>
    <div class="chips">
      <span class="chip" title="Target or patrol area">🎯 ${esc(f.target || "patrol station")}</span>
      <span class="chip" title="One-way distance and time at cruise speed">✈ ${f.distance_nm} nm · ~${f.eta_min} min</span>
      <span class="chip threat-${f.threats.level.replace(" ", "-")}" title="${THREAT_HELP}">Threat: ${f.threats.level}</span>
    </div>
    <div class="flt-detail">${threatDetail(f.threats)}</div>
    <div class="flt-detail"><span class="muted">Loadout:</span> ${esc(f.loadout_text)}</div>
    <details><summary class="muted">Crews</summary><ul class="crews">${crews}</ul></details>
    ${fly}
  </div>`;
}

export function renderATO(root, a, o, ctx) {
  const over = !!o.outcome;
  let html = `<h2>${abbr("ATO", "Air tasking order")} <span class="muted">turn ${a.turn} · ${fmtZ(a.time)} ·
      ${PERIOD_ICON[a.period] || ""} ${esc(a.period)}</span></h2>
    <p class="intro">Every NATO sortie planned for the next 6 hours, grouped into packages that fly together.
      Your squadron's missions come first. Click a card to see its route on the map.</p>`;
  if (over) html += `<p class="muted">The war is over - no more sorties.</p>`;
  if (!a.packages.length) html += `<p class="muted">Nothing planned this period${a.period === "night" ? " - at night the 1976 air forces fly only patrols" : ""}.</p>`;
  for (const p of a.packages) {
    html += `<section class="pkg ${p.flyable ? "flyable" : ""}">
      <div class="pkg-head"><span class="task-chip">${abbr(p.task, esc(p.task))}</span>
        <b>${esc(p.description.replace(/ \(with .*\)$/, ""))}</b>
        ${p.reason ? `<div class="why">Why: ${esc(p.reason)}</div>` : ""}</div>
      ${p.flights.map((f) => flightCard(f, over, ctx.selected)).join("")}
    </section>`;
  }
  html += `<p class="muted">Intelligence expects about <b>${a.enemy_expected}</b> Warsaw Pact flights this period
    (map layer "Expected enemy activity").</p>`;
  root.innerHTML = html;

  root.querySelectorAll(".flt").forEach((card) => card.addEventListener("click", (e) => {
    if (e.target.closest("button,input,label,details,a")) return;
    const f = a.flights.find((x) => x.id === card.dataset.id);
    ctx.selectFlight(f.id, f.threats);
  }));
  const startOf = (id) => (root.querySelector(`input[name="start-${id}"]:checked`) || {}).value || "runway";
  root.querySelectorAll("[data-preview]").forEach((btn) => btn.addEventListener("click", () => guarded(btn, async () => {
    const id = btn.dataset.preview;
    const p = await api(`/api/campaigns/${ctx.cid}/flights/${id}`);
    const fr = p.friendly.map((x) => `${esc(x.callsign)} <span class="muted">(${esc(x.task)}, ${esc(x.role)})</span>`).join(", ");
    const sites = p.sites.filter((s) => s.side === "red").map((s) => esc(s.name)).join(", ") || "none";
    root.querySelector(`#pv-${id}`).innerHTML = `<div class="preview-box">
      <b>In this mission</b>
      <div>Friendly: ${fr}</div>
      <div>Enemy fighters likely to engage: <b>${p.enemy_fighters}</b> flight(s)${p.enemy_strikers ? `; enemy bombers inbound: ${p.enemy_strikers}` : ""}</div>
      <div>Enemy sites present: ${sites}</div>
      <div class="muted">Nothing is written until you press Fly.</div></div>`;
    ctx.selectFlight(id, p.threats);
  })));
  const fly = (btn, bot) => guarded(btn, async () => {
    const id = btn.dataset.fly || btn.dataset.bot;
    const r = await api(`/api/campaigns/${ctx.cid}/fly`, { method: "POST", body: { flight_id: id, start: startOf(id), bot } });
    toast(`Mission written: <b>${esc(r.file)}</b><br>Open it in DCS: Mission › My Missions › ColdWarDynamic.`, 9000);
    await ctx.refresh(true);
    ctx.showTab("mission");
  });
  root.querySelectorAll("[data-fly]").forEach((btn) => btn.addEventListener("click", () => fly(btn, false)));
  root.querySelectorAll("[data-bot]").forEach((btn) => btn.addEventListener("click", () => fly(btn, true)));
}
