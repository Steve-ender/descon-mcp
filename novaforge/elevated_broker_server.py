from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from novaforge.config import load_settings
from novaforge.engines.window_engine import WINDOW_ENGINE
from novaforge.tools.orchestration_tools import execute_plan


def _ok(req_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": req_id, "ok": True, "data": data}


def _err(req_id: str, message: str) -> dict[str, Any]:
    return {"id": req_id, "ok": False, "error": {"code": "broker_error", "message": message}}


def _as_step_result(res: dict[str, Any]) -> dict[str, Any]:
    data = res.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Invalid orchestration result from broker execution")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RuntimeError("Broker execution returned no step result")
    return {"step": steps[0], "raw": res}


def _handle(req: dict[str, Any]) -> dict[str, Any]:
    req_id = str(req.get("id") or "")
    op = str(req.get("op") or "")
    payload = req.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    if not req_id:
        return _err("unknown", "Missing request id")
    if op == "ping":
        return _ok(
            req_id,
            {
                "pid": os.getpid(),
                "host_is_admin": WINDOW_ENGINE.privilege_snapshot().get("host_is_admin"),
            },
        )
    if op == "execute_step":
        step = payload.get("step")
        if not isinstance(step, dict):
            return _err(req_id, "Invalid step payload")
        runtime_profile = str(payload.get("runtime_profile", "basic_reliable"))
        runtime_options = payload.get("runtime_options")
        options = runtime_options if isinstance(runtime_options, dict) else {}
        options["broker_disabled"] = True
        default_ocr_backend = str(payload.get("default_ocr_backend", "auto"))
        settings = load_settings()
        res = asyncio.run(
            execute_plan(
                plan=[step],
                settings=settings,
                stop_on_error=True,
                default_ocr_backend=default_ocr_backend,
                runtime_profile=runtime_profile,
                runtime_options=options,
                enforce_preflight=False,
                ctx=None,
            )
        )
        if not bool(res.get("ok")):
            err_obj = res.get("error") if isinstance(res.get("error"), dict) else {}
            return {
                "id": req_id,
                "ok": False,
                "error": {
                    "code": str(err_obj.get("code") or "broker_step_failed"),
                    "message": str(err_obj.get("message") or "Broker step failed"),
                    "details": err_obj.get("details", {}),
                },
            }
        return _ok(req_id, _as_step_result(res))
    return _err(req_id, f"Unsupported op: {op}")


def main() -> None:
    # Prevent recursive broker routing from within broker process.
    os.environ["NOVAFORGE_BROKER_ROUTE_ON_PRIVILEGE_MISMATCH"] = "0"
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            if not isinstance(req, dict):
                raise RuntimeError("Request must be an object")
            out = _handle(req)
        except Exception as e:
            out = _err("unknown", str(e))
        sys.stdout.write(json.dumps(out, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
