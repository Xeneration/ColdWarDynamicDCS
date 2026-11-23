"""Campaign commands: new / status / ato / fly / ingest / skip / roster / rollback."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from cwdc import dcs_paths, store
from cwdc import theater as T
from cwdc.model import AirframeStatus, Campaign, PilotStatus, Side


def _ll(pos) -> str:
    lat, lon = T.pos_to_latlon(pos)
    return f"{lat:.2f}N {lon:.2f}E"


def _load(args) -> Campaign:
    """Load the campaign and pick up objectives added to the theater data since."""
    from cwdc.engine.factory import sync_theater
    c = store.load(args.campaign)
    added = sync_theater(c)
    if added:
        print(f"(theater update: added {', '.join(added)})")
        store.save(c)
    return c


def cmd_new(args) -> None:
    from cwdc.engine.factory import new_campaign, slug
    if (store.campaign_dir(slug(args.name)) / "campaign.json").exists() and not args.force:
        raise SystemExit(f"campaign '{slug(args.name)}' exists - use --force to overwrite")
    c = new_campaign(args.name, seed=args.seed, player_name=args.pilot)
    old = store.campaign_dir(c.id)
    if old.exists():               # --force: drop the old campaign's turn snapshots too
        import shutil
        shutil.rmtree(old)
    path = store.save(c, snapshot=True)
    print(f"New campaign '{c.name}' ({c.id}) created: {path}")
    print(f"You are {c.player_pilot().rank} {c.player_name}, "
          f"{c.squadrons[c.player_pilot().squadron_id].name}.")
    print(c.news[-1].text)
    print("\nNext: `cwdc ato` to see this turn's tasking.")


def cmd_status(args) -> None:
    from cwdc.engine.planner import period
    c = _load(args)
    print(f"{c.name} - turn {c.turn}, {c.time:%a %d %b %Y %H:%M} ({period(c)})")
    if c.pending_mission:
        pm = c.pending_mission
        print(f"PENDING MISSION: {pm.mission_id} -> fly it, then `cwdc ingest` (or `cwdc skip`)")
    print("\nSquadrons:")
    for sq in c.squadrons.values():
        if sq.side is not Side.BLUE and not args.all:
            continue
        afs = Counter(a.status for a in c.airframes.values() if a.squadron_id == sq.id)
        pilots = len(c.squadron_pilots(sq.id))
        print(f"  {sq.name:<34} {sq.aircraft_type:<10} @ {c.airbases[sq.base_id].dcs_name:<12} "
              f"ready {afs[AirframeStatus.READY]:>2}  repair {afs[AirframeStatus.DAMAGED]:>2}  "
              f"lost {afs[AirframeStatus.DESTROYED]:>2}  pilots {pilots}")
    if c.sectors:
        from cwdc.engine.ground import _fmt, active, power
        print("\nGround war:")
        for sec in c.sectors.values():
            print(f"  {sec.name:<22} {_fmt(sec):<28} red {power(c, sec.id, Side.RED):>4.0f} "
                  f"vs blue {power(c, sec.id, Side.BLUE):>4.0f}")
            for f in sorted(c.formations.values(), key=lambda f: f.side.value):
                if f.sector_id == sec.id and active(c, f) and (args.all or f.side is Side.BLUE):
                    print(f"      [{f.side.value:4}] {f.name:<50} {f.strength:>4.0f}/{f.max_strength:<4.0f}"
                          f" {f.posture.value}")
        from cwdc.engine import logistics
        supply = logistics.report(c)
        if supply:
            print("\nSupply (depot stock, formation supply level):")
            for line in supply:
                print(line)
        coming = sorted((f for f in c.formations.values()
                         if f.arrival_turn > c.turn and f.side is Side.BLUE), key=lambda f: f.arrival_turn)
        if coming:
            print("  Expected NATO reinforcements: " + ", ".join(
                f"{f.name} (turn {f.arrival_turn})" for f in coming))
    from cwdc.engine import airbases
    damaged = [ab for ab in c.airbases.values() if ab.damage > 0]
    if damaged or args.all:
        print("\nAirfields:")
        for ab in (c.airbases.values() if args.all else damaged):
            print("  " + airbases.describe(ab))
    print("\nObjectives:")
    for o in c.objectives.values():
        state = "DESTROYED" if o.destroyed else f"{len(o.alive_units)}/{len(o.units)} units"
        print(f"  [{o.side.value:4}] {o.name:<44} {state}")
    print("\nRecent news:")
    for n in c.news[-4:]:
        print(f"  {n.time:%d %b %H:%M}  {n.text}")


def cmd_ato(args) -> None:
    c = _load(args)
    print(f"Air tasking order - turn {c.turn}, {c.time:%d %b %Y %H:%M}\n")
    print("  ID    Callsign   Squadron        Task     Mission")
    for f in sorted(c.flights.values(), key=lambda f: (not f.player_eligible, f.id)):
        if f.side is not Side.BLUE:
            continue
        pk = c.packages[f.package_id]
        mark = "*" if f.player_eligible else " "
        where = _ll(f.area) if f.area else _ll(c.objectives[f.target_id].pos)
        print(f"{mark} {f.id}  {f.callsign:<10} {c.squadrons[f.squadron_id].name:<15} "
              f"{f.task.value:<8} {pk.description} ({where}) [{f.loadout or 'default'}]")
    red = [f for f in c.flights.values() if f.side is Side.RED]
    print(f"\nIntelligence expects ~{len(red)} Warsaw Pact flights this period.")
    print("* = you can fly it:  `cwdc fly <ID>`   or let the turn play out:  `cwdc skip`")


def cmd_fly(args) -> None:
    from cwdc.engine import planner
    from cwdc.missiongen import builder
    c = _load(args)
    fid = args.flight.upper()
    if fid not in c.flights:
        raise SystemExit(f"no flight {fid} this turn - see `cwdc ato`")
    if c.pending_mission and c.pending_mission.player_flight_id != fid:
        print(f"(replacing pending mission {c.pending_mission.mission_id})")
    if args.bot:
        for other in c.flights:                 # the player sits this one out
            planner.release_player(c, other)
    else:
        try:
            planner.assign_player(c, fid)
        except ValueError as e:
            raise SystemExit(str(e))
    if not dcs_paths.hook_is_current():
        print(f"hook installed/updated: {dcs_paths.install_hook()}")
    previous = c.pending_mission
    rec = builder.build(c, fid, dcs_paths.missions_dir(), start=args.start, bot=args.bot)
    if previous and previous.miz_path != rec.miz_path:
        Path(previous.miz_path).unlink(missing_ok=True)     # superseded, never flown
    c.pending_mission = rec
    store.save(c)
    f = c.flights[fid]
    print(f"Mission written: {rec.miz_path}")
    if args.bot:
        print(f"BOT mission around {f.callsign}: {c.packages[f.package_id].description}")
        print("No player slot: spectate (F10/F2), speed up with Ctrl+Z; it ends by itself.")
    else:
        print(f"You lead {f.callsign}: {c.packages[f.package_id].description}")
    others = [c.flights[x].callsign for x in rec.spawned_flights if x != fid]
    print(f"Also in this mission: {', '.join(others) or 'nobody else'}")
    print(f"\nIn DCS: Mission > My Missions > ColdWarDynamic > {Path(rec.miz_path).name}")
    print("Fly it, exit normally, then run `cwdc ingest`.")
    print("(Your in-game pilot name comes from your DCS Logbook profile, not the campaign.)")


def _load_result(c: Campaign, from_log: bool) -> dict:
    from cwdc.ingest import results
    rec = c.pending_mission
    path = dcs_paths.results_dir() / f"{rec.mission_id}.json"
    data = None
    if not from_log and path.is_file():
        data = results.load_result_file(path)
        if data.get("run_started", 0) < rec.generated_at - 60:
            print(f"ignoring {path.name}: it predates the generated mission")
            data = None
    if data is None:
        log = dcs_paths.saved_games_dcs() / "Logs" / "dcs.log"
        if log.is_file():
            data = results.from_dcs_log(log, rec.mission_id)
            if data:
                print(f"(result rebuilt from {log})")
    if data is None:
        raise SystemExit(f"no results for {rec.mission_id} yet. Fly the mission, or "
                         "`cwdc skip` to let it play out without you.")
    if not data.get("final") and not data.get("ended"):
        print("WARNING: the result is not final (DCS still running or crashed?) - using what was captured")
    return data


def cmd_ingest(args) -> None:
    from cwdc.engine import turn
    from cwdc.ingest.flown import results_from_dcs
    c = _load(args)
    if not c.pending_mission:
        raise SystemExit("no pending mission - `cwdc fly <ID>` first")
    data = _load_result(c, args.from_log)
    dcs_results, destroyed = results_from_dcs(c, c.pending_mission, data)
    lines = turn.end_turn(c, dcs_results, destroyed)
    store.save(c, snapshot=True)
    print("\n".join(lines))
    print(f"\nNow turn {c.turn}, {c.time:%d %b %H:%M}. `cwdc ato` for new tasking.")


def cmd_skip(args) -> None:
    from cwdc.engine import planner, turn
    c = _load(args)
    if c.pending_mission:
        planner.release_player(c, c.pending_mission.player_flight_id)
        print(f"(discarding pending mission {c.pending_mission.mission_id})")
    lines = turn.end_turn(c)
    store.save(c, snapshot=True)
    print("\n".join(lines))
    print(f"\nNow turn {c.turn}, {c.time:%d %b %H:%M}.")


def _tally(c: Campaign) -> dict:
    out = {}
    for side in Side:
        out[(side, "aircraft")] = sum(1 for a in c.airframes.values()
                                      if a.status is AirframeStatus.DESTROYED
                                      and c.squadrons[a.squadron_id].side is side)
        out[(side, "aircrew")] = sum(1 for p in c.pilots.values() if p.side is side
                                     and p.status in (PilotStatus.KIA, PilotStatus.MIA, PilotStatus.POW))
        out[(side, "ground")] = sum(f.strength for f in c.formations.values()
                                    if f.side is side and f.arrival_turn <= c.turn)
    return out


def cmd_autorun(args) -> None:
    """Play N turns with every sortie simulated (no DCS) - for testing the strategic layer."""
    from cwdc.engine import planner, turn
    c = _load(args)
    if c.pending_mission:
        planner.release_player(c, c.pending_mission.player_flight_id)
        print(f"(discarding pending mission {c.pending_mission.mission_id})")
    start = _tally(c)
    print(f"Autorun {args.turns} turns from turn {c.turn} ({c.time:%d %b %H:%M})")
    secs = "/".join(s.id[:4] for s in c.sectors.values())
    print(f"  turn  time           sorties | lost aircraft B/R | aircrew B/R | ground power B/R | front km ({secs})")
    for _ in range(args.turns):
        before, t0, n = _tally(c), c.time, len(c.flights)
        lines = turn.end_turn(c)
        store.save(c, snapshot=True)
        now = _tally(c)
        d = {k: now[k] - before[k] for k in now}
        print(f"  {c.turn - 1:>4}  {t0:%d %b %H:%M}  {n:>7} | "
              f"{d[(Side.BLUE, 'aircraft')]:>8} / {d[(Side.RED, 'aircraft')]:<6} | "
              f"{d[(Side.BLUE, 'aircrew')]:>4} / {d[(Side.RED, 'aircrew')]:<4} | "
              f"{now[(Side.BLUE, 'ground')]:>7.0f} / {now[(Side.RED, 'ground')]:<6.0f} | "
              + "/".join(f"{s.offset_km:.0f}" for s in c.sectors.values()))
        if args.verbose:
            for line in lines[1:]:
                print("        " + line)
    end = _tally(c)
    tot = {k: end[k] - start[k] for k in end}
    print(f"Totals: aircraft lost B {tot[(Side.BLUE, 'aircraft')]} / R {tot[(Side.RED, 'aircraft')]}, "
          f"aircrew B {tot[(Side.BLUE, 'aircrew')]} / R {tot[(Side.RED, 'aircrew')]}, "
          f"ground power now B {end[(Side.BLUE, 'ground')]:.0f} / R {end[(Side.RED, 'ground')]:.0f}")
    print(f"Now turn {c.turn}, {c.time:%d %b %Y %H:%M}. `cwdc rollback <turn>` to go back.")


def cmd_roster(args) -> None:
    c = _load(args)
    sq = next(s for s in c.squadrons.values() if s.player_squadron)
    print(f"{sq.name} roster")
    pilots = sorted((p for p in c.pilots.values() if p.squadron_id == sq.id),
                    key=lambda p: (p.status is not PilotStatus.ACTIVE, -p.sorties, p.name))
    for p in pilots:
        me = " <- you" if p.is_player else ""
        print(f"  {p.rank:<7} {p.name:<22} {p.status.value:<6} sorties {p.sorties:>3}  "
              f"air {p.air_kills}  ground {p.ground_kills}{me}")


def cmd_rollback(args) -> None:
    c = _load(args)
    c = store.rollback(c.id, args.turn)
    print(f"Rolled back to the start of turn {c.turn} ({c.time:%d %b %H:%M}).")


def register(sub) -> None:
    p = sub.add_parser("new", help="start a new campaign")
    p.add_argument("--name", default="Fulda Gap 1976")
    p.add_argument("--seed", type=int, default=1976)
    p.add_argument("--pilot", default="Player", help="your pilot's name")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)
    for name, func, hlp in [("status", cmd_status, "campaign overview"),
                            ("ato", cmd_ato, "this turn's air tasking order"),
                            ("roster", cmd_roster, "your squadron's pilots"),
                            ("skip", cmd_skip, "don't fly this turn; simulate everything"),
                            ]:
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--campaign")
        if name == "status":
            p.add_argument("--all", action="store_true", help="include enemy squadrons")
        p.set_defaults(func=func)
    p = sub.add_parser("fly", help="generate the DCS mission for a flight")
    p.add_argument("flight")
    p.add_argument("--start", choices=["runway", "ramp", "air"], default="runway",
                   help="runway (default, hot on the runway), ramp (warm on parking) or "
                        "air (airborne ~50 nm from the target)")
    p.add_argument("--bot", action="store_true",
                   help="AI-only mission around this flight (any flight id, either side); "
                        "you spectate, it ends itself")
    p.add_argument("--campaign")
    p.set_defaults(func=cmd_fly)
    p = sub.add_parser("ingest", help="read the flown mission's results and end the turn")
    p.add_argument("--from-log", action="store_true", help="rebuild results from dcs.log")
    p.add_argument("--campaign")
    p.set_defaults(func=cmd_ingest)
    p = sub.add_parser("autorun", help="simulate N turns without DCS (strategic-layer testing)")
    p.add_argument("turns", type=int)
    p.add_argument("--verbose", "-v", action="store_true", help="print every turn's debrief")
    p.add_argument("--campaign")
    p.set_defaults(func=cmd_autorun)
    p = sub.add_parser("rollback", help="restore the snapshot from the start of a turn")
    p.add_argument("turn", type=int)
    p.add_argument("--campaign")
    p.set_defaults(func=cmd_rollback)
