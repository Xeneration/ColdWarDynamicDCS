"""The pre-war crisis: 2-4 days of rising tension before the Warsaw Pact attacks.

Tension (0-100) drifts upward every turn as both sides mobilise; random events (news) push it
up or, rarely, down. Incidents in the air (engine.apply, Step 2) push it up hard. The war
starts at the start of a turn once tension has reached 100, or at the rolled deadline -
it always comes.
"""
from __future__ import annotations

from datetime import datetime

from cwdc import theater as T
from cwdc.engine import rng
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
    c.time = datetime.fromisoformat(rules["start"])
    c.tension = rules.get("tension_start", TENSION_START)
    lo, hi = rules.get("turns", (8, 16))
    c.crisis_deadline_turn = 1 + r.randint(lo, hi)
    c.war_start_turn = c.crisis_deadline_turn
    c.add_news(f"{c.time:%d %b %Y}: Crisis in Central Europe. Warsaw Pact forces in the GDR and "
               "Czechoslovakia are on the move. NATO air defence goes to a higher alert state; "
               "Quick Reaction Alert aircraft stand ready, weapons tight.")


def raise_tension(c: Campaign, amount: float, why: str = "") -> None:
    if c.at_war:
        return
    c.tension = max(0.0, min(100.0, c.tension + amount))
    if why:
        c.add_news(why)


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
        c.add_news(text)
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
    c.add_news(f"{c.time:%d %b %Y %H:%M}: WAR. Warsaw Pact forces have crossed the inner German "
               "border. USAFE squadrons are committed to the defence of the Fulda Gap; "
               "ROE weapons free.")
