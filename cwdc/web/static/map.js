// The campaign map: every mark has a plain-English tooltip, and the legend explains the rest.
import { esc } from "./api.js";
import { KIND_LABEL, shortName, symbolSVG } from "./symbols.js";

const SIDE = { blue: "#4c9be8", red: "#e5534b" };
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };

const BASES = {
  "Shaded relief": () => L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}",
    { maxNativeZoom: 13, maxZoom: 12, attribution: "Relief &copy; Esri" }),
  "Topographic (modern)": () => L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    { maxZoom: 12, attribution: "&copy; OpenTopoMap (CC-BY-SA), &copy; OpenStreetMap contributors" }),
  "Dark": () => L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png",
    { maxZoom: 12, attribution: "&copy; OpenStreetMap contributors &copy; CARTO" }),
  "None (offline)": () => L.layerGroup(),
};

function bearing(a, b) {
  const dx = (b[1] - a[1]) * Math.cos((a[0] * Math.PI) / 180);
  return (Math.atan2(dx, b[0] - a[0]) * 180) / Math.PI;
}

function arrowIcon(color, angle, len) {
  const svg = `<svg viewBox="0 0 40 ${len}" width="40" height="${len}">
    <path d="M20 2 L36 24 L26 24 L26 ${len - 2} L14 ${len - 2} L14 24 L4 24 Z" fill="${color}"
      fill-opacity="0.8" stroke="#0b0f13" stroke-width="1.5"/></svg>`;
  return L.divIcon({ className: "arrow", html: `<div style="transform: rotate(${angle}deg)">${svg}</div>`,
    iconSize: [40, len], iconAnchor: [20, len / 2] });
}

function symbolIcon(opts, label, extra = "", cls = "") {
  const svg = symbolSVG(opts);
  return L.divIcon({ className: `sym ${cls}`, iconSize: null,
    html: `<div class="sym-box">${svg}<div class="sym-label">${esc(label)}${extra}</div></div>` });
}

export class CampaignMap {
  constructor(el, onSelectFlight) {
    this.onSelectFlight = onSelectFlight;
    this.map = L.map(el, { zoomSnap: 1, minZoom: 6, maxZoom: 12 });     // whole zooms: no tile seams
    this.map.fitBounds([[49.75, 8.4], [51.75, 12.2]]);          // the front, with Frankfurt and Leipzig
    this.map.createPane("territory").style.zIndex = 250;
    const labels = this.map.createPane("labels");
    labels.style.zIndex = 380;
    labels.style.pointerEvents = "none";
    this.svg = L.svg({ padding: 0.5, pane: "territory" });     // SVG so captured ground can be hatched
    const baseLayers = {};
    for (const [name, make] of Object.entries(BASES)) baseLayers[name] = make();
    const chosen = store.get("cwdc.base") in baseLayers ? store.get("cwdc.base") : "Shaded relief";
    baseLayers[chosen].addTo(this.map);
    this.map.on("baselayerchange", (e) => store.set("cwdc.base", e.name));

    const g = () => L.layerGroup();
    this.layers = { territory: g(), front: g(), formations: g(), sites: g(), sams: g(), airbases: g(),
      flights: g(), enemy: g(), supply: g(), cap: g(), labels: g(), selection: g() };
    for (const k of ["territory", "front", "formations", "sites", "sams", "airbases", "flights", "labels", "selection"])
      this.layers[k].addTo(this.map);
    L.control.layers(baseLayers, {
      "Territory and ground taken": this.layers.territory, "Front line and attacks": this.layers.front,
      "Ground forces": this.layers.formations, "Targets and sites": this.layers.sites,
      "SAM danger rings": this.layers.sams, "Airbases": this.layers.airbases,
      "NATO flights (today's ATO)": this.layers.flights,
      "Expected enemy activity (intel)": this.layers.enemy,
      "Supply network": this.layers.supply, "CAP stations": this.layers.cap, "Place names": this.layers.labels,
    }, { collapsed: true, position: "topright" }).addTo(this.map);

    L.control.scale({ imperial: false, position: "bottomright" }).addTo(this.map);
    this._nmScale();
    this._cursor();
    this._legend();
    this.map.on("zoomend", () => this._zoomClass());
    this._zoomClass();
    this.flightLines = {};
    this.samRings = {};
    this.data = null;
  }

  _zoomClass() {
    const z = this.map.getZoom();
    const c = this.map.getContainer().classList;
    c.toggle("z-far", z < 8);
    c.toggle("z-mid", z >= 8 && z < 9);
    c.toggle("z-near", z >= 9);
  }

  _nmScale() {
    const Ctl = L.Control.extend({
      onAdd: () => { this.nmEl = L.DomUtil.create("div", "nm-scale"); return this.nmEl; },
    });
    new Ctl({ position: "bottomright" }).addTo(this.map);
    const update = () => {
      const size = this.map.getSize();
      const y = size.y / 2;
      const a = this.map.containerPointToLatLng([0, y]);
      const b = this.map.containerPointToLatLng([100, y]);
      const nmPer100 = a.distanceTo(b) / 1852;
      const nice = [1, 2, 5, 10, 20, 25, 50, 100].find((n) => n >= nmPer100 * 0.6) || 100;
      this.nmEl.innerHTML = `<i style="width:${Math.round((nice / nmPer100) * 100)}px"></i>${nice} nm`;
    };
    this.map.on("zoomend moveend", update);
    update();
  }

  _cursor() {
    const Ctl = L.Control.extend({ onAdd: () => { this.cursorEl = L.DomUtil.create("div", "cursor-pos"); return this.cursorEl; } });
    new Ctl({ position: "bottomleft" }).addTo(this.map);
    this.map.on("mousemove", (e) => {
      const { lat, lng } = e.latlng;
      this.cursorEl.textContent = `${lat.toFixed(2)}°N ${lng.toFixed(2)}°E`;
    });
  }

  _legend() {
    const row = (sym, text) => `<div class="lg-row"><span class="lg-sym">${sym}</span><span>${text}</span></div>`;
    const line = (style, text) => row(`<svg width="34" height="12"><line x1="2" y1="6" x2="32" y2="6" style="${style}"/></svg>`, text);
    const swatch = (cls, text) => row(`<span class="lg-swatch ${cls}"></span>`, text);
    const html = `<button class="lg-toggle" type="button">Legend ▴</button><div class="lg-body">
      <h4>Who is who</h4>
      ${row(symbolSVG({ side: "blue", kind: "armor", size: 26 }), "Blue rectangle = NATO (your side)")}
      ${row(symbolSVG({ side: "red", kind: "armor", size: 26 }), "Red diamond = Warsaw Pact (enemy)")}
      ${row(symbolSVG({ side: "red", kind: "armor", destroyed: true, size: 26 }), "Grey, struck through = destroyed")}
      <h4>What the symbol inside means</h4>
      ${["armor", "mech", "cavalry", "artillery", "sam", "ewr", "depot", "rail", "convoy", "airbase"].map((k) =>
        row(symbolSVG({ side: "blue", kind: k, size: 22 }), KIND_LABEL[k])).join("")}
      <h4>Size (marks above the symbol)</h4>
      ${row("<b>XX</b>", "Division (~15,000 troops)")}${row("<b>X</b>", "Brigade (~4,000)")}
      ${row("<b>III</b>", "Regiment")}${row("<b>II</b>", "Battalion - what you actually see in DCS")}
      ${row('<span class="lg-bar"><i style="width:70%"></i></span>', "Strength remaining")}
      <h4>Lines and areas</h4>
      ${line("stroke:#e0a43a;stroke-width:4", "Front line: where the armies meet now")}
      ${line("stroke:#c9d1d9;stroke-width:2;stroke-dasharray:4 4", "Pre-war inner German border")}
      ${swatch("hatch-red", "Ground the Warsaw Pact has taken")}
      ${swatch("hatch-blue", "Ground NATO has taken back / beyond")}
      ${row('<span class="lg-arrow red">➜</span>', "Attack: arrow size = odds")}
      ${line("stroke:#e5534b;stroke-width:2;stroke-dasharray:5 5", "SAM danger ring - stay outside or low")}
      ${line("stroke:#4c9be8;stroke-width:2", "NATO flight: base to target")}
      ${line("stroke:#e5534b;stroke-width:2;stroke-dasharray:6 6", "Expected enemy flight (intel)")}
      ${row('<span class="lg-swatch dmg"></span>', "Amber dashed ring = damaged airbase")}
      <p class="muted">Hover or tap anything on the map for an explanation.</p></div>`;
    const Ctl = L.Control.extend({
      onAdd: () => {
        const el = L.DomUtil.create("div", "legend collapsed");
        el.innerHTML = html;
        L.DomEvent.disableClickPropagation(el);
        L.DomEvent.disableScrollPropagation(el);
        el.querySelector(".lg-toggle").onclick = () => {
          el.classList.toggle("collapsed");
          el.querySelector(".lg-toggle").textContent = el.classList.contains("collapsed") ? "Legend ▴" : "Legend ▾";
        };
        return el;
      },
    });
    new Ctl({ position: "bottomleft" }).addTo(this.map);
  }

  _patterns() {
    const svg = this.svg._container;
    if (!svg || svg.querySelector("#hatch-red")) return;
    const ns = "http://www.w3.org/2000/svg";
    const defs = document.createElementNS(ns, "defs");
    defs.innerHTML = ["red", "blue"].map((s) =>
      `<pattern id="hatch-${s}" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect width="9" height="9" fill="${SIDE[s]}" fill-opacity="0.18"/>
        <line x1="0" y1="0" x2="0" y2="9" stroke="${SIDE[s]}" stroke-width="3" stroke-opacity="0.7"/></pattern>`).join("");
    svg.insertBefore(defs, svg.firstChild);
  }

  draw(m) {
    this.data = m;
    Object.values(this.layers).forEach((l) => l.clearLayers());
    this.flightLines = {};
    this.samRings = {};
    const L_ = this.layers;

    // territory
    for (const side of ["blue", "red"])
      L.polygon(m.territory[side], { pane: "territory", renderer: this.svg, stroke: false,
        fillColor: SIDE[side], fillOpacity: side === "blue" ? 0.09 : 0.07, interactive: false }).addTo(L_.territory);
    for (const s of m.captured) {
      L.polygon(s.polygon, { pane: "territory", renderer: this.svg, color: SIDE[s.side], weight: 1,
        fillColor: `url(#hatch-${s.side})`, fillOpacity: 1 }).bindTooltip(esc(s.text), { sticky: true })
        .addTo(L_.territory);
    }
    this._patterns();

    // front, border and what is happening in each sector
    L.polyline(m.border, { color: "#c9d1d9", weight: 2, dashArray: "5 6", opacity: 0.9 })
      .bindTooltip("Pre-war inner German border (1976). Everything west of it was West Germany (NATO).", { sticky: true })
      .addTo(L_.front);
    L.polyline(m.front, { color: "#e0a43a", weight: 4, opacity: 0.95 })
      .bindTooltip("Front line: where the armies meet now.", { sticky: true }).addTo(L_.front);
    for (const s of m.sectors) {
      const tip = `<b>${esc(s.name)}</b><br>${esc(s.text)}<br>${esc(s.where)}<br>` +
        `<span class="muted">Combat power: Warsaw Pact ${s.red} vs NATO ${s.blue}</span>`;
      if (s.attacker) {
        const len = Math.round(40 + 26 * Math.min(2, s.odds));
        L.marker(s.at, { icon: arrowIcon(SIDE[s.attacker], bearing(s.arrow[0], s.arrow[1]), len), zIndexOffset: -100 })
          .bindTooltip(tip).addTo(L_.front);
      } else {
        L.marker(s.at, { icon: L.divIcon({ className: "hold", html: "HOLD", iconSize: null }) })
          .bindTooltip(tip).addTo(L_.front);
      }
      const off = Math.abs(s.offset_km) < 0.5 ? "on the border"
        : s.offset_km > 0 ? `WP +${Math.round(s.offset_km)} km` : `NATO +${Math.round(-s.offset_km)} km`;
      L.marker(s.at, { pane: "labels", interactive: false, icon: L.divIcon({ className: "lbl sector-lbl",
        html: `${esc(s.name)}<br><small>${off}</small>`, iconSize: null }) }).addTo(L_.front);
    }

    // ground forces
    for (const f of m.formations) {
      const bar = `<span class="sbar"><i style="width:${Math.round(f.strength * 100)}%"></i></span>`;
      L.marker(f.at, { icon: symbolIcon({ side: f.side, kind: f.kind, echelon: f.echelon, size: 34 }, shortName(f.name), bar, "formation") })
        .bindTooltip(esc(f.text)).addTo(L_.formations);
    }

    // targets and sites
    for (const o of m.objectives) {
      const dead = o.alive === 0;
      const kind = o.kind === "armor" && o.element ? "armor" : o.kind;
      const tier = o.element ? "element" : ["rail", "depot", "convoy", "command", "artillery"].includes(o.kind)
        ? "logistics" : "key";
      L.marker(o.at, { icon: symbolIcon({ side: o.side, kind, echelon: o.element ? "battalion" : null,
        destroyed: dead, size: o.element ? 22 : 26 }, o.name, "", `site ${tier}`) })
        .bindTooltip(esc(o.text)).addTo(L_.sites);
      if (o.kind === "sam" && !dead && o.threat_m) {
        this.samRings[o.name] = L.circle(o.at, { radius: o.threat_m, color: SIDE[o.side], weight: 1.5,
          dashArray: "5 5", opacity: 0.9, fillColor: SIDE[o.side], fillOpacity: 0.06, interactive: false })
          .addTo(L_.sams);
      }
    }

    for (const ab of m.airbases) {
      L.marker(ab.at, { zIndexOffset: 500, icon: symbolIcon({ side: ab.side, kind: "airbase", damaged: ab.damage > 0, size: 30 },
        ab.name + (ab.player ? " ★" : ""), "", ab.player ? "home" : "") })
        .bindTooltip(esc(ab.text) + (ab.player ? "<br><b>★ Your squadron's home base.</b>" : "")).addTo(L_.airbases);
    }

    for (const [side, pts] of Object.entries(m.cap_stations))
      for (const p of pts)
        L.circleMarker(p, { radius: 10, color: SIDE[side], weight: 1.5, fill: false, dashArray: "2 3" })
          .bindTooltip(`${side === "blue" ? "NATO" : "Warsaw Pact"} combat air patrol station: fighters orbit here.`)
          .addTo(L_.cap);

    for (const l of m.supply_links) {
      const col = l.damaged > 0.05 ? `hsl(${Math.round(120 - 120 * l.damaged)}, 70%, 50%)` : SIDE[l.side];
      L.polyline([l.a, l.b], { color: col, weight: 1.5 + 3 * Math.min(1, l.capacity / 250), opacity: 0.7 })
        .bindTooltip(esc(l.text), { sticky: true }).addTo(L_.supply);
    }
    for (const n of m.supply_nodes)
      L.circleMarker(n.at, { radius: 4, color: SIDE[n.side], weight: 1, fillOpacity: 0.9 })
        .bindTooltip(`${n.side === "blue" ? "NATO" : "Warsaw Pact"} supply node ${esc(n.name)}: ${n.stock} of ${n.capacity} in stock`)
        .addTo(L_.supply);

    // flights
    for (const f of m.flights) {
      const blue = f.side === "blue";
      const line = L.polyline([f.from, f.to], { color: SIDE[f.side], weight: f.player ? 4 : 2.5,
        opacity: blue ? 0.85 : 0.6, dashArray: blue ? (f.task === "CAP" ? "2 6" : null) : "6 6" })
        .bindTooltip(`${esc(f.callsign)} - ${esc(f.task)} from ${esc(f.base)}: ${f.distance_nm} nm, ~${f.eta_min} min`, { sticky: true });
      line.baseWeight = f.player ? 4 : 2.5;
      line.baseOpacity = blue ? 0.85 : 0.6;
      if (blue) line.on("click", () => this.onSelectFlight(f.id));
      line.addTo(blue ? L_.flights : L_.enemy);
      this.flightLines[f.id] = { line, f };
    }

    // 1976 place names
    for (const c of m.countries)
      L.marker(c.latlon, { pane: "labels", interactive: false, icon: L.divIcon({ className: "country-lbl",
        html: `${esc(c.name)}<br><small>${esc(c.note)}</small>`, iconSize: null }) }).addTo(L_.labels);
    for (const p of m.places)
      L.marker(p.latlon, { pane: "labels", interactive: false, icon: L.divIcon({ className: `place r${p.rank}`,
        html: `<i></i>${esc(p.name)}`, iconSize: null, iconAnchor: [3, 3] }) }).addTo(L_.labels);
    this.select(this.selected, this.selectedThreats);
  }

  /** Emphasise one flight: route, distance, target, and the SAM rings on its way. */
  select(id, threats = null) {
    this.selected = id;
    this.selectedThreats = threats;
    this.layers.selection.clearLayers();
    for (const [fid, { line }] of Object.entries(this.flightLines)) {
      const on = fid === id;
      line.setStyle({ weight: on ? 5 : line.baseWeight, opacity: id && !on ? 0.25 : line.baseOpacity });
      if (on) line.bringToFront();
    }
    for (const [name, ring] of Object.entries(this.samRings)) {
      const hot = threats && threats.sams && threats.sams.includes(name);
      ring.setStyle({ weight: hot ? 3.5 : 1.5, fillOpacity: hot ? 0.16 : 0.06, dashArray: hot ? null : "5 5" });
    }
    const sel = id && this.flightLines[id];
    if (!sel) return;
    const f = sel.f;
    L.marker(f.to, { icon: L.divIcon({ className: "crosshair", html: "⌖", iconSize: [30, 30] }), interactive: false })
      .addTo(this.layers.selection);
    const mid = [(f.from[0] + f.to[0]) / 2, (f.from[1] + f.to[1]) / 2];
    L.marker(mid, { interactive: false, icon: L.divIcon({ className: "leg-lbl",
      html: `${esc(f.callsign)}: ${f.distance_nm} nm · ~${f.eta_min} min each way`, iconSize: null }) })
      .addTo(this.layers.selection);
    this.map.fitBounds(L.latLngBounds([f.from, f.to]).pad(0.35), { maxZoom: 9 });
  }
}
