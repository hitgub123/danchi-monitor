# tests/test_models.py
from models import parse_rent, parse_area, parse_floor, parse_access, parse_danchi, parse_room

def test_parse_rent():
    assert parse_rent("60,900円") == 60900
    assert parse_rent("（4,500円）") == 4500

def test_parse_area():
    assert parse_area("53㎡") == 53.0
    # UR API 实际返回 HTML 实体 &#13217;
    assert parse_area("53&#13217;") == 53.0
    assert parse_area("53㎡ / 4階") == 53.0

def test_parse_floor():
    assert parse_floor("4階 /5階") == (4, 5)

def test_parse_access():
    # 巴士路段(バス7分徒歩1～11分) → 距离差 walk=99, 不再当徒步1分
    stations = parse_access("<li>JR中央線「高尾」駅バス7分 徒歩1～11分</li>")
    assert stations[0]["walk"] == 99
    assert stations[0]["station_name"] == "高尾"
    assert stations[0]["has_bus"] is True

def test_parse_access_prefers_walk_over_bus_in_same_li():
    # 同一 li 里既有纯徒步又有巴士 → 取纯徒步段(勝どき徒歩8分), 忽略银座巴士线
    html = "<li>都営大江戸線「勝どき」駅 徒歩8分 東京メトロ銀座線ほか「銀座」駅 バス15分徒歩1分</li>"
    stations = parse_access(html)
    assert stations[0]["station_name"] == "勝どき"
    assert stations[0]["walk"] == 8
    assert stations[0]["has_bus"] is False

def test_parse_access_chooses_walk_across_lis():
    # 多个 li: 巴士 li 在前, 纯徒步 li 在后 → 应选纯徒步的最近站(29分), 不选巴士
    html = ("<li>JR中央線「高尾」駅バス7分 徒歩1～11分</li>"
            "<li>JR中央本線「高尾」駅 徒歩29～38分</li>")
    stations = parse_access(html)
    assert stations[0]["station_name"] == "高尾"
    assert stations[0]["walk"] == 29
    assert stations[0]["has_bus"] is False

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

def _room(total_floors, has_elevator, floor=4):
    from models import Room
    return Room(room_id="r", danchi_id="20_2600", danchi_name="館ヶ丘", name="409号室", url="",
                rent=60000, commonfee=4500, madori="3DK", area=53.0, floor=floor,
                total_floors=total_floors, has_elevator=has_elevator, renovated=False,
                walk_min=10, commute_min=40, prefecture="tokyo", skcs="")

def test_enrich_elevator_from_room_detail():
    # 2026-08-02: 电梯以房间级设施为准(团地级常误报), 楼栋>5层才默认有电梯
    from models import enrich_room_from_detail
    # 5层楼, 房间设施无エレベーター → 无电梯(覆盖初始True)
    r = _room(total_floors=5, has_elevator=True)
    enrich_room_from_detail(r, {"year":"30", "floor":"4階 /5階", "facility":"エアコン"}, ["リフォーム"])
    assert r.has_elevator is False
    # 10层楼 → 默认有电梯(即使设施没写)
    r2 = _room(total_floors=10, has_elevator=False, floor=8)
    enrich_room_from_detail(r2, {"year":"5", "floor":"8階 /10階", "facility":"エアコン"}, ["リフォーム"])
    assert r2.has_elevator is True
    # 房间设施明确有エレベーター → 有电梯(即使低层)
    r3 = _room(total_floors=5, has_elevator=False, floor=3)
    enrich_room_from_detail(r3, {"year":"5", "floor":"3階 /5階", "facility":"エレベーター、エアコン"}, ["リフォーム"])
    assert r3.has_elevator is True
