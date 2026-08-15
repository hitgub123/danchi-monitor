# config.py
import os
from dataclasses import dataclass, field
from typing import List, Optional
import yaml
from schedule import DenseWindows

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
    day_start_hour: int = 8
    day_end_hour: int = 22
    dense_windows: Optional[DenseWindows] = None
    poll_log_keep_days: int = 90
    max_jitter_sec: int = 5

@dataclass
class Discord:
    webhook_url: str
    llm_comment: bool = True  # 是否发 LLM 点评(第二波)

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
    push_threshold: Optional[float] = None  # 推送阈值; None=用 baseline 算出的金町基准分
    push_top_n: Optional[int] = None  # 每次上新最多推送新房数; None=用 push_threshold 阈值模式

def _section(cls, d):
    return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

def load_config(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    # webhook 优先取环境变量（云端部署不必把 key 写进 config.yaml）；
    # 未设置时回退到 config.yaml 的值（本地沿用原有行为）。
    discord_data = dict(d.get("discord") or {})
    webhook_env = os.getenv("DANCHI_DISCORD_WEBHOOK", "").strip()
    if webhook_env:
        discord_data["webhook_url"] = webhook_env
    schedule_data = dict(d.get("schedule") or {})
    dw = schedule_data.get("dense_windows")
    if dw:
        schedule_data["dense_windows"] = _section(DenseWindows, dw)
    return Config(
        destination=_section(Destination, d["destination"]),
        prefectures=d["prefectures"],
        areas=d["areas"],
        wide_filter=_section(WideFilter, d["wide_filter"]),
        precise=_section(Precise, d["precise"]),
        weights=_section(Weights, d["weights"]),
        baseline=_section(Baseline, d["baseline"]),
        schedule=_section(Schedule, schedule_data),
        discord=_section(Discord, discord_data),
        http=_section(Http, d["http"]),
        push_threshold=d.get("push_threshold"),
        push_top_n=d.get("push_top_n"),
    )
