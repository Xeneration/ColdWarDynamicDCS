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
    RECON = "Recon"          # crisis: Warsaw Pact border probe
    QRA = "QRA"              # crisis: NATO alert intercept of a probe
    INTERCEPT = "Intercept"  # war: scramble against an incoming raid
    SWEEP = "Sweep"          # war: fighter sweep ahead of a strike package
    RESCAP = "RESCAP"        # war: cover the rescue of a downed crew
    ARMED_RECON = "Armed Recon"   # war: hunt targets of opportunity along a road
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


class CrewRole(str, Enum):
    PILOT = "pilot"
    WSO = "wso"              # weapon systems officer: the F-4E back seat


class Pilot(BaseModel):
    """An aircrew member (front or back seat) - a persistent person."""
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
    role: CrewRole = CrewRole.PILOT
    fatigue: float = 0.0     # 0 = rested; grounded at engine.crews.GROUNDED


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
    sorties: int = 0                     # flights planned per turn (0 = planner default)
    establishment_airframes: int = 0     # authorised strength, for replacements
    establishment_pilots: int = 0
    establishment_wsos: int = 0          # two-seat types: back-seaters


# ----------------------------------------------------------------------------- places & things

class Airbase(BaseModel):
    id: str
    dcs_name: str
    side: Side
    pos: Pos
    damage: float = 0.0          # 0 = fully usable; cuts the sortie rate, closed at engine.airbases.CLOSED
    hit_turn: int = -1           # last turn it was hit (no repair that turn)


class ObjectiveKind(str, Enum):
    SAM = "sam"
    AAA = "aaa"
    EWR = "ewr"
    ARMOR = "armor"
    ARTILLERY = "artillery"
    DEPOT = "depot"
    CONVOY = "convoy"
    RAIL = "rail"            # rail yard / junction: interdiction target on a supply link
    COMMAND = "command"
    AIRBASE = "airbase"      # OCA target: parked aircraft + airfield defences


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
    supply_link: Optional[str] = None    # interdiction target: damage cuts this link
    formation_id: Optional[str] = None   # visible element of this ground formation
    sector_id: Optional[str] = None      # moves with this sector's front
    base_pos: Optional[Pos] = None       # position when the front offset is 0
    airbase_id: Optional[str] = None     # OCA target: hits damage this airbase

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
    production: float = 0.0      # supply arriving per turn (rail heads fed from the rear)


class SupplyLink(BaseModel):
    """Directed: supply flows from a (upstream) to b."""
    id: str = ""
    a: str
    b: str
    mode: str = "road"       # road | rail | air
    capacity: float = 20.0   # per turn when undamaged
    damaged: float = 0.0     # 0..1
    flow: float = 0.0        # moved last turn (for reports)


# ----------------------------------------------------------------------------- ground war

class Posture(str, Enum):
    ATTACK = "attack"
    DEFEND = "defend"
    RESERVE = "reserve"


class Sector(BaseModel):
    """A stretch of the front. offset_km > 0: the Warsaw Pact has advanced into NATO territory."""
    id: str
    name: str
    front_points: list[int]          # indices into Campaign.base_front_line
    terrain: float = 1.0             # defender multiplier (hills, forest, rivers)
    offset_km: float = 0.0
    attacker: Optional[Side] = None  # who attacked this turn (for reports)
    depots: dict[Side, str] = {}     # supply node feeding each side's formations here


class Formation(BaseModel):
    """A ground formation (division / brigade / regiment), abstracted to combat strength."""
    id: str
    name: str
    side: Side
    country: str
    sector_id: str
    kind: str = "armor"              # armor | mech | cavalry | infantry
    strength: float                  # current combat power (abstract points)
    max_strength: float
    posture: Posture = Posture.DEFEND
    supply: float = 1.0              # 0..1, from logistics (Phase 2 step 4)
    readiness: float = 1.0
    arrival_turn: int = 1            # reinforcements arrive later

    @property
    def ratio(self) -> float:
        return self.strength / self.max_strength if self.max_strength else 0.0


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
    wso_ids: list[str] = []            # back seat of each airframe (two-seat types), same order
    loadout: Optional[str] = None      # catalogue loadout name; None = AI default for task
    target_id: Optional[str] = None    # objective id (strike/SEAD/CAS)
    area: Optional[Pos] = None         # CAP / patrol centre
    status: FlightStatus = FlightStatus.PLANNED
    player_eligible: bool = False
    displaced_pilot_id: Optional[str] = None   # lead pilot stood down when the player took the seat
    supports: Optional[str] = None     # escort / SEAD flight: the package's main flight it protects
    intercepts: Optional[str] = None   # QRA / intercept flight: the enemy flight it is sent against
    rescue_of: Optional[str] = None    # RESCAP flight: the downed pilot it covers


class Package(BaseModel):
    id: str
    side: Side
    task: Task
    description: str
    flight_ids: list[str]
    target_id: Optional[str] = None
    area: Optional[Pos] = None
    reason: str = ""                   # why the air commander tasked it, in plain words


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
    briefing: list[str] = []           # the briefing text written into the mission


class Debrief(BaseModel):
    """One closed turn's debrief, kept for the debrief history (CLI and web UI)."""
    turn: int
    time: datetime
    lines: list[str]
    flown_in_dcs: bool = False
    stats: dict = {}                   # structured summary (engine.stats.debrief_stats)


class TurnStats(BaseModel):
    """The state of the war at the end of one turn (for history charts)."""
    turn: int
    time: datetime
    offsets: dict[str, float] = {}     # sector id -> km (+ = into West Germany)
    ground_power: dict[str, float] = {}
    air_strength: dict[str, float] = {}
    aircraft_lost: dict[str, int] = {}  # cumulative, per side
    aircrew_lost: dict[str, int] = {}


class NewsItem(BaseModel):
    turn: int
    time: datetime
    text: str
    category: str = "military"         # engine.news.CATEGORIES
    source: str = ""


# ----------------------------------------------------------------------------- campaign root

class Outcome(BaseModel):
    """How the war ended (engine.outcome). Once set, the campaign is over."""
    winner: Optional[Side] = None      # None = stalemate
    decisive: bool = True
    title: str                         # e.g. "Warsaw Pact victory"
    reason: str
    turn: int
    time: datetime


class WeatherState(BaseModel):
    """This turn's weather (engine.weather)."""
    kind: str = "scattered"            # clear | scattered | broken | overcast | rain | fog
    cloud_base_m: int = 2500
    visibility_m: int = 40_000
    wind_dir: int = 240                # degrees the wind blows from
    wind_kts: int = 8
    temp_c: float = 22.0


class Campaign(BaseModel):
    schema_version: int = 2
    id: str
    name: str
    seed: int
    theater: str
    time: datetime
    turn: int = 1
    turn_hours: int = 6
    player_name: str = "Player"
    instance: str = ""                 # random tag per campaign creation; keeps mission ids unique
    main_effort: Optional[str] = None  # sector of the Warsaw Pact's main thrust (rolled per campaign)
    # pre-war crisis (engine.crisis). Saves from before the crisis phase load as already at war.
    phase: str = "war"                 # "crisis" | "war"
    tension: float = 0.0               # 0-100; war breaks out at 100 (or at the deadline)
    war_start_turn: int = 1            # first turn of the war
    crisis_deadline_turn: int = 0      # the war starts on this turn at the latest
    weather: WeatherState = Field(default_factory=WeatherState)

    @property
    def at_war(self) -> bool:
        return self.phase == "war"

    @property
    def war_turn(self) -> int:
        """Turns since the war started (1 = its first turn; 0 during the crisis)."""
        return self.turn - self.war_start_turn + 1 if self.at_war else 0

    airbases: dict[str, Airbase] = {}
    squadrons: dict[str, Squadron] = {}
    pilots: dict[str, Pilot] = {}
    airframes: dict[str, Airframe] = {}
    objectives: dict[str, Objective] = {}
    supply_nodes: dict[str, SupplyNode] = {}
    supply_links: list[SupplyLink] = []
    front_line: list[tuple[float, float]] = []    # current front (lat, lon), north -> south
    base_front_line: list[tuple[float, float]] = []   # pre-war border the sector offsets apply to
    sectors: dict[str, Sector] = {}
    formations: dict[str, Formation] = {}
    cap_stations: dict[Side, list[Pos]] = {}

    packages: dict[str, Package] = {}             # current turn ATO
    flights: dict[str, Flight] = {}
    pending_mission: Optional[MissionRecord] = None
    outcome: Optional[Outcome] = None
    debriefs: list[Debrief] = []       # most recent last; capped at engine.turn.DEBRIEF_HISTORY
    history: list[TurnStats] = []      # one per closed turn
    news: list[NewsItem] = []
    counters: dict[str, int] = Field(default_factory=dict)

    def next_id(self, prefix: str) -> str:
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}{n:03d}"

    def add_news(self, text: str, category: str = "military", source: str = "") -> None:
        """Low level; engine code posts through engine.news."""
        self.news.append(NewsItem(turn=self.turn, time=self.time, text=text, category=category,
                                  source=source))

    # convenience queries
    def player_pilot(self) -> Pilot:
        return next(p for p in self.pilots.values() if p.is_player)

    def squadron_airframes(self, sq_id: str, status: AirframeStatus | None = AirframeStatus.READY):
        return [a for a in self.airframes.values()
                if a.squadron_id == sq_id and (status is None or a.status == status)]

    def squadron_pilots(self, sq_id: str, status: PilotStatus | None = PilotStatus.ACTIVE,
                        role: CrewRole | None = CrewRole.PILOT):
        """Aircrew of a squadron (front-seat pilots by default; role=None for everyone)."""
        return [p for p in self.pilots.values()
                if p.squadron_id == sq_id and (status is None or p.status == status)
                and (role is None or p.role is role)]
