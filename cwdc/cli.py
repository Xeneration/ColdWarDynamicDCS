"""Command line entry point: `python -m cwdc <command>`."""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from pathlib import Path

from cwdc import dcs_paths


def cmd_install_hook(_args) -> None:
    dest = dcs_paths.install_hook()
    print(f"hook installed: {dest}")
    print(f"results folder: {dcs_paths.results_dir()}")


def cmd_spike(args) -> None:
    from cwdc.missiongen import spike
    if not dcs_paths.hook_is_current():
        print(f"hook installed/updated: {dcs_paths.install_hook()}")
    out = Path(args.out) if args.out else dcs_paths.missions_dir()
    print(f"mission written: {spike.generate(out)}")


def cmd_show_result(args) -> None:
    from cwdc.ingest import results
    if args.from_log:
        log = dcs_paths.saved_games_dcs() / "Logs" / "dcs.log"
        m = results.from_dcs_log(log, args.file)
        if m is None:
            raise SystemExit(f"no CWDC mission data found in {log}")
        label = f"{log} (fallback)"
    else:
        files = list(dcs_paths.results_dir().glob("*.json"))
        if not args.file and not files:
            raise SystemExit(f"no results in {dcs_paths.results_dir()} - try --from-log")
        path = Path(args.file) if args.file else max(files, key=lambda p: p.stat().st_mtime)
        m = results.load_result_file(path)
        label = path.name
    snap = m.get("snapshot") or {}
    print(f"{label}: mission={m.get('mission_id')} source={m.get('source')} "
          f"final={m.get('final')} ended={m.get('ended')} t={snap.get('model_time', 0):.0f}s")
    print("events:", dict(Counter(e["event"] for e in m.get("events", []))))
    events = m.get("events", [])
    hidden = 0
    for e in events:
        if e["event"] not in ("S_EVENT_KILL", "S_EVENT_DEAD", "S_EVENT_CRASH", "S_EVENT_EJECTION",
                              "S_EVENT_LANDING_AFTER_EJECTION", "S_EVENT_LAND",
                              "S_EVENT_TAKEOFF", "S_EVENT_UNIT_LOST"):
            continue
        ini = (e.get("initiator") or {})
        tgt = (e.get("target") or {})
        if results.is_scenery(ini) or results.is_scenery(tgt):
            hidden += 1
            continue
        weapon = results.kill_weapon(events, e) if e["event"] == "S_EVENT_KILL" else e.get("weapon")
        print(f"  t={e['t']:7.0f} {e['event']:<18} {ini.get('name')} ({ini.get('type')})"
              + (f" -> {tgt.get('name')} ({tgt.get('type')})" if tgt.get("name") else "")
              + (" [collision]" if results.is_collision(e) else f" [{weapon}]" if weapon else "")
              + (f" @ {e['place']}" if e.get("place") else ""))
    if hidden:
        print(f"  ({hidden} scenery events hidden)")
    for g in snap.get("groups", []):
        print(f"  group {g['name']}: {len(g['units'])} alive")


def cmd_serve(args) -> None:
    from cwdc.web.app import serve
    serve(host=args.host, port=args.port, open_browser=not args.no_browser,
          auto_ingest=not args.no_auto_ingest)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.CRITICAL)  # pydcs livery parse noise
    ap = argparse.ArgumentParser(prog="cwdc", description="ColdWarDynamicDCS campaign manager")
    sub = ap.add_subparsers(required=True)
    p = sub.add_parser("install-hook", help="install/update the DCS GUI hook")
    p.set_defaults(func=cmd_install_hook)
    p = sub.add_parser("spike", help="generate the Phase 0 Spike C test mission")
    p.add_argument("--out", help="output folder (default: Saved Games\\DCS\\Missions\\ColdWarDynamic)")
    p.set_defaults(func=cmd_spike)
    p = sub.add_parser("show-result", help="summarise a results JSON (default: newest)")
    p.add_argument("file", nargs="?", help="results file, or a mission id with --from-log")
    p.add_argument("--from-log", action="store_true",
                   help="rebuild the result from Saved Games\\DCS\\Logs\\dcs.log instead")
    p.set_defaults(func=cmd_show_result)
    p = sub.add_parser("serve", help="run the local web UI (opens your browser)")
    p.add_argument("--port", type=int, default=8076)
    p.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: this PC only)")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    p.add_argument("--no-auto-ingest", action="store_true",
                   help="don't ingest finished missions automatically")
    p.set_defaults(func=cmd_serve)
    from cwdc import cli_campaign
    cli_campaign.register(sub)
    from cwdc.soak import cli as soak_cli
    soak_cli.register(sub)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
