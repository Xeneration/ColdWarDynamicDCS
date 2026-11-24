"""Kneeboard pages for the player's aircraft type, drawn with Pillow.

1. Mission card: flight, crews, ROE, tasking, waypoints (lat/lon, altitude), target.
2. Threats & comms: SAMs, AAA, expected fighters, GCI, bullseye, divert, codewords, weather.
3. Route sketch: legs, SAM rings, bullseye, front line, target, with a 20 nm scale bar.
DCS shows aircraft-type kneeboards to every aircraft of that type, so pages are labelled with
the flight's callsign.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from cwdc.model import Pos

W, H = 768, 1024
PAPER, INK, RED, BLUE, GREY = (238, 234, 222), (25, 25, 25), (170, 30, 30), (30, 60, 150), (120, 120, 120)
MARGIN = 36


@dataclass
class KneeboardInfo:
    title: str                                   # e.g. "COLT 1 - QRA"
    subtitle: str                                # date / turn
    lines: list[str]                             # page 1 header lines (flight, crews, ROE, tasking)
    waypoints: list[tuple[str, float, float, int, str]]   # name, lat, lon, alt ft, "MSL"/"AGL"
    threats: list[str] = field(default_factory=list)
    comms: list[str] = field(default_factory=list)
    route: list[Pos] = field(default_factory=list)
    rings: list[tuple[Pos, float, str]] = field(default_factory=list)   # centre, radius m, label
    front: list[Pos] = field(default_factory=list)
    bullseye: Pos | None = None
    target: Pos | None = None


def _font(size: int):
    return ImageFont.load_default(size=size)


def dm(value: float, pos: str, neg: str, deg_digits: int) -> str:
    h = pos if value >= 0 else neg
    v = abs(value)
    d = int(v)
    return f"{h}{d:0{deg_digits}d}\N{DEGREE SIGN}{(v - d) * 60:04.1f}'"


def _page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), PAPER)
    return img, ImageDraw.Draw(img)


def _header(d: ImageDraw.ImageDraw, info: KneeboardInfo, page: str) -> int:
    d.text((MARGIN, MARGIN), info.title, fill=INK, font=_font(34))
    d.text((MARGIN, MARGIN + 44), f"{info.subtitle}  -  {page}", fill=GREY, font=_font(20))
    d.line((MARGIN, MARGIN + 76, W - MARGIN, MARGIN + 76), fill=INK, width=2)
    return MARGIN + 90


def _para(d, y: int, text: str, size: int = 20, fill=INK, width: int = 62) -> int:
    for ln in textwrap.wrap(text, width=width) or [""]:
        d.text((MARGIN, y), ln, fill=fill, font=_font(size))
        y += size + 6
    return y


def _card(info: KneeboardInfo) -> Image.Image:
    img, d = _page()
    y = _header(d, info, "MISSION CARD")
    for ln in info.lines:
        y = _para(d, y, ln, fill=RED if ln.startswith(("ROE", "CRISIS", "WAR")) else INK) + 4
    y += 10
    cols = (0, 50, 190, 350, 520)                    # proportional font: fixed column positions
    for x, head in zip(cols, ("WPT", "NAME", "LAT", "LON", "ALT")):
        d.text((MARGIN + x, y), head, fill=GREY, font=_font(20))
    y += 30
    for i, (name, lat, lon, alt, ref) in enumerate(info.waypoints):
        alt_txt = f"{round(alt, -2):,} ft {ref}" if alt else "-"
        for x, cell in zip(cols, (str(i), name[:10], dm(lat, "N", "S", 2), dm(lon, "E", "W", 3), alt_txt)):
            d.text((MARGIN + x, y), cell, fill=INK, font=_font(20))
        y += 28
        if y > H - MARGIN - 30:
            break
    return img


def _threats(info: KneeboardInfo) -> Image.Image:
    img, d = _page()
    y = _header(d, info, "THREATS & COMMS")
    d.text((MARGIN, y), "THREATS", fill=RED, font=_font(24))
    y += 34
    for t in info.threats or ["None known."]:
        y = _para(d, y, "- " + t) + 2
    y += 16
    d.text((MARGIN, y), "COMMS / NAV", fill=BLUE, font=_font(24))
    y += 34
    for t in info.comms:
        y = _para(d, y, "- " + t) + 2
    return img


def _sketch(info: KneeboardInfo) -> Image.Image:
    img, d = _page()
    top = _header(d, info, "ROUTE SKETCH (north up)")
    pts = list(info.route) + ([info.target] if info.target else []) + \
        ([info.bullseye] if info.bullseye else [])
    if not pts:
        return img
    minx, maxx = min(p.x for p in pts), max(p.x for p in pts)
    minz, maxz = min(p.z for p in pts), max(p.z for p in pts)
    span = max(maxx - minx, maxz - minz, 40_000) * 1.25
    cx, cz = (minx + maxx) / 2, (minz + maxz) / 2
    box = W - 2 * MARGIN
    s = box / span

    def xy(p: Pos) -> tuple[float, float]:
        return (p.z - cz) * s + box / 2, (cx - p.x) * s + box / 2

    page, pd = img, d
    img = Image.new("RGB", (box, box), PAPER)          # drawn separately so it clips to the box
    d = ImageDraw.Draw(img)
    if len(info.front) > 1:
        d.line([xy(p) for p in info.front], fill=RED, width=3)
    for centre, r, label in info.rings:
        x, y = xy(centre)
        rr = r * s
        d.ellipse((x - rr, y - rr, x + rr, y + rr), outline=RED, width=2)
        d.text((x - 20, y - 10), label, fill=RED, font=_font(16))
    if len(info.route) > 1:
        d.line([xy(p) for p in info.route], fill=BLUE, width=3)
    for i, p in enumerate(info.route):
        x, y = xy(p)
        d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=BLUE)
        d.text((x + 8, y - 8), str(i), fill=BLUE, font=_font(16))
    if info.target:
        x, y = xy(info.target)
        d.line((x - 10, y - 10, x + 10, y + 10), fill=RED, width=3)
        d.line((x - 10, y + 10, x + 10, y - 10), fill=RED, width=3)
    if info.bullseye:
        x, y = xy(info.bullseye)
        for rr in (8, 16):
            d.ellipse((x - rr, y - rr, x + rr, y + rr), outline=INK, width=2)
        d.text((x + 18, y - 8), "BULLSEYE", fill=INK, font=_font(16))
    page.paste(img, (MARGIN, top))
    img, d = page, pd
    d.rectangle((MARGIN, top, MARGIN + box, top + box), outline=GREY, width=1)
    bar = 20 * 1852 * s                                  # 20 nm scale bar
    y0 = top + box + 24
    d.line((MARGIN, y0, MARGIN + bar, y0), fill=INK, width=4)
    d.text((MARGIN + bar + 10, y0 - 10), "20 nm", fill=INK, font=_font(18))
    d.text((MARGIN, y0 + 22), "Blue: route   Red: front line, SAM rings, target", fill=GREY, font=_font(16))
    return img


def render(info: KneeboardInfo, out_dir: Path, stem: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, page in enumerate((_card(info), _threats(info), _sketch(info)), start=1):
        p = out_dir / f"{stem}-{i}.png"
        page.save(p)
        paths.append(p)
    return paths
