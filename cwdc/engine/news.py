"""News: every campaign news line goes through here, tagged with a category and a source.

`post` files a line as it is; `headline` picks one of several wordings for a recurring event
(seeded, so a campaign replays the same way); `daily` adds one home-front / diplomatic item at
each dawn so the world beyond the squadron moves too. Sources are generic, not real outlets.
"""
from __future__ import annotations

from cwdc.engine import rng
from cwdc.model import Campaign

CATEGORIES = ("diplomatic", "military", "air", "ground", "home", "squadron")
SOURCES = {"diplomatic": "Wire report", "military": "Intelligence summary", "air": "USAFE release",
           "ground": "Front report", "home": "Radio bulletin", "squadron": "Squadron ops"}

TEMPLATES = {
    "objective_destroyed": ("{name} has been destroyed.",
                            "Strike reports confirm {name} destroyed.",
                            "Reconnaissance photographs show {name} wrecked."),
    "parked_lost": ("{sq} loses {tail} on the ground at {base}.",
                    "Raid on {base}: {sq} aircraft {tail} destroyed on the ground."),
    "formation_arrived": ("{name} has arrived at the front ({sector}).",
                          "Fresh troops: {name} moves up into the {sector} sector."),
    "formation_destroyed": ("{name} has been destroyed as a fighting force.",
                            "{name} has ceased to exist as a fighting formation."),
}

DAILY = {
    "crisis": (
        ("diplomatic", "The UN Security Council meets in emergency session on the German crisis."),
        ("home", "Long queues at West German petrol stations as families stock up."),
        ("diplomatic", "Washington and Moscow exchange messages over the hotline."),
        ("home", "US military families in the Federal Republic told to prepare for evacuation."),
        ("military", "Convoys of Soviet tank transporters reported on the autobahn near Erfurt."),
        ("home", "Churches across Hesse hold evening services for peace."),
    ),
    "war": (
        ("home", "Refugees crowd the roads west of Fulda; civil defence asks people to stay put."),
        ("diplomatic", "NATO ministers meet in Brussels; the alliance calls for an immediate ceasefire."),
        ("home", "Blood donation centres across West Germany report record queues."),
        ("diplomatic", "Neutral states offer to host ceasefire talks."),
        ("military", "REFORGER: the first transatlantic reinforcement flights land at Rhein-Main."),
        ("home", "Schools in Hesse and Bavaria close; air-raid sirens tested in Frankfurt."),
        ("diplomatic", "Moscow says its forces are 'repelling NATO aggression'."),
    ),
}


def post(c: Campaign, text: str, category: str = "military") -> None:
    c.add_news(text, category=category, source=SOURCES.get(category, ""))


def headline(c: Campaign, key: str, category: str, **kw) -> str:
    variants = TEMPLATES[key]
    r = rng(c.seed, c.turn, f"news:{key}:{len(c.news)}")
    text = r.choice(variants).format(**kw)
    post(c, text, category)
    return text


def daily(c: Campaign) -> None:
    """One item of wider-world news at each dawn turn (crisis and war)."""
    if not (5 <= c.time.hour < 11) or c.outcome is not None:
        return
    pool = DAILY["war" if c.at_war else "crisis"]
    used = {n.text for n in c.news}
    fresh = [p for p in pool if p[1] not in used] or list(pool)
    cat, text = rng(c.seed, c.turn, "news:daily").choice(fresh)
    post(c, text, cat)
