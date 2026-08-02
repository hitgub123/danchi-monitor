# config.py
from dataclasses import dataclass, field
from typing import List
import yaml

@dataclass
class Destination:
    station_name: str
    station_cd: str
    commute_max_min: int
    change_max: int

@dataclass
class WideFilter:
    rent_max: int
    walk_max: int
    area_min: int

@dataclass
class Precise:
    rent_max: int
    area_min: int
    walk_max: int
    walk_ideal: int
    elevator_min_floor: int
    year_max: int
    renovated_keywords: List[str]

@dataclass
class Weights:
    commute: int
    walk: int
    rent: int
    area: int
    room_type: int
    floor: int
    tokyo: int

@dataclass
class Baseline:
    rent: int
    area: int
    walk: int
    commute: int
    madori: str
    western: bool
    floor: int

@dataclass
class Schedule:
    day_interval_min: int
    night_interval_min: int
    poll_log_keep_days: int = 90

@dataclass
class Discord:
    webhook_url: str

@dataclass
class Http:
    user_agent: str
    timeout: int
    retry_max: int
    backoff_base_sec: int

@dataclass
class Config:
    destination: Destination
    prefectures: List[str]
    areas: List[str]
    wide_filter: WideFilter
    precise: Precise
    weights: Weights
    baseline: Baseline
    schedule: Schedule
    discord: Discord
    http: Http

def _section(cls, d):
    return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

def load_config(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    return Config(
        destination=_section(Destination, d["destination"]),
        prefectures=d["prefectures"],
        areas=d["areas"],
        wide_filter=_section(WideFilter, d["wide_filter"]),
        precise=_section(Precise, d["precise"]),
        weights=_section(Weights, d["weights"]),
        baseline=_section(Baseline, d["baseline"]),
        schedule=_section(Schedule, d["schedule"]),
        discord=_section(Discord, d["discord"]),
        http=_section(Http, d["http"]),
    )
