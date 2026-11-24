"""Morning report for a soak run: report.md and report.html in the run folder."""
from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from pathlib import Path


def load_rows(run_dir: Path) -> tuple[list[dict], list[dict], list[dict], dict]:
    missions, campaigns = [], []
    for f in sorted(run_dir.glob("i*/missions.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                (missions if row.get("kind") == "mission" else campaigns).append(row)
    wars = []
    sw = run_dir / "sim_wars.jsonl"
    if sw.is_file():
        wars = [json.loads(x) for x in sw.read_text(encoding="utf-8").splitlines() if x.strip()]
    meta = {}
    if (run_dir / "run.json").is_file():
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    return missions, campaigns, wars, meta


def _ratio(a: float, b: float) -> str:
    if b == 0:
        return "-" if a == 0 else "inf"
    return f"{a / b:.2f}"


def sections(run_dir: Path) -> list[tuple[str, list[str], list[list]]]:
    """[(title, header, rows)]"""
    missions, campaigns, wars, meta = load_rows(run_dir)
    out = []
    hours = sum(m.get("wall_s", 0) for m in missions) / 3600
    status = Counter(m["status"] for m in missions)
    out.append(("Summary", ["item", "value"], [
        ["DCS missions", len(missions)],
        ["DCS hours flown (all instances)", f"{hours:.1f}"],
        ["missions per instance-hour", f"{len(missions) / hours:.2f}" if hours else "-"],
        ["status", ", ".join(f"{k} {v}" for k, v in status.most_common()) or "-"],
        ["campaigns finished in DCS", sum(1 for c in campaigns if c["outcome"] != "unfinished")],
        ["simulated wars", len(wars)],
        ["cap (min)", meta.get("cap_min", "-")],
        ["eras (per instance)", ", ".join(f"i{n + 1}: {e}" for n, e in enumerate(meta.get("eras", []))) or "fulda"],
    ]))

    flags = defaultdict(list)
    for m in missions:
        for fl in (m.get("observed") or {}).get("flags", []):
            flags[fl].append(m)
        if m["status"] != "ok":
            flags[f"status:{m['status']}"].append(m)
        if m.get("log_errors"):
            flags["log_errors"].append(m)
    out.append(("Anomalies (mission files kept in i<N>/anomalies)", ["flag", "count", "tasks", "examples"],
                [[k, len(v), ", ".join(f"{t} {n}" for t, n in Counter(x["task"] for x in v).most_common()),
                  "; ".join(x["mission_id"] for x in v[:3])] for k, v in sorted(flags.items())]))

    by_task = defaultdict(list)
    for m in missions:
        if m.get("observed"):
            by_task[m["task"]].append(m)
    rows = []
    for task, ms in sorted(by_task.items()):
        ac = sum(m["aircraft"] for m in ms)
        def tot(src, k):
            return sum((m[src] or {}).get(k, 0) for m in ms)
        rows.append([task, len(ms),
                     f"{tot('observed', 'focus_lost') / ac:.1%}", f"{tot('predicted', 'focus_lost') / ac:.1%}",
                     _ratio(tot("observed", "focus_lost"), tot("predicted", "focus_lost")),
                     f"{tot('observed', 'focus_ground_kills') / len(ms):.1f}",
                     f"{tot('predicted', 'focus_ground_kills') / len(ms):.1f}",
                     f"{tot('observed', 'focus_air_kills') / len(ms):.2f}",
                     f"{tot('predicted', 'focus_air_kills') / len(ms):.2f}",
                     _ratio(tot("observed", "red_lost"), tot("predicted", "red_lost"))])
    out.append(("DCS vs resolver, focus flight (ratio > 1: DCS is bloodier than the simulation)",
                ["task", "n", "loss/ac DCS", "loss/ac sim", "loss ratio", "gnd kills DCS", "gnd kills sim",
                 "air kills DCS", "air kills sim", "red losses ratio"], rows))

    rows = []
    for task, ms in sorted(by_task.items()):
        shots = [m["observed"]["t_first_shot"] for m in ms if m["observed"].get("t_first_shot") is not None]
        dur = [m["observed"]["model_time_s"] for m in ms]
        near = [m["observed"]["closest_to_target_km"] for m in ms
                if m["observed"].get("closest_to_target_km") is not None]
        med = lambda v: f"{sorted(v)[len(v) // 2] / 60:.0f}" if v else "-"
        rows.append([task, med(shots), med(dur),
                     f"{sorted(near)[len(near) // 2]:.0f}" if near else "-",
                     f"{sum(1 for m in ms if 'nothing_happened' in m['observed']['flags']) / len(ms):.0%}"])
    out.append(("Pacing (medians)", ["task", "first shot (min)", "mission length (min)",
                                     "closest to target (km)", "nothing happened"], rows))

    oc = Counter((w.get("era", "fulda"), w["outcome"]) for w in wars)
    per_era = Counter(w.get("era", "fulda") for w in wars)
    out.append(("Simulated war outcomes", ["era", "outcome", "wars", "share of era"],
                [[e, k, v, f"{v / per_era[e]:.0%}"] for (e, k), v in sorted(oc.items(), key=lambda kv: (kv[0][0], -kv[1]))]))
    out.append(("Campaigns played in DCS", ["instance", "seed", "turns", "outcome", "day"],
                [[c["instance"], c["seed"], c["turns"], c["outcome"], c.get("day")] for c in campaigns]))
    errs = Counter(e for m in missions for e in m.get("log_errors", []))
    out.append(("dcs.log errors (most common)", ["count", "line"],
                [[n, e] for e, n in errs.most_common(15)]))
    return out


def to_markdown(secs) -> str:
    md = ["# Soak report", ""]
    for title, head, rows in secs:
        md += [f"## {title}", ""]
        if not rows:
            md += ["(none)", ""]
            continue
        md.append("| " + " | ".join(head) + " |")
        md.append("|" + "---|" * len(head))
        md += ["| " + " | ".join(str(x).replace("|", "/") for x in r) + " |" for r in rows]
        md.append("")
    return "\n".join(md)


def to_html(secs) -> str:
    h = ["<!doctype html><meta charset=utf-8><title>Soak report</title><style>"
         "body{font:14px system-ui;margin:16px;max-width:1100px}table{border-collapse:collapse;margin-bottom:18px}"
         "td,th{border:1px solid #ccc;padding:3px 8px;text-align:left}th{background:#eee}</style><h1>Soak report</h1>"]
    for title, head, rows in secs:
        h.append(f"<h2>{html.escape(title)}</h2>")
        if not rows:
            h.append("<p>(none)</p>")
            continue
        h.append("<table><tr>" + "".join(f"<th>{html.escape(x)}</th>" for x in head) + "</tr>")
        h += ["<tr>" + "".join(f"<td>{html.escape(str(x))}</td>" for x in r) + "</tr>" for r in rows]
        h.append("</table>")
    return "\n".join(h)


def write(run_dir: Path) -> Path:
    secs = sections(run_dir)
    (run_dir / "report.md").write_text(to_markdown(secs), encoding="utf-8")
    path = run_dir / "report.html"
    path.write_text(to_html(secs), encoding="utf-8")
    return path
