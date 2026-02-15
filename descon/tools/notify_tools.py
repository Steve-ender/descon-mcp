from __future__ import annotations

import base64
import subprocess

from fastmcp import FastMCP

from descon.config import load_settings
from descon.result import err, now_ms, ok
from descon.safety import assert_can_run
from descon.state import STATE
from descon.tools.recording_tools import maybe_record


def register_notify_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "Show a Windows desktop toast/balloon notification. "
            "title: notification title, message: notification body. "
            "timeout_ms: how long the notification stays visible (default 5000)."
        ),
    )
    def desktop_notify(
        title: str = "Descon",
        message: str = "",
        timeout_ms: int = 5000,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)

            # Base64-encode strings for PowerShell injection safety
            title_b64 = base64.b64encode(title.encode("utf-16-le")).decode("ascii")
            msg_b64 = base64.b64encode(message.encode("utf-16-le")).decode("ascii")
            display_ms = max(1000, timeout_ms)

            ps_script = (
                "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.Visible = $true; "
                f"$title = [System.Text.Encoding]::Unicode.GetString([System.Convert]::FromBase64String('{title_b64}')); "
                f"$msg = [System.Text.Encoding]::Unicode.GetString([System.Convert]::FromBase64String('{msg_b64}')); "
                f"$n.ShowBalloonTip({display_ms}, $title, $msg, 'Info'); "
                f"Start-Sleep -Milliseconds {display_ms}; "
                "$n.Dispose();"
            )

            timeout_sec = (display_ms / 1000.0) + 10
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )

            data = {
                "notified": result.returncode == 0,
                "title": title,
                "message": message,
                "timeout_ms": timeout_ms,
                "exit_code": result.returncode,
            }
            if result.stderr.strip():
                data["stderr"] = result.stderr.strip()

            maybe_record("desktop_notify", {"title": title, "message": message, "timeout_ms": timeout_ms}, data, session_id=resolved_session_id)
            return ok("desktop_notify", data=data, started_ms=started)
        except subprocess.TimeoutExpired:
            return err("desktop_notify", "Notification timed out", started_ms=started, code="timeout")
        except Exception as e:
            return err("desktop_notify", str(e), started_ms=started)
