"""Phase G guard rails: generated wars stay in the tuned band in early and late eras on both
theatres (see tools/balance.py --year/--theatre for the full report)."""
from collections import Counter, defaultdict

import pytest

from cwdc.model import Side
from tools import balance


@pytest.mark.parametrize("theatre,year", [("germany", 1966), ("germany", 1985), ("syria", 1976)])
def test_generated_wars_first_week(theatre, year):
    stats = {"cause": Counter(), "task": defaultdict(lambda: [0, 0]), "sorties": Counter(),
             "lost_air": Counter(), "destroyed": Counter()}
    for seed in (2, 3, 4):
        balance.run(seed, 7, stats, year=year, theatre=theatre)
    rate = {s: stats["lost_air"][s] / stats["sorties"][s] for s in Side}
    for s in Side:
        assert 0.01 <= rate[s] <= 0.08, f"{theatre} {year}: {s.value} loses {rate[s]:.1%} per sortie"
    # NATO's air war is at least even: its squadrons are not traded worse than the East's
    assert rate[Side.BLUE] <= rate[Side.RED] * 1.15, (theatre, year, rate)
    assert stats["sorties"][Side.RED] >= 0.5 * stats["sorties"][Side.BLUE]   # the East still flies
