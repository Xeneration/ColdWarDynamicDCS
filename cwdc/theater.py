"""Geography: coordinate conversion, territory (which side of the front), data files."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from cwdc.model import Pos, Side

DATA_DIR = Path(__file__).resolve().parent / "data"


def load_data(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def terrain():
    import logging
    logging.getLogger().setLevel(logging.CRITICAL)   # pydcs livery parse noise
    from dcs.terrain import Germany
    return Germany()


def latlon_to_pos(lat: float, lon: float) -> Pos:
    from dcs.mapping import LatLng, Point
    p = Point.from_latlng(LatLng(lat, lon), terrain())
    return Pos(x=p.x, z=p.y)          # pydcs y == DCS z


def pos_to_latlon(pos: Pos) -> tuple[float, float]:
    from dcs.mapping import Point
    ll = Point(pos.x, pos.z, terrain()).latlng()
    return ll.lat, ll.lng


def to_point(pos: Pos):
    from dcs.mapping import Point
    return Point(pos.x, pos.z, terrain())


def _blue_polygon(front_line: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """NATO territory: the front line closed off far to the west."""
    north, south = front_line[0], front_line[-1]
    return [*front_line, (south[0] - 5, south[1]), (south[0] - 5, 0.0),
            (north[0] + 5, 0.0), (north[0] + 5, north[1])]


def _inside(lat: float, lon: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def side_at(front_line: list[tuple[float, float]], lat: float, lon: float) -> Side:
    return Side.BLUE if _inside(lat, lon, _blue_polygon(front_line)) else Side.RED


def side_at_pos(front_line, pos: Pos) -> Side:
    return side_at(front_line, *pos_to_latlon(pos))


@lru_cache(maxsize=1)
def airframe_catalogue() -> dict[str, dict]:
    return load_data("airframes.yaml")
