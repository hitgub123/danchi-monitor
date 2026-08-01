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
