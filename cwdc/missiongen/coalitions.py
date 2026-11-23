"""Period-correct coalition setup for 1970s Central Europe."""
from __future__ import annotations

from dcs import countries
from dcs.coalition import Coalition
from dcs.mission import Mission

NATO = [countries.USA, countries.Germany, countries.UK, countries.Belgium,
        countries.TheNetherlands, countries.Canada, countries.Denmark]
WARSAW_PACT = [countries.USSR, countries.GDR, countries.Poland]


def setup(mission: Mission) -> None:
    """Replace pydcs' modern default coalitions with NATO vs Warsaw Pact."""
    blue = Coalition("blue", mission.coalition["blue"].bullseye)
    red = Coalition("red", mission.coalition["red"].bullseye)
    for c in NATO:
        blue.add_country(c())
    for c in WARSAW_PACT:
        red.add_country(c())
    mission.coalition = {"blue": blue, "red": red}
