"""Campaign state: the single source of truth for the war.

A DCS mission is only a window onto one slice of this state (see docs/design.md,
"Extensibility principles"). Everything is plain pydantic so the whole campaign
serialises to readable JSON.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Side(str, Enum):
    BLUE = "blue"
    RED = "red"

    @property
    def enemy(self) -> "Side":
        return Side.RED if self is Side.BLUE else Side.BLUE


class Task(str, Enum):
    CAP = "CAP"
    ESCORT = "Escort"
    STRIKE = "Strike"
    SEAD = "SEAD"
    CAS = "CAS"
    BAI = "BAI"
    INTERDICTION = "Interdiction"
    RECON = "Recon"
    AIRLIFT = "Airlift"


class Pos(BaseModel):
    """DCS map coordinates in metres (x = north, z = east)."""
    x: float
    z: float

    def dist(self, other: "Pos") -> float:
        return ((self.x - other.x) ** 2 + (self.z - other.z) ** 2) ** 0.5


# ----------------------------------------------------------------------------- people & aircraft

class PilotStatus(str, Enum):
    ACTIVE = "active"
    WIA = "wia"        # wounded, back after recovery_turns
    KIA = "kia"
    MIA = "mia"
    POW = "pow"


class Pilot(BaseModel):
    id: str
    name: str
    rank: str
    side: Side
    squadron_id: str
    status: PilotStatus = PilotStatus.ACTIVE
    is_player: bool = False
    sorties: int = 0
    air_kills: int = 0
    ground_kills: int = 0
    recovery_turns: int = 0


class AirframeStatus(str, Enum):
    READY = "ready"
    DAMAGED = "damaged"      # in repair for repair_turns
    DESTROYED = "destroyed"


class Airframe(BaseModel):
    id: str                  # tail number
    type: str                # DCS type id, e.g. "F-4E-45MC"
    squadron_id: str
    status: AirframeStatus = AirframeStatus.READY
    quality: int = 100       # HB F-4E "Quality" property (aircraft condition)
    repair_turns: int = 0
    sorties: int = 0


class Squadron(BaseModel):
    id: str
    name: str
    side: Side
    country: str             # pydcs country name
    aircraft_type: str       # DCS type id
    base_id: str
    callsign: str
    tasks: list[Task]
    player_squadron: bool = False
    establishment_airframes: int = 0     # authorised strength, for replacements
    establishment_pilots: int = 0


# ----------------------------------------------------------------------------- places & things

class Airbase(BaseModel):
    id: str
    dcs_name: str
    side: Side
    pos: Pos
    runway_damage: float = 0.0   # 0 = fully usable


class ObjectiveKind(str, Enum):
    SAM = "sam"
    AAA = "aaa"
    EWR = "ewr"
    ARMOR = "armor"
    ARTILLERY = "artillery"
    DEPOT = "depot"
    CONVOY = "convoy"
    COMMAND = "command"


class ObjUnit(BaseModel):
    type: str                # DCS vehicle type id
    dx: float = 0.0          # offset from objective position (m)
    dz: float = 0.0
    alive: bool = True


class Objective(BaseModel):
    id: str
    name: str
    kind: ObjectiveKind
    side: Side
    country: str
    pos: Pos
    units: list[ObjUnit]
    value: int = 1           # planning priority
    threat_range_m: float = 0.0  # air defence engagement radius
    supply_node: Optional[str] = None

    @property
    def alive_units(self) -> list[ObjUnit]:
        return [u for u in self.units if u.alive]

    @property
    def destroyed(self) -> bool:
        return not self.alive_units


# Logistics network schema (principle 5). Phase 1 stores it; Phase 2 simulates flow.
class SupplyNode(BaseModel):
    id: str
    name: str
    side: Side
    pos: Pos
    stock: float = 100.0
    capacity: float = 100.0


class SupplyLink(BaseModel):
    a: str
    b: str
    mode: str = "road"       # road | rail | air
    capacity: float = 20.0
    damaged: float = 0.0


# ----------------------------------------------------------------------------- tasking (ATO)

class FlightStatus(str, Enum):
    PLANNED = "planned"
    ASSIGNED_PLAYER = "player"   # the player is flying it in DCS
    DONE = "done"


class Flight(BaseModel):
    id: str
    package_id: str
    side: Side
    squadron_id: str
    task: Task
    callsign: str
    airframe_ids: list[str]
    pilot_ids: list[str]
    loadout: Optional[str] = None      # catalogue loadout name; None = AI default for task
    target_id: Optional[str] = None    # objective id (strike/SEAD/CAS)
    area: Optional[Pos] = None         # CAP / patrol centre
    status: FlightStatus = FlightStatus.PLANNED
    player_eligible: bool = False
    displaced_pilot_id: Optional[str] = None   # lead pilot stood down when the player took the seat


class Package(BaseModel):
    id: str
    side: Side
    task: Task
    description: str
    flight_ids: list[str]
    target_id: Optional[str] = None
    area: Optional[Pos] = None


# ----------------------------------------------------------------------------- results (principle 2)

class PilotFate(BaseModel):
    pilot_id: str
    status: PilotStatus
    note: str = ""


class Kill(BaseModel):
    pilot_id: Optional[str]            # who scored it (None = SAM/AAA/unknown)
    target: str                        # airframe id, or "objective_id#unit_index"
    target_type: str
    weapon: Optional[str] = None
    air: bool = True


class SortieResult(BaseModel):
    """Outcome of one flight, flown in DCS or resolved abstractly. Both feed apply_results."""
    flight_id: str
    source: str                        # "dcs" | "sim"
    airframes_lost: list[str] = []
    airframes_damaged: list[str] = []
    pilot_fates: list[PilotFate] = []
    kills: list[Kill] = []
    objective_units_destroyed: list[str] = []   # "objective_id#unit_index"
    summary: str = ""


class MissionRecord(BaseModel):
    """A generated DCS mission waiting for (or done with) ingest."""
    mission_id: str
    turn: int
    player_flight_id: str
    miz_path: str
    generated_at: float                # unix time; results older than this are stale
    # DCS unit name -> what it is in the campaign:
    #   {"kind": "airframe", "flight": id, "airframe": id, "pilot": id}
    #   {"kind": "objective", "objective": id, "unit": idx}
    manifest: dict[str, dict] = {}
    spawned_flights: list[str] = []    # non-player flights present (and so resolved) in DCS
    bot: bool = False                  # AI-only mission (no player slot)
    ingested: bool = False


class NewsItem(BaseModel):
    turn: int
    time: datetime
    text: str


# ----------------------------------------------------------------------------- campaign root

class Campaign(BaseModel):
    schema_version: int = 1
    id: str
    name: str
    seed: int
    theater: str
    time: datetime
    turn: int = 1
    turn_hours: int = 6
    player_name: str = "Player"
    instance: str = ""                 # random tag per campaign creation; keeps mission ids unique

    airbases: dict[str, Airbase] = {}
    squadrons: dict[str, Squadron] = {}
    pilots: dict[str, Pilot] = {}
    airframes: dict[str, Airframe] = {}
    objectives: dict[str, Objective] = {}
    supply_nodes: dict[str, SupplyNode] = {}
    supply_links: list[SupplyLink] = []
    front_line: list[tuple[float, float]] = []    # (lat, lon), north -> south
    cap_stations: dict[Side, list[Pos]] = {}

    packages: dict[str, Package] = {}             # current turn ATO
    flights: dict[str, Flight] = {}
    pending_mission: Optional[MissionRecord] = None
    news: list[NewsItem] = []
    counters: dict[str, int] = Field(default_factory=dict)

    def next_id(self, prefix: str) -> str:
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}{n:03d}"

    def add_news(self, text: str) -> None:
        self.news.append(NewsItem(turn=self.turn, time=self.time, text=text))

    # convenience queries
    def player_pilot(self) -> Pilot:
        return next(p for p in self.pilots.values() if p.is_player)

    def squadron_airframes(self, sq_id: str, status: AirframeStatus | None = AirframeStatus.READY):
        return [a for a in self.airframes.values()
                if a.squadron_id == sq_id and (status is None or a.status == status)]

    def squadron_pilots(self, sq_id: str, status: PilotStatus | None = PilotStatus.ACTIVE):
        return [p for p in self.pilots.values()
                if p.squadron_id == sq_id and (status is None or p.status == status)]
