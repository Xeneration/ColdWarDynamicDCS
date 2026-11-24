"""Saving and loading campaigns: campaign.json plus one snapshot per turn."""
from __future__ import annotations

import os
from pathlib import Path

from cwdc.model import Campaign


def campaigns_root() -> Path:
    override = os.environ.get("CWDC_CAMPAIGNS_DIR")
    if override:
        return Path(override)
    from cwdc.dcs_paths import saved_games_dcs
    return saved_games_dcs() / "CWDC" / "campaigns"


def campaign_dir(campaign_id: str) -> Path:
    return campaigns_root() / campaign_id


def save(c: Campaign, snapshot: bool = False) -> Path:
    d = campaign_dir(c.id)
    (d / "turns").mkdir(parents=True, exist_ok=True)
    text = c.model_dump_json(indent=1)
    tmp = d / "campaign.json.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, d / "campaign.json")
    if snapshot:
        (d / "turns" / f"{c.turn:03d}.json").write_text(text, encoding="utf-8")
    (campaigns_root() / "current.txt").write_text(c.id, encoding="utf-8")
    return d / "campaign.json"


def load(campaign_id: str | None = None) -> Campaign:
    if campaign_id is None:
        marker = campaigns_root() / "current.txt"
        if not marker.is_file():
            raise FileNotFoundError("no current campaign - run `cwdc new` first")
        campaign_id = marker.read_text(encoding="utf-8").strip()
    path = campaign_dir(campaign_id) / "campaign.json"
    c = Campaign.model_validate_json(path.read_text(encoding="utf-8"))
    from cwdc import theater
    theater.use(c)
    return c


def rollback(campaign_id: str, turn: int) -> Campaign:
    """Restore the snapshot taken at the start of `turn`."""
    path = campaign_dir(campaign_id) / "turns" / f"{turn:03d}.json"
    c = Campaign.model_validate_json(path.read_text(encoding="utf-8"))
    save(c)
    return c
