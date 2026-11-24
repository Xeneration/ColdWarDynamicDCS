"""Campaign weather: a seasonal state machine rolled every turn (August in central Germany).

The state is stored on the campaign (Campaign.weather), shown in the UI and briefings, applied to
generated missions, and affects the war: 1976 bombing is visual, so low cloud, rain and fog cut
strike effect (resolver) and how many strikes are planned (planner); fog at dawn also limits
launches from the airfields.
"""
from __future__ import annotations

from cwdc.engine import rng
from cwdc.model import Campaign, WeatherState

KINDS = ("clear", "scattered", "broken", "overcast", "rain", "fog")

# next-turn weights, from the current kind (fog is handled separately at dawn)
TRANSITIONS = {
    "clear":     {"clear": .55, "scattered": .35, "broken": .10},
    "scattered": {"clear": .25, "scattered": .45, "broken": .20, "overcast": .10},
    "broken":    {"scattered": .30, "broken": .35, "overcast": .20, "rain": .15},
    "overcast":  {"broken": .30, "overcast": .35, "rain": .35},
    "rain":      {"broken": .20, "overcast": .40, "rain": .40},
    "fog":       {"clear": .50, "scattered": .50},
}
P_DAWN_FOG = 0.35            # after a clear or scattered night with light wind
FOG_MAX_WIND = 8             # kt

# (cloud base m, visibility m) ranges per kind
CLOUD_BASE = {"clear": (0, 0), "scattered": (1500, 3500), "broken": (900, 2500),
              "overcast": (500, 1800), "rain": (420, 1200), "fog": (0, 0)}
VISIBILITY = {"clear": (40_000, 60_000), "scattered": (30_000, 50_000), "broken": (15_000, 30_000),
              "overcast": (8_000, 20_000), "rain": (3_000, 8_000), "fog": (600, 1_500)}
STRIKE_FACTOR = {"clear": 1.0, "scattered": 1.0, "broken": 0.85, "overcast": 0.7, "rain": 0.55,
                 "fog": 0.5}
LOW_CEILING_M = 900          # below this an overcast is worse for visual bombing
FOG_LAUNCH_FACTOR = 0.5

LABELS = {"clear": "clear skies", "scattered": "scattered cloud", "broken": "broken cloud",
          "overcast": "overcast", "rain": "rain, low overcast", "fog": "fog"}


def _roll(kind: str, r, hour: int) -> WeatherState:
    lo, hi = CLOUD_BASE[kind]
    vlo, vhi = VISIBILITY[kind]
    wind = r.randint(3, 15) + (5 if kind == "rain" else 0)
    if kind == "fog":
        wind = r.randint(0, 4)
    night = hour < 6 or hour >= 20
    temp = r.uniform(13, 17) if night else r.uniform(21, 27)
    if kind in ("rain", "overcast"):
        temp -= 3
    return WeatherState(kind=kind, cloud_base_m=r.randint(lo, hi) if hi else 0,
                        visibility_m=r.randint(vlo, vhi), wind_dir=r.randrange(220, 290, 10),
                        wind_kts=wind, temp_c=round(temp, 1))


def initial(c: Campaign) -> None:
    r = rng(c.seed, 0, "weather")
    c.weather = _roll(r.choice(["clear", "scattered", "scattered", "broken"]), r, c.time.hour)


def advance(c: Campaign) -> None:
    """Roll the weather for the (new) current turn."""
    r = rng(c.seed, c.turn, "weather")
    prev = c.weather
    weights = TRANSITIONS[prev.kind]
    kind = r.choices(list(weights), weights=list(weights.values()))[0]
    dawn = 5 <= c.time.hour < 11
    if dawn and prev.kind in ("clear", "scattered") and prev.wind_kts <= FOG_MAX_WIND \
            and r.random() < P_DAWN_FOG:
        kind = "fog"
    c.weather = _roll(kind, r, c.time.hour)


def strike_factor(w: WeatherState) -> float:
    """Multiplier on air-to-ground effect (and on how many strikes are worth planning)."""
    f = STRIKE_FACTOR[w.kind]
    if w.kind == "overcast" and w.cloud_base_m < LOW_CEILING_M:
        f -= 0.15
    return f


def launch_factor(w: WeatherState) -> float:
    return FOG_LAUNCH_FACTOR if w.kind == "fog" else 1.0


def describe(w: WeatherState) -> str:
    parts = [LABELS[w.kind].capitalize()]
    if w.cloud_base_m:
        parts.append(f"base {w.cloud_base_m * 3.28084 / 100:.0f}00 ft")
    vis = w.visibility_m
    parts.append(f"visibility {vis / 1000:.0f} km" if vis >= 2000 else f"visibility {vis} m")
    parts.append(f"wind {w.wind_dir:03d}/{w.wind_kts} kt")
    parts.append(f"{w.temp_c:.0f} C")
    return ", ".join(parts)
