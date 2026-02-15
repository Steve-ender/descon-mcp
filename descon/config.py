from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from descon.paths import artifacts_root


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: str | None, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        out = int(value) if value is not None else int(default)
    except Exception:
        out = int(default)
    if minimum is not None:
        out = max(int(minimum), out)
    if maximum is not None:
        out = min(int(maximum), out)
    return out


def _sanitize_hex_color(value: str | None, default: str = "#00ff88") -> str:
    raw = (value or "").strip()
    if not raw:
        return default
    if re.fullmatch(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", raw):
        return raw
    return default


@dataclass
class Settings:
    allowlist: set[str]
    require_allowlist: bool
    allowlist_hard_enforce: bool
    max_actions: int
    auto_start_session: bool
    input_failsafe: bool
    enable_host_ocr: bool
    window_resolve_fallback_foreground: bool
    strict_window_visibility: bool
    strict_privilege_check: bool
    allow_unknown_actions: bool
    best_effort_vision: bool
    best_effort_ocr: bool
    artifacts_dir: str
    enable_activity_glow: bool
    activity_glow_color: str
    activity_glow_thickness: int
    enable_elevated_broker: bool
    elevated_broker_command: str
    elevated_broker_timeout_ms: int
    broker_route_on_privilege_mismatch: bool


_settings_cache: Settings | None = None
_cache_time: float = 0.0
_CACHE_TTL: float = 1.0  # seconds


def invalidate_settings_cache() -> None:
    global _settings_cache, _cache_time
    _settings_cache = None
    _cache_time = 0.0


def load_settings() -> Settings:
    global _settings_cache, _cache_time
    now = time.monotonic()
    if _settings_cache is not None and (now - _cache_time) < _CACHE_TTL:
        return _settings_cache
    raw_allowlist = os.getenv("NOVAFORGE_ALLOWLIST", "")
    allowlist = {x.strip().lower() for x in raw_allowlist.split(",") if x.strip()}
    require_allowlist = _to_bool(os.getenv("NOVAFORGE_REQUIRE_ALLOWLIST"), default=False)
    allowlist_hard_enforce = _to_bool(os.getenv("NOVAFORGE_ALLOWLIST_HARD_ENFORCE"), default=False)
    max_actions = _to_int(os.getenv("NOVAFORGE_MAX_ACTIONS"), default=0, minimum=0, maximum=1000000)
    auto_start_session = _to_bool(os.getenv("NOVAFORGE_AUTO_START_SESSION"), default=True)
    input_failsafe = _to_bool(os.getenv("NOVAFORGE_INPUT_FAILSAFE"), default=False)
    enable_host_ocr = _to_bool(os.getenv("NOVAFORGE_ENABLE_HOST_OCR"), default=False)
    window_resolve_fallback_foreground = _to_bool(
        os.getenv("NOVAFORGE_WINDOW_RESOLVE_FALLBACK_FOREGROUND"),
        default=True,
    )
    strict_window_visibility = _to_bool(os.getenv("NOVAFORGE_STRICT_WINDOW_VISIBILITY"), default=False)
    strict_privilege_check = _to_bool(os.getenv("NOVAFORGE_STRICT_PRIVILEGE_CHECK"), default=False)
    allow_unknown_actions = _to_bool(os.getenv("NOVAFORGE_ALLOW_UNKNOWN_ACTIONS"), default=True)
    best_effort_vision = _to_bool(os.getenv("NOVAFORGE_BEST_EFFORT_VISION"), default=True)
    best_effort_ocr = _to_bool(os.getenv("NOVAFORGE_BEST_EFFORT_OCR"), default=True)
    artifacts_dir = str(artifacts_root())
    enable_activity_glow = _to_bool(os.getenv("NOVAFORGE_ENABLE_ACTIVITY_GLOW"), default=True)
    activity_glow_color = _sanitize_hex_color(os.getenv("NOVAFORGE_ACTIVITY_GLOW_COLOR"), default="#00ff88")
    activity_glow_thickness = _to_int(
        os.getenv("NOVAFORGE_ACTIVITY_GLOW_THICKNESS"),
        default=14,
        minimum=2,
        maximum=64,
    )
    enable_elevated_broker = _to_bool(os.getenv("NOVAFORGE_ENABLE_ELEVATED_BROKER"), default=False)
    elevated_broker_command = (os.getenv("NOVAFORGE_ELEVATED_BROKER_COMMAND") or "").strip()
    elevated_broker_timeout_ms = _to_int(
        os.getenv("NOVAFORGE_ELEVATED_BROKER_TIMEOUT_MS"),
        default=15000,
        minimum=1000,
        maximum=180000,
    )
    broker_route_on_privilege_mismatch = _to_bool(
        os.getenv("NOVAFORGE_BROKER_ROUTE_ON_PRIVILEGE_MISMATCH"),
        default=True,
    )
    settings = Settings(
        allowlist=allowlist,
        require_allowlist=require_allowlist,
        allowlist_hard_enforce=allowlist_hard_enforce,
        max_actions=max_actions,
        auto_start_session=auto_start_session,
        input_failsafe=input_failsafe,
        enable_host_ocr=enable_host_ocr,
        window_resolve_fallback_foreground=window_resolve_fallback_foreground,
        strict_window_visibility=strict_window_visibility,
        strict_privilege_check=strict_privilege_check,
        allow_unknown_actions=allow_unknown_actions,
        best_effort_vision=best_effort_vision,
        best_effort_ocr=best_effort_ocr,
        artifacts_dir=artifacts_dir,
        enable_activity_glow=enable_activity_glow,
        activity_glow_color=activity_glow_color,
        activity_glow_thickness=activity_glow_thickness,
        enable_elevated_broker=enable_elevated_broker,
        elevated_broker_command=elevated_broker_command,
        elevated_broker_timeout_ms=elevated_broker_timeout_ms,
        broker_route_on_privilege_mismatch=broker_route_on_privilege_mismatch,
    )
    _settings_cache = settings
    _cache_time = now
    return settings
