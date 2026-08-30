# notify.py
import requests

def send_discord(webhook_url: str, title: str, fields: list, color: int, url: str = "", content: str = "") -> bool:
    if not webhook_url:
        return False
    embed = {"title": title, "color": color, "fields": fields}
    if url:
        embed["url"] = url if url.startswith("http") else "https://www.ur-net.go.jp" + url
    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        return r.status_code in (200, 204)
    except Exception:
        return False

def notify_new_room(webhook_url: str, room, score: float, reason: str, mention: str = "@everyone") -> bool:
    color = 0x00ff66 if score >= 70 else 0xff9900
    is_online = getattr(room, "online_apply", False)
    apply_status = "⚡ 支持网上秒杀" if is_online else "🏢 窗口/电话限定"
    fields = [
        {"name": "申請方式", "value": apply_status, "inline": True},
        {"name": "間取り", "value": f"{room.madori} / {room.area:.0f}㎡", "inline": True},
        {"name": "月租", "value": f"{room.rent:,}円(+共益{room.commonfee:,})", "inline": True},
        {"name": "步行/通勤", "value": f"駅{room.walk_min}分 / 浜松町{room.commute_min}分", "inline": True},
        {"name": "楼层", "value": f"{room.floor}階/{room.total_floors}階" + (" 电梯" if room.has_elevator else ""), "inline": True},
        {"name": "位置", "value": f"{room.skcs}({room.prefecture})", "inline": True},
        {"name": "理由", "value": reason, "inline": False},
    ]
    room_display = f"{room.danchi_name} {room.name}".strip() if getattr(room, "name", "") else room.danchi_name
    if is_online:
        title = f"⚡ [网上秒杀] {room_display} · 评分 {score}"
        content = f"{mention} ⚡ 发现支持网上直接秒杀的房源！"
    else:
        title = f"🏠 [窗口限定] {room_display} · 评分 {score}"
        content = ""
    return send_discord(webhook_url, title, fields, color, room.url, content=content)

def notify_llm_comment(webhook_url: str, room, comment: str) -> bool:
    fields = [{"name": "🤖 LLM点评", "value": comment or "（无）", "inline": False}]
    room_display = f"{room.danchi_name} {room.name}".strip() if getattr(room, "name", "") else room.danchi_name
    return send_discord(webhook_url, f"📝 {room_display} 点评", fields, 0x9b59b6, room.url)
