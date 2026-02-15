from __future__ import annotations

import time
from typing import Any


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def ok(action: str, data: dict[str, Any] | None = None, started_ms: int | None = None) -> dict[str, Any]:
    res: dict[str, Any] = {"ok": True, "action": action, "data": data or {}}
    if started_ms is not None:
        res["timing_ms"] = max(0, now_ms() - started_ms)
    return res


def err(
    action: str,
    message: str,
    hint: str | None = None,
    started_ms: int | None = None,
    code: str = "runtime_error",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    res: dict[str, Any] = {
        "ok": False,
        "action": action,
        "error": {
            "code": code,
            "message": message,
            "hint": hint or "",
            "details": details or {},
        },
    }
    if started_ms is not None:
        res["timing_ms"] = max(0, now_ms() - started_ms)
    return res
