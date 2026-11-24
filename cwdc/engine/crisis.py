"""The pre-war crisis: 2-4 days of rising tension before the Warsaw Pact attacks.

Tension (0-100) drifts upward every turn as both sides mobilise; random events (news) push it
up or, rarely, down. Incidents in the air (engine.apply, Step 2) push it up hard. The war
starts at the start of a turn once tension has reached 100, or at the rolled deadline -
it always comes.
"""
from __future__ import annotations

from datetime import datetime

from cwdc import theater as T
from cwdc.engine import news, rng
from cwdc.model import Campaign

TENSION_START = 20.0
DRIFT = (3.0, 7.0)               # tension added per turn by mobilisation
P_EVENT = 0.5                    # chance of a crisis event (news) each turn
INCIDENT_TENSION = 25.0          # NATO fires first (engine.apply)
KILL_DEADLINE_TURNS = 2          # ...and shoots one down: war within this many turns

# (tension change, headline) - {date} is filled in
EVENTS = [
    (6, "Soviet Group of Forces Germany cancels all leave; armoured units leave their barracks."),
    (5, "East German border troops reinforced; new obstacles seen along the inner German border."),
    (4, "Warsaw Pact announces large-scale 'autumn exercise' months ahead of schedule."),
    (5, "Moscow recalls its ambassador from Bonn for consultations."),
    (3, "USAREUR raises its alert state; reservists recalled in West Germany."),
    (4, "Soviet air regiments disperse to forward and reserve airfields in the GDR."),
    (2, "Rail traffic from Poland into the GDR reported unusually heavy, mostly military."),
    (-4, "Talks in Vienna continue; both sides say they want to avoid 'miscalculation'."),
    (-3, "The Soviet embassy in Bonn calls reports of mobilisation 'Western hysteria'."),
]


def crisis_rules(c: Campaign) -> dict:
    from cwdc.engine.factory import THEATERS
    return T.load_data(THEATERS[c.theater][0]).get("crisis", {})


def begin(c: Campaign) -> None:
    """Put a freshly created campaign into the crisis phase."""
    rules = crisis_rules(c)
    r = rng(c.seed, 0, "crisis")
    c.phase = "crisis"
    c.time = datetime.fromisoformat(rules["start"]).replace(year=c.time.year)   # campaign-maker year
    c.tension = rules.get("tension_start", TENSION_START)
    lo, hi = rules.get("turns", (8, 16))
    c.crisis_deadline_turn = 1 + r.randint(lo, hi)
    c.war_start_turn = c.crisis_deadline_turn
    text = rules.get("headline", "Crisis in Central Europe. Warsaw Pact forces in the GDR and "
                     "Czechoslovakia are on the move.")
    news.post(c, f"{c.time:%d %b %Y}: {text} NATO air defence goes to a higher alert state; "
               "Quick Reaction Alert aircraft stand ready, weapons tight.", "diplomatic")


def raise_tension(c: Campaign, amount: float, why: str = "") -> None:
    if c.at_war:
        return
    c.tension = max(0.0, min(100.0, c.tension + amount))
    if why:
        news.post(c, why, "military")


def bring_forward(c: Campaign, turns: int) -> None:
    """The war starts within `turns` turns at the latest."""
    if not c.at_war:
        c.crisis_deadline_turn = min(c.crisis_deadline_turn, c.turn + turns)


def crisis_turn(c: Campaign) -> list[str]:
    """End-of-turn crisis update. Returns debrief lines."""
    r = rng(c.seed, c.turn, "crisis")
    before = c.tension
    c.tension = min(100.0, c.tension + r.uniform(*DRIFT))
    lines = []
    if r.random() < P_EVENT:
        delta, text = r.choice(EVENTS)
        c.tension = max(0.0, min(100.0, c.tension + delta))
        news.post(c, text, "diplomatic" if delta < 0 or "Moscow" in text or "Talks" in text else "military")
        lines.append(text)
    lines.append(f"Tension {before:.0f} -> {c.tension:.0f} / 100.")
    return lines


def due(c: Campaign) -> bool:
    """Does the war start this turn (checked right after the turn advances)?"""
    return not c.at_war and (c.tension >= 100 or c.turn >= c.crisis_deadline_turn)


def start_war(c: Campaign) -> None:
    """The Warsaw Pact attacks at the start of the current turn."""
    c.phase = "war"
    c.war_start_turn = c.turn
    c.tension = 100.0
    for f in c.formations.values():         # mobilisation timetables run from the war's start
        f.arrival_turn += c.war_start_turn - 1
    news.post(c, f"{c.time:%d %b %Y %H:%M}: WAR. Warsaw Pact forces have crossed the inner German "
               "border. USAFE squadrons are committed to the defence of the Fulda Gap; "
               "ROE weapons free.", "military")


# ----------------------------------------------------------------------------- air incidents
P_SIM_INTERCEPT = 0.8            # a simulated QRA finds and shadows its probe
P_CAP_INTERCEPT = 0.4            # ...or a barrier CAP does, when no QRA was sent
INTERCEPTED = -2.0               # tension change: probe seen off
UNOPPOSED = 3.0                  # tension change: probe got its photographs
P_SIM_INCIDENT = (0.02, 0.10)    # chance of a (bloodless) incident per probe, at tension 0 / 100
SIM_INCIDENT = 8.0
HOSTILE_ACT = 15.0               # the Warsaw Pact fired first in a DCS mission


def air_ops(c: Campaign, flights) -> list[str]:
    """Simulated crisis air activity for flights not flown in DCS: probes are intercepted or
    not, and the odd incident happens. Nobody is shot down in the simulation."""
    from cwdc.model import Side, Task
    r = rng(c.seed, c.turn, "crisis-air")
    flights = list(flights)
    qra = {f.intercepts: f for f in flights if f.task is Task.QRA and f.intercepts}
    caps = [f for f in flights if f.side is Side.BLUE and f.task is Task.CAP]
    lines = []
    for probe in (f for f in flights if f.task is Task.RECON):
        q = qra.get(probe.id)
        caught = r.random() < (P_SIM_INTERCEPT if q else P_CAP_INTERCEPT if caps else 0.0)
        if caught:
            c.tension = max(0.0, c.tension + INTERCEPTED)
            who = q.callsign if q else caps[0].callsign
            lines.append(f"{who} intercepted {probe.callsign} at the border and shadowed it back east.")
            lo, hi = P_SIM_INCIDENT
            if r.random() < lo + (hi - lo) * c.tension / 100:
                c.tension = min(100.0, c.tension + SIM_INCIDENT)
                text = (f"Incident at the border: {probe.callsign} locked up {who} with its radar and "
                        "made a firing pass before turning away. No shots were fired.")
                news.post(c, text, "air")
                lines.append(text)
        else:
            c.tension = min(100.0, c.tension + UNOPPOSED)
            lines.append(f"{probe.callsign} probed the border unopposed and turned back.")
    return lines


def dcs_incidents(c: Campaign, mission, data: dict) -> list[str]:
    """Consequences of a crisis mission flown in DCS: intercepts, hostile acts and ROE breaches
    recorded by the mission runtime. Call before the turn is closed."""
    from cwdc.engine.crews import GROUNDED
    from cwdc.model import Task
    if c.at_war or mission is None:
        return []
    lines = []
    events = data.get("events", []) or []
    shots = [e for e in events if e.get("event") == "S_EVENT_SHOT"]
    first = shots[0] if shots else None
    first_side = ((first or {}).get("initiator") or {}).get("coalition")
    breach = next((e for e in shots if e.get("roe_violation")), None)
    if breach is not None:
        c.tension = min(100.0, c.tension + INCIDENT_TENSION)
        unit = breach["initiator"].get("name")
        pid = (mission.manifest.get(unit) or {}).get("pilot")
        pilot = c.pilots.get(pid) if pid else None
        if pilot is not None:
            pilot.fatigue = max(pilot.fatigue, GROUNDED + 0.1)        # grounded pending inquiry
        who = f"{pilot.rank} {pilot.name}" if pilot else unit
        text = (f"ROE BREACH: {who} fired first on a Warsaw Pact aircraft while weapons tight. "
                "Moscow protests; the pilot is grounded pending an inquiry.")
        news.post(c, text, "air")
        lines.append(text)
        c.counters["roe_breaches"] = c.counters.get("roe_breaches", 0) + 1
        red_down = any(e.get("event") in ("S_EVENT_KILL",) and (e.get("target") or {}).get("coalition") == 1
                       for e in events)
        if red_down:
            bring_forward(c, KILL_DEADLINE_TURNS)
            text = "A Warsaw Pact aircraft was shot down. War now looks only hours away."
            news.post(c, text, "air")
            lines.append(text)
    elif first_side == 1:
        c.tension = min(100.0, c.tension + HOSTILE_ACT)
        text = "HOSTILE ACT: Warsaw Pact aircraft opened fire on NATO aircraft over the border."
        news.post(c, text, "air")
        lines.append(text)
    intercepted = {(e.get("target") or {}).get("group") for e in events if e.get("event") == "CWDC_INTERCEPT"}
    for fid in mission.spawned_flights:
        f = c.flights.get(fid)
        if f is None or f.task is not Task.RECON:
            continue
        if f"{f.id} {f.callsign}" in intercepted:
            c.tension = max(0.0, c.tension + INTERCEPTED)
            lines.append(f"{f.callsign} was intercepted and shadowed back east.")
        else:
            c.tension = min(100.0, c.tension + UNOPPOSED)
            lines.append(f"{f.callsign} probed the border without being intercepted.")
    return lines
