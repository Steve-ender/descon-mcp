from __future__ import annotations

import subprocess

from fastmcp import FastMCP

from novaforge.config import load_settings
from novaforge.result import err, now_ms, ok
from novaforge.safety import assert_can_run
from novaforge.state import STATE
from novaforge.tools.recording_tools import maybe_record


def register_shell_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        description=(
            "Run a shell command and return stdout/stderr. "
            "Uses PowerShell on Windows by default. "
            "Set shell='cmd' for cmd.exe, or shell='bash' for Git Bash / WSL. "
            "timeout_ms limits execution time (default 30000). "
            "Set cwd to change the working directory."
        ),
    )
    def desktop_shell(
        command: str,
        shell: str = "powershell",
        cwd: str | None = None,
        timeout_ms: int = 30000,
        session_id: str | None = None,
    ) -> dict:
        started = now_ms()
        settings = load_settings()
        try:
            resolved_session_id = assert_can_run(settings, session_id=session_id)
            STATE.mark_action(session_id=resolved_session_id)

            shell_lower = shell.strip().lower()
            if shell_lower in {"powershell", "pwsh"}:
                cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
            elif shell_lower == "cmd":
                cmd = ["cmd", "/c", command]
            elif shell_lower == "bash":
                cmd = ["bash", "-c", command]
            else:
                return err(
                    "desktop_shell",
                    f"Unknown shell: {shell}",
                    hint="Use: powershell, cmd, bash",
                    started_ms=started,
                    code="validation_error",
                )

            timeout_sec = max(1, timeout_ms) / 1000.0
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=cwd,
            )

            data = {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "command": command,
                "shell": shell_lower,
            }
            maybe_record(
                "desktop_shell",
                {"command": command, "shell": shell_lower, "cwd": cwd},
                data,
                session_id=resolved_session_id,
            )
            return ok("desktop_shell", data=data, started_ms=started)
        except subprocess.TimeoutExpired:
            return err(
                "desktop_shell",
                f"Command timed out after {timeout_ms}ms",
                started_ms=started,
                code="timeout",
            )
        except Exception as e:
            return err("desktop_shell", str(e), started_ms=started)
