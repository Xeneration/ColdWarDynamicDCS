"""Campaign commands: new / status / ato / fly / ingest / skip / roster / rollback."""
from __future__ import annotations

from collections import Counter

from cwdc import dcs_paths, store
from cwdc import theater as T
from cwdc.model import AirframeStatus, Campaign, PilotStatus, Side


def _ll(pos) -> str:
    lat, lon = T.pos_to_latlon(pos)
    return f"{lat:.2f}N {lon:.2f}E"


def cmd_new(args) -> None:
    from cwdc.engine.factory import new_campaign, slug
    if (store.campaign_dir(slug(args.name)) / "campaign.json").exists() and not args.force:
        raise SystemExit(f"campaign '{slug(args.name)}' exists - use --force to overwrite")
    c = new_campaign(args.name, seed=args.seed, player_name=args.pilot)
    path = store.save(c, snapshot=True)
    print(f"New campaign '{c.name}' ({c.id}) created: {path}")
    print(f"You are {c.player_pilot().rank} {c.player_name}, "
          f"{c.squadrons[c.player_pilot().squadron_id].name}.")
    print(c.news[-1].text)
    print("\nNext: `cwdc ato` to see this turn's tasking.")


def cmd_status(args) -> None:
    from cwdc.engine.planner import period
    c = store.load(args.campaign)
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
    print("\nObjectives:")
    for o in c.objectives.values():
        state = "DESTROYED" if o.destroyed else f"{len(o.alive_units)}/{len(o.units)} units"
        print(f"  [{o.side.value:4}] {o.name:<44} {state}")
    print("\nRecent news:")
    for n in c.news[-4:]:
        print(f"  {n.time:%d %b %H:%M}  {n.text}")


def cmd_ato(args) -> None:
    c = store.load(args.campaign)
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
    c = store.load(args.campaign)
    fid = args.flight.upper()
    if fid not in c.flights:
        raise SystemExit(f"no flight {fid} this turn - see `cwdc ato`")
    if c.pending_mission and c.pending_mission.player_flight_id != fid:
        print(f"(replacing pending mission {c.pending_mission.mission_id})")
    try:
        planner.assign_player(c, fid)
    except ValueError as e:
        raise SystemExit(str(e))
    if not dcs_paths.hook_is_current():
        print(f"hook installed/updated: {dcs_paths.install_hook()}")
    rec = builder.build(c, fid, dcs_paths.missions_dir())
    c.pending_mission = rec
    store.save(c)
    f = c.flights[fid]
    print(f"Mission written: {rec.miz_path}")
    print(f"You lead {f.callsign}: {c.packages[f.package_id].description}")
    others = [c.flights[x].callsign for x in rec.spawned_flights if x != fid]
    print(f"Also in this mission: {', '.join(others) or 'nobody else'}")
    print("\nFly it in DCS, exit normally, then run `cwdc ingest`.")


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
    c = store.load(args.campaign)
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
    c = store.load(args.campaign)
    if c.pending_mission:
        planner.release_player(c, c.pending_mission.player_flight_id)
        print(f"(discarding pending mission {c.pending_mission.mission_id})")
    lines = turn.end_turn(c)
    store.save(c, snapshot=True)
    print("\n".join(lines))
    print(f"\nNow turn {c.turn}, {c.time:%d %b %H:%M}.")


def cmd_roster(args) -> None:
    c = store.load(args.campaign)
    sq = next(s for s in c.squadrons.values() if s.player_squadron)
    print(f"{sq.name} roster")
    pilots = sorted((p for p in c.pilots.values() if p.squadron_id == sq.id),
                    key=lambda p: (p.status is not PilotStatus.ACTIVE, -p.sorties, p.name))
    for p in pilots:
        me = " <- you" if p.is_player else ""
        print(f"  {p.rank:<7} {p.name:<22} {p.status.value:<6} sorties {p.sorties:>3}  "
              f"air {p.air_kills}  ground {p.ground_kills}{me}")


def cmd_rollback(args) -> None:
    c = store.load(args.campaign)
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
    p.add_argument("--campaign")
    p.set_defaults(func=cmd_fly)
    p = sub.add_parser("ingest", help="read the flown mission's results and end the turn")
    p.add_argument("--from-log", action="store_true", help="rebuild results from dcs.log")
    p.add_argument("--campaign")
    p.set_defaults(func=cmd_ingest)
    p = sub.add_parser("rollback", help="restore the snapshot from the start of a turn")
    p.add_argument("turn", type=int)
    p.add_argument("--campaign")
    p.set_defaults(func=cmd_rollback)
