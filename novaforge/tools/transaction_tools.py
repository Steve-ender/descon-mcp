from __future__ import annotations

from fastmcp import FastMCP

from novaforge.config import load_settings
from novaforge.engines.transaction_engine import TRANSACTION_ENGINE
from novaforge.result import err, now_ms, ok
from novaforge.safety import assert_can_run
from novaforge.state import STATE


def register_transaction_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "Manage transactions for grouping actions with checkpoints and rollback hints. "
            "action: start (begin transaction, name=, mode=fail|reuse|restart), "
            "status (get current transaction state), "
            "checkpoint (save screenshot checkpoint, label=), "
            "rollback_hint (add undo instructions, reason=), "
            "end (finalize, committed=true|false, summary=), "
            "export (save manifest to JSON, path=)."
        ),
    )
    def desktop_transaction(
        action: str,
        name: str | None = None,
        mode: str = "fail",
        label: str | None = None,
        monitor_index: int = 1,
        left: int | None = None,
        top: int | None = None,
        width: int | None = None,
        height: int | None = None,
        reason: str | None = None,
        max_hints: int = 5,
        committed: bool = True,
        summary: str | None = None,
        path: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        act = action.strip().lower()

        if act == "start":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                data = TRANSACTION_ENGINE.start(name=name, mode=mode, session_id=resolved_session_id)
                STATE.recording_append(
                    "desktop_transaction",
                    {"action": "start", "name": name, "mode": mode},
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_transaction", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_transaction", str(e), started_ms=started, code="transaction_start_failed")

        elif act == "status":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                data = TRANSACTION_ENGINE.status(session_id=resolved_session_id)
                return ok("desktop_transaction", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_transaction", str(e), started_ms=started)

        elif act == "checkpoint":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                region = None
                if None not in (left, top, width, height):
                    region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
                data = TRANSACTION_ENGINE.checkpoint(
                    label=label,
                    monitor_index=monitor_index,
                    region=region,
                    session_id=resolved_session_id,
                )
                STATE.recording_append(
                    "desktop_transaction",
                    {"action": "checkpoint", "label": label, "monitor_index": monitor_index},
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_transaction", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_transaction", str(e), started_ms=started)

        elif act == "rollback_hint":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                data = TRANSACTION_ENGINE.rollback_hint(
                    reason=reason,
                    max_hints=max_hints,
                    session_id=resolved_session_id,
                )
                STATE.recording_append(
                    "desktop_transaction",
                    {"action": "rollback_hint", "reason": reason, "max_hints": max_hints},
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_transaction", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_transaction", str(e), started_ms=started)

        elif act == "end":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                STATE.mark_action(session_id=resolved_session_id)
                data = TRANSACTION_ENGINE.end(
                    committed=committed,
                    summary=summary,
                    session_id=resolved_session_id,
                )
                STATE.recording_append(
                    "desktop_transaction",
                    {"action": "end", "committed": committed, "summary": summary},
                    data,
                    session_id=resolved_session_id,
                )
                return ok("desktop_transaction", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_transaction", str(e), started_ms=started)

        elif act == "export":
            try:
                resolved_session_id = assert_can_run(settings, session_id=session_id)
                data = TRANSACTION_ENGINE.export(path=path, session_id=resolved_session_id)
                return ok("desktop_transaction", data=data, started_ms=started)
            except Exception as e:
                return err("desktop_transaction", str(e), started_ms=started)

        else:
            return err(
                "desktop_transaction",
                f"Unknown action: {action}",
                hint="Use one of: start, status, checkpoint, rollback_hint, end, export",
                started_ms=started,
                code="validation_error",
            )
