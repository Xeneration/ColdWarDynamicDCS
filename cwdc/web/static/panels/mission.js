// Mission tab: a live checklist from "mission written" to "turn closed", and the briefing.
import { api, esc, guarded, toast } from "../api.js";
import { annotate } from "../glossary.js";

const SECTIONS = ["Flight", "Tasking", "Target", "Crews", "Known SAM threats", "Friendly flights in the area", "Intel"];

function briefingHTML(lines) {
  const rows = [], notes = [];
  for (const l of lines || []) {
    const k = SECTIONS.find((s) => l.startsWith(s + ":"));
    if (k) rows.push(`<dt>${esc(k)}</dt><dd>${annotate(esc(l.slice(k.length + 1).trim()))}</dd>`);
    else if (l.trim()) notes.push(`<p>${annotate(esc(l))}</p>`);
  }
  return `<dl class="brief">${rows.join("")}</dl>${notes.join("")}`;
}

export function renderMission(root, o, ctx) {
  const p = o.pending;
  if (!p) {
    root.innerHTML = `<h2>Mission</h2><p class="intro">No mission is waiting to be flown.</p>
      <ol class="steps"><li>Open the <b>ATO</b> tab and pick a card marked <span class="tag amber">You can fly this</span>.</li>
      <li>Choose how to start and press <b>Fly this mission</b>.</li>
      <li>The mission appears here with step-by-step instructions.</li></ol>
      <p class="muted">Not flying this turn? Use <b>Skip turn</b> at the top.</p>`;
    return;
  }
  const r = p.result || {};
  const running = r.exists && r.fresh && !r.final;
  const done = r.exists && r.fresh && r.final;
  const auto = ctx.autoIngest();
  const file = p.miz_path.split(/[\\/]/).pop();
  const step = (state, title, body = "") => `<li class="ck ${state}"><span class="ck-dot"></span>
    <div><b>${title}</b>${body ? `<div class="muted">${body}</div>` : ""}</div></li>`;
  root.innerHTML = `
    <h2>${p.bot ? "Bot test mission" : "Your mission"}: ${esc(p.callsign)}</h2>
    ${p.bot ? `<p class="intro">AI-only test: there is no cockpit for you. Load it in DCS and watch (F10 map, F2 views,
      Ctrl+Z to speed up); it ends by itself.</p>` : ""}
    <ol class="checklist">
      ${step("done", "Mission written", `<span class="mono">${esc(file)}</span>`)}
      ${step(running || done ? "done" : "now", "Load it in DCS",
        `Mission › My Missions › ColdWarDynamic › ${esc(file)}
         <div class="row"><button class="small ghost" id="btn-copy">Copy file name</button>
         <button class="small ghost" id="btn-folder">Open missions folder</button></div>`)}
      ${step(done ? "done" : running ? "now" : "todo", "Fly it, then exit the mission normally",
        running ? `DCS is running the mission - ${r.events} events recorded so far.`
          : "When DCS asks whether to save a track or debrief you can simply exit.")}
      ${step(done ? "now" : "todo", "Results received", done ? `${r.events} events captured.` :
        "The DCS hook writes them when the mission ends.")}
      ${step("todo", "Turn closed and debriefed", auto ? "Automatic: Auto-ingest is on." :
        "Auto-ingest is off - press Ingest now when the results are in.")}
    </ol>
    <div class="row">
      <button id="btn-ingest" ${done || !auto ? "" : "class=\"ghost\""}>Ingest now</button>
      <button id="btn-ingest-log" class="ghost" title="If the hook missed it: rebuild the result from Saved Games\\DCS\\Logs\\dcs.log">Ingest from dcs.log</button>
    </div>
    <h3>Briefing</h3>${briefingHTML(p.briefing)}
    ${p.others.length ? `<h3>Also in this mission</h3><p>${p.others.map(esc).join(", ")}</p>` : ""}
    <p class="muted">Your in-game pilot name comes from your DCS Logbook profile, not from the campaign.</p>`;

  root.querySelector("#btn-copy").onclick = () => navigator.clipboard.writeText(file)
    .then(() => toast("File name copied."), () => toast(esc(file)));
  root.querySelector("#btn-folder").onclick = (e) => guarded(e.target, () =>
    api("/api/open-missions-folder", { method: "POST" }));
  const ingest = (fromLog) => (e) => guarded(e.target, async () => {
    const res = await api(`/api/campaigns/${ctx.cid}/ingest`, { method: "POST", body: { from_log: fromLog } });
    toast(`Turn closed.${res.notes.length ? "<br>" + res.notes.map(esc).join("<br>") : ""}`);
    await ctx.refresh(true);
    ctx.showTab("debrief");
  });
  root.querySelector("#btn-ingest").onclick = ingest(false);
  root.querySelector("#btn-ingest-log").onclick = ingest(true);
}
