// Small helpers shared by every module.

export const $ = (sel, root = document) => root.querySelector(sel);
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
export const pct = (x) => `${Math.round(x * 100)}%`;
export const fmtZ = (iso) => new Date(iso).toLocaleString("en-GB",
  { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + "Z";
export const fmtLocal = (iso) => new Date(new Date(iso).getTime() + 2 * 3600e3).toLocaleTimeString("en-GB",
  { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
export const PERIOD_ICON = { dawn: "🌅", day: "☀️", dusk: "🌇", night: "🌙" };
export const SIDE_NAME = { blue: "NATO", red: "Warsaw Pact" };

export async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${r.status} ${r.statusText}`);
  return data;
}

export function toast(html, ms = 6000) {
  const t = $("#toast");
  t.innerHTML = html;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}

export async function guarded(btn, fn) {
  if (btn) btn.disabled = true;
  try { await fn(); } catch (e) { toast(`<b>Error:</b> ${esc(e.message)}`, 9000); }
  finally { if (btn) btn.disabled = false; }
}

/** A tiny SVG line chart. series: [{name, color, values:[number|null]}] */
export function sparkline(series, { width = 480, height = 120, min = null, max = null, zero = false, labels = [] } = {}) {
  const all = series.flatMap((s) => s.values.filter((v) => v != null));
  if (all.length < 2) return `<p class="muted">The chart fills in as turns are played.</p>`;
  let lo = min ?? Math.min(...all), hi = max ?? Math.max(...all);
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (hi - lo < 1e-6) hi = lo + 1;
  const n = Math.max(...series.map((s) => s.values.length));
  const pad = 26;
  const x = (i) => pad + (width - pad - 6) * (n > 1 ? i / (n - 1) : 0);
  const y = (v) => 6 + (height - 22) * (1 - (v - lo) / (hi - lo));
  let svg = `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">`;
  for (const t of [lo, (lo + hi) / 2, hi])
    svg += `<line x1="${pad}" x2="${width}" y1="${y(t)}" y2="${y(t)}" class="grid"/>` +
      `<text x="2" y="${y(t) + 4}" class="axis">${Math.round(t)}</text>`;
  if (zero) svg += `<line x1="${pad}" x2="${width}" y1="${y(0)}" y2="${y(0)}" class="zero"/>`;
  for (const s of series) {
    const pts = s.values.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean).join(" ");
    svg += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
  }
  labels.forEach((l, i) => { if (l) svg += `<text x="${x(i)}" y="${height - 2}" class="axis" text-anchor="middle">${l}</text>`; });
  svg += `</svg><div class="chart-key">${series.map((s) =>
    `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`;
  return svg;
}
