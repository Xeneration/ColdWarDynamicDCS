"""Coalitions: NATO (and its friends) against the Warsaw Pact, for any year of the campaign maker."""
from __future__ import annotations

from dcs import countries
from dcs.coalition import Coalition
from dcs.mission import Mission

NATO = [countries.USA, countries.Germany, countries.UK, countries.Belgium,
        countries.TheNetherlands, countries.Canada, countries.Denmark]
WARSAW_PACT = [countries.USSR, countries.GDR, countries.Poland]


def _era_countries(side: str) -> list:
    from cwdc import era
    by_name = {c.name: c for c in countries.country_dict.values()}
    return [by_name[s["country"]] for s in era.nations().values()
            if s["side"] == side and s["country"] in by_name]


def setup(mission: Mission) -> None:
    """Replace pydcs' modern default coalitions with NATO vs Warsaw Pact."""
    blue = Coalition("blue", mission.coalition["blue"].bullseye)
    red = Coalition("red", mission.coalition["red"].bullseye)
    for side, coal, base in (("west", blue, NATO), ("east", red, WARSAW_PACT)):
        seen = set()
        for c in base + _era_countries(side):
            if c.name not in seen:
                seen.add(c.name)
                coal.add_country(c())
    mission.coalition = {"blue": blue, "red": red}
