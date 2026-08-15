from datetime import datetime
import pytest
from schedule import PollSchedule, DenseWindows

DENSE = DenseWindows(hours=[10, 12, 14, 16, 18], start_min=27, end_min=42, interval_min=3)

def make_sched(dense=DENSE):
    return PollSchedule(day_interval_min=10, night_interval_min=60,
                        day_start_hour=8, day_end_hour=22, dense=dense)

def dt(hh, mm, day=15):
    return datetime(2026, 8, day, hh, mm)

@pytest.mark.parametrize("minute", [27, 30, 33, 36, 39, 42])
def test_dense_grid_points_are_next_targets(minute):
    # 窗口内网格点前一刻 → 下一网格就是该分钟
    assert make_sched().next_poll_at(dt(12, minute - 1)) == dt(12, minute)

def test_dense_window_first_grid():
    assert make_sched().next_poll_at(dt(12, 20)) == dt(12, 27)

def test_non_grid_minute_in_window_advances():
    assert make_sched().next_poll_at(dt(12, 28)) == dt(12, 30)

def test_after_dense_window_returns_to_day_grid():
    # 12:45 已出密集窗口(>42), 回落 10 分钟日网格
    assert make_sched().next_poll_at(dt(12, 45)) == dt(12, 50)

def test_non_dense_hour_uses_day_grid():
    assert make_sched().next_poll_at(dt(13, 5)) == dt(13, 10)

def test_night_grid_every_60_min():
    assert make_sched().next_poll_at(dt(23, 10)) == dt(0, 0, day=16)

def test_strictly_after_when_exactly_on_grid():
    assert make_sched().next_poll_at(dt(12, 30)) == dt(12, 33)

def test_day_night_boundary_morning():
    assert make_sched().next_poll_at(dt(7, 59)) == dt(8, 0)

def test_day_night_boundary_evening():
    assert make_sched().next_poll_at(dt(21, 59)) == dt(22, 0)

def test_midnight_rollover():
    assert make_sched().next_poll_at(dt(23, 59)) == dt(0, 0, day=16)

def test_no_dense_degradation():
    s = make_sched(dense=None)
    assert s.next_poll_at(dt(12, 27)) == dt(12, 30)
    assert s.next_poll_at(dt(12, 30)) == dt(12, 40)

def test_rejects_zero_intervals():
    with pytest.raises(ValueError):
        PollSchedule(day_interval_min=0, night_interval_min=60)
    with pytest.raises(ValueError):
        PollSchedule(day_interval_min=10, night_interval_min=0)
    with pytest.raises(ValueError):
        PollSchedule(day_interval_min=10, night_interval_min=60,
                     dense=DenseWindows(hours=[10], start_min=27, end_min=42, interval_min=0))
