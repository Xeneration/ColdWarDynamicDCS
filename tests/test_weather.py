import logging

import dcs

from cwdc.engine import planner, turn, weather
from cwdc.engine.factory import new_campaign
from cwdc.missiongen import builder
from cwdc.model import Side, WeatherState


def _kinds(seed, n=40):
    c = new_campaign("Wx", seed=seed)
    out = [(c.time.hour, c.weather.kind)]
    for _ in range(n):
        turn._advance(c)
        out.append((c.time.hour, c.weather.kind))
    return out


def test_weather_is_deterministic_seasonal_and_fog_only_at_dawn():
    a, b = _kinds(5), _kinds(5)
    assert a == b
    seen = {k for _, k in a + _kinds(6) + _kinds(7)}
    assert seen <= set(weather.KINDS) and len(seen) >= 3
    assert all(h in (5, 6, 7, 8, 9, 10) for h, k in a + _kinds(6) + _kinds(7) if k == "fog")


def test_bad_weather_means_fewer_strikes_and_fog_fewer_launches():
    def strikes(kind, seed):
        c = new_campaign("Wx", seed=seed)
        c.weather = WeatherState(kind=kind, cloud_base_m=600 if kind != "clear" else 0)
        planner.plan_turn(c)
        return sum(1 for p in c.packages.values() if p.target_id and p.side is Side.BLUE), \
            sum(1 for f in c.flights.values())
    clear = [strikes("clear", s) for s in range(8)]
    rain = [strikes("rain", s) for s in range(8)]
    fog = [strikes("fog", s) for s in range(8)]
    assert sum(r[0] for r in rain) < sum(r[0] for r in clear)
    assert sum(f[1] for f in fog) < sum(r[1] for r in clear)
    assert weather.strike_factor(WeatherState(kind="overcast", cloud_base_m=500)) < \
        weather.strike_factor(WeatherState(kind="overcast", cloud_base_m=1500))


def test_mission_gets_the_campaign_weather(tmp_path):
    c = new_campaign("Wx", seed=3)
    f = next(x for x in c.flights.values() if x.player_eligible)
    logging.disable(logging.CRITICAL)
    for kind in ("rain", "fog", "clear"):
        c.weather = WeatherState(kind=kind, cloud_base_m=800 if kind == "rain" else 0,
                                 visibility_m=900 if kind == "fog" else 5000, wind_kts=10)
        rec = builder.build(c, f.id, tmp_path, start="air")
        m = dcs.Mission()
        m.load_file(rec.miz_path)
        w = m.weather
        if kind == "rain":
            assert w.clouds_preset is not None and "Rain" in w.clouds_preset.name
        if kind == "fog":
            assert w.enable_fog and w.fog_visibility == 900
        if kind == "clear":
            assert w.clouds_preset is None and not w.enable_fog
        assert any(l.startswith("Weather: ") for l in rec.briefing)
    assert "visibility" in weather.describe(c.weather)
