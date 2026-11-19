"""Agent profile: character, agent name, and the owner's UI theme.

Stored in <home>/profile.json on the user's disk. The agent writes it; the gateway reads it (for /auth/me and
the Agent Card) without waking the agent.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

CHARACTERS = {"hadaeng": "하댕이", "hanyang": "하냥이"}  # puppy, kitten
FONTS = ("gowun", "jua", "dongle", "system")
MODES = ("system", "light", "dark")
DEFAULT_CHARACTER = "hadaeng"  # Nixie feels like a puppy
PROFILE_EXTENSION = "https://nixie.dev/a2a/ext/profile/v1"  # Agent Card extension with name and character
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class ProfileError(ValueError):
    pass


def defaults() -> dict[str, Any]:
    return {"character": DEFAULT_CHARACTER, "agent_name": CHARACTERS[DEFAULT_CHARACTER],
            "theme": {"mode": "system", "accent": "#4f7cff", "font": "gowun"}}


def _clean(raw: dict[str, Any]) -> dict[str, Any]:
    out = defaults()
    if raw.get("character") in CHARACTERS:
        out["character"] = raw["character"]
        out["agent_name"] = CHARACTERS[raw["character"]]
    name = raw.get("agent_name")
    if isinstance(name, str) and name.strip() and not _CTRL.search(name):
        out["agent_name"] = name.strip()[:20]
    theme = raw.get("theme") if isinstance(raw.get("theme"), dict) else {}
    if theme.get("mode") in MODES:
        out["theme"]["mode"] = theme["mode"]
    if isinstance(theme.get("accent"), str) and _HEX.match(theme["accent"]):
        out["theme"]["accent"] = theme["accent"].lower()
    if theme.get("font") in FONTS:
        out["theme"]["font"] = theme["font"]
    return out


def load_profile(home: Path) -> dict[str, Any]:
    """Profile with defaults. `chosen` is false until the owner saves a profile once."""
    p = Path(home) / "profile.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {**_clean(raw if isinstance(raw, dict) else {}), "chosen": True}
    except (OSError, ValueError):
        return {**defaults(), "chosen": False}


def save_profile(home: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Validate and merge `patch`, then write atomically. Invalid values raise ProfileError."""
    cur = load_profile(home)
    cur.pop("chosen", None)
    if "character" in patch and patch["character"] not in CHARACTERS:
        raise ProfileError(f"character must be one of {sorted(CHARACTERS)}")
    if "agent_name" in patch:
        n = patch["agent_name"]
        if not isinstance(n, str) or not n.strip() or len(n.strip()) > 20 or _CTRL.search(n):
            raise ProfileError("agent_name: 1 to 20 characters, no control characters")
    theme = patch.get("theme") or {}
    if not isinstance(theme, dict):
        raise ProfileError("theme must be an object")
    if "accent" in theme and not (isinstance(theme["accent"], str) and _HEX.match(theme["accent"])):
        raise ProfileError("theme.accent must be #rrggbb")
    if "mode" in theme and theme["mode"] not in MODES:
        raise ProfileError(f"theme.mode must be one of {MODES}")
    if "font" in theme and theme["font"] not in FONTS:
        raise ProfileError(f"theme.font must be one of {FONTS}")
    merged = {**cur, **{k: v for k, v in patch.items() if k in ("character", "agent_name")},
              "theme": {**cur["theme"], **theme}}
    if "character" in patch and "agent_name" not in patch and cur["agent_name"] == CHARACTERS[cur["character"]]:
        merged["agent_name"] = CHARACTERS[patch["character"]]  # default name follows the character
    clean = _clean(merged)
    path = Path(home) / "profile.json"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".profile-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return {**clean, "chosen": True}
