"""Human-readable lines for the agent-to-agent conversation.

Each A2A message carries one line of plain text (for people) and one JSON data part (for machines).
The text is built from the same data, so the transcript always matches what the agents did.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _hm(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return d.strftime("%m/%d %H:%M")


def _range(start: str, end: str) -> str:
    return f"{_hm(start)}–{datetime.fromisoformat(end).strftime('%H:%M')}"


def request(intent: str, data: dict[str, Any], owner: str) -> str:
    if intent == "availability.query":
        title = data.get("title") or "회의"
        return (f"안녕하세요, {owner}님의 Agent입니다. {owner}님이 '{title}'({int(float(data['duration_min']))}분) 일정을 원합니다. "
                f"{_range(data['window_start'], data['window_end'])} 사이에 가능한 시간을 알려 주세요.")
    if intent == "meeting.propose":
        return f"두 분 모두 가능한 {_range(data['start'], data['end'])}에 '{data['title']}'을(를) 제안합니다."
    if intent == "meeting.confirm":
        return f"{owner}님이 승인했습니다. {_range(data['start'], data['end'])} '{data['title']}' 일정을 확정합니다."
    if intent == "meeting.decline":
        return f"{owner}님이 이번 제안을 거절했습니다. '{data['title']}' 일정은 등록하지 않습니다."
    if intent == "agent.ask":
        return str(data.get("question", ""))
    return intent


def reply(intent: str, result: dict[str, Any], owner: str) -> str:
    if intent == "availability.query":
        slots = result.get("slots", [])
        if not slots:
            return f"{owner}님은 그 기간에 가능한 시간이 없습니다."
        shown = ", ".join(_range(s["start"], s["end"]) for s in slots[:5])
        more = f" 외 {len(slots) - 5}개" if len(slots) > 5 else ""
        return f"{owner}님은 다음 시간에 가능합니다: {shown}{more}. (일정 내용은 공유하지 않습니다.)"
    if intent == "meeting.propose":
        st = result.get("status")
        if st == "accepted":
            return f"수락합니다. {owner}님 일정에 등록했습니다."
        if st == "pending_owner_approval":
            return f"{owner}님께 승인을 요청했습니다. 결정이 나오면 이 대화로 알려 드리겠습니다."
        return f"그 시간은 {owner}님이 가능하지 않습니다. ({result.get('reason', st)})"
    if intent in ("meeting.confirm", "meeting.decline"):
        return "확인했습니다." + (f" {owner}님 일정에 등록했습니다." if intent == "meeting.confirm" else "")
    if intent == "agent.ask":
        return str(result.get("answer", ""))
    return str(result)
