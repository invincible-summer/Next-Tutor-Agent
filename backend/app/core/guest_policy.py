"""Live administrator policy for anonymous, temporary learning."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .atomic import atomic_write_text, file_lock

POLICY_FILE = Path(__file__).resolve().parents[3] / "chat_history/settings/guest_policy.json"


def get_policy() -> dict:
    try:
        data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or type(data.get("allow_guests")) is not bool:
            raise ValueError("invalid guest policy")
        return {"allow_guests": data["allow_guests"],
                "updated_at": float(data.get("updated_at") or 0)}
    except (OSError, ValueError, TypeError):
        return {"allow_guests": False, "updated_at": 0.0}


def guests_allowed() -> bool:
    return get_policy()["allow_guests"]


def set_policy(allow_guests: bool) -> dict:
    policy = {"allow_guests": allow_guests, "updated_at": time.time()}
    with file_lock(POLICY_FILE):
        POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(POLICY_FILE, json.dumps(policy, ensure_ascii=False))
        if not allow_guests:
            from .guest_runtime import purge_all
            purge_all()
    return policy
