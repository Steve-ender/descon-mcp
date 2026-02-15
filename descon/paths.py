from __future__ import annotations

import os
from pathlib import Path


def artifacts_root() -> Path:
    raw = (os.getenv("DESCON_ARTIFACTS_DIR") or "").strip()
    if raw:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
    else:
        # Repository-local default keeps artifacts colocated with this server.
        root = (Path(__file__).resolve().parents[1] / "artifacts").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifacts_subdir(*parts: str) -> Path:
    out = artifacts_root()
    for part in parts:
        out = out / part
    out.mkdir(parents=True, exist_ok=True)
    return out
