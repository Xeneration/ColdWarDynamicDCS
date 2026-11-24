"""CLI: cwdc soak / soak-worker / soak-report."""
from __future__ import annotations

from pathlib import Path


def cmd_soak(args) -> None:
    from cwdc.soak import runner
    exe = runner.dcs_exe()
    if not exe.is_file():
        raise SystemExit(f"DCS.exe not found at {exe}")
    print(f"Soak: {args.instances} DCS instance(s) for {args.hours} h, missions capped at "
          f"{args.cap_min} min. Results in {args.out or runner.runs_root()}")
    run_dir = runner.run(args.hours, args.instances, args.cap_min, args.seed,
                         Path(args.out) if args.out else None, args.sim_wars, args.timeout_min)
    print(f"done: {run_dir}")


def cmd_worker(args) -> None:
    from cwdc.soak import runner
    runner.run_worker(args.instance, Path(args.run_dir), args.until, args.cap_min * 60, args.seed,
                      timeout_s=args.timeout_min * 60 if args.timeout_min else None)


def _latest() -> Path:
    from cwdc.soak import runner
    root = runner.runs_root()
    runs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if not runs:
        raise SystemExit("no soak runs yet")
    return runs[-1]


def cmd_stop(args) -> None:
    from cwdc.soak import runner
    run_dir = Path(args.run) if args.run else _latest()
    runner.stop_file(run_dir).write_text("stop", encoding="utf-8")
    print(f"stop requested for {run_dir}: running missions are cut short, then DCS exits "
          "and the report is written (a few minutes).")


def cmd_report(args) -> None:
    from cwdc.soak import report
    run_dir = Path(args.run) if args.run else _latest()
    print(f"report: {report.write(run_dir)}")


def register(sub) -> None:
    p = sub.add_parser("soak", help="unattended AI-only DCS missions (overnight testing)")
    p.add_argument("--hours", type=float, default=8)
    p.add_argument("--instances", type=int, default=1, help="parallel DCS server instances")
    p.add_argument("--cap-min", type=int, default=75, help="maximum mission time (minutes)")
    p.add_argument("--timeout-min", type=int, default=None,
                   help="real-time limit per mission, minutes (default: cap + 20); stuck missions "
                        "are aborted and DCS restarted")
    p.add_argument("--seed", type=int, default=5000)
    p.add_argument("--sim-wars", type=int, default=200, help="fully simulated wars to play meanwhile")
    p.add_argument("--out", help="folder for run results (default Saved Games\\CWDC-soak)")
    p.set_defaults(func=cmd_soak)
    p = sub.add_parser("soak-worker", help="(internal) one soak instance")
    p.add_argument("--instance", type=int, required=True)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--until", type=float, required=True)
    p.add_argument("--cap-min", type=int, default=75)
    p.add_argument("--timeout-min", type=int, default=None)
    p.add_argument("--seed", type=int, default=5000)
    p.set_defaults(func=cmd_worker)
    p = sub.add_parser("soak-report", help="(re)build the report of a soak run")
    p.add_argument("run", nargs="?", help="run folder (default: the latest)")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("soak-stop", help="stop a running soak early (it still writes the report)")
    p.add_argument("run", nargs="?", help="run folder (default: the latest)")
    p.set_defaults(func=cmd_stop)
