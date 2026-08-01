# UR団地 新空房监控系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 监控东京圈 UR賃貸住宅中「到浜松町≤60分」的团地，发现新空房即打分并推送到 Discord，同时记录每次轮询快照供时间序列分析。

**Architecture:** 三层结构 —— ① 每月 discovery 拉全量团地并按通勤时间筛出目标列表；② 高频轮询 UR 内部 JSON API（宽筛选出当前有空房的团地）；③ 对有空房团地下钻拿房间、diff 出新房、规则打分 + 异步 LLM 点评，两段式推送 Discord。所有可调项集中在 `config.yaml`。

**Tech Stack:** Python 3.10+ · requests · APScheduler · SQLite(标准库 `sqlite3`) · pytest · Discord webhook。LLM 点评用 Anthropic Messages 格式打本地 cc-switch 代理（环境变量 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`），无新增 key。

**Spec:** `docs/superpowers/specs/2026-08-01-danchi-monitor-design.md`

## Global Constraints

- Python ≥3.10；依赖仅 `requests`、`APScheduler`、`pyyaml`（运行时）+ `pytest`（开发）
- 所有可调项（宽筛参数、精确规则、权重、轮询频率、URL）必须集中在 `config.yaml`，代码不硬编码
- 数据源只用 UR 内部 JSON API（`https://chintai.r6.ur-net.go.jp/chintai/api/`），不抓 HTML 页面；不使用 robots.txt 禁止的 `/result/?skcs= / ?line= / ?station= / ?station_nm=` 参数
- 请求需带固定 User-Agent + 随机抖动（50-150ms）+ 403/429 指数退避重试
- 硬条件（淘汰制）：通勤≤60分 / 月租≤10万 / 面积≥40㎡ / 步行≤15分 / 3层以上必须有电梯 / 必须翻新（近似：築年数≤配置上限 或 facility 含"リフォーム"关键字）
- 数据库为 SQLite `data.db`；每次轮询必须写 `poll_log`（时间序列分析用）
- 项目根：`~/agent-learning/danchi-monitor/`

---

### Task 1: 项目脚手架 + config.yaml + 配置加载

**Files:**
- Create: `config.yaml`
- Create: `config.py`
- Create: `requirements.txt`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `load_config(path) -> Config` dataclass；`Config` 含 `destination`, `prefectures`, `wide_filter`, `precise`, `weights`, `baseline`, `schedule`, `discord`, `http` 各节

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py
from config import load_config

def test_load_config():
    cfg = load_config("config.yaml")
    assert cfg.destination.station_cd == "2827"
    assert cfg.destination.commute_max_min == 60
    assert cfg.precise.rent_max == 100000
    assert cfg.weights.commute == 30

def test_config_missing_file_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_config("nope.yaml")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ~/agent-learning/danchi-monitor && python -m pytest tests/test_config.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: 写 config.yaml**

```yaml
destination:
  station_name: "浜松町"
  station_cd: "2827"          # UR station_cd（已实测 station/suggest 返回）
  commute_max_min: 60
  change_max: 2

prefectures: ["tokyo"]        # UR 块下可用: tokyo/kanagawa/chiba/saitama
areas: ["01","02","03","04","05","06"]   # tokyo 的 area-id（已实测 list_bukken 需要）

wide_filter:                  # 传给 UR API 的宽松参数（防漏房）
  rent_max: 110000
  walk_max: 15
  area_min: 35

precise:                      # 本地精确规则（淘汰制）
  rent_max: 100000
  area_min: 40
  walk_max: 15
  walk_ideal: 10
  elevator_min_floor: 3
  year_max: 30                # 築年数上限（翻新判断近似）
  renovated_keywords: ["リフォーム", "リノベーション", "リフォーム済み"]

weights:                      # 打分权重（合计100）
  commute: 30
  walk: 20
  rent: 20
  area: 15
  room_type: 5                # 洋室>和室
  floor: 5
  tokyo: 5

baseline:                     # 基准房（金町），用于计算推送阈值
  rent: 95000
  area: 43
  walk: 2
  commute: 30
  madori: "1DK"
  western: false
  floor: 5

schedule:
  discover_cron: "0 3 1 * *"           # 每月1日03:00
  day_interval_min: 5                  # 日间 08-22
  night_interval_min: 30               # 夜间 22-08

discord:
  webhook_url: ""                      # 用户填入 Discord webhook

http:
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
  timeout: 30
  retry_max: 3
  backoff_base_sec: 2
```

- [ ] **Step 4: 写配置加载**

```python
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
    discover_cron: str
    day_interval_min: int
    night_interval_min: int

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
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: 提交**

```bash
cd ~/agent-learning/danchi-monitor
git add config.yaml config.py requirements.txt tests/test_config.py
git commit -m "feat: 项目脚手架 + 可配置项加载"
```

---

### Task 2: UR API 客户端（含退避重试）

**Files:**
- Create: `ur_api.py`
- Test: `tests/test_ur_api.py`

**Interfaces:**
- Consumes: `config.py` 的 `Http` 配置
- Produces:
  - `class UrApi`: `suggest_station(name) -> list[dict]`；`get_cost_time_xml(station_cd) -> bytes`；`get_danchi_list(area, station_condition, wide, prefecture) -> list[dict]`；`get_room_list(danchi_id, station_condition, wide, prefecture) -> list[dict]`；`get_room_detail(danchi_id, room_id) -> dict`
  - `class RateLimitedError(Exception)`
  - 所有方法带退避重试，403/429 抛 `RateLimitedError`

**已验证的关键事实（已实测，直接照做）：**
- API base: `https://chintai.r6.ur-net.go.jp/chintai/api/`
- 全部 POST，Content-Type `application/x-www-form-urlencoded; charset=UTF-8`，需 `Referer: https://www.ur-net.go.jp/` + `X-Requested-With: XMLHttpRequest`
- `station/suggest/`：`search_value=浜松町&block=kanto` → `[{"value":"2827","text":"浜松町",...}]`
- `bukken/search/list_bukken/`：必带 `area`（tokyo 为 01-06）；带 `station_condition` 时只返回通勤合格的团地，字段含 `id`(如`20_2600`)、`name`、`skcs`(市区町村)、`roomCount`、`rent`、`access`(HTML字符串)、`bukkenUrl`
- `room/list/`：`mode=init&id=<danchi_id>` → 房间数组，字段含 `id`、`rent`(`"60,900円"`)、`commonfee`、`type`(`"3DK"`)、`floorspace`、`floor`、`urlDetail`
- `bukken/detail/detail_room/`：`id=<room_id>&shisya=<前2位>&danchi=<中间3位>&shikibetu=<第6位>&sp=`（从 danchi_id `20_2600` 拆出 shisya=20, danchi=260, shikibetu=0）→ 字段含 `year`(築年数)、`floor`(`"4階 /5階"`)、`facility`(含"エレベーター"等)、`feature`、`madoriYuka`(`"3DK /53㎡"`)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ur_api.py
import pytest
from ur_api import UrApi

def make_api(monkeypatch, resp):
    class FakeResp:
        def read(self): return resp if isinstance(resp, bytes) else resp.encode()
    def fake_open(req, timeout=30):
        assert "chintai.r6.ur-net.go.jp" in req.full_url
        return FakeResp()
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    return UrApi(user_agent="test", timeout=30, retry_max=2, backoff_base_sec=0)

def test_suggest_station(monkeypatch):
    api = make_api(monkeypatch, '[{"value":"2827","text":"浜松町"}]')
    res = api.suggest_station("浜松町")
    assert res[0]["value"] == "2827"

def test_get_room_list(monkeypatch):
    api = make_api(monkeypatch, '[{"id":"001080409","rent":"60,900円","type":"3DK"}]')
    rooms = api.get_room_list("20_2600", "2827", None, "tokyo")
    assert rooms[0]["id"] == "001080409"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_ur_api.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'ur_api'`

- [ ] **Step 3: 写实现**

```python
# ur_api.py
import random
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError

API_BASE = "https://chintai.r6.ur-net.go.jp/chintai/api/"

class RateLimitedError(Exception):
    pass

class UrApi:
    def __init__(self, user_agent, timeout=30, retry_max=3, backoff_base_sec=2):
        self.user_agent = user_agent
        self.timeout = timeout
        self.retry_max = retry_max
        self.backoff_base_sec = backoff_base_sec

    def _post(self, path: str, params: dict) -> bytes:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            API_BASE + path,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "User-Agent": self.user_agent,
                "Referer": "https://www.ur-net.go.jp/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        for attempt in range(self.retry_max):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.read()
            except HTTPError as e:
                if e.code in (403, 429):
                    if attempt == self.retry_max - 1:
                        raise RateLimitedError("rate limited") from e
                    time.sleep(self.backoff_base_sec * (2 ** attempt))
                else:
                    raise
            except Exception:
                if attempt == self.retry_max - 1:
                    raise
                time.sleep(self.backoff_base_sec * (2 ** attempt))
            time.sleep(random.uniform(0.05, 0.15))  # 抖动

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

    def suggest_station(self, name: str, block: str = "kanto") -> list:
        import json
        return json.loads(self._post("station/suggest/", {"search_value": name, "block": block}).decode("utf-8"))

    def get_cost_time_xml(self, station_cd: str) -> bytes:
        url = f"https://www.ur-net.go.jp/chintai/common/xml/cost-time/cost-time_{int(station_cd):08d}.xml"
        return self._get(url)

    def get_danchi_list(self, area: str, station_condition: str, wide, prefecture: str) -> list:
        import json
        station_cd1 = station_condition.split(",")[0] if station_condition else ""
        params = {
            "block": "kanto", "tdfk": "13", "vacancy": "1",
            "area": area,
            "leadtimeCount": "1",
            "station_cd1": station_cd1, "station_cost1": "60", "station_change1": "2",
            "station_condition": station_condition,
        }
        # 宽筛参数：用实测正确的名称（rent_high / walk / floorspace_low）。
        # 这些是"尽力而为"的体积缩减，服务端可能忽略；精确判定永远在本地 score.py，
        # 因此即使宽筛无效也不会漏房，只是多抓几条。
        if wide:
            params["rent_high"] = str(wide.rent_max)
            params["walk"] = str(wide.walk_max)
            params["floorspace_low"] = str(wide.area_min)
        raw = self._post("bukken/search/list_bukken/", params)
        return json.loads(raw.decode("utf-8")) if raw.strip() else []

    def get_room_list(self, danchi_id: str, station_condition: str, wide, prefecture: str) -> list:
        import json
        station_cd1 = station_condition.split(",")[0] if station_condition else ""
        params = {
            "block": "kanto", "tdfk": "13", "area": "", "vacancy": "1",
            "leadtimeCount": "1",
            "station_cd1": station_cd1, "station_cost1": "60", "station_change1": "2",
            "station_condition": station_condition,
            "mode": "init", "id": danchi_id,
        }
        if wide:
            params["rent_high"] = str(wide.rent_max)
            params["walk"] = str(wide.walk_max)
            params["floorspace_low"] = str(wide.area_min)
        raw = self._post("room/list/", params)
        return json.loads(raw.decode("utf-8")) if raw.strip() else []

    def get_room_detail(self, danchi_id: str, room_id: str) -> dict:
        import json
        shisya = danchi_id[:2]
        danchi = danchi_id[3:6]
        shikibetu = danchi_id[6]
        params = {"id": room_id, "shisya": shisya, "danchi": danchi, "shikibetu": shikibetu, "sp": ""}
        raw = self._post("bukken/detail/detail_room/", params)
        data = json.loads(raw.decode("utf-8"))
        return data[0] if data else {}

    def get_danchi_detail(self, danchi_id: str) -> dict:
        """团地级详情。电梯等设施在此层（room 详情不含电梯）。已实测。"""
        import json
        shisya = danchi_id[:2]
        danchi = danchi_id[3:6]
        shikibetu = danchi_id[6]
        params = {"id": danchi_id, "shisya": shisya, "danchi": danchi, "shikibetu": shikibetu, "sp": ""}
        raw = self._post("bukken/detail/detail_bukken_bukken/", params)
        data = json.loads(raw.decode("utf-8"))
        return data[0] if data else {}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_ur_api.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 真实冒烟测试**（可选，需网络）

Run: `python -c "from ur_api import UrApi; a=UrApi('Mozilla/5.0'); print(a.suggest_station('浜松町'))"`
Expected: 返回含 `"value":"2827"` 的列表

- [ ] **Step 6: 提交**

```bash
git add ur_api.py tests/test_ur_api.py
git commit -m "feat: UR API 客户端（suggest/列表/房间/详情 + 退避重试）"
```

---

### Task 3: 通勤时间表解析 + station_condition 生成

**Files:**
- Create: `costtime.py`
- Test: `tests/test_costtime.py`

**Interfaces:**
- Consumes: `UrApi.get_cost_time_xml()`
- Produces: `parse_cost_time(xml_bytes, cost_max, change_max) -> dict[str, tuple[int,int]]`（station_cd → (costTime, changeTimes)）；`build_station_condition(dest_cd, table, cost_max, change_max) -> str`（`"2827,2354,2498,..."`，dest_cd 开头）

**要点：** XML 是 EUC-JP 编码，需先解码再喂给 ElementTree（替换编码声明）。已验证：浜松町 XML 内 ≤60分且≤2次换乘的车站共 829 个。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_costtime.py
from costtime import parse_cost_time, build_station_condition

SAMPLE = """<?xml version="1.0" encoding="euc-jp"?>
<trainDoc status="0"><condition><stationFrom code="2827">
<stationName>浜松町</stationName></stationFrom>
<TimeNmin>60</TimeNmin></condition>
<stationList>
  <stationTo code="2354"><stationName>新橋</stationName>
    <costTime>2</costTime><changeTimes>0</changeTimes></stationTo>
  <stationTo code="9999"><stationName>遠方</stationName>
    <costTime>70</costTime><changeTimes>2</changeTimes></stationTo>
  <stationTo code="8888"><stationName>乗換多</stationName>
    <costTime>40</costTime><changeTimes>5</changeTimes></stationTo>
</stationList></trainDoc>"""

def test_parse_filters_by_cost_and_change():
    table = parse_cost_time(SAMPLE.encode("euc-jp"), 60, 2)
    assert table["2354"] == (2, 0)
    assert "9999" not in table  # cost 70 > 60
    assert "8888" not in table  # change 5 > 2

def test_build_station_condition():
    table = parse_cost_time(SAMPLE.encode("euc-jp"), 60, 2)
    cond = build_station_condition("2827", table, 60, 2)
    assert cond.startswith("2827,")
    assert "2354" in cond
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_costtime.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'costtime'`

- [ ] **Step 3: 写实现**

```python
# costtime.py
import re
import xml.etree.ElementTree as ET

def _fix_encoding(raw: bytes) -> bytes:
    text = raw.decode("euc-jp")
    text = re.sub(r'encoding="[^"]*"', 'encoding="utf-8"', text, count=1)
    return text.encode("utf-8")

def parse_cost_time(xml_bytes: bytes, cost_max: int, change_max: int) -> dict:
    root = ET.fromstring(_fix_encoding(xml_bytes))
    table = {}
    for st in root.iter("stationTo"):
        cost = int(st.find("costTime").text)
        change = int(st.find("changeTimes").text)
        if cost <= cost_max and change <= change_max:
            table[st.get("code")] = (cost, change)
    return table

def build_station_condition(dest_cd: str, table: dict, cost_max: int, change_max: int) -> str:
    codes = sorted(table.keys())
    return dest_cd + "," + ",".join(codes)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_costtime.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git add costtime.py tests/test_costtime.py
git commit -m "feat: 通勤时间表解析 + station_condition 生成"
```

---

### Task 4: 数据模型 + 解析器（API JSON → 领域对象）

**Files:**
- Create: `models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `config.py` 的 `WideFilter`/`Precise`
- Produces:
  - `@dataclass Room`: `room_id, danchi_id, danchi_name, name, url, rent:int, commonfee:int, madori:str, area:float, floor:int, total_floors:int, has_elevator:bool, renovated:bool, walk_min:int, commute_min:int, prefecture:str, skcs:str`
  - `parse_danchi(d, prefecture) -> Danchi`（含 access 解析 → 候选 station/walk）
  - `parse_room(r, danchi) -> Room`
  - `parse_room_detail(detail, room) -> None`（补 year/电梯/翻新/总层数）
  - `parse_access(access_html) -> list[(station_cd, walk_min)]`，取最短路程
  - 字符串清洗函数：`parse_rent("60,900円") -> 60900`；`parse_area("53㎡") -> 53.0`；`parse_floor("4階 /5階") -> (4, 5)`

**access 示例（实测）：** `"<li>JR中央線「高尾」駅バス7分 徒歩1～11分</li>"` —— 提取「駅」前站名、`徒歩N分`（取最小 N）；若无 `徒歩` 只有 `バスX分`，walk 记 `x`（config 可调偏移）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models.py
from models import parse_rent, parse_area, parse_floor, parse_access, parse_danchi, parse_room

def test_parse_rent():
    assert parse_rent("60,900円") == 60900
    assert parse_rent("（4,500円）") == 4500

def test_parse_area():
    assert parse_area("53㎡") == 53.0

def test_parse_floor():
    assert parse_floor("4階 /5階") == (4, 5)

def test_parse_access():
    stations = parse_access("<li>JR中央線「高尾」駅バス7分 徒歩1～11分</li>")
    assert stations[0]["walk"] == 1  # 取最小
    assert stations[0]["station_name"] == "高尾"

def test_parse_danchi_and_room():
    d = parse_danchi({"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10,
                      "access":"<li>JR中央線「高尾」駅 徒歩10分</li>"}, "tokyo")
    r = parse_room({"id":"001080409","rent":"60,900円","type":"3DK","floorspace":"53㎡",
                    "floor":"4階","urlDetail":"/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409"}, d)
    assert r.rent == 60900
    assert r.area == 53.0
    assert r.madori == "3DK"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'models'`

- [ ] **Step 3: 写实现**

```python
# models.py
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

def parse_rent(s: str) -> int:
    m = re.search(r"([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else 0

def parse_area(s: str) -> float:
    m = re.search(r"([\d.]+)\s*㎡", s or "")
    return float(m.group(1)) if m else 0.0

def parse_floor(s: str) -> Tuple[int, int]:
    if not s:
        return (0, 0)
    nums = [int(x) for x in re.findall(r"(\d+)階", s)]
    if len(nums) >= 2:
        return (nums[0], nums[1])
    return (nums[0], 0) if nums else (0, 0)

def parse_access(access_html: str) -> List[dict]:
    entries = []
    for li in re.findall(r"<li>(.*?)</li>", access_html or ""):
        station = re.search(r"「([^」]+)」駅", li)
        walk_m = re.findall(r"徒歩(\d+)", li)
        bus_m = re.search(r"バス(\d+)分", li)
        if not station:
            continue
        walk = min(int(x) for x in walk_m) if walk_m else (int(bus_m.group(1)) if bus_m else 99)
        entries.append({"station_name": station.group(1), "walk": walk})
    return entries

@dataclass
class Danchi:
    danchi_id: str
    name: str
    skcs: str
    room_count: int
    prefecture: str
    station_name: str = ""
    walk_min: int = 99
    has_elevator: bool = False
    commute_min: int = 60

@dataclass
class Room:
    room_id: str
    danchi_id: str
    danchi_name: str
    name: str
    url: str
    rent: int
    commonfee: int
    madori: str
    area: float
    floor: int
    total_floors: int
    has_elevator: bool
    renovated: bool
    walk_min: int
    commute_min: int
    prefecture: str
    skcs: str
    year: int = 0
    facility: str = ""

def parse_danchi(d: dict, prefecture: str) -> Danchi:
    access = parse_access(d.get("access") or "")
    station_name = access[0]["station_name"] if access else ""
    walk_min = access[0]["walk"] if access else 99
    return Danchi(
        danchi_id=d["id"], name=d.get("name") or "", skcs=d.get("skcs") or "",
        room_count=int(d.get("roomCount") or 0), prefecture=prefecture,
        station_name=station_name, walk_min=walk_min,
    )

def parse_room(r: dict, danchi: Danchi) -> Room:
    floor, total = parse_floor(r.get("floor"))
    return Room(
        room_id=r["id"], danchi_id=danchi.danchi_id, danchi_name=danchi.name,
        name=r.get("name") or "", url=r.get("urlDetail") or "",
        rent=parse_rent(r.get("rent")), commonfee=parse_rent(r.get("commonfee")),
        madori=r.get("type") or "", area=parse_area(r.get("floorspace")),
        floor=floor, total_floors=total, has_elevator=danchi.has_elevator,
        renovated=False, walk_min=danchi.walk_min, commute_min=danchi.commute_min,
        prefecture=danchi.prefecture, skcs=danchi.skcs,
    )

def enrich_room_from_detail(room: Room, detail: dict, renovated_keywords: list) -> None:
    room.year = int(re.search(r"\d+", detail.get("year") or "0").group())
    floor, total = parse_floor(detail.get("floor"))
    if total:
        room.total_floors = total
    facility = detail.get("facility") or ""
    room.facility = facility
    # 注意：电梯在团地级，不在房间级 —— 由 danchi.has_elevator 流入 room（parse_room），此处不改写
    room.renovated = any(k in (facility + (detail.get("feature") or "")) for k in renovated_keywords)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 提交**

```bash
git add models.py tests/test_models.py
git commit -m "feat: 数据模型 + API JSON 解析器"
```

---

### Task 5: 打分引擎（硬条件 + 加权分 + 阈值）

**Files:**
- Create: `score.py`
- Test: `tests/test_score.py`

**Interfaces:**
- Consumes: `models.Room`, `config.Precise`, `config.Weights`, `config.Baseline`
- Produces:
  - `hard_pass(room, precise) -> bool`
  - `score_room(room, weights) -> float`（0-100）
  - `baseline_score(cfg) -> float`（金町基准房分数）
  - `should_push(room, cfg) -> tuple[bool, float, str]`（是否推、分数、理由）

**评分曲线（线性）：**
- 通勤：`30 * (1 - commute/60)`，commute≥60 记 0
- 步行：walk≤10 满分20；10-15 线性递减到 6；>15 记 0（但硬条件已挡）
- 月租：`20 * (1 - rent/100000)`，rent≥100000 记 0
- 面积：`15 * min(1, (area-40)/40)`，area≤40 记 0
- 洋室：madori 含"LDK"或 facility 无"和室"记 5，否则 2（数据不明记 2）
- 楼层：floor 1-2 满分5，每高1层扣1，≥6 记 0
- 东京：prefecture=="tokyo" 记 5，否则 0

- [ ] **Step 1: 写失败测试**

```python
# tests/test_score.py
from models import Room
from score import hard_pass, score_room, should_push, baseline_score

def make_room(**kw):
    base = dict(room_id="r1", danchi_id="20_2600", danchi_name="館ヶ丘", name="409号室",
                url="/x", rent=60900, commonfee=4500, madori="3DK", area=53.0,
                floor=4, total_floors=5, has_elevator=True, renovated=True,
                walk_min=10, commute_min=40, prefecture="tokyo", skcs="八王子市",
                year=20, facility="エレベーター、リフォーム")
    base.update(kw)
    return Room(**base)

class Precise:
    rent_max=100000; area_min=40; walk_max=15; walk_ideal=10
    elevator_min_floor=3; year_max=30; renovated_keywords=["リフォーム"]

class Weights:
    commute=30; walk=20; rent=20; area=15; room_type=5; floor=5; tokyo=5

def test_hard_pass_ok():
    assert hard_pass(make_room(), Precise())

def test_hard_pass_rejects_high_floor_no_elevator():
    r = make_room(floor=3, has_elevator=False)
    assert not hard_pass(r, Precise())

def test_hard_pass_rejects_too_old():
    r = make_room(year=51, renovated=False)
    assert not hard_pass(r, Precise())

def test_hard_pass_rejects_too_expensive():
    r = make_room(rent=120000)
    assert not hard_pass(r, Precise())

def test_hard_pass_rejects_small():
    r = make_room(area=30)
    assert not hard_pass(r, Precise())

def test_score_better_than_baseline():
    good = make_room()
    baseline = make_room(rent=95000, area=43, walk=2, commute=30, madori="1DK",
                         floor=5, has_elevator=False, renovated=False)
    assert score_room(good, Weights()) > score_room(baseline, Weights())

def test_should_push_flags_new():
    r = make_room()
    # baseline_score < good score → push
    ok, s, reason = should_push(r, type("Cfg", (), {"precise": Precise(), "weights": Weights(),
                                                    "baseline": None})())
    assert ok
    assert 0 <= s <= 100
    assert reason
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_score.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'score'`

- [ ] **Step 3: 写实现**

```python
# score.py
from models import Room

def hard_pass(room: Room, precise) -> bool:
    if room.commute_min > 60:
        return False
    if room.rent > precise.rent_max:
        return False
    if room.area < precise.area_min:
        return False
    if room.walk_min > precise.walk_max:
        return False
    if room.floor >= precise.elevator_min_floor and not room.has_elevator:
        return False
    if room.year > precise.year_max and not room.renovated:
        return False
    return True

def score_room(room: Room, weights) -> float:
    commute = weights.commute * max(0.0, 1 - room.commute_min / 60)
    if room.walk_min <= 10:
        walk = weights.walk
    elif room.walk_min <= 15:
        walk = weights.walk - 2.8 * (room.walk_min - 10)  # 10→20, 15→6，线性
    else:
        walk = 0.0
    rent = weights.rent * max(0.0, 1 - room.rent / 100000)
    area = weights.area * max(0.0, min(1.0, (room.area - 40) / 40))
    room_type = weights.room_type if ("LDK" in room.madori) else weights.room_type * 0.4
    floor = weights.floor * max(0.0, 1 - max(0, room.floor - 1))
    tokyo = weights.tokyo if room.prefecture == "tokyo" else 0
    return round(commute + walk + rent + area + room_type + floor + tokyo, 1)

def baseline_score(cfg) -> float:
    b = cfg.baseline
    r = Room(room_id="b", danchi_id="", danchi_name="金町", name="", url="",
             rent=b.rent, commonfee=0, madori=b.madori, area=b.area,
             floor=b.floor, total_floors=0, has_elevator=False, renovated=False,
             walk_min=b.walk, commute_min=b.commute, prefecture="tokyo", skcs="")
    return score_room(r, cfg.weights)

def should_push(room: Room, cfg) -> tuple:
    if not hard_pass(room, cfg.precise):
        return False, 0.0, "未通过硬条件"
    s = score_room(room, cfg.weights)
    if cfg.baseline is not None:
        thr = baseline_score(cfg)
    else:
        thr = 0.0
    if s > thr:
        reasons = []
        if room.walk_min <= cfg.precise.walk_ideal:
            reasons.append(f"步行{room.walk_min}分(≤{cfg.precise.walk_ideal}理想)")
        reasons.append(f"月租{room.rent:,}円 面积{room.area:.0f}㎡ {room.madori}")
        if room.has_elevator:
            reasons.append("有电梯")
        return True, s, "；".join(reasons)
    return False, s, "未超过基准房分数"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_score.py -v`
Expected: PASS (8 passed)。若 `test_hard_pass_rejects_too_old` 失败，确认 `year_max=30` 且 renovated=False 时 `year=51 > 30` → 返回 False。

- [ ] **Step 5: 提交**

```bash
git add score.py tests/test_score.py
git commit -m "feat: 打分引擎（硬条件+加权分+基准阈值）"
```

---

### Task 6: SQLite 存储层

**Files:**
- Create: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `models.Room`, `models.Danchi`
- Produces:
  - `class DB`: `init()`；`upsert_danchi(danchi) -> bool`(是否新增)；`upsert_danchi_from_search(d) -> bool`；`is_new_room(room_id) -> bool`；`mark_room_seen(room_id, danchi_id)`；`log_poll(danchi_id, vacancy_count, room_ids)`；`get_all_target_danchi() -> list`；`is_target_danchi(danchi_id) -> bool`
  - 表：`target_danchi`、`seen_rooms`、`poll_log`、`history`
  - 可传 `db_path`（测试用 `:memory:`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_db.py
from db import DB

def make_db():
    return DB(":memory:")

def test_upsert_danchi_returns_new_flag():
    db = make_db()
    db.init()
    d = {"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10}
    assert db.upsert_danchi_from_search(d) is True
    assert db.upsert_danchi_from_search(d) is False  # 已存在

def test_room_seen_flow():
    db = make_db()
    db.init()
    assert db.is_new_room("r1") is True
    db.mark_room_seen("r1", "20_2600")
    assert db.is_new_room("r1") is False

def test_poll_log_written():
    db = make_db()
    db.init()
    db.log_poll("20_2600", 10, ["a","b"])
    rows = db.fetch_poll_log("20_2600", 5)
    assert len(rows) == 1
    assert rows[0]["vacancy_count"] == 10

def test_target_danchi_list():
    db = make_db()
    db.init()
    db.upsert_danchi_from_search({"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10})
    assert db.is_target_danchi("20_2600") is True
    assert len(db.get_all_target_danchi()) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: 写实现**

```python
# db.py
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS target_danchi (
    danchi_id TEXT PRIMARY KEY, name TEXT, skcs TEXT,
    prefecture TEXT, station_name TEXT, walk_min INTEGER,
    first_seen TEXT DEFAULT (datetime('now','localtime')),
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS seen_rooms (
    room_id TEXT PRIMARY KEY, danchi_id TEXT,
    first_seen TEXT DEFAULT (datetime('now','localtime')),
    last_seen TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS poll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    polled_at TEXT DEFAULT (datetime('now','localtime')),
    danchi_id TEXT, vacancy_count INTEGER, room_ids TEXT
);
CREATE TABLE IF NOT EXISTS history (
    room_id TEXT PRIMARY KEY, danchi_id TEXT, score REAL,
    detail TEXT, llm_comment TEXT, found_at TEXT DEFAULT (datetime('now','localtime'))
);
"""

class DB:
    def __init__(self, db_path="data.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert_danchi_from_search(self, d: dict) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM target_danchi WHERE danchi_id=?", (d["id"],))
        exists = cur.fetchone() is not None
        self.conn.execute(
            "INSERT INTO target_danchi(danchi_id,name,skcs) VALUES(?,?,?) "
            "ON CONFLICT(danchi_id) DO UPDATE SET name=excluded.name, skcs=excluded.skcs",
            (d["id"], d.get("name"), d.get("skcs") or ""))
        self.conn.commit()
        return not exists

    def is_target_danchi(self, danchi_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM target_danchi WHERE danchi_id=?", (danchi_id,))
        return cur.fetchone() is not None

    def get_all_target_danchi(self) -> list:
        rows = self.conn.execute(
            "SELECT danchi_id,name,skcs,prefecture FROM target_danchi WHERE active=1")
        return [dict(r) for r in rows]

    def is_new_room(self, room_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM seen_rooms WHERE room_id=?", (room_id,))
        return cur.fetchone() is None

    def mark_room_seen(self, room_id: str, danchi_id: str) -> None:
        self.conn.execute(
            "INSERT INTO seen_rooms(room_id,danchi_id) VALUES(?,?) "
            "ON CONFLICT(room_id) DO UPDATE SET last_seen=datetime('now','localtime')",
            (room_id, danchi_id))
        self.conn.commit()

    def log_poll(self, danchi_id: str, vacancy_count: int, room_ids: list) -> None:
        self.conn.execute(
            "INSERT INTO poll_log(danchi_id,vacancy_count,room_ids) VALUES(?,?,?)",
            (danchi_id, vacancy_count, json.dumps(room_ids)))
        self.conn.commit()

    def fetch_poll_log(self, danchi_id: str, limit: int = 50) -> list:
        rows = self.conn.execute(
            "SELECT * FROM poll_log WHERE danchi_id=? ORDER BY id DESC LIMIT ?",
            (danchi_id, limit))
        return [dict(r) for r in rows]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_db.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 提交**

```bash
git add db.py tests/test_db.py
git commit -m "feat: SQLite 存储层（目标团地/已见房间/poll_log/history）"
```

---

### Task 7: Discord 通知

**Files:**
- Create: `notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `models.Room`, `config.Discord`
- Produces: `send_discord(webhook_url, title, fields, color) -> bool`；`notify_new_room(webhook_url, room, score, reason) -> bool`；`notify_llm_comment(webhook_url, room, comment) -> bool`

**Discord webhook 消息格式：** POST JSON `{"embeds":[{"title":..., "fields":[...], "color":..., "url":...}]}`，字段如：`間取り/月租/面积/步行/通勤/楼层/电梯/链接`。color 用整型（绿色 0x00ff66、橙色 0xff9900）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_notify.py
import json
import notify

def make_poster(monkeypatch, status=204, body=""):
    captured = {}
    def fake_post(url, json=None, timeout=10):
        captured["url"] = url
        captured["json"] = json
        class R:
            status_code = status
            text = body
        return R()
    monkeypatch.setattr("requests.post", fake_post)
    return captured

def test_notify_new_room(monkeypatch):
    from models import Room
    r = Room(room_id="r1", danchi_id="20_2600", danchi_name="館ヶ丘", name="409号室",
             url="https://www.ur-net.go.jp/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409",
             rent=60900, commonfee=4500, madori="3DK", area=53.0, floor=4, total_floors=5,
             has_elevator=True, renovated=True, walk_min=10, commute_min=40,
             prefecture="tokyo", skcs="八王子市")
    cap = make_poster(monkeypatch, 204)
    ok = notify.notify_new_room("https://discord.test/hook", r, 78.5, "步行10分")
    assert ok is True
    embed = cap["json"]["embeds"][0]
    assert "館ヶ丘" in embed["title"]
    assert any(f["name"] == "月租" for f in embed["fields"])
    assert cap["url"] == "https://discord.test/hook"

def test_notify_returns_false_on_error(monkeypatch):
    cap = make_poster(monkeypatch, 500, "boom")
    assert notify.send_discord("https://discord.test/hook", "t", [], 0) is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_notify.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'notify'`

- [ ] **Step 3: 写实现**

```python
# notify.py
import requests

def send_discord(webhook_url: str, title: str, fields: list, color: int, url: str = "") -> bool:
    if not webhook_url:
        return False
    embed = {"title": title, "color": color, "fields": fields}
    if url:
        embed["url"] = url
    try:
        r = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        return r.status_code in (200, 204)
    except Exception:
        return False

def notify_new_room(webhook_url: str, room, score: float, reason: str) -> bool:
    color = 0x00ff66 if score >= 70 else 0xff9900
    fields = [
        {"name": "間取り", "value": f"{room.madori} / {room.area:.0f}㎡", "inline": True},
        {"name": "月租", "value": f"{room.rent:,}円(+共益{room.commonfee:,})", "inline": True},
        {"name": "步行/通勤", "value": f"駅{room.walk_min}分 / 浜松町{room.commute_min}分", "inline": True},
        {"name": "楼层", "value": f"{room.floor}階/{room.total_floors}階" + (" 电梯" if room.has_elevator else ""), "inline": True},
        {"name": "位置", "value": f"{room.skcs}({room.prefecture})", "inline": True},
        {"name": "理由", "value": reason, "inline": False},
    ]
    title = f"🏠 新房源 {room.danchi_name} · 评分 {score}"
    return send_discord(webhook_url, title, fields, color, room.url)

def notify_llm_comment(webhook_url: str, room, comment: str) -> bool:
    fields = [{"name": "🤖 LLM点评", "value": comment or "（无）", "inline": False}]
    return send_discord(webhook_url, f"📝 {room.danchi_name} 点评", fields, 0x9b59b6, room.url)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_notify.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git add notify.py tests/test_notify.py
git commit -m "feat: Discord webhook 通知（新房+LLM点评两波）"
```

---

### Task 8: LLM 点评（Anthropic 格式，读环境变量）

**Files:**
- Create: `llm_comment.py`
- Test: `tests/test_llm_comment.py`

**Interfaces:**
- Consumes: `models.Room`
- Produces: `llm_comment(room, base_url=None, auth_token=None, model=None) -> str`
  - 默认从环境变量 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_DEFAULT_*_MODEL` 读取；未配置或调用失败返回 `""`（不阻塞主流程）
  - 用 POST `{base_url}/v1/messages`，header `x-api-key`（或 `Authorization: Bearer`）为 token，body `{"model":..., "max_tokens":200, "messages":[{"role":"user","content":prompt}]}`

**Prompt 要点：** 把房间的关键字段（団地名/間取り/月租/面积/步行/通勤/楼层/电梯/築年数/设施）拼成一段，要求模型用 ≤3 句中文点评"是否值得考虑、为什么"，只陈述数据里有的信息，不许编造。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_llm_comment.py
import llm_comment

def test_returns_empty_without_config(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert llm_comment.llm_comment(None) == ""

def test_calls_proxy(monkeypatch):
    from models import Room
    captured = {}
    def fake_post(url, headers=None, json=None, timeout=10):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        class R:
            status_code = 200
            def json(self):
                return {"content": [{"text": "值得看：位置好租金低"}]}
        return R()
    monkeypatch.setattr("requests.post", fake_post)
    r = Room("r1","20_2600","館ヶ丘","409号室","",60900,4500,"3DK",53.0,4,5,True,True,10,40,"tokyo","八王子市")
    out = llm_comment.llm_comment(r, base_url="http://127.0.0.1:15721",
                                  auth_token="tk", model="claude-sonnet-4-5")
    assert "值得看" in out
    assert captured["url"] == "http://127.0.0.1:15721/v1/messages"
    assert captured["headers"]["x-api-key"] == "tk"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_llm_comment.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'llm_comment'`

- [ ] **Step 3: 写实现**

```python
# llm_comment.py
import os
import requests

def _build_prompt(room) -> str:
    if room is None:
        return ""
    return (
        f"UR賃貸 新房源评估：\n"
        f"団地：{room.danchi_name}（{room.skcs}，{room.prefecture}）\n"
        f"間取り：{room.madori} / {room.area:.0f}㎡\n"
        f"月租：{room.rent:,}円 + 共益{room.commonfee:,}円\n"
        f"步行到站：{room.walk_min}分，电车到浜松町：{room.commute_min}分\n"
        f"楼层：{room.floor}階/{room.total_floors}階，电梯：{'有' if room.has_elevator else '无'}\n"
        f"築年数：{room.year}年，翻新：{'是' if room.renovated else '未知'}\n"
        f"设施：{room.facility[:80]}\n"
        f"请用不超过3句中文点评这套房是否值得考虑、为什么。只基于以上信息，不要编造。"
    )

def llm_comment(room, base_url=None, auth_token=None, model=None) -> str:
    base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL")
    auth_token = auth_token or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    model = model or os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or "claude-sonnet-4-5"
    if not (base_url and auth_token):
        return ""
    try:
        r = requests.post(
            f"{base_url}/v1/messages",
            headers={"x-api-key": auth_token, "content-type": "application/json"},
            json={"model": model, "max_tokens": 200,
                  "messages": [{"role": "user", "content": _build_prompt(room)}]},
            timeout=60,
        )
        if r.status_code != 200:
            return ""
        data = r.json()
        blocks = data.get("content") or []
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    except Exception:
        return ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_llm_comment.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 真实冒烟测试**（可选，确认本机代理配置）

Run: `python -c "import os; print(os.environ.get('ANTHROPIC_BASE_URL'))"`
Expected: 输出 `http://127.0.0.1:15721`（若为空，说明 cc-switch 代理未启动，LLM 点评会自动跳过，不影响主流程）

- [ ] **Step 6: 提交**

```bash
git add llm_comment.py tests/test_llm_comment.py
git commit -m "feat: LLM点评（Anthropic格式打本地代理，失败静默降级）"
```

---

### Task 9: 发现任务（每月）—— 构建目标团地列表

**Files:**
- Create: `discover.py`
- Test: `tests/test_discover.py`

**Interfaces:**
- Consumes: `UrApi`、`costtime.build_station_condition`、`DB`
- Produces: `run_discover(cfg, api, db) -> int`（新增目标团地数）
  - 逻辑：下载浜松町 cost-time XML → 生成 station_condition → 遍历 config.areas 调 `get_danchi_list`（带宽筛）→ 每个团地 `db.upsert_danchi_from_search` → 返回新增数
  - 容错：单个 area 失败不中断（try/except，记录日志）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_discover.py
import json
import discover
from ur_api import UrApi
from db import DB

class FakeApi:
    def __init__(self, cost_xml, danchi_by_area):
        self._xml = cost_xml
        self._d = danchi_by_area
        self.calls = []
    def get_cost_time_xml(self, station_cd):
        self.calls.append(("xml", station_cd))
        return self._xml
    def get_danchi_list(self, area, cond, wide, pref):
        self.calls.append(("list", area))
        return self._d.get(area, [])

SAMPLE_XML = '<?xml version="1.0" encoding="euc-jp"?><trainDoc><stationList><stationTo code="2354"><stationName>新橋</stationName><costTime>2</costTime><changeTimes>0</changeTimes></stationTo></stationList></trainDoc>'.encode("euc-jp")

def make_cfg():
    class C: pass
    c = C()
    c.areas = ["01"]
    c.prefectures = ["tokyo"]
    c.destination = type("D", (), {"station_cd":"2827","commute_max_min":60,"change_max":2})()
    c.wide_filter = None
    return c

def test_run_discover_adds_new_danchi():
    api = FakeApi(SAMPLE_XML, {"01":[{"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10}]})
    db = DB(":memory:"); db.init()
    n = discover.run_discover(make_cfg(), api, db)
    assert n == 1
    assert db.is_target_danchi("20_2600")

def test_run_discover_idempotent():
    api = FakeApi(SAMPLE_XML, {"01":[{"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10}]})
    db = DB(":memory:"); db.init()
    discover.run_discover(make_cfg(), api, db)
    n = discover.run_discover(make_cfg(), api, db)
    assert n == 0  # 第二次无新增
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_discover.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'discover'`

- [ ] **Step 3: 写实现**

```python
# discover.py
import logging
log = logging.getLogger("discover")

def run_discover(cfg, api, db) -> int:
    table = {}
    try:
        xml = api.get_cost_time_xml(cfg.destination.station_cd)
        import costtime
        table = costtime.parse_cost_time(xml, cfg.destination.commute_max_min, cfg.destination.change_max)
    except Exception:
        log.exception("下载通勤表失败")
        raise
    cond = costtime.build_station_condition(cfg.destination.station_cd, table,
                                            cfg.destination.commute_max_min, cfg.destination.change_max)
    added = 0
    for area in cfg.areas:
        try:
            danchi_list = api.get_danchi_list(area, cond, cfg.wide_filter, "tokyo")
            for d in danchi_list:
                if db.upsert_danchi_from_search(d):
                    added += 1
        except Exception:
            log.exception("area %s 处理失败，跳过", area)
    log.info("discover 完成，新增 %d 个目标团地", added)
    return added
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_discover.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git add discover.py tests/test_discover.py
git commit -m "feat: 每月发现任务（构建目标团地列表）"
```

---

### Task 10: 监控循环（搜索→下钻→diff→打分→两段推送）

**Files:**
- Create: `monitor.py`
- Test: `tests/test_monitor.py`

**Interfaces:**
- Consumes: `UrApi`、`costtime`、`DB`、`models`、`score.should_push`、`notify`、`llm_comment`
- Produces: `run_monitor(cfg, api, db, notify_fn=notify.notify_new_room, comment_fn=llm_comment.llm_comment) -> dict`
  - 返回统计 `{"danchi_checked": n, "new_rooms": n, "pushed": n, "errors": [..]}`
  - 流程：
    1. 生成 station_condition（缓存：若 db 已存通勤表则复用，否则下载）
    2. 遍历 areas → `get_danchi_list`（宽筛）→ 对 roomCount>0 的团地下钻 `get_room_list`
    3. 对每个房间：`db.is_new_room`？→ 是：取 `get_room_detail` 补全 → `score.should_push` → 通过则 `notify_new_room`（第一波）→ `db.mark_room_seen` + 写 history
    4. 每个被查团地写 `poll_log`
    5. 对已推送的房间，异步调 `comment_fn` → `notify_llm_comment`（第二波，失败不阻塞）
  - 反封：两次 API 调用间 `time.sleep(random.uniform(0.2, 0.5))`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_monitor.py
import monitor
from db import DB

class FakeApi:
    def __init__(self, danchi_by_area, rooms_by_danchi, details):
        self._d = danchi_by_area; self._r = rooms_by_danchi; self._det = details
    def get_cost_time_xml(self, cd):
        return '<?xml version="1.0" encoding="euc-jp"?><trainDoc><stationList><stationTo code="2354"><costTime>2</costTime><changeTimes>0</changeTimes></stationTo></stationList></trainDoc>'.encode("euc-jp")
    def suggest_station(self, name):
        return [{"value": "2354", "text": name}]  # 站名→code，落入通勤表
    def get_danchi_list(self, area, cond, wide, pref):
        return self._d.get(area, [])
    def get_room_list(self, danchi_id, cond, wide, pref):
        return self._r.get(danchi_id, [])
    def get_room_detail(self, danchi_id, room_id):
        return self._det.get((danchi_id, room_id), {})
    def get_danchi_detail(self, danchi_id):
        return {"facility": "エレベーター"}  # 电梯在团地级

DANCHI = [{"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":1,
           "access":"<li>JR中央線「高尾」駅 徒歩10分</li>"}]
ROOMS = [{"id":"001080409","rent":"60,900円","type":"3DK","floorspace":"53㎡","floor":"4階",
          "urlDetail":"/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409"}]
DETAIL = {("20_2600","001080409"): {"year":"20","floor":"4階 /5階",
            "facility":"エレベーター、リフォーム"}}

def make_cfg():
    class P: rent_max=100000; area_min=40; walk_max=15; walk_ideal=10; elevator_min_floor=3; year_max=30; renovated_keywords=["リフォーム"]
    class W: commute=30; walk=20; rent=20; area=15; room_type=5; floor=5; tokyo=5
    class B: rent=95000; area=43; walk=2; commute=30; madori="1DK"; western=False; floor=5
    class Dest: station_cd="2827"; commute_max_min=60; change_max=2
    class C: pass
    c = C()
    c.destination = Dest(); c.areas=["01"]; c.prefectures=["tokyo"]
    c.wide_filter=None; c.precise=P(); c.weights=W(); c.baseline=B()
    return c

def test_run_monitor_pushes_new_room():
    api = FakeApi({"01":DANCHI}, {"20_2600":ROOMS}, DETAIL)
    db = DB(":memory:"); db.init()
    pushed = []
    def fake_notify(url, room, score, reason):
        pushed.append((room.room_id, score)); return True
    def fake_comment(room, **kw):
        return "不错"
    stat = monitor.run_monitor(make_cfg(), api, db, notify_fn=fake_notify, comment_fn=fake_comment)
    assert stat["new_rooms"] == 1
    assert stat["pushed"] == 1
    assert pushed[0][0] == "001080409"
    # 幂等：第二次不再推
    stat2 = monitor.run_monitor(make_cfg(), api, db, notify_fn=fake_notify, comment_fn=fake_comment)
    assert stat2["new_rooms"] == 0
    # poll_log 已写
    assert len(db.fetch_poll_log("20_2600", 10)) == 2

def test_run_monitor_records_poll_even_no_new():
    api = FakeApi({"01":DANCHI}, {"20_2600":[]}, {})
    db = DB(":memory:"); db.init()
    stat = monitor.run_monitor(make_cfg(), api, db)
    assert stat["new_rooms"] == 0
    assert len(db.fetch_poll_log("20_2600", 10)) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_monitor.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'monitor'`

- [ ] **Step 3: 写实现**

```python
# monitor.py
import logging
import random
import time
log = logging.getLogger("monitor")

def _resolve_commute(danchi, api, table) -> int:
    """通过站名反查 station_cd → 通勤时间。查不到返回 60（0 分，最坏情况）。"""
    try:
        hits = api.suggest_station(danchi.station_name) if danchi.station_name else []
        for h in hits:
            cd = str(h["value"])
            if cd in table:
                return table[cd][0]
    except Exception:
        pass
    return 60

def run_monitor(cfg, api, db, notify_fn=None, comment_fn=None):
    import costtime
    import models as M
    import score as S
    if notify_fn is None:
        import notify
        notify_fn = notify.notify_new_room
    if comment_fn is None:
        import llm_comment
        comment_fn = llm_comment.llm_comment

    stat = {"danchi_checked": 0, "new_rooms": 0, "pushed": 0, "errors": []}
    table = costtime.parse_cost_time(
        api.get_cost_time_xml(cfg.destination.station_cd),
        cfg.destination.commute_max_min, cfg.destination.change_max)
    cond = costtime.build_station_condition(cfg.destination.station_cd, table,
                                            cfg.destination.commute_max_min, cfg.destination.change_max)
    webhook = cfg.discord.webhook_url

    for area in cfg.areas:
        try:
            danchi_list = api.get_danchi_list(area, cond, cfg.wide_filter, "tokyo")
        except Exception as e:
            stat["errors"].append(f"area {area}: {e}"); continue
        for d in danchi_list:
            db.upsert_danchi_from_search(d)
            if int(d.get("roomCount") or 0) <= 0:
                db.log_poll(d["id"], 0, [])
                continue
            stat["danchi_checked"] += 1
            try:
                danchi = M.parse_danchi(d, "tokyo")
                danchi.commute_min = _resolve_commute(danchi, api, table)
                # 电梯在团地级详情里
                d_detail = api.get_danchi_detail(d["id"])
                danchi.has_elevator = "エレベーター" in (d_detail.get("facility") or "")
                rooms = api.get_room_list(d["id"], cond, cfg.wide_filter, "tokyo")
            except Exception as e:
                stat["errors"].append(f"{d['id']}: {e}"); continue
            current_ids = []
            for r in rooms:
                room = M.parse_room(r, danchi)
                current_ids.append(room.room_id)
                if not db.is_new_room(room.room_id):
                    continue
                try:
                    detail = api.get_room_detail(d["id"], room.room_id)
                    M.enrich_room_from_detail(room, detail, cfg.precise.renovated_keywords)
                except Exception as e:
                    stat["errors"].append(f"detail {room.room_id}: {e}")
                ok, score, reason = S.should_push(room, cfg)
                db.mark_room_seen(room.room_id, d["id"])
                stat["new_rooms"] += 1
                if ok:
                    if notify_fn(webhook, room, score, reason):
                        stat["pushed"] += 1
                        comment = comment_fn(room) if webhook else ""
                        if comment:
                            import notify
                            notify.notify_llm_comment(webhook, room, comment)
                db.conn.execute("INSERT OR IGNORE INTO history(room_id,danchi_id,score,detail) VALUES(?,?,?,?)",
                                (room.room_id, d["id"], score, reason))
                db.conn.commit()
            db.log_poll(d["id"], len(current_ids), current_ids)
            time.sleep(random.uniform(0.2, 0.5))
    return stat
```

> 说明：通勤时间 = 团地最寄駅 → 浜松町的 costTime（从通勤表查得）。`_resolve_commute` 用 `suggest_station` 把 access 里的站名反查成 code，再查表。list_bukken 已用 station_condition 过滤过，因此目标团地必然落在表内；反查失败的兜底记 60 分（0 分）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_monitor.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git add monitor.py tests/test_monitor.py
git commit -m "feat: 监控循环（宽筛→下钻→diff→打分→两段推送）"
```

---

### Task 11: 调度入口（APScheduler）+ 反封节奏 + 运行说明

**Files:**
- Create: `main.py`
- Create: `README.md`
- Create: `run_monitor_once.py`（手动单次运行调试用）

**Interfaces:**
- Consumes: 全部
- Produces: `main.py` 可被 `python main.py` 运行：
  - 启动时加载 config、初始化 DB、先跑一次 `run_discover`
  - APScheduler：
    - `CronTrigger.from_crontab(cfg.schedule.discover_cron)` → `run_discover`（每月）
    - 日间（08:00-22:00）：`IntervalTrigger(minutes=cfg.schedule.day_interval_min)` → `run_monitor`
    - 夜间（22:00-08:00）：`IntervalTrigger(minutes=cfg.schedule.night_interval_min)` → `run_monitor`
    - 用两个独立 trigger 重叠区间即可：日间 job 只在 08-22 生效需 cron 表达或 `IntervalTrigger` + 时间判断；实现采用 **单个 interval job + 根据当前小时选择间隔**（简单可靠）
  - 异常处理：任何 job 抛异常记日志不退出
- `README.md`：安装、配置 webhook、启动、日志路径

- [ ] **Step 1: 写失败测试**

```python
# tests/test_main.py
import main

def test_pick_interval_day():
    cfg = type("S", (), {"day_interval_min":5,"night_interval_min":30})()
    assert main.pick_interval(cfg, 10) == 5  # 10点=日间

def test_pick_interval_night():
    cfg = type("S", (), {"day_interval_min":5,"night_interval_min":30})()
    assert main.pick_interval(cfg, 23) == 30
    assert main.pick_interval(cfg, 3) == 30
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: 写实现**

```python
# main.py
import logging
import time
import random
from config import load_config
from ur_api import UrApi
from db import DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("main")

def pick_interval(cfg, hour: int) -> int:
    if 8 <= hour < 22:
        return cfg.day_interval_min
    return cfg.night_interval_min

def _api(cfg):
    return UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)

def _loop(cfg, db, api):
    from monitor import run_monitor
    from discover import run_discover
    last_discover = 0.0
    while True:
        now = time.time()
        if now - last_discover > 24 * 3600:  # 至少每24h跑一次 discover
            try:
                run_discover(cfg, api, db)
                last_discover = now
            except Exception:
                log.exception("discover 失败")
        try:
            run_monitor(cfg, api, db)
        except Exception:
            log.exception("monitor 失败")
        interval = pick_interval(cfg, time.localtime().tm_hour) * 60
        time.sleep(interval + random.uniform(0, 5))

def main():
    cfg = load_config("config.yaml")
    db = DB("data.db"); db.init()
    api = _api(cfg)
    _loop(cfg, db, api)

if __name__ == "__main__":
    main()
```

```python
# run_monitor_once.py — 手动单次运行（调试用）
import logging
logging.basicConfig(level=logging.INFO)
from config import load_config
from ur_api import UrApi
from db import DB
from monitor import run_monitor

cfg = load_config("config.yaml")
db = DB("data.db"); db.init()
api = UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)
stat = run_monitor(cfg, api, db)
print(stat)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_main.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 写 README.md**

```markdown
# UR団地 新空房监控

监控东京圈 UR賃貸住宅中「到浜松町≤60分」的团地，发现新空房即打分推送 Discord，并记录每次轮询快照。

## 安装
```bash
pip install -r requirements.txt
```

## 配置
1. `config.yaml` → `discord.webhook_url` 填入你的 Discord webhook URL
2. 调整 `precise`（精确条件）、`weights`（打分权重）、`schedule`（轮询节奏）

## 运行
- 手动单次监控：`python run_monitor_once.py`
- 常驻运行：`python main.py`（每月 discover + 日间5分/夜间30分轮询）
- 日志：控制台 + 可重定向到文件

## 数据
- `data.db`：目标团地 / 已见房间 / poll_log（轮询快照，可做时间序列分析）/ history

## 反封说明
轻量 JSON API + 固定 UA + 随机抖动 + 403/429 指数退避。若频繁被封，调大 `schedule` 间隔。
```

- [ ] **Step 6: 全量测试**

Run: `python -m pytest tests/ -v`
Expected: PASS (全部通过)

- [ ] **Step 7: 提交**

```bash
git add main.py run_monitor_once.py README.md tests/test_main.py
git commit -m "feat: 调度入口 + 手动运行脚本 + README"
```

---

### Task 12: 端到端真实验证 + 收尾

**Files:**
- Modify: 可能需要微调 `config.yaml` 的 `areas`/`prefectures`/宽筛

**Interfaces:**
- Consumes: 全部

- [ ] **Step 1: 跑一次真实 discover**

Run: `python -c "import logging;logging.basicConfig(level=logging.INFO);from config import load_config;from ur_api import UrApi;from db import DB;from discover import run_discover;c=load_config('config.yaml');db=DB('data.db');db.init();print(run_discover(c,UrApi(c.http.user_agent),db))"`
Expected: 输出新增目标团地数（应为正数，几十~几百）。若为 0 或报错，检查 `areas` 是否正确、网络是否可达。

- [ ] **Step 2: 跑一次真实 monitor（不推真实 Discord）**

Run: `python -c "import logging;logging.basicConfig(level=logging.INFO);from config import load_config;from ur_api import UrApi;from db import DB;from monitor import run_monitor;c=load_config('config.yaml');c.discord.webhook_url='';db=DB('data.db');db.init();print(run_monitor(c,UrApi(c.http.user_agent),db))"`
Expected: `danchi_checked>0`，新房间按实际数据。确认无 RateLimitedError。

- [ ] **Step 3: 核对通勤时间合理性**

用 `sqlite3 data.db "SELECT danchi_id,name FROM target_danchi LIMIT 10"` 抽查，再人工比对 ur-net.go.jp 详情页确认几个团地确实在浜松町60分圈内。

- [ ] **Step 4: 真实 Discord 推送测试**（用户配合）

把 `config.yaml` 的 webhook_url 填真实值，跑 `run_monitor_once.py`，确认第一波（评分）+ 第二波（LLM点评）都到 Discord。

- [ ] **Step 5: 配置 cron / 开机自启**（用户配合）

将 `python main.py` 加入系统定时任务或开机自启（参考用户已有的 cc-switch/桥接自启方案），并确认机器睡眠策略（见设计文档 §8）。

- [ ] **Step 6: 最终提交**

```bash
git add -A && git commit -m "chore: 端到端验证与配置微调"
```

---

## Self-Review 记录

（写完计划后填写）对照 spec §4-§11：
- §4 三层架构 → Task 9(discover) + Task 10(monitor) + Task 2(API) ✅
- §5 数据模型 → Task 6(db) + Task 4(models) ✅
- §6 打分 → Task 5(score) ✅
- §7 两段推送 → Task 10 内 notify 两次 + Task 7/8 ✅
- §8 轮询频率+反封 → Task 11(pick_interval) + Task 2(退避) + Task 10(抖动) ✅
- §9 项目结构 → Task 1-11 逐步生成 ✅
- §10 LLM 环境变量 → Task 8 ✅
- §11 测试计划 → 每任务 TDD + Task 12 E2E ✅
