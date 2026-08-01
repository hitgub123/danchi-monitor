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
