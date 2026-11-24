// Simplified NATO (APP-6 style) map symbols, drawn as inline SVG.
// Friendly = blue rectangle, enemy = red diamond (shape AND colour, for colour-blind players).

const FILL = { blue: "#9fd0ff", red: "#ff9c93", grey: "#9aa4ad" };
const STROKE = "#0b0f13";

// glyphs in a box centred on 0,0 roughly 40 x 26 (the friendly frame interior)
const GLYPH = {
  armor: `<ellipse cx="0" cy="0" rx="12" ry="6" fill="none" stroke="${STROKE}" stroke-width="2.2"/>`,
  infantry: `<path d="M-20 -13 L20 13 M-20 13 L20 -13" stroke="${STROKE}" stroke-width="2"/>`,
  mech: `<path d="M-20 -13 L20 13 M-20 13 L20 -13" stroke="${STROKE}" stroke-width="1.6"/>
         <ellipse cx="0" cy="0" rx="12" ry="6" fill="none" stroke="${STROKE}" stroke-width="2.2"/>`,
  cavalry: `<path d="M-20 13 L20 -13" stroke="${STROKE}" stroke-width="2.2"/>`,
  artillery: `<circle cx="0" cy="0" r="4" fill="${STROKE}"/>`,
  sam: `<path d="M-14 9 Q0 -12 14 9" fill="none" stroke="${STROKE}" stroke-width="2"/>
        <path d="M0 7 L0 -9 M-3 -5 L0 -10 L3 -5" fill="none" stroke="${STROKE}" stroke-width="2"/>`,
  ship: `<path d="M-14 0 L14 0 L9 7 L-9 7 Z" fill="none" stroke="${STROKE}" stroke-width="2"/>
         <path d="M-3 0 L-3 -6 L4 -6 L4 0 M0 -6 L0 -11" fill="none" stroke="${STROKE}" stroke-width="1.8"/>`,
  aaa: `<path d="M-14 9 Q0 -12 14 9" fill="none" stroke="${STROKE}" stroke-width="2"/>
        <circle cx="0" cy="2" r="3" fill="${STROKE}"/>`,
  ewr: `<path d="M-10 -8 Q-8 8 8 9" fill="none" stroke="${STROKE}" stroke-width="2.2"/>
        <path d="M-4 3 L7 -8" stroke="${STROKE}" stroke-width="1.8"/><circle cx="8" cy="-9" r="2.2" fill="${STROKE}"/>`,
  depot: `<path d="M-20 8 L20 8" stroke="${STROKE}" stroke-width="3"/>
          <rect x="-7" y="-7" width="14" height="10" fill="none" stroke="${STROKE}" stroke-width="1.8"/>`,
  rail: `<path d="M-16 -3 L16 -3 M-16 3 L16 3 M-11 -6 L-11 6 M-4 -6 L-4 6 M3 -6 L3 6 M10 -6 L10 6"
          stroke="${STROKE}" stroke-width="1.6"/>`,
  convoy: `<path d="M-20 8 L20 8" stroke="${STROKE}" stroke-width="3"/>
           <circle cx="-7" cy="1" r="3" fill="none" stroke="${STROKE}" stroke-width="1.8"/>
           <circle cx="7" cy="1" r="3" fill="none" stroke="${STROKE}" stroke-width="1.8"/>`,
  command: `<path d="M-6 9 L-6 -9 L9 -5 L-6 -1" fill="none" stroke="${STROKE}" stroke-width="2"/>`,
  airbase: `<path d="M0 -10 L0 10 M-13 1 L13 1 M-5 8 L5 8 M-2 -7 L2 -7" stroke="${STROKE}" stroke-width="2.4"
             stroke-linecap="round"/>`,
};
const ECHELON = { division: "XX", brigade: "X", regiment: "III", battalion: "II" };

/** SVG markup for one symbol. side: "blue"|"red"; kind: key of GLYPH. */
export function symbolSVG({ side, kind, echelon = null, destroyed = false, damaged = false, size = 34 }) {
  const fill = destroyed ? FILL.grey : FILL[side] || FILL.grey;
  const friendly = side === "blue";
  const frame = friendly
    ? `<rect x="10" y="25" width="44" height="28" fill="${fill}" stroke="${STROKE}" stroke-width="2"/>`
    : `<path d="M32 13 L55 39 L32 65 L9 39 Z" fill="${fill}" stroke="${STROKE}" stroke-width="2"/>`;
  const cy = 39;
  const scale = friendly ? 1 : 0.68;
  const glyph = `<g transform="translate(32 ${cy}) scale(${scale})">${GLYPH[kind] || ""}</g>`;
  const ech = echelon && ECHELON[echelon]
    ? `<text x="32" y="${friendly ? 21 : 10}" text-anchor="middle" font-size="11" font-weight="700"
         font-family="Segoe UI, sans-serif" fill="#fff" stroke="${STROKE}" stroke-width="2.5"
         paint-order="stroke">${ECHELON[echelon]}</text>` : "";
  const dead = destroyed ? `<path d="M8 66 L56 14" stroke="#e5534b" stroke-width="3"/>` : "";
  const ring = damaged ? `<circle cx="32" cy="${cy}" r="29" fill="none" stroke="#e0a43a" stroke-width="3"
       stroke-dasharray="5 4"/>` : "";
  const h = Math.round(size * 68 / 64);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 68" width="${size}" height="${h}">` +
    `${ring}${frame}${glyph}${ech}${dead}</svg>`;
}

/** Short unit designation: "47th Guards Tank Division" -> "47 GTD". */
export function shortName(name) {
  const clean = name.replace(/\(.*?\)/g, "").replace(/,.*$/, "").trim();
  const special = { panzerbrigade: "PzBrig", panzergrenadierbrigade: "PzGrenBrig" };
  const num = (clean.match(/\d+/) || [""])[0];
  const words = clean.split(/\s+/).filter((w) => !/^\d/.test(w));
  const parts = words.map((w) => special[w.toLowerCase()] || (/^[A-Z]/.test(w) ? w[0] : ""));
  const abbr = parts.some((p) => p.length > 1) ? parts.join(" ") : parts.join("");
  return `${num} ${abbr}`.trim();
}

export const KIND_LABEL = {
  armor: "Armour (tanks)", mech: "Mechanised infantry", cavalry: "Armoured cavalry (recon/screen)",
  infantry: "Infantry", artillery: "Artillery", sam: "Surface-to-air missiles (SAM)",
  aaa: "Anti-aircraft guns", ewr: "Early-warning radar", depot: "Supply depot", rail: "Rail yard",
  convoy: "Supply column", command: "Command post", airbase: "Airbase", ship: "Warships at sea",
};
