"""Geography: coordinate conversion, territory (which side of the front), data files."""
from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import yaml

from cwdc.model import Pos, Side

DATA_DIR = Path(__file__).resolve().parent / "data"


def load_data(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))


# campaign.theater -> theatre data file. The engine works on one theatre at a time: `use(c)` (or
# `activate`) switches the map, the coordinate conversions and which side of the front is whose.
THEATRE_FILES = {"fulda_1976": "theater_fulda_1976.yaml", "syria": "theater_syria.yaml"}
_active = threading.local()          # per thread: the web UI serves several campaigns at once


def activate(theater: str) -> None:
    if theater not in THEATRE_FILES:
        raise ValueError(f"unknown theatre {theater!r}")
    _active.theater = theater


def use(c) -> None:
    """Switch the geography to a campaign's theatre (call on every way into the engine)."""
    activate(getattr(c, "theater", None) or "fulda_1976")


def active() -> str:
    return getattr(_active, "theater", "fulda_1976")


def geometry(theater: str | None = None) -> dict:
    """{dcs_terrain, blue_side (west|north), lands {blue, red}, border_note, blue_zones} of a theatre
    (default: the active one)."""
    return _geometry(theater or active())


@lru_cache(maxsize=None)
def _geometry(theater: str) -> dict:
    th = load_data(THEATRE_FILES[theater])
    g = th.get("geometry", {})
    return {"dcs_terrain": g.get("dcs_terrain", "Germany"), "blue_side": g.get("blue_side", "west"),
            "lands": g.get("lands", {"blue": "West Germany", "red": "East Germany"}),
            "border_note": g.get("border_note", "Pre-war inner German border (1976). Everything west of "
                                                "it was West Germany (NATO)."),
            "blue_zones": [[tuple(p) for p in z] for z in g.get("blue_zones", [])],
            "front_shift": g.get("front_shift", "point")}


@lru_cache(maxsize=None)
def _terrain(name: str):
    import logging
    logging.getLogger().setLevel(logging.CRITICAL)   # pydcs livery parse noise
    import dcs.terrain
    return getattr(dcs.terrain, name)()


def terrain():
    return _terrain(geometry()["dcs_terrain"])


def nato_dir() -> tuple[float, float]:
    """Unit (x, z) pointing from the front into NATO's side (DCS x is north, z is east)."""
    return {"west": (0.0, -1.0), "north": (1.0, 0.0)}[geometry()["blue_side"]]


def toward_nato(pos: Pos, dist: float) -> Pos:
    dx, dz = nato_dir()
    return Pos(x=pos.x + dx * dist, z=pos.z + dz * dist)


def toward_enemy(pos: Pos, dist: float) -> Pos:
    return toward_nato(pos, -dist)


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
    """NATO territory: the front line closed off far to NATO's side (west, or north)."""
    first, last = front_line[0], front_line[-1]
    if geometry()["blue_side"] == "north":            # front listed west -> east, NATO above it
        return [*front_line, (last[0], last[1] + 5), (last[0] + 10, last[1] + 5),
                (first[0] + 10, first[1] - 5), (first[0], first[1] - 5)]
    return [*front_line, (last[0] - 5, last[1]), (last[0] - 5, 0.0),     # listed north -> south
            (first[0] + 5, 0.0), (first[0] + 5, first[1])]


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


def airbase_name(theater_file: str, ab) -> str:
    """The name players know a base by (Rhein-Main), not DCS's airport name (Frankfurt)."""
    th = load_data(theater_file)
    gen = th.get("generator", {}).get("bases", {})
    carriers = th.get("generator", {}).get("carriers", [])
    for a in th.get("airbases", []) + [b for side in gen.values() for b in side] + carriers:
        if a["id"] == ab.id:
            return a.get("name", ab.dcs_name)
    return ab.dcs_name


def territory_polygons(front_line: list[tuple[float, float]], west: float = 4.5,
                       east: float = 16.0) -> dict[str, list[tuple[float, float]]]:
    """Each side's ground for display: the front line closed off to each side's rear."""
    if geometry()["blue_side"] == "north":
        w, e = front_line[0], front_line[-1]          # the front starts on the coast: keep the
        w_far, e_far = (w[0], w[1] - 3), (e[0], e[1] + 3)   # enemy's shading off the sea (and Cyprus)
        w_coast = (w[0], w[1] - 0.2)
        return {"blue": [w_far, *front_line, e_far, (e_far[0] + 4, e_far[1]), (w_far[0] + 4, w_far[1])],
                "red": [w_coast, *front_line, e_far, (e_far[0] - 5, e_far[1]), (w_coast[0] - 5, w_coast[1])]}
    north, south = front_line[0], front_line[-1]
    s_far, n_far = (south[0] - 3, south[1]), (north[0] + 3, north[1])      # run past the map edges
    return {"blue": [n_far, *front_line, s_far, (s_far[0], west), (n_far[0], west)],
            "red": [n_far, *front_line, s_far, (s_far[0], east), (n_far[0], east)]}


def side_at(front_line: list[tuple[float, float]], lat: float, lon: float) -> Side:
    if any(_inside(lat, lon, z) for z in geometry()["blue_zones"]):     # e.g. Cyprus, off the front
        return Side.BLUE
    return Side.BLUE if _inside(lat, lon, _blue_polygon(front_line)) else Side.RED


def side_at_pos(front_line, pos: Pos) -> Side:
    return side_at(front_line, *pos_to_latlon(pos))


@lru_cache(maxsize=1)
def airframe_catalogue() -> dict[str, dict]:
    """Per-DCS-type flight data: hand-written airframes.yaml over era-database defaults."""
    from cwdc import era
    out = era.catalogue_defaults()
    out.update(load_data("airframes.yaml"))
    return out
