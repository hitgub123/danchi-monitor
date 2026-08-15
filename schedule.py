# schedule.py — 相位锁定的轮询调度
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

@dataclass
class DenseWindows:
    hours: List[int]
    start_min: int
    end_min: int
    interval_min: int

class PollSchedule:
    """按一天中的时间段返回"下一网格轮询时刻"。纯逻辑、无 IO、可单测。

    网格判定（对任意分钟 t）：
    - 密集窗口内:   (t.minute - start_min) % interval_min == 0
    - 白天:         t.minute % day_interval_min == 0
    - 夜间:         t.minute % night_interval_min == 0
    每个整点 :00 恒为网格点（0 % 任何数 == 0），故 next_poll_at 永不落空。
    """

    def __init__(self, day_interval_min, night_interval_min,
                 day_start_hour=8, day_end_hour=22, dense=None):
        self.day_interval_min = day_interval_min
        self.night_interval_min = night_interval_min
        self.day_start_hour = day_start_hour
        self.day_end_hour = day_end_hour
        self.dense = dense

    def _is_grid(self, t: datetime) -> bool:
        d = self.dense
        if d and t.hour in d.hours and d.start_min <= t.minute <= d.end_min:
            return (t.minute - d.start_min) % d.interval_min == 0
        if self.day_start_hour <= t.hour < self.day_end_hour:
            return t.minute % self.day_interval_min == 0
        return t.minute % self.night_interval_min == 0

    def next_poll_at(self, now: datetime) -> datetime:
        t = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(24 * 60 * 2):  # :00 恒为网格点, 60 分钟内必收敛; 上限仅兜底
            if self._is_grid(t):
                return t
            t += timedelta(minutes=1)
        return now + timedelta(minutes=max(self.night_interval_min, self.day_interval_min))
