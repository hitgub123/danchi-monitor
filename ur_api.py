# ur_api.py
import logging
import random
import re
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError

log = logging.getLogger("ur_api")

API_BASE = "https://chintai.r6.ur-net.go.jp/chintai/api/"
_DANCHI_ID_RE = re.compile(r"^\d+_\d+$")

class RateLimitedError(Exception):
    pass

class UrApi:
    def __init__(self, user_agent, timeout=30, retry_max=3, backoff_base_sec=2):
        self.user_agent = user_agent
        self.timeout = timeout
        self.retry_max = retry_max
        self.backoff_base_sec = backoff_base_sec

    def _request(self, req: urllib.request.Request) -> bytes:
        """发送请求并统一处理可重试错误。

        GET 和 POST 使用相同的退避策略，避免两条实现逐渐产生行为差异。
        ``retry_max`` 表示最多尝试次数，而不是重试次数。
        """
        for attempt in range(self.retry_max):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.read()
            except HTTPError as e:
                if e.code in (403, 429):
                    if attempt == self.retry_max - 1:
                        raise RateLimitedError("rate limited") from e
                    self._sleep_before_retry(attempt)
                else:
                    raise
            except Exception:
                if attempt == self.retry_max - 1:
                    raise
                self._sleep_before_retry(attempt)
        raise RuntimeError("request loop exhausted unexpectedly")

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self.backoff_base_sec * (2 ** attempt)
        time.sleep(delay + random.uniform(0.05, 0.15))

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
        return self._request(req)

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        return self._request(req)

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

    def _split_danchi_id(self, danchi_id: str):
        """UR 团地 id 形如 XX_YYYY（如 20_2600）。非法/长度异常时记 warning 并返回 None，
        避免每轮轮询 IndexError 静默跳过该团地。"""
        if not _DANCHI_ID_RE.match(danchi_id or ""):
            log.warning("非法 danchi_id，跳过详情获取: %r", danchi_id)
            return None
        shisya, rest = danchi_id.split("_", 1)
        if len(shisya) != 2 or len(rest) != 4:
            log.warning("danchi_id 长度异常，跳过详情获取: %r", danchi_id)
            return None
        return shisya, rest[:3], rest[3]

    def get_room_detail(self, danchi_id: str, room_id: str) -> dict:
        import json
        parts = self._split_danchi_id(danchi_id)
        if parts is None:
            return {}
        shisya, danchi, shikibetu = parts
        params = {"id": room_id, "shisya": shisya, "danchi": danchi, "shikibetu": shikibetu, "sp": ""}
        raw = self._post("bukken/detail/detail_room/", params)
        data = json.loads(raw.decode("utf-8"))
        return data[0] if data else {}

    def get_danchi_detail(self, danchi_id: str) -> dict:
        """团地级详情。电梯等设施在此层（room 详情不含电梯）。已实测。"""
        import json
        parts = self._split_danchi_id(danchi_id)
        if parts is None:
            return {}
        shisya, danchi, shikibetu = parts
        params = {"id": danchi_id, "shisya": shisya, "danchi": danchi, "shikibetu": shikibetu, "sp": ""}
        raw = self._post("bukken/detail/detail_bukken_bukken/", params)
        data = json.loads(raw.decode("utf-8"))
        return data[0] if data else {}
