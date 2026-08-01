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

def test_parse_access_halfwidth_brackets():
    # UR API 部分团地返回半角括弧 ｢｣（如 神田小川町ハイツ）
    stations = parse_access("<li>都営新宿線｢小川町｣駅 徒歩2分</li>")
    assert stations[0]["station_name"] == "小川町"
    assert stations[0]["walk"] == 2

def test_parse_danchi_and_room():
    d = parse_danchi({"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10,
                      "access":"<li>JR中央線「高尾」駅 徒歩10分</li>"}, "tokyo")
    r = parse_room({"id":"001080409","rent":"60,900円","type":"3DK","floorspace":"53㎡",
                    "floor":"4階","urlDetail":"/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409"}, d)
    assert r.rent == 60900
    assert r.area == 53.0
    assert r.madori == "3DK"
